"""The full-page web app at /app/ (plan 7.4, 18.4 item 5).

Served from ``assistant/static/app/`` or, when ASSISTANT_STATIC_DIR is set (dev and qa only), from that folder.
No directory listing, no path outside the folder (traversal and links that leave it are 404), no dotfiles.
The bearer check does not apply here (auth.static_path_exempt); everything under /v1/ still needs it.
"""
import logging
import os
from pathlib import Path
from typing import Optional

from starlette.responses import FileResponse, RedirectResponse, Response
from starlette.routing import Route

log = logging.getLogger("assistant.server.static")

ENV_VAR = "ASSISTANT_STATIC_DIR"
PACKAGED_DIR = Path(__file__).resolve().parent.parent / "static" / "app"

_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json", ".map": "application/json", ".wasm": "application/wasm",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8",
}
_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}
_CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' blob: data:; font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; "
        "frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def static_dir(override: Optional[str] = None) -> Path:
    chosen = override or os.environ.get(ENV_VAR)
    return Path(chosen) if chosen else PACKAGED_DIR


def _not_found() -> Response:
    return Response("Not found", status_code=404, media_type="text/plain", headers=dict(_HEADERS))


def _serve(root: Path, rel: str) -> Response:
    rel = rel.strip("/") or "index.html"
    parts = rel.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") or "\\" in p or ":" in p or "\x00" in p for p in parts):
        return _not_found()
    try:
        real_root = root.resolve()
        target = (real_root / Path(*parts)).resolve()
    except (OSError, ValueError):
        return _not_found()
    if real_root != target and real_root not in target.parents:
        return _not_found()
    if not target.is_file():
        return _not_found()
    headers = dict(_HEADERS)
    headers["Content-Security-Policy"] = _CSP
    if parts == ["index.html"]:
        headers["Cache-Control"] = "no-store"
    else:
        headers["Cache-Control"] = "no-cache"
    media_type = _TYPES.get(target.suffix.lower(), "application/octet-stream")
    return FileResponse(target, media_type=media_type, headers=headers)


def register_static(app, directory=None) -> Path:
    """Mount GET /app/ on `app`. Returns the folder being served."""
    root = static_dir(str(directory) if directory else None)

    async def serve(request):
        return _serve(root, request.path_params.get("path", ""))

    async def redirect(request):
        return RedirectResponse("/app/", status_code=307)

    async def favicon(request):
        icon = root / "favicon.ico"
        if icon.is_file():
            return _serve(root, "favicon.ico")
        return Response(status_code=204, headers=dict(_HEADERS))

    app.router.routes.extend([
        Route("/favicon.ico", favicon, methods=["GET", "HEAD"]),
        Route("/app", redirect, methods=["GET", "HEAD"]),
        Route("/app/", serve, methods=["GET", "HEAD"]),
        Route("/app/{path:path}", serve, methods=["GET", "HEAD"]),
    ])
    log.info("serving /app/ from %s", root)
    return root
