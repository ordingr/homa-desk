"""Preview and action fixtures (M3b-6), generated at test time into a directory the caller owns.

``build_preview_tree(dest)`` writes every sample under ``dest`` and returns ``{key: Path}``. Nothing here is
committed as binary; the Office files are hand-built zips, the PDF is a hand-written single page, the 60 MB file
is a sparse file (``set_len``), and the OFFLINE file gets the cloud-placeholder attribute through the Win32 API.
All text uses placeholder names.
"""
import ctypes
import io
import struct
import zipfile
import zlib
from pathlib import Path

FILE_ATTRIBUTE_OFFLINE = 0x1000
MB = 1024 * 1024
BIG_BYTES = 60 * MB
LOG_BYTES = 3 * MB
BAT_MARKER = "m3b6-marker.txt"

PEM = ("-----BEGIN RSA PRIVATE KEY-----\n"
       "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n"
       "KUpRKfFLfRYC9AIKjbJTWit+CqvjWYzvQwECAwEAAQJAIJLixBy2qpFoS4DSmoEm\n"
       "-----END RSA PRIVATE KEY-----\n")
OFFLINE_MARKER = "OFFLINE-CONTENT-MARKER-m3b6"

MARKDOWN = """# Preview sample

Plain paragraph with **bold** text.

<script>alert(1)</script>

```mermaid
graph TD
  A[Start] --> B[End]
```

```markmap
# Root
## Branch one
## Branch two
```
"""

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" onload="alert(1)">'
       '<script>alert(2)</script><rect width="40" height="40" fill="#3a7"/></svg>\n')


def _png(width: int = 1, height: int = 1) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x20\x90\x40" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG = _png()


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


CT = "http://schemas.openxmlformats.org/package/2006/content-types"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
OD = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def docx_bytes(text: str = "Quarterly budget hello-docx") -> bytes:
    return _zip({
        "[Content_Types].xml": f'<?xml version="1.0"?><Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": f'<?xml version="1.0"?><Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{OD}/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": f'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:p><w:r><w:t>second paragraph</w:t></w:r></w:p></w:body></w:document>',
    })


def xlsx_bytes(rows: int = 3) -> bytes:
    cells = "".join(
        f'<row r="{r}"><c r="A{r}" t="inlineStr"><is><t>{"name" if r == 1 else f"item{r}"}</t></is></c>'
        f'<c r="B{r}"><v>{r * 10}</v></c></row>' for r in range(1, rows + 1))
    return _zip({
        "[Content_Types].xml": f'<?xml version="1.0"?><Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": f'<?xml version="1.0"?><Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{OD}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="{OD}"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": f'<?xml version="1.0"?><Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{OD}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{cells}</sheetData></worksheet>',
    })


def xlsx_malformed_shared_bytes(size: int = 1_000_000) -> bytes:
    """A workbook whose sharedStrings.xml is ``size`` bytes of unterminated ``<si><t>`` openers (parser stress)."""
    base = zipfile.ZipFile(io.BytesIO(xlsx_bytes()))
    files = {n: base.read(n) for n in base.namelist()}
    files["xl/sharedStrings.xml"] = ('<?xml version="1.0"?><sst>' + "<si><t>x" * (size // 8)).encode()
    return _zip(files)


def pptx_bytes(text: str = "Roadmap slide hello-pptx") -> bytes:
    return _zip({
        "[Content_Types].xml": f'<?xml version="1.0"?><Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/></Types>',
        "_rels/.rels": f'<?xml version="1.0"?><Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{OD}/officeDocument" Target="ppt/presentation.xml"/></Relationships>',
        "ppt/presentation.xml": f'<?xml version="1.0"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="{OD}"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>',
        "ppt/_rels/presentation.xml.rels": f'<?xml version="1.0"?><Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{OD}/slide" Target="slides/slide1.xml"/></Relationships>',
        "ppt/slides/slide1.xml": f'<?xml version="1.0"?><p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
        "ppt/media/image1.png": PNG,
    })


def pdf_bytes(text: str = "Hello PDF preview") -> bytes:
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


CSV = "name,qty,price\nwidget,4,2.50\ngadget,9,1.25\n"
IPYNB = ('{"cells":[{"cell_type":"code","execution_count":1,"metadata":{},"outputs":[],"source":["print(\\"hi-notebook\\")"]},'
         '{"cell_type":"markdown","metadata":{},"source":["# Title"]}],"metadata":{},"nbformat":4,"nbformat_minor":5}\n')


def make_sparse(path: Path, size: int = BIG_BYTES) -> Path:
    """A file of ``size`` bytes without writing the zeros (SetEndOfFile; NTFS fills lazily)."""
    with open(path, "wb") as f:
        f.truncate(size)
    return path


def make_log(path: Path, size: int = LOG_BYTES) -> Path:
    line = "2026-11-07 12:00:00 INFO worker heartbeat ok\n"
    count = size // len(line) + 1
    path.write_text((line * count)[:size], encoding="ascii")
    return path


def set_offline(path: Path) -> None:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
    k32.GetFileAttributesW.restype = ctypes.c_uint32
    k32.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    attrs = k32.GetFileAttributesW(str(path))
    if attrs == 0xFFFFFFFF or not k32.SetFileAttributesW(str(path), attrs | FILE_ATTRIBUTE_OFFLINE):
        raise ctypes.WinError(ctypes.get_last_error())


def build_preview_tree(dest) -> dict:
    """Write all samples under ``dest``; returns ``{key: Path}``."""
    dest = Path(dest)
    for sub in ("notes", "art", "secrets", "big", "docs", "logs", "img", "cloud", "run", "tools", "acts"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    f = {}

    def put(key, rel, data):
        path = dest / rel
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        f[key] = path

    put("md", "notes/hostile.md", MARKDOWN)
    put("svg", "art/onload.svg", SVG)
    put("pem", "secrets/server.pem", PEM)
    put("docx", "docs/sample.docx", docx_bytes())
    put("xlsx", "docs/sample.xlsx", xlsx_bytes())
    put("pptx", "docs/sample.pptx", pptx_bytes())
    put("pdf", "docs/sample.pdf", pdf_bytes())
    put("csv", "docs/sample.csv", CSV)
    put("ipynb", "docs/sample.ipynb", IPYNB)
    put("png", "img/pic.png", PNG)
    put("webp", "img/pic.webp", b"RIFF\x1a\x00\x00\x00WEBPVP8 \x0e\x00\x00\x00" + b"\x00" * 18)
    put("avif", "img/pic.avif", b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1miaf" + b"\x00" * 16)
    put("heic", "img/pic.heic", b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 16)
    put("exe", "tools/setup.exe", b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00" + b"\x00" * 48)
    put("bat", "run/marker.bat", f'@echo off\r\necho ran> "%~dp0{BAT_MARKER}"\r\n')
    put("offline", "cloud/offline.txt", OFFLINE_MARKER + "\n")
    set_offline(f["offline"])
    put("keybackup", "notes/keybackup.txt", "my backup\n" + PEM)
    put("latekey", "notes/late-key.txt", "filler line of ordinary text\n" * 52429 + PEM)
    put("binary", "notes/binary.txt", b"text start" + bytes([0, 1, 2, 3, 255, 254]) + b" binary tail" * 40)
    put("key10k", "notes/key-at-10000.txt", "A" * 9999 + "\n" + PEM)
    put("pyscript", "acts/script_qa.py", "print('qa')\n")
    put("hta", "acts/page_qa.hta", "<html></html>\n")
    (dest / "acts" / "exclst" / "node_modules").mkdir(parents=True, exist_ok=True)
    put("excl_keep", "acts/exclst/keep.txt", "keep\n")
    put("excl_nm", "acts/exclst/node_modules/dep.txt", "dep\n")
    for n in range(12):
        put(f"act{n:02d}", f"acts/act_{n:02d}.txt", f"action fixture {n}\n")
    (dest / "acts" / "folder_a").mkdir(exist_ok=True)
    (dest / "acts" / "folder_b").mkdir(exist_ok=True)
    put("rev1", "acts/folder_a/reveal_one.txt", "one\n")
    put("rev2", "acts/folder_a/reveal_two.txt", "two\n")
    put("revb", "acts/folder_b/reveal_three.txt", "three\n")
    f["log"] = make_log(dest / "logs" / "big.log")
    f["big"] = make_sparse(dest / "big" / "huge-60mb.dat")
    f["marker"] = dest / "run" / BAT_MARKER
    return f
