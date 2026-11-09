"""Search executor for Files mode (DEC 5, AC-FI-8 .. AC-FI-18).

Every plain term and phrase may match anywhere in the full path. Candidates
come from ``IndexStore.find_via_dirs`` on the longest separator-free literal
run of any term (complete for the whole query); when that is too common the
run falls back to ``find_substring`` on the names and says ``truncated``.
Every other condition is verified per hit; path-only hits rank after rows
whose terms all hit the name.
"""
from __future__ import annotations

import heapq
import re
import time
from array import array
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import rank
from .query import ALL_FILES, Query
from .rank import Matcher

try:
    from . import store as _store_mod
except ImportError:
    _store_mod = None

FLAG_DIR = getattr(_store_mod, "FLAG_DIR", 1)
FLAG_HIDDEN = getattr(_store_mod, "FLAG_HIDDEN", 2)
FLAG_SYSTEM = getattr(_store_mod, "FLAG_SYSTEM", 4)
FLAG_PLACEHOLDER = getattr(_store_mod, "FLAG_PLACEHOLDER", 8)
FLAG_DENIED = getattr(_store_mod, "FLAG_DENIED", 16)

MAX_LIMIT = 10_000
DENSE_CAP = 100_000
NEWEST_CAP = 10_000
TIME_BUDGET_S = 0.4
VIA_DIRS_MAX = 50_000

CHIP_LABELS = {
    "docs": "Docs", "sheets": "Sheets", "pdf": "PDF",
    "images": "Images", "code": "Code", "folders": "Folders",
}
CHIP_EXTS = {
    "docs": frozenset("doc docx dot dotx odt rtf txt md markdown rst tex ppt pptx pps ppsx odp".split()),
    "sheets": frozenset("xls xlsx xlsm xlsb xlt xltx ods csv tsv".split()),
    "pdf": frozenset(["pdf"]),
    "images": frozenset("png jpg jpeg gif bmp svg webp tif tiff ico heic heif raw psd".split()),
    "code": frozenset(
        "py pyw js mjs cjs ts tsx jsx java kt c h cpp cc cxx hpp cs go rs rb php swift sh bash "
        "ps1 psm1 bat cmd sql html htm css scss json yml yaml toml xml ini cfg lua r pl vue".split()
    ),
}
SORTS = ("best", "name", "ext", "date")


@dataclass(slots=True)
class FileRow:
    id: int
    name: str
    path: str
    parent: str
    ext: str
    size: int
    mtime: float
    is_dir: bool
    tier: int
    spans: list = field(default_factory=list)
    placeholder: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "path": self.path, "parent": self.parent,
            "ext": self.ext, "size": self.size, "mtime": self.mtime, "is_dir": self.is_dir,
            "tier": self.tier, "spans": [list(s) for s in self.spans], "placeholder": self.placeholder,
        }


@dataclass
class SearchResult:
    rows: list
    total: int
    elapsed_ms: float
    interpretation: str
    built_at: float
    truncated: bool = False


def _norm_chips(chips) -> list[str]:
    out: list[str] = []
    for c in chips or ():
        key = str(c).strip().casefold()
        if key not in CHIP_LABELS:
            raise ValueError(f"unknown chip: {c!r}")
        if key not in out:
            out.append(key)
    return out


def _fmt_day(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _resolve_date(date_filter, now: float):
    """Return (lo, hi, label) with open ends as None, or None when unfiltered.

    Accepts "today", "7d", "30d" (also "last7", "last30"), "YYYY-MM-DD..YYYY-MM-DD" (a closed range of
    local days), a (lo, hi) pair of epoch seconds (either may be None), or a dict with "from"/"to".
    """
    if not date_filter:
        return None
    if isinstance(date_filter, str):
        key = date_filter.strip().casefold().replace(" ", "").replace("_", "")
        if key == "today":
            midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
            return midnight.timestamp(), None, "today"
        if key in ("7d", "last7", "last7days", "7days"):
            return now - 7 * 86400, None, "last 7 days"
        if key in ("30d", "last30", "last30days", "30days"):
            return now - 30 * 86400, None, "last 30 days"
        if ".." in key:
            return _day_range(key, date_filter)
        raise ValueError(f"unknown date filter: {date_filter!r}")
    if isinstance(date_filter, dict):
        lo, hi = date_filter.get("from"), date_filter.get("to")
    else:
        lo, hi = date_filter
    if lo is None and hi is None:
        return None
    lo = None if lo is None else float(lo)
    hi = None if hi is None else float(hi)
    if lo is not None and hi is not None:
        label = f"{_fmt_day(lo)} to {_fmt_day(hi)}"
    elif lo is not None:
        label = f"since {_fmt_day(lo)}"
    else:
        label = f"until {_fmt_day(hi)}"
    return lo, hi, label


def _day_range(key: str, given) -> tuple:
    first, _, last = key.partition("..")
    try:
        lo_day = datetime.strptime(first, "%Y-%m-%d")
        hi_day = datetime.strptime(last, "%Y-%m-%d")
        lo = lo_day.timestamp()
        hi = (hi_day + timedelta(days=1)).timestamp() - 0.001
    except (ValueError, OverflowError, OSError):
        raise ValueError(f"invalid date range {given!r}: expected YYYY-MM-DD..YYYY-MM-DD") from None
    if hi < lo:
        raise ValueError(f"invalid date range {given!r}: the first day is after the last")
    return lo, hi, f"{_fmt_day(lo)} to {_fmt_day(hi)}"


def _interpretation(query: Query, chips: list[str], date) -> str:
    extras = []
    if chips:
        extras.append("type: " + ", ".join(CHIP_LABELS[c] for c in chips))
    if date:
        extras.append("modified: " + date[2])
    base = query.interpretation
    if base == ALL_FILES and extras:
        return " · ".join(extras)
    return " · ".join([base] + extras)


def _spans(name_cf: str, name: str, matchers) -> list:
    if len(name_cf) != len(name):
        return []
    spans = []
    for m in matchers:
        r = m.find(name_cf)
        if r is not None and r[1] > r[0]:
            spans.append(r)
    spans.sort()
    merged: list = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _norm_path(p: str) -> str:
    return p.replace("/", "\\")


def run(query: Query, store, delta=None, chips=(), date_filter=None, sort: str = "best",
        limit: int = 50, offset: int = 0, *, show_denied: bool = False, show_hidden: bool = False,
        skip_roots: bool = False, now: float | None = None, budget_s: float | None = TIME_BUDGET_S):
    t0 = time.perf_counter()
    sort = (sort or "best").casefold()
    if sort not in SORTS:
        raise ValueError(f"unknown sort: {sort!r}")
    if limit < 0 or offset < 0:
        raise ValueError("limit and offset must be >= 0")
    limit = min(limit, MAX_LIMIT)
    now = time.time() if now is None else now
    chip_keys = _norm_chips(chips)
    date = _resolve_date(date_filter, now)
    interpretation = _interpretation(query, chip_keys, date)

    anywhere = ([Matcher(t) for t in query.terms] + [Matcher(t) for t in query.path_terms]
                + [Matcher(p, literal=True) for p in query.phrases])
    not_m = [Matcher(n) for n in query.nots]
    part_m = [Matcher(_norm_path(p)) for p in query.path_parts]
    exts = {e.casefold().lstrip(".") for e in query.exts}
    chip_dirs = "folders" in chip_keys
    chip_exts = frozenset().union(*(CHIP_EXTS[c] for c in chip_keys if c in CHIP_EXTS))
    use_chips = bool(chip_keys)
    need_path = bool(not_m or part_m)

    capped = len(anywhere) == 1 and not (exts or need_path or use_chips or date)
    everything = not (exts or need_path) and not any(m.longest_literal() for m in anywhere)
    deadline = t0 + budget_s if budget_s else None
    ids, truncated, name_only = _candidates(store, anywhere, exts, capped, everything, deadline, part_m)
    if everything and truncated and sort == "best":
        sort = "date"
    if delta is not None:
        ids = delta.apply_to(ids)

    flags_of, name_of, path_of, mtime_of = store.flags, store.name, store.path, store.mtime
    lo, hi = (date[0], date[1]) if date else (None, None)
    hide = 0 if show_hidden else FLAG_HIDDEN | FLAG_SYSTEM
    is_root = store.is_root if skip_roots else None
    items: list = []
    append = items.append
    pre_top, pre_total = [], 0
    lit = anywhere[0].longest_literal() if len(anywhere) == 1 else ""
    if (name_only and lit and sort == "best" and anywhere[0].text == lit and hasattr(store, "rank_best_literal")
            and not (exts or need_path or use_chips or date)):
        cut = bisect_left(ids, store.entry_count)
        pre_top, pre_total, cut_off = store.rank_best_literal(ids, cut, lit, show_denied, deadline,
                                                              offset + limit, hide, skip_roots)
        truncated = truncated or cut_off
        ids = [] if cut_off else ids[cut:]
    for seen, i in enumerate(ids):
        if deadline is not None and not seen & 1023 and seen and time.perf_counter() > deadline:
            truncated = True
            break
        fl = flags_of(i)
        if fl & FLAG_DENIED and not show_denied:
            continue
        if fl & hide or (is_root is not None and is_root(i)):
            continue
        is_dir = bool(fl & FLAG_DIR)
        name = name_of(i)
        ncf = name.casefold()
        ext = "" if is_dir else rank.ext_of(ncf)
        if exts and ext not in exts:
            continue
        if use_chips and not ((chip_dirs and is_dir) or (not is_dir and ext in chip_exts)):
            continue
        ok = True
        if date:
            mt = mtime_of(i)
            if (lo is not None and mt < lo) or (hi is not None and mt > hi):
                continue
        else:
            mt = 0.0
        tiers = []
        pcf = _norm_path(path_of(i).casefold()) if need_path else None
        if need_path:
            for m in part_m:
                if not m.contains_in(pcf):
                    ok = False
                    break
            if not ok:
                continue
            for m in not_m:
                if m.contains_in(pcf):
                    ok = False
                    break
            if not ok:
                continue
        for m in anywhere:
            t = rank.name_tier(ncf, m)
            if t is None:
                if pcf is None:
                    pcf = _norm_path(path_of(i).casefold())
                if not m.contains_in(pcf):
                    ok = False
                    break
                t = rank.PATH
            tiers.append(t)
        if not ok:
            continue
        if sort == "best":
            key = rank.best_key(tiers, ncf)
        elif sort == "name":
            key = (ncf,)
        elif sort == "ext":
            key = (ext, ncf)
        else:
            key = (-(mt if date else mtime_of(i)), ncf)
        append((key, i, max(tiers) if tiers else rank.SUBSTRING))

    total = len(items) + pre_total
    want = offset + limit
    items = pre_top + items
    top = heapq.nsmallest(want, items) if want < total // 4 else sorted(items)[:want]
    page = top[offset:want]

    highlight = anywhere
    rows = []
    for _key, i, tier in page:
        name = store.name(i)
        fl = store.flags(i)
        is_dir = bool(fl & FLAG_DIR)
        ncf = name.casefold()
        rows.append(FileRow(
            id=i, name=name, path=store.path(i), parent=store.parent_path(i),
            ext="" if is_dir else rank.ext_of(ncf), size=store.size(i), mtime=store.mtime(i),
            is_dir=is_dir, tier=tier, spans=_spans(ncf, name, highlight),
            placeholder=bool(fl & FLAG_PLACEHOLDER),
        ))
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return SearchResult(rows=rows, total=total, elapsed_ms=elapsed_ms,
                        interpretation=interpretation, built_at=getattr(store, "built_at", 0.0),
                        truncated=truncated)


def _path_chunks(matchers) -> list:
    """Runs of 2+ characters, free of wildcards and separators, that any path matching a term must contain,
    longest first (at most four)."""
    seen: dict = {}
    for m in matchers:
        for part in re.split(r"[*?\\/]", m.text):
            if len(part) >= 2:
                seen[part] = None
    return sorted(seen, key=len, reverse=True)[:4]


def _candidates(store, anywhere, exts, capped=False, everything=False, deadline=None, parts=()):
    """(ids, truncated, name_only). The id set must contain every entry whose path matches all of ``anywhere``:
    ``find_via_dirs`` on the first path chunk of any term or ``path:`` part that is selective enough gives exactly that. Otherwise the ids
    are the names containing the longest literal (``name_only``), path-only hits are missing, and ``truncated`` is
    set. ``capped`` limits a lone plain term to DENSE_CAP name hits; ``everything`` (nothing to match on) yields
    only the newest NEWEST_CAP entries. The name fetch stops at ``deadline``."""
    chunks = [] if everything or not hasattr(store, "find_via_dirs") else _path_chunks([*anywhere, *parts])
    for chunk in chunks:
        hits = store.find_via_dirs("", chunk, VIA_DIRS_MAX, deadline)
        if hits is not None:
            return hits, hits.cut, False
    incomplete = bool(anywhere) and not everything
    lit = max((m.longest_literal() for m in anywhere), key=len, default="")
    if lit:
        if capped:
            hits = store.find_substring(lit, DENSE_CAP, deadline)
            return hits, len(hits) >= DENSE_CAP or hits.cut or incomplete, True
        hits = store.find_substring(lit, None, deadline)
        return hits, hits.cut or incomplete, True
    if exts:
        merged: set = set()
        for e in exts:
            merged.update(store.find_substring("." + e))
        return array("I", sorted(merged)), incomplete, True
    if everything and store.entry_count > NEWEST_CAP:
        return store.newest_ids(NEWEST_CAP), True, True
    return array("I", range(store.entry_count)), False, True
