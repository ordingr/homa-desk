"""POST /v1/actions/{verb}: the same in-process functions and action log the popup uses (plan 18.4 item 2)."""
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..actions import VERBS, claude, dispatch
from ..actions.shell import CLIPBOARD_OPS, MAX_CLIPBOARD_PATHS, clipboard_files
from .routes_cards import ApiError, read_json

ACTION_BODY_MAX = 4_096
CLIPBOARD_BODY_MAX = 262_144
_STATUS = {"refused": 403, "invalid": 422, "not_found": 404, "error": 500, "not_implemented": 501,
           "unavailable": 503}
_FIELD = {"invalid": "new_name"}


def _body(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ApiError(422, "invalid_body", "The request body must be a JSON object.")
    path = raw.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ApiError(422, "invalid_parameter", "path must be a non-empty string.", "path")
    new_name = raw.get("new_name")
    if new_name is not None and not isinstance(new_name, str):
        raise ApiError(422, "invalid_parameter", "new_name must be a string.", "new_name")
    if "confirm" in raw and not isinstance(raw["confirm"], bool):
        raise ApiError(422, "invalid_parameter", "confirm must be true or false.", "confirm")
    if "whole_file" in raw and not isinstance(raw["whole_file"], bool):
        raise ApiError(422, "invalid_parameter", "whole_file must be true or false.", "whole_file")
    return raw


async def ask_claude_status(request: Request) -> JSONResponse:
    refresh = request.query_params.get("refresh") in ("1", "true")
    return JSONResponse(await run_in_threadpool(claude.status, refresh))


async def ask_claude_continue(request: Request) -> JSONResponse:
    raw = await read_json(request, ACTION_BODY_MAX)
    path = raw.get("path") if isinstance(raw, dict) else None
    if not isinstance(path, str) or not path.strip():
        raise ApiError(422, "invalid_parameter", "path must be a non-empty string.", "path")
    if not claude.has_context(path):
        raise ApiError(404, "no_answer", "There is no Claude answer for that file in this session.", "path")
    result = await run_in_threadpool(claude.continue_session, path)
    if not result["ok"]:
        raise ApiError(500, "error", result["message"])
    return JSONResponse(result)


async def action(request: Request) -> JSONResponse:
    verb = request.path_params["verb"]
    if verb not in VERBS:
        raise ApiError(404, "unknown_action", "No such action.", "verb")
    body = _body(await read_json(request, ACTION_BODY_MAX))
    state = request.app.state
    if getattr(state, "index", None) is None:
        raise ApiError(503, "index_unavailable", "The file index is not ready yet.")
    result = await run_in_threadpool(
        dispatch, verb, body["path"], body.get("new_name"), body.get("confirm") is True,
        index=state.index, settings=getattr(state, "settings", None), whole_file=body.get("whole_file") is True)
    kind = result.get("kind")
    if kind is None:
        return JSONResponse(result)
    if kind == "invalid" and verb != "rename":
        return JSONResponse(ApiError(422, "invalid_parameter", result["message"], "path").to_dict(), status_code=422)
    status = _STATUS.get(kind, 500)
    code = {"refused": "refused", "invalid": "invalid_name"}.get(kind, kind)
    envelope = ApiError(status, code, result["message"], _FIELD.get(kind)).to_dict()
    # copy_path promises the path forms even when the clipboard refused (api.md 12.6).
    for key in ("plain", "quoted"):
        if key in result:
            envelope[key] = result[key]
    return JSONResponse(envelope, status_code=status)


async def files_clipboard(request: Request) -> JSONResponse:
    """POST /v1/files/clipboard {"paths": [...], "op": "copy" | "cut"}: files on the clipboard for Explorer's Paste."""
    raw = await read_json(request, CLIPBOARD_BODY_MAX)
    if not isinstance(raw, dict):
        raise ApiError(422, "invalid_body", "The request body must be a JSON object.")
    op = raw.get("op")
    if not isinstance(op, str) or op not in CLIPBOARD_OPS:
        raise ApiError(422, "invalid_parameter", "op must be copy or cut.", "op")
    paths = raw.get("paths")
    if (not isinstance(paths, list) or not 0 < len(paths) <= MAX_CLIPBOARD_PATHS
            or not all(isinstance(p, str) for p in paths)):
        raise ApiError(422, "invalid_parameter", f"paths must be a list of 1 to {MAX_CLIPBOARD_PATHS} strings.",
                       "paths")
    index = getattr(request.app.state, "index", None)
    if index is None:
        raise ApiError(503, "index_unavailable", "The file index is not ready yet.")
    result = await run_in_threadpool(clipboard_files, paths, op, index=index)
    kind = result.get("kind")
    if kind is None:
        return JSONResponse(result)
    status = _STATUS.get(kind, 500)
    envelope = ApiError(status, "invalid_parameter" if kind == "invalid" else kind, result["message"],
                        result.get("field")).to_dict()
    if "skipped" in result:
        envelope["skipped"] = result["skipped"]
    return JSONResponse(envelope, status_code=status)


routes = [Route("/v1/actions/ask_claude/status", ask_claude_status, methods=["GET"]),
          Route("/v1/actions/ask_claude/continue", ask_claude_continue, methods=["POST"]),
          Route("/v1/actions/{verb}", action, methods=["POST"]),
          Route("/v1/files/clipboard", files_clipboard, methods=["POST"])]
