"""File previews over HTTP and the full-page window close route (plan 7.4 M3b-3; AC-PV-3..6, SEC-3, SEC-4, SEC-5).

GET /v1/files/preview?path=<abs>&range=<start>-<end>
  Returns JSON ``{kind, ...}`` (markdown, text, html, table, docx, xlsx, pptx, meta, protected, placeholder,
  too_large) or streams bytes (images, pdf; text kinds when the request carries an HTTP Range header) with
  Content-Type and Accept-Ranges. The server never returns file content as text/html; ``html`` and ``markdown``
  come back as JSON text for the client to sanitise and render (DOMPurify, strict Mermaid). ``range`` is a byte
  window, both ends inclusive, at most 1 MiB; the answer carries ``start``, ``end`` (exclusive offset of the next
  byte) and ``truncated``.

Every request is vetted by ``mcp._vet`` (roots, deny-list on given and resolved path, Homa's own folder,
index membership), then ``os.lstat`` decides placeholder / size before the file is opened. The open handle is
checked once more (GetFinalPathNameByHandle) before any byte is read. The private-key scan covers the returned
range plus a short overlap on each side.

A ``hydrate`` query parameter is ignored: a cloud placeholder stays a placeholder and is never downloaded; the
Open action is the only way the user causes a download.

POST /v1/fullpage/close lives here too: it closes the Edge window ``overlay.fullpage`` opened.
"""
import codecs
import logging
import msvcrt
import os
import re
import stat
from typing import Any, Optional

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.background import BackgroundTask

from .. import paths
from ..preview import extract
from ..security import denylist, pathcheck
from . import auth, mcp
from .routes_cards import ApiError, read_json

log = logging.getLogger("homa.server.routes_files")

DEFAULT_MAX_BYTES = 50_000_000
TEXT_DEFAULT_WINDOW = 262_144
TEXT_MAX_WINDOW = 1_048_576
HTTP_RANGE_TEXT_CAP = 4_194_304
SVG_MAX_BYTES = 8_388_608
SCAN_OVERLAP = 8192
CHUNK = 262_144
FULLPAGE_BODY_MAX = 262_144
_HANDOFF_FIELDS = ("chips", "modified", "sort", "select_path")

_PLACEHOLDER_ATTRS = 0x1000 | 0x40000 | 0x400000
_DIR_ATTR = 0x10
_EXEC_EXTS = frozenset("exe dll sys msi msp bat cmd ps1 scr com".split())
_HEIC_EXTS = frozenset(("heic", "heif"))
_MD_EXTS = frozenset(("md", "markdown"))
_HTML_EXTS = frozenset(("html", "htm", "xhtml"))
_TABLE_EXTS = frozenset(("csv", "tsv"))
_OFFICE = {"docx": "docx", "xlsx": "xlsx", "xlsm": "xlsx", "pptx": "pptx"}
_IMAGES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
           "webp": "image/webp", "avif": "image/avif", "bmp": "image/bmp", "ico": "image/x-icon",
           "svg": "image/svg+xml"}
_INERT = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
_RANGE_PARAM = re.compile(r"^(\d{1,15})-(\d{0,15})$")
_RANGE_HEADER = re.compile(r"^bytes=(\d{0,15})-(\d{0,15})$")
_RANGE_SHAPE = re.compile(r"^bytes=\d*-\d*$")

R_PROTECTED = "refused: protected file (secrets deny-list)"
R_KEY = "refused: the file contains a private key (secrets deny-list)"
R_PLACEHOLDER = "refused: the file is a cloud placeholder that is not downloaded; it is never fetched"
R_FOLDER = "refused: that is a folder, not a file"
R_BINARY = "refused: the file content looks binary, only text is previewed"
R_UNREADABLE = "unavailable: the file could not be read"
R_MOVED = "refused: the file is no longer where it was checked (outside the indexed roots or protected)"


class _Stream:
    def __init__(self, fh, start: int, length: int, size: int, content_type: str, partial: bool) -> None:
        self.fh, self.start, self.length, self.size = fh, start, length, size
        self.content_type, self.partial = content_type, partial


class _Bytes:
    def __init__(self, data: bytes, content_type: str, headers: dict, status: int = 200) -> None:
        self.data, self.content_type, self.headers, self.status = data, content_type, headers, status


def _cap(settings: Any) -> int:
    try:
        value = (settings.get("preview") or {}).get("max_bytes") if settings is not None else None
    except Exception:
        value = None
    if isinstance(value, int) and not isinstance(value, bool) and 1_048_576 <= value <= 4_294_967_296:
        return value
    return DEFAULT_MAX_BYTES


def _protected(reason: str = R_PROTECTED) -> dict:
    return {"kind": "protected", "reason": reason}


def _refusal(reason: str) -> Optional[dict]:
    """Maps a ``_vet`` reason to a JSON answer or raises the matching ApiError."""
    if "secrets deny-list" in reason:
        return _protected(reason)
    if reason.startswith("unavailable"):
        raise ApiError(503, "index_unavailable", reason, "path")
    if "not in the index" in reason:
        raise ApiError(404, "not_found", reason, "path")
    if "usable path" in reason or "absolute path" in reason:
        raise ApiError(422, "invalid_parameter", reason, "path")
    raise ApiError(403, "refused", reason, "path")


def _roots(index) -> list:
    return [r["path"] for r in index.status().get("roots", [])]


def _handle_ok(fh, roots: list) -> bool:
    """Open-then-verify (R7-N2): the path the handle really points at is inside the roots, not protected and
    not Homa's own folder. Fails closed."""
    try:
        import win32file
        final = pathcheck._plain(win32file.GetFinalPathNameByHandle(msvcrt.get_osfhandle(fh.fileno()), 0))
    except Exception:
        return False
    if pathcheck.resolve_in_roots(final, roots) is None or denylist.matches(final):
        return False
    own = pathcheck._resolve(str(paths.home()))
    return not (own and pathcheck._inside(final, own))


def _handle_attrs(fh) -> int:
    """File attributes read through the open handle (R9-N12); a handle that cannot be queried reads as a placeholder."""
    try:
        import win32file
        return win32file.GetFileInformationByHandle(msvcrt.get_osfhandle(fh.fileno()))[0]
    except Exception:
        return _PLACEHOLDER_ATTRS


def _parse_window(raw: Optional[str], size: int, default_len: int) -> tuple:
    """(start, length) for the ``range`` query parameter."""
    if raw is None or raw == "":
        return 0, default_len
    m = _RANGE_PARAM.match(raw.strip())
    if not m:
        raise ApiError(422, "invalid_parameter", "range must look like 0-65535.", "range")
    start = int(m.group(1))
    if start > 0 and start >= size:
        raise ApiError(422, "invalid_parameter", "range starts past the end of the file.", "range")
    if m.group(2) == "":
        return start, default_len
    end = int(m.group(2))
    if end < start:
        raise ApiError(422, "invalid_parameter", "range ends before it starts.", "range")
    return start, min(end - start + 1, TEXT_MAX_WINDOW)


def _read_scanned(fh, start: int, length: int) -> tuple:
    """(body bytes, scanned text): the window, and the file's first MiB plus the window with overlap, decoded for the key scan."""
    pre = min(SCAN_OVERLAP, start)
    fh.seek(0)
    head = fh.read(mcp.PREVIEW_SCAN_BYTES)
    fh.seek(start - pre)
    blob = fh.read(pre + length + SCAN_OVERLAP)
    return blob[pre:pre + length], head.decode("utf-8", "replace") + "\n" + blob.decode("utf-8", "replace")


def _decode_window(body: bytes, start: int) -> tuple:
    """(text, bytes consumed from the start offset): drops a split multibyte character at either edge."""
    skipped = 0
    if start > 0:
        while skipped < 3 and skipped < len(body) and 0x80 <= body[skipped] < 0xC0:
            skipped += 1
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    text = decoder.decode(body[skipped:], final=False)
    pending = len(decoder.getstate()[0])
    if start == 0 and text.startswith("﻿"):
        text = text[1:]
    return text, len(body) - pending


def _text_answer(fh, kind: str, start: int, length: int, size: int, ext: str) -> dict:
    body, scanned = _read_scanned(fh, start, length)
    if mcp._looks_binary(body[:8192]):
        return {"kind": "meta", "size": size, "reason": R_BINARY}
    if denylist.content_has_private_key(scanned):
        return _protected(R_KEY)
    text, consumed = _decode_window(body, start)
    end = start + consumed
    truncated = end < size
    if kind == "table":
        table = extract.parse_table(text, ext, complete=not truncated)
        if denylist.content_has_private_key(extract.table_text(table["rows"])):
            return _protected(R_KEY)
        return {"kind": "table", "rows": table["rows"], "truncated": truncated or table["truncated"],
                "size": size, "start": start, "end": end}
    return {"kind": kind, "text": text, "truncated": truncated, "size": size, "start": start, "end": end}


def _parse_http_range(value: str, size: int) -> Any:
    """(start, end_inclusive), None when the header is absent, malformed or multi-range, or 'unsatisfiable'."""
    m = _RANGE_HEADER.match(value.strip()) if value else None
    if not m and value and _RANGE_SHAPE.match(value.strip()):
        return "unsatisfiable"
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return None
    if m.group(1) == "":
        suffix = int(m.group(2))
        if suffix == 0:
            return "unsatisfiable"
        return max(0, size - suffix), size - 1
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else size - 1
    if start >= size or (m.group(2) and end < start):
        return "unsatisfiable"
    return start, min(end, size - 1)


def _text_via_range_header(fh, rng: tuple, size: int) -> Any:
    start, end = rng
    end = min(end, start + HTTP_RANGE_TEXT_CAP - 1)
    body, scanned = _read_scanned(fh, start, end - start + 1)
    if mcp._looks_binary(body[:8192]):
        return {"kind": "meta", "size": size, "reason": R_BINARY}
    if denylist.content_has_private_key(scanned):
        return _protected(R_KEY)
    last = start + len(body) - 1
    return _Bytes(body, "text/plain; charset=utf-8",
                  {"Content-Range": f"bytes {start}-{last}/{size}"}, status=206)


def _preview_sync(index, settings, path: Any, range_param: Optional[str], range_header: str) -> Any:
    resolved, reason = mcp._vet(index, path)
    if resolved is None:
        return _refusal(reason)
    try:
        st = os.lstat(resolved)
    except OSError:
        raise ApiError(404, "not_found", R_UNREADABLE, "path") from None
    attrs = getattr(st, "st_file_attributes", 0)
    if stat.S_ISDIR(st.st_mode) or attrs & _DIR_ATTR:
        raise ApiError(422, "invalid_parameter", R_FOLDER, "path")
    name = os.path.basename(resolved)
    base = {"name": name, "size": st.st_size, "mtime": st.st_mtime}
    if attrs & _PLACEHOLDER_ATTRS:
        return {"kind": "placeholder", **base, "reason": R_PLACEHOLDER}
    cap = _cap(settings)
    if st.st_size > cap:
        return {"kind": "too_large", **base, "cap": cap}
    ext = os.path.splitext(name)[1].lstrip(".").casefold()
    if ext in _EXEC_EXTS:
        return {"kind": "meta", **base, "reason": "executable file: not previewed"}
    if ext in _HEIC_EXTS:
        return {"kind": "meta", **base, "reason": "HEIC images are not supported"}
    office = _OFFICE.get(ext)
    is_image, is_pdf = ext in _IMAGES, ext == "pdf"
    if not (office or is_image or is_pdf) and ext in mcp._BINARY_EXTS:
        return {"kind": "meta", **base, "reason": "binary file type: not previewed"}

    fh = open(resolved, "rb")
    keep = False
    try:
        if not _handle_ok(fh, _roots(index)):
            raise ApiError(403, "refused", R_MOVED, "path")
        if _handle_attrs(fh) & (_PLACEHOLDER_ATTRS | _DIR_ATTR):
            return {"kind": "placeholder", **base, "reason": R_PLACEHOLDER}
        size = os.fstat(fh.fileno()).st_size
        if size > cap:
            return {"kind": "too_large", **base, "size": size, "cap": cap}
        base["size"] = size
        if is_pdf or (is_image and ext != "svg"):
            rng = _parse_http_range(range_header, size) if range_header else None
            if rng == "unsatisfiable":
                raise _Unsatisfiable(size)
            start, end = rng if rng else (0, size - 1)
            keep = True
            return _Stream(fh, start, end - start + 1 if size else 0, size,
                           "application/pdf" if is_pdf else _IMAGES[ext], rng is not None)
        if ext == "svg":
            if size > SVG_MAX_BYTES:
                return {"kind": "too_large", **base, "cap": SVG_MAX_BYTES}
            data = fh.read(SVG_MAX_BYTES + 1)
            if denylist.content_has_private_key(data.decode("utf-8", "replace")):
                return _protected(R_KEY)
            return _Bytes(data, _IMAGES["svg"], {"Content-Security-Policy": _INERT})
        if office:
            try:
                result = getattr(extract, f"extract_{office}")(fh)
            except extract.ExtractError as exc:
                return {"kind": "meta", **base, "reason": str(exc)}
            if denylist.content_has_private_key(extract.result_text(result)):
                return _protected(R_KEY)
            return {"kind": office, **base, **result}
        if range_header:
            rng = _parse_http_range(range_header, size)
            if rng == "unsatisfiable":
                raise _Unsatisfiable(size)
            if rng:
                return _text_via_range_header(fh, rng, size)
        kind = ("markdown" if ext in _MD_EXTS else "html" if ext in _HTML_EXTS
                else "table" if ext in _TABLE_EXTS else "text")
        window = TEXT_MAX_WINDOW if kind == "table" else TEXT_DEFAULT_WINDOW
        start, length = _parse_window(range_param, size, window)
        answer = _text_answer(fh, kind, start, length, size, ext)
        if answer["kind"] == "text" or answer["kind"] in ("markdown", "html", "table"):
            answer["name"] = name
            answer["mtime"] = base["mtime"]
        return answer
    finally:
        if not keep:
            fh.close()


class _Unsatisfiable(Exception):
    def __init__(self, size: int) -> None:
        self.size = size


_INERT_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Disposition": "inline",
                  "Accept-Ranges": "bytes", "Cache-Control": "no-store"}


async def _chunks(fh, start: int, length: int):
    try:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            chunk = await run_in_threadpool(fh.read, min(CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        fh.close()


def _stream_response(out: _Stream) -> Response:
    headers = dict(_INERT_HEADERS)
    headers["Content-Length"] = str(out.length)
    if out.content_type.startswith("image/"):
        headers["Content-Security-Policy"] = _INERT
    status = 200
    if out.partial:
        status = 206
        headers["Content-Range"] = f"bytes {out.start}-{out.start + out.length - 1}/{out.size}"
    return StreamingResponse(_chunks(out.fh, out.start, out.length), status_code=status,
                             media_type=out.content_type, headers=headers,
                             background=BackgroundTask(out.fh.close))


async def preview(request: Request) -> Response:
    state = request.app.state
    try:
        out = await run_in_threadpool(
            _preview_sync, state.files_index, state.files_settings, request.query_params.get("path"),
            request.query_params.get("range"), request.headers.get("range", ""))
    except _Unsatisfiable as exc:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{exc.size}", **_INERT_HEADERS})
    except OSError:
        raise ApiError(404, "not_found", R_UNREADABLE, "path") from None
    if isinstance(out, _Stream):
        return _stream_response(out)
    if isinstance(out, _Bytes):
        headers = {**_INERT_HEADERS, **out.headers}
        return Response(out.data, status_code=out.status, media_type=out.content_type, headers=headers)
    return JSONResponse(out, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


async def fullpage_close(request: Request) -> JSONResponse:
    closer = getattr(request.app.state, "files_closer", None)
    if closer is None:
        from ..overlay import fullpage
        closer = fullpage.close
    closed = await run_in_threadpool(closer)
    return JSONResponse({"ok": True, "closed": bool(closed)})


async def fullpage_open(request: Request) -> JSONResponse:
    opener = request.app.state.files_opener
    body = await read_json(request, FULLPAGE_BODY_MAX)
    query = body.get("query", "") if isinstance(body, dict) else None
    if not isinstance(query, str) or len(query) > 1000:
        raise ApiError(422, "invalid_parameter", "query must be a string of at most 1000 characters.", "query")
    present = [name for name in _HANDOFF_FIELDS if name in body]
    for name in present:
        try:
            auth.check_handoff({name: body[name]})
        except ValueError as exc:
            raise ApiError(422, "invalid_parameter", str(exc), name) from None
    handoff = auth.check_handoff({"query": query, **{n: body[n] for n in present}}) if present else None
    opened = await run_in_threadpool(opener, query, handoff)
    return JSONResponse({"ok": True, "opened": bool(opened)})


def register_files_routes(app, index, settings=None, closer=None, opener=None) -> None:
    """Adds GET /v1/files/preview, POST /v1/fullpage/open and POST /v1/fullpage/close to `app` (behind the auth
    middleware). `opener(query, handoff) -> bool` launches the full page (the open route exists only when it is given); `closer() -> bool` defaults to fullpage.close."""
    app.state.files_index = index
    app.state.files_settings = settings
    app.state.files_closer = closer
    app.state.files_opener = opener
    app.router.routes.extend([
        Route("/v1/files/preview", preview, methods=["GET"]),
        Route("/v1/fullpage/close", fullpage_close, methods=["POST"]),
    ])
    if opener is not None:
        app.router.routes.append(Route("/v1/fullpage/open", fullpage_open, methods=["POST"]))
