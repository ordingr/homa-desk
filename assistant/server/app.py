import json
import logging
import threading
from typing import Callable, Optional

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware

from . import routes_health
from .auth import AuthMiddleware

log = logging.getLogger("assistant.server.app")

BIND_HOST = "127.0.0.1"
MAX_BODY_BYTES = 1_048_576


class _BodyTooLarge(BaseException):
    """BaseException so a route's `except Exception` around a body read cannot swallow the 413."""


class BodyLimitMiddleware:
    """413 on a declared or streamed body above MAX_BODY_BYTES; never reads the body itself."""

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self._max = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > self._max:
                    await self._reject(scope, send)
                    return
        seen = 0
        started = False

        async def counting_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self._max:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await self._reject(scope, send)

    @staticmethod
    async def _reject(scope, send) -> None:
        log.warning("rejected http %r with 413 (payload too large)", scope.get("path", "")[:200])
        body = json.dumps({"error": "payload too large"}).encode("utf-8")
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode("ascii")),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})


def create_app(token_provider: Callable[[], str]) -> Starlette:
    return Starlette(
        routes=list(routes_health.routes),
        middleware=[Middleware(AuthMiddleware, token_provider=token_provider),
                    Middleware(BodyLimitMiddleware)],
    )


class ServerThread:
    def __init__(self, app: Starlette, port: int) -> None:
        config = uvicorn.Config(app, host=BIND_HOST, port=port, log_config=None,
                                access_log=False, lifespan="off")
        self._server = uvicorn.Server(config)
        self._thread: Optional[threading.Thread] = None
        self.port = port

    def start(self, timeout: float = 10.0) -> bool:
        self._thread = threading.Thread(target=self._run, name="server", daemon=True)
        self._thread.start()
        deadline = timeout
        step = 0.05
        while deadline > 0:
            if self._server.started:
                return True
            if not self._thread.is_alive():
                return False
            self._thread.join(step)
            deadline -= step
        return self._server.started

    def _run(self) -> None:
        try:
            self._server.run()
        except SystemExit:
            log.error("server failed to start on %s:%d (port in use?)", BIND_HOST, self.port)
        except Exception:
            log.exception("server crashed")

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
