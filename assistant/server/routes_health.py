import time

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import __version__, paths

_STARTED = time.monotonic()
MCP_LOG_NAME = "mcp.log"
MCP_LOG_DEFAULT_LINES = 200
MCP_LOG_MAX_LINES = 1000
_TAIL_READ_BYTES = 512 * 1024


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "version": __version__,
                         "uptime_s": int(time.monotonic() - _STARTED),
                         "index": None, "plugins": [], "calendars": []})


def _tail(path, count: int) -> list:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_READ_BYTES))
            data = fh.read()
    except OSError:
        return []
    lines = [line.rstrip("\r") for line in data.decode("utf-8", "replace").split("\n")]
    if lines and not lines[-1]:
        lines.pop()
    if size > _TAIL_READ_BYTES and lines:
        lines = lines[1:]
    return lines[-count:]


def mcp_log_tail(count: int) -> list:
    return _tail(paths.logs_dir() / MCP_LOG_NAME, count)


async def mcp_log(request: Request) -> JSONResponse:
    raw = request.query_params.get("lines", str(MCP_LOG_DEFAULT_LINES))
    try:
        count = int(raw)
    except ValueError:
        count = MCP_LOG_DEFAULT_LINES
    count = max(1, min(count, MCP_LOG_MAX_LINES))
    return JSONResponse({"lines": mcp_log_tail(count)})


routes = [Route("/v1/health", health, methods=["GET"]),
          Route("/v1/mcp/log", mcp_log, methods=["GET"])]
