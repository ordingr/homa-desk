from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import __version__


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "version": __version__})


routes = [Route("/v1/health", health, methods=["GET"])]
