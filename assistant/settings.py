import copy
import json
import logging
import ntpath
import os
import threading
import time
from typing import Any, Callable, Optional

from . import paths, uibus
from .index.excludes import DEFAULT_EXCLUDES

log = logging.getLogger("assistant.settings")

REPLACE_ATTEMPTS = 10
REPLACE_FIRST_DELAY_S = 0.01
REPLACE_MAX_DELAY_S = 0.2

MAX_ICS_URLS = 20
MAX_ICS_URL_CHARS = 2048
MAX_LABEL_CHARS = 64
MAX_INDEX_ROOTS = 20
MAX_INDEX_EXCLUDES = 200
MAX_PATH_CHARS = 1024
MAX_EXCLUDE_CHARS = 512

MUTE_SOURCES = ("hooks", "http", "mcp", "file", "reminders", "calendar")

DEFAULTS: dict[str, Any] = {
    "schema": 1,
    "port": 17171,
    "hotkey": "ctrl+alt+space",
    "search_placement": "icon",
    "theme": "dark",
    "sound_on": True,
    "cards_every_monitor": True,
    "mute_sources": {name: False for name in MUTE_SOURCES},
    "calendars": {"ics_urls": [], "labels": {}, "outlook": False, "lead_minutes": 5, "poll_minutes": 5},
    "claude_code": {},
    "mcp": {},
    "index": {"roots": [], "excludes": list(DEFAULT_EXCLUDES), "show_protected_by_name": False},
    "notes": {"dir": ""},
}

_NESTED = ("mute_sources", "calendars", "claude_code", "mcp", "index", "notes")
_BOOLS = ("sound_on", "cards_every_monitor")


def _valid_port(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1024 <= value <= 65535


def _int_in(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _check_calendars(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("calendars must be an object")
    urls = value.get("ics_urls", [])
    if not isinstance(urls, list) or not all(isinstance(u, str) and len(u) <= MAX_ICS_URL_CHARS for u in urls):
        raise ValueError("calendars.ics_urls must be a list of strings")
    if len(urls) > MAX_ICS_URLS:
        raise ValueError(f"calendars.ics_urls can hold at most {MAX_ICS_URLS} addresses")
    labels = value.get("labels", {})
    if (not isinstance(labels, dict) or len(labels) > MAX_ICS_URLS
            or not all(isinstance(k, str) and len(k) <= MAX_ICS_URL_CHARS and isinstance(v, str)
                       and len(v) <= MAX_LABEL_CHARS for k, v in labels.items())):
        raise ValueError("calendars.labels must map addresses to short text")
    if not isinstance(value.get("outlook", False), bool):
        raise ValueError("calendars.outlook must be true or false")
    if not _int_in(value.get("lead_minutes", 5), 0, 120):
        raise ValueError("lead minutes must be a whole number from 0 to 120")
    if not _int_in(value.get("poll_minutes", 5), 1, 60):
        raise ValueError("poll minutes must be a whole number from 1 to 60")


def _abs_path(value: Any) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= MAX_PATH_CHARS and "\x00" not in value
            and bool(ntpath.splitdrive(value)[0]) and ntpath.isabs(value))


def _check_index(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("index must be an object")
    roots = value.get("roots", [])
    if not isinstance(roots, list) or not all(isinstance(r, str) for r in roots):
        raise ValueError("index.roots must be a list of folder paths")
    if len(roots) > MAX_INDEX_ROOTS:
        raise ValueError(f"index.roots can hold at most {MAX_INDEX_ROOTS} folders")
    if not all(_abs_path(r) for r in roots):
        raise ValueError("index.roots must be absolute folder paths such as C:\\Users\\name\\Documents")
    excludes = value.get("excludes", [])
    if (not isinstance(excludes, list)
            or not all(isinstance(e, str) and len(e) <= MAX_EXCLUDE_CHARS and "\x00" not in e
                       and "\n" not in e and "\r" not in e for e in excludes)):
        raise ValueError("index.excludes must be a list of pattern lines")
    if len(excludes) > MAX_INDEX_EXCLUDES:
        raise ValueError(f"index.excludes can hold at most {MAX_INDEX_EXCLUDES} lines")
    if not isinstance(value.get("show_protected_by_name", False), bool):
        raise ValueError("index.show_protected_by_name must be true or false")
    for key, low, high in (("rescan_hours", 0.01, 720), ("root_timeout_s", 0.1, 3600)):
        v = value.get(key, low)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not low <= v <= high:
            raise ValueError(f"index.{key} must be a number from {low:g} to {high:g}")
    if not isinstance(value.get("pause_on_battery", True), bool):
        raise ValueError("index.pause_on_battery must be true or false")


def _check_notes(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("notes must be an object")
    folder = value.get("dir", "")
    if folder != "" and not _abs_path(folder):
        raise ValueError("notes.dir must be empty or an absolute folder path")


def _check(key: str, value: Any) -> None:
    if key == "port" and not _valid_port(value):
        raise ValueError("port must be an integer between 1024 and 65535")
    if key in _BOOLS and not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false")
    if key in _NESTED and not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    if key == "mute_sources" and not all(isinstance(v, bool) for v in value.values()):
        raise ValueError("mute_sources values must be true or false")
    if key == "calendars":
        _check_calendars(value)
    if key == "index":
        _check_index(value)
    if key == "notes":
        _check_notes(value)
    if key == "hotkey" and not (isinstance(value, str) and len(value) <= 64):
        raise ValueError("hotkey must be text such as ctrl+alt+space")
    if key == "search_placement" and not (isinstance(value, str) and 0 < len(value) <= 32):
        raise ValueError("search_placement must be short text such as icon")


def _merged(loaded: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(DEFAULTS)
    for key, value in loaded.items():
        if key in _NESTED and isinstance(value, dict):
            merged[key].update(value)
        else:
            merged[key] = value
    for key in ("port", "hotkey", "search_placement", *_BOOLS, *_NESTED):
        try:
            _check(key, merged[key])
        except ValueError:
            if key == "calendars" and isinstance(merged[key], dict):
                _repair_calendars(merged[key])
                continue
            if key in ("index", "notes") and isinstance(merged[key], dict):
                _repair_nested(key, merged[key])
                continue
            log.warning("invalid %s in settings; using default", key)
            merged[key] = copy.deepcopy(DEFAULTS[key])
    return merged


def _repair_calendars(value: dict) -> None:
    for sub, default in DEFAULTS["calendars"].items():
        if sub == "ics_urls" and isinstance(value.get(sub), list):
            kept = [u for u in value[sub] if isinstance(u, str) and len(u) <= MAX_ICS_URL_CHARS]
            if len(kept) != len(value[sub]) or len(kept) > MAX_ICS_URLS:
                log.warning("calendars.ics_urls trimmed to %d valid addresses", min(len(kept), MAX_ICS_URLS))
            value[sub] = kept[:MAX_ICS_URLS]
        try:
            _check_calendars({sub: value.get(sub, default)})
        except ValueError:
            log.warning("invalid calendars.%s in settings; using default", sub)
            value[sub] = copy.deepcopy(default)


_NESTED_CHECKS = {"index": _check_index, "notes": _check_notes}


def _repair_nested(key: str, value: dict) -> None:
    for sub, default in DEFAULTS[key].items():
        try:
            _NESTED_CHECKS[key]({sub: value.get(sub, default)})
        except ValueError:
            log.warning("invalid %s.%s in settings; using default", key, sub)
            value[sub] = copy.deepcopy(default)
    try:
        _NESTED_CHECKS[key](value)
    except ValueError:
        log.warning("invalid %s in settings; using default", key)
        value.clear()
        value.update(copy.deepcopy(DEFAULTS[key]))


def _stamp(path) -> Optional[tuple[int, int]]:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class Settings:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self._listeners: list[tuple[Optional[str], Callable[[str, Any], None]]] = []
        self._seen: Optional[tuple[int, int]] = None

    def load(self) -> None:
        path = paths.settings_file()
        with self._save_lock:
            merged = _merged(self._read(path))
            with self._lock:
                self._data = merged
            self._seen = _stamp(path)
        if not path.exists():
            self.save()

    @staticmethod
    def _read(path) -> dict[str, Any]:
        loaded: dict[str, Any] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    loaded = raw
                else:
                    log.warning("settings.json is not an object; using defaults")
            except (OSError, ValueError):
                log.warning("settings.json unreadable; using defaults")
        return loaded

    def save(self) -> None:
        path = paths.settings_file()
        tmp = path.with_name(path.name + ".tmp")
        with self._save_lock:
            with self._lock:
                payload = json.dumps(self._data, indent=2, sort_keys=True)
                has_ics = bool(self._data.get("calendars", {}).get("ics_urls"))
            tmp.write_text(payload, encoding="utf-8")
            if has_ics:
                try:
                    from .security import acl
                    acl.restrict_to_current_user(tmp)
                except Exception as exc:
                    tmp.unlink(missing_ok=True)
                    raise OSError("could not restrict settings.json to the current user "
                                  f"({type(exc).__name__}); not saving calendar URLs") from None
            self._replace(tmp, path)
            self._seen = _stamp(path)

    @staticmethod
    def _replace(tmp, path) -> None:
        delay = REPLACE_FIRST_DELAY_S
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    tmp.unlink(missing_ok=True)
                    raise
                time.sleep(delay)
                delay = min(delay * 2, REPLACE_MAX_DELAY_S)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise

    def watch(self, scheduler, interval_s: float = 5.0):
        """Reload settings.json when another process edits it and fire on_change for each changed key."""
        return scheduler.every(interval_s, self._check_external)

    def _check_external(self) -> None:
        path = paths.settings_file()
        with self._save_lock:
            stamp = _stamp(path)
            if stamp is None or stamp == self._seen:
                return
            self._seen = stamp
            if not path.exists():
                return
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("settings.json changed but is unreadable; keeping current settings")
                return
            if not isinstance(raw, dict):
                log.warning("settings.json changed but is not an object; keeping current settings")
                return
            merged = _merged(raw)
            if merged["calendars"].get("ics_urls"):
                try:
                    from .security import acl
                    acl.restrict_to_current_user(path)
                except Exception as exc:
                    log.warning("could not restrict settings.json to the current user (%s)", type(exc).__name__)
            with self._lock:
                old = self._data
                self._data = merged
                listeners = list(self._listeners)
        for key in merged:
            if key in old and old[key] == merged[key]:
                continue
            for only, fn in listeners:
                if only is None or only == key:
                    uibus.post(self._notify, fn, key, copy.deepcopy(merged[key]))

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return copy.deepcopy(self._data.get(key, default))

    def set(self, key: str, value: Any) -> None:
        _check(key, value)
        with self._lock:
            self._data[key] = copy.deepcopy(value)
            listeners = list(self._listeners)
        self.save()
        for only, fn in listeners:
            if only is None or only == key:
                uibus.post(self._notify, fn, key, copy.deepcopy(value))

    @staticmethod
    def _notify(fn: Callable[[str, Any], None], key: str, value: Any) -> None:
        try:
            fn(key, value)
        except Exception:
            log.exception("settings listener failed")

    def on_change(self, key_or_fn, fn: Optional[Callable[[str, Any], None]] = None) -> None:
        """on_change(fn) hears every key; on_change(key, fn) hears one. Callbacks run on the UI thread."""
        only, callback = (None, key_or_fn) if fn is None else (key_or_fn, fn)
        with self._lock:
            self._listeners.append((only, callback))

    @property
    def port(self) -> int:
        return self.get("port", DEFAULTS["port"])


def peek_port() -> int:
    """Configured port without creating or writing anything (for --doctor)."""
    try:
        raw = json.loads(paths.settings_file().read_text(encoding="utf-8"))
        port = raw.get("port") if isinstance(raw, dict) else None
        if _valid_port(port):
            return port
    except (OSError, ValueError):
        pass
    return DEFAULTS["port"]
