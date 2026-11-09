"""Match primitives and ranking for Files-mode search (AC-FI-13).

Tiers, best first: exact name, name prefix, word boundary in name, substring
in name, path only. Wildcards (``*`` ``?``) are matched without regular-
expression backtracking: the pattern is split on ``*`` and each segment is
located left to right, so cost stays linear in the text length.
"""
from __future__ import annotations

import re

EXACT, PREFIX, WORD, SUBSTRING, PATH = 0, 1, 2, 3, 4

_MAX_BOUNDARY_SCANS = 16


class Matcher:
    """Case-insensitive substring matcher with optional ``*``/``?`` wildcards."""

    __slots__ = ("text", "_lit", "_segs", "match_all")

    def __init__(self, text: str, literal: bool = False):
        self.text = text.casefold()
        self._lit: str | None = None
        self._segs: list | None = None
        self.match_all = False
        if literal or ("*" not in self.text and "?" not in self.text):
            self._lit = self.text
            return
        segs: list = []
        for part in self.text.split("*"):
            if not part:
                continue
            if "?" in part:
                segs.append(re.compile(".".join(re.escape(p) for p in part.split("?")), re.DOTALL))
            else:
                segs.append(part)
        if not segs:
            self.match_all = True
        self._segs = segs

    def find(self, text: str, start: int = 0):
        """Return (start, end) of the leftmost match at or after ``start``, else None."""
        if self._lit is not None:
            i = text.find(self._lit, start)
            return None if i < 0 else (i, i + len(self._lit))
        if self.match_all:
            return (start, len(text))
        pos = start
        first = -1
        for seg in self._segs:
            if isinstance(seg, str):
                i = text.find(seg, pos)
                if i < 0:
                    return None
                e = i + len(seg)
            else:
                m = seg.search(text, pos)
                if m is None:
                    return None
                i, e = m.span()
            if first < 0:
                first = i
            pos = e
        return (first, pos)

    def contains_in(self, text: str) -> bool:
        return self.find(text) is not None

    def longest_literal(self) -> str:
        """Longest wildcard-free run, usable to ask the index for candidates."""
        if self._lit is not None:
            return self._lit
        best = ""
        for part in re.split(r"[*?]", self.text):
            if len(part) > len(best):
                best = part
        return best


def stem_end(name_cf: str) -> int:
    """Index where the extension (including its dot) begins; len(name) if none."""
    dot = name_cf.rfind(".")
    return dot if dot > 0 else len(name_cf)


def ext_of(name_cf: str) -> str:
    dot = name_cf.rfind(".")
    return name_cf[dot + 1:] if dot > 0 else ""


def name_tier(name_cf: str, m: Matcher):
    """Tier of ``m`` within a casefolded name, or None when it does not match."""
    r = m.find(name_cf)
    if r is None:
        return None
    if m.match_all:
        return SUBSTRING
    s, e = r
    if s == 0:
        if e == len(name_cf) or e == stem_end(name_cf):
            return EXACT
        return PREFIX
    for _ in range(_MAX_BOUNDARY_SCANS):
        if not name_cf[s - 1].isalnum():
            return WORD
        r = m.find(name_cf, s + 1)
        if r is None:
            break
        s = r[0]
    return SUBSTRING


def best_key(tiers, name_cf: str) -> tuple:
    """Sort key: worst tier, then sum of tiers, then shorter name, then name."""
    if not tiers:
        return (SUBSTRING, SUBSTRING, len(name_cf), name_cf)
    return (max(tiers), sum(tiers), len(name_cf), name_cf)
