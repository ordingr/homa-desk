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


def create_app(token_provider: Callable[[], str]) -> Starlette:
    return Starlette(
        routes=list(routes_health.routes),
        middleware=[Middleware(AuthMiddleware, token_provider=token_provider)],
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
