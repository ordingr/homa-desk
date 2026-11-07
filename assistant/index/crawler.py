"""Directory crawl with os.scandir: no file is ever opened, reparse points are
never followed, excluded directories are never entered.

Builder contract (plan 4b): ``add_dir(path, parent_id) -> id`` (the root gets
parent_id -1), ``add(parent_id, name, size, mtime, flags) -> id``,
``finish() -> store``. Excludes contract (probe-compatible):
``matching_rule(full, rel, name, is_dir) -> rule label | None``; per-rule counts
are kept here and returned in ``CrawlResult.excludes``.
"""
import ctypes
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger("assistant.index.crawler")

ATTR_HIDDEN = 0x2
ATTR_SYSTEM = 0x4
ATTR_DIRECTORY = 0x10
ATTR_REPARSE = 0x400
ATTR_OFFLINE = 0x1000
ATTR_RECALL_ON_OPEN = 0x40000
ATTR_RECALL_ON_DATA_ACCESS = 0x400000
_PLACEHOLDER_ATTRS = ATTR_OFFLINE | ATTR_RECALL_ON_OPEN | ATTR_RECALL_ON_DATA_ACCESS

try:
    from . import store as _store_mod
except Exception:
    _store_mod = None

FLAG_DIR = getattr(_store_mod, "FLAG_DIR", 1)
FLAG_HIDDEN = getattr(_store_mod, "FLAG_HIDDEN", 2)
FLAG_SYSTEM = getattr(_store_mod, "FLAG_SYSTEM", 4)
FLAG_PLACEHOLDER = getattr(_store_mod, "FLAG_PLACEHOLDER", 8)
FLAG_DENIED = getattr(_store_mod, "FLAG_DENIED", 16)

THREAD_MODE_BACKGROUND_BEGIN = 0x10000
THREAD_MODE_BACKGROUND_END = 0x20000
DRIVE_REMOTE = 4
YIELD_EVERY = 2000


def _denylist_matches() -> Callable[[str], bool]:
    try:
        from ..security import denylist
        return denylist.matches
    except Exception:
        return lambda path: False


def background_begin() -> bool:
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentThread.restype = ctypes.c_void_p
        k32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        return bool(k32.SetThreadPriority(k32.GetCurrentThread(), THREAD_MODE_BACKGROUND_BEGIN))
    except Exception:
        return False


def background_end() -> None:
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentThread.restype = ctypes.c_void_p
        k32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        k32.SetThreadPriority(k32.GetCurrentThread(), THREAD_MODE_BACKGROUND_END)
    except Exception:
        pass


def is_remote(root: str) -> bool:
    if root.startswith("\\\\") and not root.startswith("\\\\?\\"):
        return True
    drive = os.path.splitdrive(root)[0]
    if not drive:
        return False
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == DRIVE_REMOTE
    except Exception:
        return False


def entry_flags(attrs: int, is_dir: bool) -> int:
    flags = FLAG_DIR if is_dir else 0
    if attrs & ATTR_HIDDEN:
        flags |= FLAG_HIDDEN
    if attrs & ATTR_SYSTEM:
        flags |= FLAG_SYSTEM
    if attrs & _PLACEHOLDER_ATTRS:
        flags |= FLAG_PLACEHOLDER
    return flags


class _Recorder:
    """Stands in for a builder while a remote root is walked on a worker thread,
    so an abandoned (timed out) walk can never touch the real builder."""

    def __init__(self) -> None:
        self.ops: list = []

    def add_dir(self, path, parent_id):
        self.ops.append((0, path, parent_id))
        return len(self.ops) - 1

    def add(self, parent_id, name, size, mtime, flags):
        self.ops.append((1, parent_id, name, size, mtime, flags))
        return len(self.ops) - 1

    def replay(self, builder) -> None:
        ids: list = []
        for op in self.ops:
            if op[0] == 0:
                parent = op[2] if op[2] < 0 else ids[op[2]]
                ids.append(builder.add_dir(op[1], parent))
            else:
                ids.append(builder.add(ids[op[1]], op[2], op[3], op[4], op[5]))


@dataclass
class RootResult:
    path: str
    state: str = "pending"
    entries: int = 0
    dirs: int = 0
    seconds: float = 0.0
    errors: int = 0
    error: Optional[str] = None


@dataclass
class CrawlResult:
    store: Any
    roots: list = field(default_factory=list)
    excludes: dict = field(default_factory=dict)
    seconds: float = 0.0


class Crawler:
    def __init__(self, roots, excludes, builder_factory, root_timeout_s: float = 60.0,
                 stop: Optional[threading.Event] = None) -> None:
        self.roots = [os.path.abspath(r) for r in roots]
        self.excludes = excludes
        self.builder_factory = builder_factory
        self.root_timeout_s = float(root_timeout_s)
        self.stop = stop or threading.Event()
        self.rule_counts: dict = {}
        self._matches = _denylist_matches()
        self._lock = threading.Lock()

    def run(self) -> CrawlResult:
        t0 = time.perf_counter()
        background = background_begin()
        results = []
        try:
            builder = self.builder_factory()
            for root in self.roots:
                if self.stop.is_set():
                    break
                results.append(self._do_root(root, builder))
            store = (self.builder_factory() if self.stop.is_set() else builder).finish()
        finally:
            if background:
                background_end()
        return CrawlResult(store=store, roots=results, excludes=dict(self.rule_counts),
                           seconds=time.perf_counter() - t0)

    def _do_root(self, root: str, builder) -> RootResult:
        res = RootResult(root)
        t0 = time.perf_counter()
        try:
            remote = is_remote(root)
            if not remote and not os.path.isdir(root):
                res.state, res.error = "missing", "folder not found"
                return res
            if remote:
                self._do_remote(root, builder, res)
            else:
                res.state = "walking"
                self._walk(root, builder, res, abort=self.stop)
                res.state = "stopped" if self.stop.is_set() else "ok"
        except Exception as exc:
            log.exception("crawl of a root failed")
            res.state, res.error = "error", type(exc).__name__
        finally:
            res.seconds = time.perf_counter() - t0
        return res

    def _do_remote(self, root: str, builder, res: RootResult) -> None:
        recorder = _Recorder()
        abort = threading.Event()
        local = RootResult(root)
        failure: list = []

        def work() -> None:
            background_begin()
            try:
                if not os.path.isdir(root):
                    local.state, local.error = "missing", "folder not reachable"
                    return
                self._walk(root, recorder, local, abort=abort, extra_stop=self.stop)
                local.state = "ok"
            except Exception as exc:
                failure.append(type(exc).__name__)

        th = threading.Thread(target=work, name="index-remote-walk", daemon=True)
        th.start()
        th.join(self.root_timeout_s)
        if th.is_alive():
            abort.set()
            res.state = "timeout"
            res.error = f"no answer within {self.root_timeout_s:g} s"
            return
        if failure:
            res.state, res.error = "error", failure[0]
            return
        res.errors = local.errors
        if local.state == "ok":
            recorder.replay(builder)
            res.entries, res.dirs = local.entries, local.dirs
        res.state, res.error = local.state, local.error

    def _walk(self, root: str, builder, res: RootResult, abort: threading.Event,
              extra_stop: Optional[threading.Event] = None) -> None:
        excludes = self.excludes
        matches = self._matches
        rules: dict = {}
        root_id = builder.add_dir(root, -1)
        res.dirs += 1
        res.entries += 1
        stack = [(root, "", root_id)]
        since_yield = 0
        try:
            while stack:
                if abort.is_set() or (extra_stop is not None and extra_stop.is_set()):
                    return
                path, rel, dir_id = stack.pop()
                try:
                    it = os.scandir(path)
                except OSError:
                    res.errors += 1
                    continue
                with it:
                    while True:
                        try:
                            entry = next(it)
                        except StopIteration:
                            break
                        except OSError:
                            res.errors += 1
                            break
                        try:
                            st = entry.stat(follow_symlinks=False)
                            attrs = st.st_file_attributes
                        except OSError:
                            res.errors += 1
                            continue
                        name = entry.name
                        is_dir = bool(attrs & ATTR_DIRECTORY)
                        erel = f"{rel}/{name}" if rel else name
                        if excludes is not None:
                            rule = excludes.matching_rule(entry.path, erel, name, is_dir)
                            if rule:
                                rules[rule] = rules.get(rule, 0) + 1
                                continue
                        flags = entry_flags(attrs, is_dir)
                        if matches(entry.path):
                            flags |= FLAG_DENIED
                        res.entries += 1
                        if is_dir:
                            res.dirs += 1
                            sub_id = builder.add_dir(entry.path, dir_id)
                            if not attrs & ATTR_REPARSE:
                                stack.append((entry.path, erel, sub_id))
                        else:
                            builder.add(dir_id, name, st.st_size, st.st_mtime, flags)
                        since_yield += 1
                        if since_yield >= YIELD_EVERY:
                            since_yield = 0
                            time.sleep(0)
                            if abort.is_set():
                                return
        finally:
            with self._lock:
                for rule, n in rules.items():
                    self.rule_counts[rule] = self.rule_counts.get(rule, 0) + n
