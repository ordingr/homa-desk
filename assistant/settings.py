import json
import logging
import os
import threading
from typing import Any, Callable

from . import paths

log = logging.getLogger("assistant.settings")

DEFAULTS: dict[str, Any] = {
    "schema": 1,
    "port": 17171,
}


def _valid_port(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1024 <= value <= 65535


class Settings:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = dict(DEFAULTS)
        self._listeners: list[Callable[[str, Any], None]] = []

    def load(self) -> None:
        path = paths.settings_file()
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
        merged = dict(DEFAULTS)
        merged.update(loaded)
        if not _valid_port(merged["port"]):
            log.warning("invalid port in settings; using default")
            merged["port"] = DEFAULTS["port"]
        with self._lock:
            self._data = merged
        if not path.exists():
            self.save()

    def save(self) -> None:
        path = paths.settings_file()
        tmp = path.with_name(path.name + ".tmp")
        with self._lock:
            payload = json.dumps(self._data, indent=2, sort_keys=True)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if key == "port" and not _valid_port(value):
            raise ValueError("port must be an integer between 1024 and 65535")
        with self._lock:
            self._data[key] = value
            listeners = list(self._listeners)
        self.save()
        for fn in listeners:
            try:
                fn(key, value)
            except Exception:
                log.exception("settings listener failed")

    def on_change(self, fn: Callable[[str, Any], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

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
