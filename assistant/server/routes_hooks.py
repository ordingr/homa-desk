import logging

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .routes_cards import ApiError, read_json

log = logging.getLogger("assistant.server.routes_hooks")


async def claude_code_hook(request: Request) -> JSONResponse:
    payload = await read_json(request, None)
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_body", "The request body must be a JSON object.")
    try:
        request.app.state.sessions.handle(payload)
    except Exception:
        log.exception("hook event could not be processed")
    return JSONResponse({})


routes = [Route("/v1/hooks/claude-code", claude_code_hook, methods=["POST"])]
