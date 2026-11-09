"""Search-mode registry and the row / result shapes the popup renders.

No tkinter here: the registry is shared with the HTTP layer and plugins.
"""
import os
import re
import threading
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Protocol, Sequence, runtime_checkable

INDEX_UNAVAILABLE = "index not available"
NOTES_UNAVAILABLE = "Ask notes is not available yet"

CHIPS = ("docs", "sheets", "pdf", "images", "code", "folders")
CHIP_LABELS = {"docs": "Docs", "sheets": "Sheets", "pdf": "PDF", "images": "Images",
               "code": "Code", "folders": "Folders"}
MODIFIED_CHOICES = (("", "Any time"), ("today", "Today"), ("7d", "7 days"), ("30d", "30 days"))
SORT_CHOICES = (("", "Best"), ("name", "Name"), ("ext", "Ext"), ("date", "Date"))
_DAY_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$")


def valid_modified(value: Any) -> bool:
    """A preset key, or a closed range of local days written YYYY-MM-DD..YYYY-MM-DD."""
    if not isinstance(value, str):
        return False
    if value in dict(MODIFIED_CHOICES):
        return True
    found = _DAY_RANGE.match(value)
    if not found:
        return False
    try:
        first, last = (datetime.strptime(d, "%Y-%m-%d") for d in found.groups())
    except ValueError:
        return False
    return first <= last


class ModeUnavailable(Exception):
    """Raised by a mode that cannot run right now; the message is shown to the user as-is."""


@dataclass
class Row:
    name: str
    folder: str = ""
    ext: str = ""
    size: Optional[int] = None
    mtime: Optional[float] = None
    is_dir: bool = False
    spans: tuple = ()
    passage: str = ""
    title: str = ""
    kind: str = "file"
    placeholder: bool = False

    @property
    def path(self) -> str:
        if not self.folder:
            return self.name
        return os.path.join(self.folder, self.name)


@dataclass
class SearchResult:
    rows: list = field(default_factory=list)
    total: int = 0
    elapsed_ms: Optional[float] = None
    interpretation: str = ""
    built_at: Optional[float] = None
    notice: str = ""
    footer: str = ""
    notes: list = field(default_factory=list)
    truncated: bool = False


@runtime_checkable
class Mode(Protocol):
    keyword: str
    label: str
    local: bool

    def search(self, text: str) -> list: ...


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                return obj[name]
        else:
            value = getattr(obj, name, None)
            if value is not None:
                return value
    return default


def to_row(obj: Any) -> Row:
    """Build a Row from a Row, a mapping or any object with the usual file attributes."""
    if isinstance(obj, Row):
        return obj
    if hasattr(obj, "passage") and hasattr(obj, "path"):
        path = str(obj.path)
        return Row(name=os.path.basename(path), folder=os.path.dirname(path),
                   ext=os.path.splitext(path)[1].lstrip(".").lower(), passage=str(obj.passage),
                   title=str(getattr(obj, "title", "") or ""), kind="note",
                   spans=tuple(tuple(h) for h in (getattr(obj, "highlights", None) or ())))
    name = str(_get(obj, "name", default=""))
    full = _get(obj, "full_path", "fullpath")
    folder = _get(obj, "folder", "dir", "parent")
    path = _get(obj, "path")
    if full is None and path is not None:
        p = str(path)
        if name and os.path.basename(p) == name:
            full = p
        elif folder is None:
            folder = p
    if full is not None:
        full = str(full)
        if not name:
            name = os.path.basename(full)
        if folder is None:
            folder = os.path.dirname(full)
    is_dir = bool(_get(obj, "is_dir", "isdir", "dir_flag", default=False))
    ext = _get(obj, "ext", default=None)
    if ext is None:
        ext = "" if is_dir else os.path.splitext(name)[1]
    size = _get(obj, "size", "bytes")
    mtime = _get(obj, "mtime", "modified", "date")
    spans = _get(obj, "spans", "highlights", default=())
    return Row(name=name, folder=str(folder or ""), ext=str(ext).lstrip(".").lower(),
               size=int(size) if isinstance(size, (int, float)) else None,
               mtime=float(mtime) if isinstance(mtime, (int, float)) else None,
               is_dir=is_dir, spans=tuple(tuple(s) for s in spans),
               passage=str(_get(obj, "snippet", default="")),
               placeholder=_get(obj, "placeholder", default=False) is True)


def to_result(obj: Any, elapsed_ms: Optional[float] = None) -> SearchResult:
    """Accept a SearchResult, an index.search result (rows/total/elapsed_ms/...) or a plain row list."""
    if isinstance(obj, SearchResult):
        return obj
    if isinstance(obj, (list, tuple)):
        rows = [to_row(r) for r in obj]
        return SearchResult(rows=rows, total=len(rows), elapsed_ms=elapsed_ms)
    rows = [to_row(r) for r in (_get(obj, "rows", default=[]) or [])]
    total = _get(obj, "total", default=len(rows))
    return SearchResult(
        rows=rows, total=int(total), elapsed_ms=_get(obj, "elapsed_ms", default=elapsed_ms),
        interpretation=str(_get(obj, "interpretation", default="")),
        built_at=_get(obj, "built_at"), notice=str(_get(obj, "notice", default="")),
        footer=str(_get(obj, "footer", default="")),
        notes=[str(n) for n in (_get(obj, "notes", default=[]) or [])],
        truncated=_get(obj, "truncated", default=False) is True)


_FLAG_WITH_ARG = {"-e", "-p", "-x"}


def query_terms(text: str) -> list[str]:
    """Words worth highlighting in a name: skips flags, their arguments, ext:/path: filters and negations."""
    terms: list[str] = []
    skip = False
    for token in re.findall(r'"[^"]*"|\S+', text):
        if skip:
            skip = False
            continue
        if token in _FLAG_WITH_ARG:
            skip = True
            continue
        if token.startswith(("-", "!")) or (":" in token and not token.startswith('"')):
            continue
        for piece in re.split(r"[*?\"]+", token):
            if piece:
                terms.append(piece.lower())
    return terms


def highlight_spans(name: str, text: str) -> tuple:
    lowered = name.lower()
    marks = [False] * len(lowered)
    for term in query_terms(text):
        start = lowered.find(term)
        while start != -1:
            for i in range(start, start + len(term)):
                marks[i] = True
            start = lowered.find(term, start + len(term))
    spans, begin = [], None
    for i, on in enumerate(marks + [False]):
        if on and begin is None:
            begin = i
        elif not on and begin is not None:
            spans.append((begin, i))
            begin = None
    return tuple(spans)


def mode_prefix(mode: Any) -> str:
    """Explicit `prefix`, else a one-character non-alphanumeric keyword such as '?'."""
    prefix = getattr(mode, "prefix", None)
    if prefix is not None:
        return prefix
    keyword = getattr(mode, "keyword", "")
    return keyword if len(keyword) == 1 and not keyword.isalnum() else ""


class ModeRegistry:
    """Ordered set of search modes; Tab cycles through them. Plugins register more later."""

    def __init__(self) -> None:
        self._modes: list = []
        self._lock = threading.Lock()

    def register(self, mode: Any, replace: bool = True) -> None:
        with self._lock:
            for i, existing in enumerate(self._modes):
                if existing.keyword == mode.keyword:
                    if replace:
                        self._modes[i] = mode
                    return
            self._modes.append(mode)

    def unregister(self, keyword: str) -> None:
        with self._lock:
            self._modes = [m for m in self._modes if m.keyword != keyword]

    def all(self) -> list:
        with self._lock:
            return list(self._modes)

    def get(self, keyword: str) -> Optional[Any]:
        for mode in self.all():
            if mode.keyword == keyword:
                return mode
        return None

    def resolve(self, text: str, current: Any) -> tuple:
        """(mode, query, switched). A mode's optional `prefix` ('?' for Ask notes) selects it for one query."""
        stripped = text.lstrip()
        for mode in self.all():
            prefix = mode_prefix(mode)
            if prefix and stripped.startswith(prefix):
                return mode, stripped[len(prefix):].strip(), True
        return current, text.strip(), False


class FilesMode:
    keyword = "files"
    label = "Files"
    local = True
    noun = "file(s)"
    prefix = ""

    def __init__(self, source: Optional[Callable[[], Optional[tuple]]] = None, settings: Any = None) -> None:
        """`source()` returns (store, delta, known_exts) or None while no index exists."""
        self._source = source
        self._settings = settings

    def _show_denied(self) -> bool:
        try:
            index = self._settings.get("index") if self._settings is not None else None
        except Exception:
            return False
        return isinstance(index, dict) and index.get("show_protected_by_name") is True

    def _show_hidden(self) -> bool:
        try:
            index = self._settings.get("index") if self._settings is not None else None
        except Exception:
            return False
        return isinstance(index, dict) and index.get("include_hidden") is True

    def search(self, text: str) -> list:
        return self.run(text).rows

    def run(self, text: str, chips: Sequence[str] = (), modified: str = "", sort: str = "",
            limit: int = 200, offset: int = 0) -> SearchResult:
        try:
            from ..index import query as iquery, search as isearch
        except ImportError:
            raise ModeUnavailable(INDEX_UNAVAILABLE) from None
        source = self._source() if self._source else None
        if source is None:
            raise ModeUnavailable(INDEX_UNAVAILABLE)
        store, delta, known_exts = source
        parsed = iquery.parse(text, known_exts)
        started = time.perf_counter()
        raw = isearch.run(parsed, store, delta, list(chips), modified or None, sort or None, limit, offset,
                          show_denied=self._show_denied(), show_hidden=self._show_hidden())
        result = to_result(raw, elapsed_ms=(time.perf_counter() - started) * 1000.0)
        result.notes = list(parsed.notes)
        for row in result.rows:
            if not row.spans:
                row.spans = highlight_spans(row.name, text)
        return result


class AskNotesPlaceholder:
    """Stands in for Ask notes (`search.notes_bm25.AskNotesMode`) until the app wires the real one."""
    keyword = "?"
    label = "Ask notes"
    local = True
    noun = "passage(s)"
    message = NOTES_UNAVAILABLE
    footer = ""

    def search(self, text: str) -> list:
        return []


def default_registry(files_source: Optional[Callable[[], Optional[tuple]]] = None,
                     notes_mode: Optional[Any] = None, settings: Any = None) -> ModeRegistry:
    """`notes_mode` is a notes_bm25.AskNotesMode; without one a placeholder keeps the Tab cycle shape."""
    registry = ModeRegistry()
    registry.register(FilesMode(files_source, settings))
    registry.register(notes_mode if notes_mode is not None else AskNotesPlaceholder())
    return registry


def make_run_search(modes: Any) -> Callable[..., SearchResult]:
    """Default `run_search(mode, text, chips=, modified=, sort=)`; `mode` is a Mode or its keyword."""
    def run_search(mode: Any, text: str, chips: Iterable[str] = (), modified: str = "",
                   sort: str = "") -> SearchResult:
        if isinstance(mode, str):
            found = modes.get(mode) if hasattr(modes, "get") else None
            if found is None:
                raise ModeUnavailable("unknown search mode")
            mode = found
        runner = getattr(mode, "run", None)
        if runner is not None:
            return to_result(runner(text, chips=list(chips), modified=modified, sort=sort))
        footer = str(getattr(mode, "footer", "") or "")
        message = getattr(mode, "message", None)
        if message:
            return SearchResult(notice=str(message), footer=footer)
        started = time.perf_counter()
        result = to_result(mode.search(text), elapsed_ms=(time.perf_counter() - started) * 1000.0)
        result.footer = result.footer or footer
        return result
    return run_search
