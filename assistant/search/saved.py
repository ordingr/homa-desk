"""Saved searches (AC-FI-16): name, query, chips, date filter, sort; stored per machine."""
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .. import paths

log = logging.getLogger("assistant.search.saved")

FILE_NAME = "saved_searches.json"
MAX_SAVED = 200
MAX_NAME = 80
MAX_QUERY = 500


@dataclass
class SavedSearch:
    name: str
    query: str
    chips: list = field(default_factory=list)
    modified: str = ""
    sort: str = ""
    mode: str = "files"


def default_path() -> Path:
    return paths.home() / FILE_NAME


def _clean(raw: Any) -> Optional[SavedSearch]:
    if not isinstance(raw, dict):
        return None
    name, query = raw.get("name"), raw.get("query")
    if not isinstance(name, str) or not isinstance(query, str) or not name.strip():
        return None
    chips = raw.get("chips", [])
    chips = [c for c in chips if isinstance(c, str)][:12] if isinstance(chips, list) else []
    return SavedSearch(
        name=name.strip()[:MAX_NAME], query=query[:MAX_QUERY], chips=chips,
        modified=raw.get("modified", "") if isinstance(raw.get("modified", ""), str) else "",
        sort=raw.get("sort", "") if isinstance(raw.get("sort", ""), str) else "",
        mode=raw.get("mode", "files") if isinstance(raw.get("mode", "files"), str) else "files")


class SavedSearches:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else None
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path or default_path()

    def _load(self) -> list:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("searches") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        cleaned = (_clean(i) for i in items)
        return [s for s in cleaned if s is not None][:MAX_SAVED]

    def _store(self, items: list) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"schema": 1, "searches": [asdict(i) for i in items]},
                                  indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def list(self) -> list:
        with self._lock:
            return self._load()

    def get(self, name: str) -> Optional[SavedSearch]:
        for item in self.list():
            if item.name == name:
                return item
        return None

    def add(self, name: str, query: str, chips=(), modified: str = "", sort: str = "",
            mode: str = "files") -> SavedSearch:
        """Add or replace (same name) a saved search; raises ValueError for an empty name or query."""
        name, query = (name or "").strip()[:MAX_NAME], (query or "").strip()[:MAX_QUERY]
        if not name or not query:
            raise ValueError("a saved search needs a name and a query")
        entry = SavedSearch(name=name, query=query, chips=sorted(str(c) for c in chips)[:12],
                            modified=modified or "", sort=sort or "", mode=mode or "files")
        with self._lock:
            items = [i for i in self._load() if i.name != name]
            if len(items) >= MAX_SAVED:
                raise ValueError(f"at most {MAX_SAVED} saved searches")
            items.append(entry)
            self._store(items)
        return entry

    def remove(self, name: str) -> bool:
        with self._lock:
            items = self._load()
            kept = [i for i in items if i.name != name]
            if len(kept) == len(items):
                return False
            self._store(kept)
            return True

    def run(self, name: str, runner: Callable[[SavedSearch], Any]) -> Any:
        """Call runner(saved) for the named search; KeyError when it does not exist."""
        item = self.get(name)
        if item is None:
            raise KeyError(name)
        return runner(item)
