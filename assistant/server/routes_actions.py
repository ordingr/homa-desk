"""POST /v1/actions/{verb}: the same in-process functions and action log the popup uses (plan 18.4 item 2)."""
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..actions import VERBS, dispatch
from .routes_cards import ApiError, read_json

ACTION_BODY_MAX = 4_096
_STATUS = {"refused": 403, "invalid": 422, "not_found": 404, "error": 500, "not_implemented": 501}
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
    return raw


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
        index=state.index, settings=getattr(state, "settings", None))
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


routes = [Route("/v1/actions/{verb}", action, methods=["POST"])]
