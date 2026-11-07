"""Render a Markdown passage into a tk.Text using tags; no HTML, no renderer library."""
import re
import tkinter as tk
import tkinter.font as tkfont
from typing import Callable, Iterable, Optional

from . import gfx
from ..search.notes_bm25 import _STOP, _WORD, stem

_FENCE = re.compile(r"^\s{0,3}(```|~~~)\s*(\S*)")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_HR = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_INLINE = re.compile(
    r"(?P<code>`+)(?P<codetext>.+?)(?P=code)"
    r"|!?\[\[(?P<wiki>[^\]]+)\]\]"
    r"|!?\[(?P<linktext>[^\]]*)\]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
    r"|(?P<bi>\*\*\*|___)(?=\S)(?P<bitext>.+?)(?<=\S)(?P=bi)"
    r"|(?P<bold>\*\*|__)(?=\S)(?P<boldtext>.+?)(?<=\S)(?P=bold)"
    r"|(?P<em>[*_])(?=\S)(?P<emtext>.+?)(?<=\S)(?P=em)"
)


def configure_tags(text: tk.Text, family: str = "Segoe UI", size: int = 10,
                   fg: str = gfx.PANEL_FG, dim: str = gfx.PANEL_DIM, bg: str = gfx.PANEL_BG,
                   hit_bg: str = "#6B5A12", mono: str = "Consolas") -> None:
    base = tkfont.Font(family=family, size=size)
    bold = tkfont.Font(family=family, size=size, weight="bold")
    italic = tkfont.Font(family=family, size=size, slant="italic")
    bolditalic = tkfont.Font(family=family, size=size, weight="bold", slant="italic")
    code = tkfont.Font(family=mono, size=max(size - 1, 7))
    codebold = tkfont.Font(family=mono, size=max(size - 1, 7), weight="bold")
    text._md_fonts = (base, bold, italic, bolditalic, code, codebold)
    text.configure(font=base, fg=fg, bg=bg)
    text.tag_configure("md_h1", font=tkfont.Font(family=family, size=size + 5, weight="bold"),
                       spacing1=4, spacing3=4)
    text.tag_configure("md_h2", font=tkfont.Font(family=family, size=size + 3, weight="bold"),
                       spacing1=3, spacing3=3)
    text.tag_configure("md_h3", font=tkfont.Font(family=family, size=size + 1, weight="bold"),
                       spacing1=2, spacing3=2)
    text.tag_configure("md_bold", font=bold)
    text.tag_configure("md_italic", font=italic)
    text.tag_configure("md_bolditalic", font=bolditalic)
    text.tag_configure("md_code", font=code, background=gfx.INPUT_BG)
    text.tag_configure("md_codeblock", font=code, background=gfx.INPUT_BG, lmargin1=12, lmargin2=12,
                       rmargin=6, spacing1=0, spacing3=0)
    text.tag_configure("md_quote", foreground=dim, lmargin1=12, lmargin2=12)
    text.tag_configure("md_link", foreground=gfx.PANEL_LINK, underline=True)
    text.tag_configure("md_list", lmargin1=14, lmargin2=28)
    text.tag_configure("md_table", font=code, lmargin1=6, lmargin2=6)
    text.tag_configure("md_tablehead", font=codebold, lmargin1=6, lmargin2=6)
    text.tag_configure("md_rule", foreground=dim)
    text.tag_configure("md_dim", foreground=dim)
    text.tag_configure("md_hit", background=hit_bg, foreground="#FFFFFF")
    text.tag_raise("md_hit")
    text.tag_raise("md_link")


def _emit(text: tk.Text, s: str, tags: tuple) -> None:
    if s:
        text.insert("end", s, tags)


def _inline(text: tk.Text, s: str, tags: tuple, links: list) -> None:
    if "md_bold" in tags and "md_italic" in tags and "md_bolditalic" not in tags:
        tags = tags + ("md_bolditalic",)
    pos = 0
    for m in _INLINE.finditer(s):
        _emit(text, s[pos:m.start()], tags)
        pos = m.end()
        if m.group("code"):
            _emit(text, m.group("codetext").strip(), tags + ("md_code",))
        elif m.group("wiki") is not None:
            label = m.group("wiki").split("|")[-1].split("#")[0] or m.group("wiki")
            _emit(text, label, tags + ("md_link",))
        elif m.group("url") is not None:
            label = m.group("linktext") or m.group("url")
            tag = f"md_url_{len(links)}"
            links.append((tag, m.group("url")))
            _inline(text, label, tags + ("md_link", tag), links)
        elif m.group("bi"):
            _inline(text, m.group("bitext"), tags + ("md_bold", "md_italic"), links)
        elif m.group("bold"):
            _inline(text, m.group("boldtext"), tags + ("md_bold",), links)
        else:
            _inline(text, m.group("emtext"), tags + ("md_italic",), links)
    _emit(text, s[pos:], tags)


def _split_row(line: str) -> list:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", line)]


def _plain(s: str) -> str:
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"(\*\*|__|\*|_|`)", "", s)
    return s


def _table(text: tk.Text, rows: list) -> None:
    header = [_plain(c) for c in rows[0]]
    body = [[_plain(c) for c in r] for r in rows[1:]]
    ncol = max(len(header), *(len(r) for r in body)) if body else len(header)
    grid = [header + [""] * (ncol - len(header))] + [r + [""] * (ncol - len(r)) for r in body]
    widths = [max(len(r[i]) for r in grid) for i in range(ncol)]

    def line(r: list) -> str:
        return " | ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip() + "\n"

    _emit(text, line(grid[0]), ("md_tablehead",))
    _emit(text, "-+-".join("-" * w for w in widths) + "\n", ("md_table", "md_dim"))
    for r in grid[1:]:
        _emit(text, line(r), ("md_table",))


def _apply_hits(text: tk.Text, terms: Iterable[str]) -> None:
    stems = {stem(w) for t in terms for w in _WORD.findall(t.lower()) if w not in _STOP}
    if not stems:
        return
    content = text.get("1.0", "end-1c")
    for m in _WORD.finditer(content):
        w = m.group().lower()
        if w not in _STOP and stem(w) in stems:
            text.tag_add("md_hit", f"1.0+{m.start()}c", f"1.0+{m.end()}c")


def render(text: tk.Text, markdown: str, terms: Iterable[str] = (), clear: bool = True,
           on_link: Optional[Callable[[str], None]] = None) -> None:
    """Draw `markdown` into `text`. `terms` are the raw query words to highlight."""
    if "md_h1" not in text.tag_names():
        configure_tags(text)
    prior = str(text.cget("state"))
    text.configure(state="normal")
    if clear:
        text.delete("1.0", "end")
    links: list = []
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            i += 1
            code = []
            while i < len(lines) and not _FENCE.match(lines[i]):
                code.append(lines[i])
                i += 1
            i += 1
            _emit(text, "\n".join(code) + "\n", ("md_codeblock",))
            continue
        heading = _HEADING.match(line)
        if heading:
            level = min(len(heading.group(1)), 3)
            _inline(text, heading.group(2), (f"md_h{level}",), links)
            text.insert("end", "\n")
            i += 1
            continue
        if _HR.match(line):
            _emit(text, "─" * 24 + "\n", ("md_rule",))
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            rows = [_split_row(line)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            _table(text, rows)
            continue
        lst = _LIST.match(line)
        if lst:
            depth = len(lst.group(1).replace("\t", "    ")) // 2
            marker = "•" if lst.group(2) in "-*+" else lst.group(2)
            body = lst.group(3)
            box = re.match(r"\[( |x|X)\]\s+(.*)", body)
            if box:
                marker, body = ("☑" if box.group(1) != " " else "☐"), box.group(2)
            _emit(text, "  " * depth, ("md_list",))
            _emit(text, marker + " ", ("md_list",))
            _inline(text, body, ("md_list",), links)
            text.insert("end", "\n")
            i += 1
            continue
        if line.lstrip().startswith(">"):
            _inline(text, re.sub(r"^\s*>+\s?", "", line), ("md_quote",), links)
            text.insert("end", "\n")
            i += 1
            continue
        if not line.strip():
            text.insert("end", "\n")
            i += 1
            continue
        _inline(text, line, (), links)
        text.insert("end", "\n")
        i += 1

    for tag, url in links:
        text.tag_configure(tag)
        if on_link is not None:
            text.tag_bind(tag, "<Button-1>", lambda _e, u=url: on_link(u))
            text.tag_bind(tag, "<Enter>", lambda _e: text.configure(cursor="hand2"))
            text.tag_bind(tag, "<Leave>", lambda _e: text.configure(cursor=""))
    _apply_hits(text, terms)
    text.configure(state=prior)
