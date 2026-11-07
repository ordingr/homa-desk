"""Owns the live index: serves the persisted store at start, rescans in the
background, folds watcher hints into a delta, swaps finished walks in atomically
and keeps ``built_at`` equal to the completion time of the last full walk.

Settings read (all optional, defensive): ``index.roots`` (strings or
``{"path": ...}``), ``index.excludes`` (pattern lines), ``index.rescan_hours``
(24), ``index.root_timeout_s`` (60), ``index.pause_on_battery`` (true; start,
daily and overflow scans wait while on battery, a manual rescan never does),
``index.scan_if_older_hours`` (12; a persisted index younger than this, built for
the same roots, is served without a start scan).
"""
import collections
import ctypes
import logging
import os
import queue
import threading
import time
from typing import Any, Callable, Optional

from .. import paths
from ..security import pathcheck
from . import crawler as crawler_mod
from . import watcher as watcher_mod

log = logging.getLogger("assistant.index.manager")

DELTA_FOLD_LIMIT = 50_000
HINT_REPLAY_LIMIT = 200_000
MINI_SCAN_BUDGET = 50_000
OVERFLOW_DEBOUNCE_S = 1.0
HINT_IDLE_WAIT_S = 5.0
HINT_BATCH_S = 0.25
HINT_BATCH_MAX = 5000
BATTERY_RETRY_S = 900.0
BATTERY_REASONS = ("start", "daily", "overflow")


def _default_builder_factory() -> Callable[[], Any]:
    from .store import IndexBuilder
    return IndexBuilder


def _default_excludes_factory(patterns: list) -> Optional[Any]:
    if not patterns:
        return None
    from .excludes import Excludes
    try:
        return Excludes(patterns)
    except (TypeError, AttributeError):
        return Excludes("\n".join(patterns))


def _default_delta_factory() -> Any:
    from .store import IndexDelta
    return IndexDelta()


def _on_battery() -> bool:
    class Status(ctypes.Structure):
        _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte), ("pct", ctypes.c_ubyte),
                    ("saver", ctypes.c_ubyte), ("life", ctypes.c_ulong), ("full", ctypes.c_ulong)]
    try:
        st = Status()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return st.ac == 0
    except Exception:
        pass
    return False


class IndexManager:
    def __init__(self, settings, scheduler, store_path,
                 builder_factory: Optional[Callable[[], Any]] = None,
                 store_cls: Optional[Any] = None,
                 excludes_factory: Optional[Callable[[list], Any]] = None,
                 delta_factory: Optional[Callable[[], Any]] = None) -> None:
        self._settings = settings
        self._scheduler = scheduler
        self._store_path = str(store_path)
        self._builder_factory = builder_factory or _default_builder_factory()
        if store_cls is None:
            from .store import IndexStore as store_cls
        self._store_cls = store_cls
        self._excludes_factory = excludes_factory or _default_excludes_factory
        self._delta_factory = delta_factory or _default_delta_factory

        self._stop = threading.Event()
        self._lifecycle = threading.Lock()
        self._cond = threading.Condition()
        self._requested: Optional[str] = None
        self._due = 0.0
        self._reconfigure = True
        self._walking = False
        self._walk_thread: Optional[threading.Thread] = None
        self._hint_thread: Optional[threading.Thread] = None
        self._hints: queue.SimpleQueue = queue.SimpleQueue()
        self._replay: collections.deque = collections.deque()
        self._replay_lock = threading.Lock()
        self._replay_overflowed = False
        self._daily = None
        self._watchers: dict = {}
        self._roots: list = []
        self._patterns: list = []
        self._scan_sig: Optional[tuple] = None
        self._excludes: Any = None
        self._root_timeout = 60.0
        self._pause_on_battery = True
        self._delta_ops = 0
        self._delta_paths: set = set()
        self._overflow_count = 0
        self._rewalks_scheduled = 0
        self._last_walk_started: Optional[float] = None
        self._last_walk_seconds: Optional[float] = None
        self._exclude_counts: dict = {}
        self._root_status: dict = {}
        self._lost: set = set()
        self._dirs: Optional[int] = None
        self._loaded_from_disk = False
        self._built_at: Optional[float] = None
        self._matches = crawler_mod._denylist_matches()
        self._state: tuple = (None, None)

    def start(self) -> None:
        store = self._load_persisted()
        if store is None:
            store = self._builder_factory().finish()
        else:
            self._loaded_from_disk = True
        self._built_at = getattr(store, "built_at", None) if self._loaded_from_disk else None
        self._state = (store, self._delta_factory())
        with self._lifecycle:
            if self._stop.is_set():
                return
            self._settings.on_change("index", self._on_settings)
            self._hint_thread = threading.Thread(target=self._hint_loop, name="index-hints", daemon=True)
            self._hint_thread.start()
            self._walk_thread = threading.Thread(target=self._walk_loop, name="index-walk", daemon=True)
            self._walk_thread.start()
        self.request_rescan(self._start_reason(store))

    def _start_reason(self, store) -> str:
        """"start" when the index must be rebuilt now; "config" to only apply the settings (watchers, daily
        timer) because the persisted index is recent enough and was built for the configured roots."""
        cfg = self._config()
        if not self._loaded_from_disk or self._built_at is None:
            return "start"
        if time.time() - self._built_at > cfg["scan_if_older_hours"] * 3600.0:
            return "start"
        built_for = {os.path.normcase(r) for r in getattr(store, "roots", ())}
        if built_for != {os.path.normcase(r) for r in cfg["roots"]}:
            return "start"
        return "config"

    @property
    def settings(self):
        return self._settings

    def stop(self) -> None:
        self._stop.set()
        self._hints.put(None)
        with self._cond:
            self._cond.notify_all()
        with self._lifecycle:
            threads = (self._walk_thread, self._hint_thread)
        for th in threads:
            if th is None or th is threading.current_thread():
                continue
            try:
                th.join(timeout=3)
            except RuntimeError:
                pass
        daily, self._daily = self._daily, None
        if daily is not None:
            try:
                daily.cancel()
            except Exception:
                log.exception("daily scan handle could not be cancelled")
        for w in list(self._watchers.values()):
            try:
                w.stop()
            except Exception:
                log.exception("watcher could not be stopped")
        self._watchers.clear()

    def current(self) -> tuple:
        return self._state

    def request_rescan(self, reason: str = "manual", delay: float = 0.0) -> None:
        when = time.monotonic() + delay
        with self._cond:
            if self._requested is None:
                self._requested, self._due = reason, when
            else:
                self._due = min(self._due, when)
            self._cond.notify_all()

    def status(self) -> dict:
        store, _ = self._state
        roots = []
        for path in self._roots:
            info = dict(self._root_status.get(path) or {"state": "pending", "entries": 0,
                                                       "last_walk_s": None, "error": None})
            roots.append({"path": path, **info})
        return {
            "entries": getattr(store, "entry_count", 0) if store is not None else 0,
            "dirs": self._dirs,
            "built_at": self._built_at,
            "walking": self._walking,
            "roots": roots,
            "excludes": dict(self._exclude_counts),
            "overflow_count": self._overflow_count,
            "rewalks_scheduled": self._rewalks_scheduled,
            "delta_ops": self._delta_ops,
            "last_walk_s": self._last_walk_seconds,
            "loaded_from_disk": self._loaded_from_disk,
        }

    def _load_persisted(self):
        if not os.path.exists(self._store_path):
            return None
        try:
            return self._store_cls.load(self._store_path)
        except Exception:
            log.exception("persisted index could not be loaded; starting empty")
            return None

    def _on_settings(self, key: str, value: Any) -> None:
        cfg = self._config()
        sig = (tuple(cfg["roots"]), tuple(cfg["patterns"]))
        with self._cond:
            self._reconfigure = True
        if sig == self._scan_sig:
            return
        self.request_rescan("settings")

    def _config(self) -> dict:
        try:
            raw = self._settings.get("index") or {}
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        roots = []
        for item in raw.get("roots") or []:
            path = item.get("path") if isinstance(item, dict) else item
            if isinstance(path, str) and path.strip():
                roots.append(os.path.abspath(os.path.expandvars(os.path.expanduser(path.strip()))))
        patterns = [p for p in (raw.get("excludes") or []) if isinstance(p, str)]

        def num(key, default, low):
            v = raw.get(key, default)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= low else default

        hours = raw.get("scan_if_older_hours", 12)
        valid = isinstance(hours, (int, float)) and not isinstance(hours, bool) and 1 <= hours <= 168
        fresh_hours = float(hours) if valid else 12.0
        return {"roots": list(dict.fromkeys(roots)), "patterns": patterns,
                "rescan_hours": num("rescan_hours", 24.0, 0.01),
                "root_timeout_s": num("root_timeout_s", 60.0, 0.1),
                "pause_on_battery": raw.get("pause_on_battery", True) is not False,
                "scan_if_older_hours": fresh_hours}

    @staticmethod
    def _own_data_rules(roots=()) -> list:
        """The Assistant's data folder is never indexed, whatever the user's exclude settings say. A root that
        is a junction or symlink over (a parent of) the folder reaches it under that root's own spelling, so
        the rule is emitted in that spelling too."""
        rules = ["**/.assistant/"]
        try:
            home = os.path.realpath(str(paths.home())).rstrip("\\/")
        except (OSError, ValueError):
            return rules
        if len(home) > 1 and home[1] == ":":
            rules.append(home)
            for root in roots:
                real = pathcheck._resolve(root)
                if not real or not pathcheck._inside(home, real):
                    continue
                spelling = os.path.abspath(root).rstrip("\\/") + home[len(real.rstrip("\\/")):]
                if os.path.normcase(spelling) != os.path.normcase(home) and spelling not in rules:
                    rules.append(spelling)
        return rules

    def _apply_config(self) -> dict:
        cfg = self._config()
        self._scan_sig = (tuple(cfg["roots"]), tuple(cfg["patterns"]))
        self._patterns = cfg["patterns"]
        try:
            self._excludes = self._excludes_factory(cfg["patterns"] + self._own_data_rules(cfg["roots"]))
        except Exception:
            log.exception("excludes could not be built; crawling without excludes is refused")
            self._excludes = None
            cfg["roots"] = []
        self._lost &= set(cfg["roots"])
        self._root_timeout = cfg["root_timeout_s"]
        self._pause_on_battery = cfg["pause_on_battery"]
        self._roots = cfg["roots"]
        for path in list(self._watchers):
            if path not in cfg["roots"]:
                self._watchers.pop(path).stop()
        for path in cfg["roots"]:
            if path in self._watchers or crawler_mod.is_remote(path) or not os.path.isdir(path):
                continue
            w = watcher_mod.Watcher(path, lambda action, p, root=path: self._hints.put((action, p, root)),
                                    self._on_overflow, lambda root=path: self._on_missing(root))
            self._watchers[path] = w
            self._lost.discard(path)
            w.start()
        if self._daily is not None:
            self._daily.cancel()
        self._daily = self._scheduler.every(cfg["rescan_hours"] * 3600.0, lambda: self.request_rescan("daily"))
        return cfg

    def _on_missing(self, root: str) -> None:
        """A watched folder disappeared: show it as missing and stop watching; no rescan is requested. The next
        scan (daily or manual) re-applies the settings, which re-arms the watcher if the folder is back."""
        self._watchers.pop(root, None)
        self._lost.add(root)
        prev = self._root_status.get(root) or {}
        self._root_status[root] = {**prev, "state": "missing", "error": "folder not found"}

    def _on_overflow(self) -> None:
        self._overflow_count += 1
        self._rewalks_scheduled += 1
        self.request_rescan("overflow", delay=OVERFLOW_DEBOUNCE_S)

    def _walk_loop(self) -> None:
        while not self._stop.is_set():
            with self._cond:
                while self._requested is None and not self._stop.is_set():
                    self._cond.wait()
                if self._stop.is_set():
                    return
                wait = self._due - time.monotonic()
                if wait > 0:
                    self._cond.wait(timeout=wait)
                    continue
                reason, self._requested, self._due = self._requested, None, 0.0
                reconfigure, self._reconfigure = self._reconfigure, False
            try:
                if reconfigure or self._lost:
                    self._apply_config()
                if reason == "config":
                    self._seed_status()
                    continue
                if reason in BATTERY_REASONS and self._pause_on_battery and _on_battery():
                    self._scheduler.at(time.time() + BATTERY_RETRY_S, lambda r=reason: self.request_rescan(r))
                    continue
                self._walk(reason)
            except Exception:
                log.exception("index walk failed")
                self._walking = False

    def _seed_status(self) -> None:
        """Per-root state for an index that was loaded from disk and not rewalked."""
        store, _ = self._state
        counter = getattr(store, "count_under", None)
        for path in self._roots:
            if path in self._root_status:
                continue
            if os.path.isdir(path) or crawler_mod.is_remote(path):
                entries = counter(path) if counter is not None else 0
                self._root_status[path] = {"state": "ok", "entries": entries, "last_walk_s": None, "error": None}
            else:
                self._root_status[path] = {"state": "missing", "entries": 0, "last_walk_s": None,
                                           "error": "folder not found"}
        self._dirs = getattr(store, "dir_count", None)

    def _walk(self, reason: str) -> None:
        roots = list(self._roots)
        if not roots:
            empty = self._builder_factory().finish()
            self._persist(empty)
            self._state = (empty, self._delta_factory())
            self._root_status, self._exclude_counts, self._dirs = {}, {}, 0
            self._built_at = None
            return
        started = time.time()
        with self._replay_lock:
            self._replay.clear()
            self._replay_overflowed = False
            self._walking = True
        self._last_walk_started = started
        for p in roots:
            prev = self._root_status.get(p) or {}
            self._root_status[p] = {**prev, "state": "walking", "error": None}
        crawler = crawler_mod.Crawler(roots, self._excludes, self._builder_factory,
                                      root_timeout_s=self._root_timeout, stop=self._stop)
        result = crawler.run()
        if self._stop.is_set():
            self._walking = False
            return
        store = result.store
        done = time.time()
        try:
            if getattr(store, "built_at", None) is None or store.built_at < started:
                store.built_at = done
        except Exception:
            pass
        self._persist(store)
        new_delta = self._delta_factory()
        with self._replay_lock:
            pending = list(self._replay)
            overflowed = self._replay_overflowed
            self._replay.clear()
            self._walking = False
            self._state = (store, new_delta)
            self._delta_ops = 0
            self._delta_paths = set()
        self._built_at = getattr(store, "built_at", done)
        self._loaded_from_disk = False
        self._last_walk_seconds = result.seconds
        self._exclude_counts = result.excludes
        self._dirs = sum(r.dirs for r in result.roots)
        self._root_status = {
            r.path: {"state": r.state, "entries": r.entries, "last_walk_s": round(r.seconds, 3),
                     "error": r.error, "errors": r.errors}
            for r in result.roots}
        for action, path, root in pending:
            self._apply_hint(action, path, root)
        if overflowed:
            self.request_rescan("hint-replay-overflow")
        log.info("index walk (%s) finished: %s entries in %.1fs", reason,
                 getattr(store, "entry_count", "?"), result.seconds)

    def _persist(self, store) -> None:
        tmp = self._store_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self._store_path) or ".", exist_ok=True)
            store.save(tmp)
            os.replace(tmp, self._store_path)
        except Exception:
            log.exception("could not persist the index; serving it from memory only")
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _hint_loop(self) -> None:
        while not self._stop.is_set():
            try:
                first = self._hints.get(timeout=HINT_IDLE_WAIT_S)
            except queue.Empty:
                continue
            batch = [first]
            deadline = time.monotonic() + HINT_BATCH_S
            while len(batch) < HINT_BATCH_MAX:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                try:
                    batch.append(self._hints.get(timeout=left))
                except queue.Empty:
                    break
            last = None
            for hint in batch:
                if hint is None or hint == last:
                    continue
                last = hint
                action, path, root = hint
                try:
                    with self._replay_lock:
                        if self._walking:
                            if len(self._replay) < HINT_REPLAY_LIMIT:
                                self._replay.append((action, path, root))
                            else:
                                self._replay_overflowed = True
                    self._apply_hint(action, path, root)
                except Exception:
                    log.exception("hint handling failed")

    def _excluded(self, root: str, path: str, leaf_is_dir: bool) -> bool:
        excludes = self._excludes
        if excludes is None:
            return False
        rel = os.path.relpath(path, root)
        if rel.startswith(".."):
            return True
        parts = rel.split(os.sep)
        acc = ""
        base = root
        for i, part in enumerate(parts):
            acc = f"{acc}/{part}" if acc else part
            base = os.path.join(base, part)
            is_dir = True if i < len(parts) - 1 else leaf_is_dir
            if excludes.matching_rule(base, acc, part, is_dir):
                return True
        return False

    def _apply_hint(self, action: int, path: str, root: str) -> None:
        store, delta = self._state
        if store is None or delta is None:
            return
        if action in (watcher_mod.ACTION_REMOVED, watcher_mod.ACTION_RENAMED_OLD):
            self._remove_tree(store, delta, path)
            return
        try:
            st = os.lstat(path)
        except OSError:
            self._remove_tree(store, delta, path)
            return
        attrs = getattr(st, "st_file_attributes", 0)
        is_dir = bool(attrs & crawler_mod.ATTR_DIRECTORY)
        if self._excluded(root, path, is_dir):
            return
        if is_dir and action == watcher_mod.ACTION_MODIFIED:
            return
        flags = crawler_mod.entry_flags(attrs, is_dir)
        if self._matches(path):
            flags |= crawler_mod.FLAG_DENIED
        delta.add(path, st.st_size, st.st_mtime, flags)
        self._delta_paths.add(path)
        self._bump_delta(1)
        if is_dir and not attrs & crawler_mod.ATTR_REPARSE and action in (
                watcher_mod.ACTION_RENAMED_NEW, watcher_mod.ACTION_ADDED):
            self._scan_into_delta(root, path, delta)

    def _remove_tree(self, store, delta, path: str) -> None:
        delta.remove(path)
        n = 1
        prefix = path + os.sep
        for added in [p for p in self._delta_paths if p.startswith(prefix)]:
            delta.remove(added)
            self._delta_paths.discard(added)
            n += 1
        stack = [path]
        while stack and n < DELTA_FOLD_LIMIT:
            d = stack.pop()
            try:
                ids = list(store.iter_in_dir(d))
            except Exception:
                break
            for i in ids:
                child = store.path(i)
                delta.remove(child)
                n += 1
                if store.flags(i) & crawler_mod.FLAG_DIR:
                    stack.append(child)
        self._bump_delta(n)

    def _scan_into_delta(self, root: str, top: str, delta) -> None:
        stack = [top]
        budget = MINI_SCAN_BUDGET
        while stack and budget > 0 and not self._stop.is_set():
            d = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for entry in it:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    attrs = st.st_file_attributes
                    is_dir = bool(attrs & crawler_mod.ATTR_DIRECTORY)
                    if self._excluded(root, entry.path, is_dir):
                        continue
                    flags = crawler_mod.entry_flags(attrs, is_dir)
                    if self._matches(entry.path):
                        flags |= crawler_mod.FLAG_DENIED
                    delta.add(entry.path, st.st_size, st.st_mtime, flags)
                    self._delta_paths.add(entry.path)
                    budget -= 1
                    if is_dir and not attrs & crawler_mod.ATTR_REPARSE:
                        stack.append(entry.path)
        self._bump_delta(MINI_SCAN_BUDGET - budget)

    def _bump_delta(self, n: int) -> None:
        self._delta_ops += n
        if self._delta_ops > DELTA_FOLD_LIMIT:
            self._delta_ops = 0
            self.request_rescan("delta-fold", delay=OVERFLOW_DEBOUNCE_S)
