"""Files-mode search over HTTP: /v1/search, /v1/roots and the saved-search routes (PRD section 8, AC-FI-16).

The "show protected files by name" setting (SEC-3, PM 13) is honoured here and nowhere else: this is the local
search UI's route. MCP never reads it.
"""
import logging
from dataclasses import asdict
from typing import Any, Optional

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..index import query as iquery
from ..index import search as isearch
from .routes_cards import ApiError, read_json

log = logging.getLogger("assistant.server.routes_search")

DEFAULT_LIMIT = 50
SAVED_BODY_MAX = 4_096
MAX_LIMIT = 500
MAX_OFFSET = 100_000

# `doc` is left out on purpose: the PRD's own example `design doc` is a path term, not an extension filter.
KNOWN_EXTS = frozenset(
    "md markdown txt rtf pdf docx xls xlsx xlsm csv tsv ppt pptx odt ods json xml yml yaml toml ini cfg log "
    "py js mjs ts tsx jsx html htm css scss java kt cs cpp c h go rs rb php sh ps1 bat sql "
    "png jpg jpeg gif bmp svg webp ico zip".split()
)


def _int_param(request: Request, name: str, default: int, maximum: Optional[int] = None) -> int:
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(422, "invalid_parameter", f"{name} must be an integer.", name) from None
    if value < 0:
        raise ApiError(422, "invalid_parameter", f"{name} must be 0 or more.", name)
    return value if maximum is None else min(value, maximum)


def _chips(request: Request) -> list:
    out: list = []
    for raw in request.query_params.getlist("chips"):
        out.extend(part.strip() for part in raw.split(",") if part.strip())
    return out


def show_protected(settings: Any) -> bool:
    if settings is None:
        return False
    index = settings.get("index") or {}
    return isinstance(index, dict) and index.get("show_protected_by_name") is True


def _search_sync(index: Any, settings: Any, text: str, chips: list, modified: str, sort: str,
                 limit: int, offset: int) -> dict:
    store, delta = index.current()
    if store is None:
        raise ApiError(503, "index_unavailable", "The file index is not ready yet.")
    parsed = iquery.parse(text, KNOWN_EXTS)
    try:
        result = isearch.run(parsed, store, delta, chips, modified or None, sort, limit, offset,
                             show_denied=show_protected(settings))
    except ValueError as exc:
        message = str(exc)
        field = "chips" if "chip" in message else "sort" if "sort" in message else \
            "modified" if "date" in message else None
        raise ApiError(422, "invalid_parameter", message, field) from None
    body = {"rows": [row.to_dict() for row in result.rows], "total": result.total,
            "elapsed_ms": result.elapsed_ms, "interpretation": result.interpretation,
            "built_at": result.built_at, "truncated": result.truncated}
    if parsed.notes:
        body["notes"] = list(parsed.notes)
    return body


async def search(request: Request) -> JSONResponse:
    state = request.app.state
    limit = _int_param(request, "limit", DEFAULT_LIMIT, MAX_LIMIT)
    offset = _int_param(request, "offset", 0, MAX_OFFSET)
    sort = (request.query_params.get("sort") or "best").strip().casefold()
    body = await run_in_threadpool(
        _search_sync, state.index, getattr(state, "settings", None), request.query_params.get("q", ""),
        _chips(request), (request.query_params.get("modified") or "").strip(), sort, limit, offset)
    return JSONResponse(body)


def _configured_roots(settings: Any) -> list:
    raw = (settings.get("index") or {}).get("roots") if settings is not None else None
    roots = []
    for item in raw or []:
        path = item.get("path") if isinstance(item, dict) else item
        if isinstance(path, str) and path.strip():
            roots.append({"path": path.strip(), "state": "pending", "entries": 0, "last_walk_s": None, "error": None})
    return roots


async def roots(request: Request) -> JSONResponse:
    state = request.app.state
    status = dict(state.index.status())
    if not status.get("roots"):
        status["roots"] = _configured_roots(getattr(state, "settings", None))
    return JSONResponse(status)


async def saved_list(request: Request) -> JSONResponse:
    return JSONResponse({"searches": [asdict(s) for s in request.app.state.saved.list()]})


async def saved_add(request: Request) -> JSONResponse:
    body = _object_body(await read_json(request, SAVED_BODY_MAX))
    chips = body.get("chips", [])
    if not isinstance(chips, list) or not all(isinstance(c, str) for c in chips):
        raise ApiError(422, "invalid_parameter", "chips must be a list of strings.", "chips")
    for key in ("name", "query", "modified", "sort", "mode"):
        if key in body and not isinstance(body[key], str):
            raise ApiError(422, "invalid_parameter", f"{key} must be a string.", key)
    try:
        saved = request.app.state.saved.add(body.get("name", ""), body.get("query", ""), chips,
                                            body.get("modified", ""), body.get("sort", ""), body.get("mode", "files"))
    except ValueError as exc:
        raise ApiError(422, "invalid_parameter", str(exc)) from None
    return JSONResponse(asdict(saved), status_code=201)


async def saved_delete(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    if not request.app.state.saved.remove(name):
        raise ApiError(404, "not_found", "No saved search with that name.")
    return JSONResponse({"ok": True})


def _object_body(body: Any) -> dict:
    if not isinstance(body, dict):
        raise ApiError(422, "invalid_body", "The request body must be a JSON object.")
    return body


routes = [
    Route("/v1/search", search, methods=["GET"]),
    Route("/v1/roots", roots, methods=["GET"]),
    Route("/v1/saved", saved_list, methods=["GET"]),
    Route("/v1/saved", saved_add, methods=["POST"]),
    Route("/v1/saved/{name}", saved_delete, methods=["DELETE"]),
]
