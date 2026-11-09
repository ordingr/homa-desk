import asyncio
import errno
import json
import logging
import socket
import threading
from typing import Callable, Optional

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount

from ..cards.claude_sessions import ClaudeSessions
from ..cards.filedrop import FileDrop
from ..cards.store import CardStore
from ..reminders.store import ReminderStore
from ..scheduler import Scheduler
from ..search.saved import SavedSearches
from . import routes_cards, routes_health, routes_hooks, routes_reminders, routes_search
from .auth import AuthMiddleware

log = logging.getLogger("homa.server.app")

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
        body = json.dumps({"error": {"code": "payload_too_large", "message": "payload too large", "field": None}}).encode("utf-8")
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode("ascii")),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})


def create_app(token_provider: Callable[[], str], store: Optional[CardStore] = None,
               reminders: Optional[ReminderStore] = None,
               scheduler: Optional[Scheduler] = None, mcp_app=None, index=None,
               settings=None, saved=None) -> Starlette:
    """Card, hook and file-drop routes need `store`; reminder routes need `reminders`.
    With `scheduler` as well, the events/ folder watcher is started on it.
    `mcp_app` (an ASGI app) is mounted at /mcp behind the same middleware.
    Search routes need `index` (an object with current() -> (store, delta) and status()); `settings`
    supplies index.show_protected_by_name, `saved` is the saved-search store (default per-machine file)."""
    routes = list(routes_health.routes)
    if store is not None:
        routes.extend(routes_cards.routes)
        routes.extend(routes_hooks.routes)
    if reminders is not None:
        routes.extend(routes_reminders.routes)
    if index is not None:
        routes.extend(routes_search.routes)
    if mcp_app is not None:
        routes.append(Mount("/mcp", app=mcp_app))
    app = Starlette(
        routes=routes,
        middleware=[Middleware(AuthMiddleware, token_provider=token_provider),
                    Middleware(BodyLimitMiddleware)],
        exception_handlers=routes_cards.exception_handlers,
    )
    if store is not None:
        app.state.store = store
        app.state.sessions = ClaudeSessions(store)
        if scheduler is not None:
            app.state.filedrop = FileDrop(store)
            app.state.filedrop.start(scheduler)
    if reminders is not None:
        app.state.reminders = reminders
    if index is not None:
        app.state.index = index
        app.state.settings = settings
        app.state.saved = saved if saved is not None else SavedSearches()
    return app


IDLE_TICK_SECONDS = 1.0
GRACEFUL_SHUTDOWN_S = 2
STOP_JOIN_S = 4.0


class _QuietServer(uvicorn.Server):
    """uvicorn's main loop wakes 10x/s; one wake per second is enough and keeps idle CPU near zero. An exit
    request wakes it at once."""

    _loop: Optional[asyncio.AbstractEventLoop] = None
    _wake: Optional[asyncio.Event] = None

    async def main_loop(self) -> None:
        self._loop, self._wake = asyncio.get_running_loop(), asyncio.Event()
        should_exit = await self.on_tick(0)
        while not should_exit:
            try:
                await asyncio.wait_for(self._wake.wait(), IDLE_TICK_SECONDS)
            except asyncio.TimeoutError:
                pass
            should_exit = await self.on_tick(0)

    def request_exit(self) -> None:
        self.should_exit = True
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:
                pass


class ServerThread:
    def __init__(self, app: Starlette, port: int) -> None:
        config = uvicorn.Config(app, host=BIND_HOST, port=port, log_config=None,
                                access_log=False, lifespan="off",
                                timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S)
        self._server = _QuietServer(config)
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
            log.error("server failed to start on %s:%d (%s)", BIND_HOST, self.port, self._bind_error())
        except Exception:
            log.exception("server crashed")

    def _bind_error(self) -> str:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind((BIND_HOST, self.port))
        except OSError as exc:
            code = exc.winerror if getattr(exc, "winerror", None) else exc.errno
            if code in (10048, errno.EADDRINUSE):
                return f"address in use ({code})"
            return f"bind failed: {type(exc).__name__} {code} {exc.strerror}"
        finally:
            probe.close()
        return "startup failed; the port is bindable now, see earlier log lines"

    def request_stop(self) -> None:
        self._server.request_exit()

    def stop(self) -> None:
        self._server.request_exit()
        if self._thread is not None:
            self._thread.join(timeout=STOP_JOIN_S)
