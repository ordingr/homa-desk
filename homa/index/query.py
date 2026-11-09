"""Files-mode query grammar (DEC 5, AC-FI-8 .. AC-FI-12).

Space is AND. Every plain term and every quoted phrase may match anywhere in
the full path (which includes the name); rows where all of them hit the file
name rank above rows where some only hit the folders. A bare token that equals a known extension and is
not the only token becomes an extension filter. ``-e``/``-p`` take a value;
``ext:``/``path:`` are the long forms; ``!word`` excludes; ``"..."`` is a
literal phrase; ``*`` and ``?`` are wildcards. Any other ``-word`` is a
literal term, never NOT.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_QUERY_LEN = 512
MAX_TOKENS = 32
ALL_FILES = "all files"


@dataclass
class Query:
    terms: list[str] = field(default_factory=list)
    path_terms: list[str] = field(default_factory=list)
    exts: list[str] = field(default_factory=list)
    path_parts: list[str] = field(default_factory=list)
    nots: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)
    interpretation: str = ALL_FILES
    notes: list[str] = field(default_factory=list)


@dataclass
class _Tok:
    text: str
    quoted: bool
    neg: bool


def _tokenize(text: str) -> list[_Tok]:
    toks: list[_Tok] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        neg = False
        if c == "!" and i + 1 < n and not text[i + 1].isspace():
            neg = True
            i += 1
            c = text[i]
        if c == '"':
            j = text.find('"', i + 1)
            if j < 0:
                body, i = text[i + 1:], n
            else:
                body, i = text[i + 1:j], j + 1
            if body.strip():
                toks.append(_Tok(body, True, neg))
            continue
        j = i
        while j < n and not text[j].isspace():
            if text[j] == '"' and text[i:j].casefold() in ("ext:", "path:"):
                break
            j += 1
        word = text[i:j]
        i = j
        if word.casefold() in ("ext:", "path:") and i < n and text[i] == '"':
            k = text.find('"', i + 1)
            if k < 0:
                word, i = word + text[i + 1:], n
            else:
                word, i = word + text[i + 1:k], k + 1
        toks.append(_Tok(word, False, neg))
    return toks


def _split_exts(value: str) -> list[str]:
    out = []
    for part in re.split(r"[,;\s]+", value):
        part = part.strip().casefold()
        if part.startswith("*."):
            part = part[2:]
        part = part.lstrip(".")
        if part:
            out.append(part)
    return out


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for x in items:
        k = x.casefold()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _kind(text: str) -> str:
    return "matches" if ("*" in text or "?" in text) else "contains"


def _describe(q: Query) -> str:
    parts: list[str] = []
    for t in q.terms:
        parts.append(f'name or path {_kind(t)} "{t}"')
    for p in q.phrases:
        parts.append(f'name or path contains "{p}"')
    for p in q.path_parts:
        parts.append(f'path {_kind(p)} "{p}"')
    if q.exts:
        parts.append("ext: " + ", ".join(q.exts))
    for x in q.nots:
        parts.append(f'excluding "{x}"')
    return " · ".join(parts) if parts else ALL_FILES


def parse(text: str, known_exts=()) -> Query:
    q = Query()
    text = text or ""
    if len(text) > MAX_QUERY_LEN:
        text = text[:MAX_QUERY_LEN]
        q.notes.append(f"query truncated to {MAX_QUERY_LEN} characters")
    toks = _tokenize(text)
    if len(toks) > MAX_TOKENS:
        toks = toks[:MAX_TOKENS]
        q.notes.append(f"only the first {MAX_TOKENS} words were used")
    known = {e.casefold().lstrip(".") for e in known_exts}
    single = len(toks) == 1
    bare: list[str] = []
    k = 0
    while k < len(toks):
        t = toks[k]
        k += 1
        if t.neg:
            q.nots.append(t.text)
            continue
        if t.quoted:
            q.phrases.append(t.text)
            continue
        low = t.text.casefold()
        if low in ("-e", "-p") and k < len(toks) and not toks[k].neg:
            value = toks[k].text
            vals = _split_exts(value) if low == "-e" else [value]
            if vals:
                k += 1
                if low == "-e":
                    q.exts.extend(vals)
                else:
                    q.path_parts.append(value)
                continue
        if low.startswith("ext:") and len(low) > 4:
            vals = _split_exts(t.text[4:])
            if vals:
                q.exts.extend(vals)
                continue
        if low.startswith("path:") and len(t.text) > 5:
            q.path_parts.append(t.text[5:])
            continue
        if not single and low in known:
            q.exts.append(low)
            continue
        bare.append(t.text)
    q.exts = _dedupe(q.exts)
    q.terms = bare
    q.interpretation = _describe(q)
    return q
