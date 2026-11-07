"""Text extraction for previews (SEC-5): Office files are read as zip archives and their XML is scanned with
regular expressions. Nothing is executed, no XML parser is used (no entity expansion), no member is written to
disk, and the total bytes unpacked per file are capped (zip-bomb guard).
"""
import csv
import html
import io
import re
import zipfile
from typing import Any, Optional

UNPACK_CAP = 50 * 1024 * 1024
TEXT_CAP = 1_000_000
ROW_CAP = 1000
COL_CAP = 50
CELL_CAP = 1000
SLIDE_CAP = 200
MEDIA_CAP = 200
MEMBER_CAP = 8 * 1024 * 1024


class ExtractError(Exception):
    """The file is not a readable Office archive, or it unpacks to more than UNPACK_CAP."""


class _Budget:
    def __init__(self, zf: zipfile.ZipFile, cap: int = UNPACK_CAP) -> None:
        self._zf = zf
        self.remaining = cap

    def read(self, name: str, limit: Optional[int] = None) -> bytes:
        try:
            info = self._zf.getinfo(name)
        except KeyError:
            return b""
        want = info.file_size if limit is None else min(info.file_size, limit)
        if want > self.remaining:
            raise ExtractError("the file unpacks to more than the 50 MB safety limit")
        with self._zf.open(info) as member:
            data = member.read(want + 1)
        if limit is None and len(data) > want:
            raise ExtractError("the archive member is larger than it declares")
        data = data[:want]
        self.remaining -= len(data)
        return data


def _open(fh) -> tuple:
    try:
        zf = zipfile.ZipFile(fh)
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        raise ExtractError("not a readable Office file (damaged, encrypted or an old format)") from None
    return zf, _Budget(zf)


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def _unescape(value: str) -> str:
    return html.unescape(value)


_PFX = r"(?:\w+:)?"
_W_TOKEN = re.compile(
    rf"<{_PFX}t(?:\s[^<>]*)?>([^<]*)</{_PFX}t>|<{_PFX}tab\s*/>|<{_PFX}br\s*/>|</{_PFX}p>|</{_PFX}tc>")


def extract_docx(fh) -> dict:
    zf, budget = _open(fh)
    with zf:
        xml = _text(budget.read("word/document.xml", MEMBER_CAP))
        truncated = _member_size(zf, "word/document.xml") > MEMBER_CAP
    parts: list = []
    size = 0
    for m in _W_TOKEN.finditer(xml):
        token = m.group(0)
        if m.group(1) is not None:
            piece = _unescape(m.group(1))
        elif token.startswith("</") and token.rstrip(">").endswith("tc"):
            piece = "\t"
        elif token.startswith("</"):
            piece = "\n"
        elif "tab" in token:
            piece = "\t"
        else:
            piece = "\n"
        parts.append(piece)
        size += len(piece)
        if size > TEXT_CAP:
            truncated = True
            break
    return {"text": "".join(parts)[:TEXT_CAP], "truncated": truncated}


def _member_size(zf: zipfile.ZipFile, name: str) -> int:
    try:
        return zf.getinfo(name).file_size
    except KeyError:
        return 0


def _numbered(names, prefix: str, suffix: str) -> list:
    pat = re.compile(re.escape(prefix) + r"(\d+)" + re.escape(suffix) + "$")
    found = [(int(m.group(1)), n) for n in names for m in [pat.match(n)] if m]
    return [n for _, n in sorted(found)]


_SI_OPEN = re.compile(rf"<{_PFX}si\b[^<>]*>")
_SI_CLOSE = re.compile(rf"</{_PFX}si>")
_PHONETIC_OPEN = re.compile(rf"<{_PFX}rPh\b")
_PHONETIC_CLOSE = re.compile(rf"</{_PFX}rPh>")
_T = re.compile(rf"<{_PFX}t(?:\s[^<>]*)?>([^<]*)</{_PFX}t>")
_ROW_OPEN = re.compile(rf"<{_PFX}row\b[^<>]*>")
_ROW_CLOSE = re.compile(rf"</{_PFX}row>")
_CELL_OPEN = re.compile(rf"<{_PFX}c\b([^<>]*?)(/>|>)")
_CELL_CLOSE = re.compile(rf"</{_PFX}c>")
_V = re.compile(rf"<{_PFX}v>([^<]*)</{_PFX}v>")
_REF = re.compile(r'\br="([A-Z]+)\d+"')
_TYPE = re.compile(r'\bt="(\w+)"')
_SHEET_NAME = re.compile(r'<(?:\w+:)?sheet\b[^<>]*?\bname="([^"]*)"')


def _blocks(xml: str, open_re, close_re):
    """(open match, content) for each element, found by one forward scan: every search starts where the last
    ended, and an element with no closing tag ends the scan, so the cost is linear in len(xml)."""
    pos = 0
    while True:
        opened = open_re.search(xml, pos)
        if opened is None:
            return
        if open_re is _CELL_OPEN and opened.group(2) == "/>":
            yield opened, ""
            pos = opened.end()
            continue
        closed = close_re.search(xml, opened.end())
        if closed is None:
            return
        yield opened, xml[opened.end():closed.start()]
        pos = closed.end()


def _strip_phonetic(xml: str) -> str:
    out: list = []
    pos = 0
    while True:
        opened = _PHONETIC_OPEN.search(xml, pos)
        if opened is None:
            break
        closed = _PHONETIC_CLOSE.search(xml, opened.end())
        if closed is None:
            break
        out.append(xml[pos:opened.start()])
        pos = closed.end()
    out.append(xml[pos:])
    return "".join(out)


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def extract_xlsx(fh) -> dict:
    zf, budget = _open(fh)
    with zf:
        names = zf.namelist()
        sheets = _numbered(names, "xl/worksheets/sheet", ".xml")
        if not sheets:
            raise ExtractError("the workbook has no worksheets")
        shared_xml = _strip_phonetic(_text(budget.read("xl/sharedStrings.xml", MEMBER_CAP)))
        shared = [_unescape("".join(_T.findall(si))) for _, si in _blocks(shared_xml, _SI_OPEN, _SI_CLOSE)]
        titles = [_unescape(n) for n in _SHEET_NAME.findall(_text(budget.read("xl/workbook.xml", 1_000_000)))]
        sheet_xml = _text(budget.read(sheets[0], MEMBER_CAP))
        truncated = zf.getinfo(sheets[0]).file_size > MEMBER_CAP
    rows: list = []
    for _, row_xml in _blocks(sheet_xml, _ROW_OPEN, _ROW_CLOSE):
        if len(rows) >= ROW_CAP:
            truncated = True
            break
        row: list = []
        for cell_m, inner in _blocks(row_xml, _CELL_OPEN, _CELL_CLOSE):
            attrs = cell_m.group(1)
            ref = _REF.search(attrs)
            col = _col_index(ref.group(1)) if ref else len(row)
            if col >= COL_CAP:
                truncated = True
                continue
            kind = (_TYPE.search(attrs) or [None, "n"])[1]
            value = ""
            if kind == "inlineStr":
                value = _unescape("".join(_T.findall(_strip_phonetic(inner))))
            else:
                v = _V.search(inner)
                if v:
                    value = _unescape(v.group(1))
                    if kind == "s":
                        try:
                            value = shared[int(value)]
                        except (ValueError, IndexError):
                            value = ""
                    elif kind == "b":
                        value = "TRUE" if value == "1" else "FALSE"
            while len(row) <= col:
                row.append("")
            row[col] = value[:CELL_CAP]
        rows.append(row)
    width = max((len(r) for r in rows), default=0)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return {"rows": rows, "sheets": titles or [f"Sheet{i + 1}" for i in range(len(sheets))],
            "shown_sheet": 0, "truncated": truncated}


_A_TOKEN = re.compile(r"<a:t(?:\s[^<>]*)?>([^<]*)</a:t>|</a:p>")


def extract_pptx(fh) -> dict:
    zf, budget = _open(fh)
    with zf:
        names = zf.namelist()
        slide_names = _numbered(names, "ppt/slides/slide", ".xml")
        media = [n.rsplit("/", 1)[-1] for n in names if n.startswith("ppt/media/") and not n.endswith("/")]
        out: list = []
        size = 0
        truncated = len(slide_names) > SLIDE_CAP or len(media) > MEDIA_CAP
        for number, name in enumerate(slide_names[:SLIDE_CAP], 1):
            xml = _text(budget.read(name, MEMBER_CAP))
            if _member_size(zf, name) > MEMBER_CAP:
                truncated = True
            lines = ["" if m.group(1) is None else _unescape(m.group(1)) for m in _A_TOKEN.finditer(xml)]
            body = "".join(piece if piece else "\n" for piece in lines).strip()
            block = f"--- Slide {number} ---\n{body}\n"
            size += len(block)
            if size > TEXT_CAP:
                truncated = True
                break
            out.append(block)
    return {"text": "\n".join(out), "slide_count": len(slide_names), "media": media[:MEDIA_CAP],
            "truncated": truncated}


def parse_table(text: str, ext: str = "csv", complete: bool = True) -> dict:
    """Rows from CSV or TSV text. `complete` False means the text ends mid-file, so the last row is dropped."""
    lines = text.splitlines()
    head = lines[0] if lines else ""
    if ext == "tsv":
        delimiter = "\t"
    else:
        counts = {d: head.count(d) for d in (",", ";", "\t", "|")}
        delimiter = max(counts, key=counts.get) if any(counts.values()) else ","
    rows: list = []
    truncated = not complete
    try:
        for record in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter):
            if len(rows) >= ROW_CAP:
                truncated = True
                break
            if len(record) > COL_CAP:
                truncated = True
            rows.append([cell[:CELL_CAP] for cell in record[:COL_CAP]])
    except csv.Error:
        truncated = True
    if not complete and rows:
        rows = rows[:-1]
    width = max((len(r) for r in rows), default=0)
    return {"rows": [r + [""] * (width - len(r)) for r in rows], "truncated": truncated}


def table_text(rows: list) -> str:
    """The cells of a table as one string, for the private-key scan."""
    return "\n".join("\t".join(row) for row in rows)


def result_text(result: dict) -> str:
    """Everything textual in an extraction result, for the private-key scan."""
    value: Any = result.get("rows")
    return table_text(value) if value else result.get("text", "")
