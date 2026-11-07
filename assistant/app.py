import logging
import logging.handlers
import sys
import threading

from . import __version__, paths, uibus
from .scheduler import Scheduler
from .security import token as token_store
from .server.app import ServerThread, create_app
from .settings import Settings

log = logging.getLogger("assistant.app")


def _setup_logging() -> None:
    handler = logging.handlers.RotatingFileHandler(
        paths.logs_dir() / "app.log", maxBytes=2 * 1024 * 1024, backupCount=4, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(console)


class App:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.settings = Settings()
        self.scheduler = Scheduler()
        self.server: ServerThread | None = None
        self.root = None
        self._token = ""

    def run(self) -> int:
        import tkinter

        paths.ensure_layout()
        _setup_logging()
        self.settings.load()
        self._token = token_store.ensure()
        log.info("starting assistant %s, home %s", __version__, paths.home())

        self.scheduler.start()
        self.server = ServerThread(create_app(lambda: self._token), self.settings.port)
        if not self.server.start():
            log.error("could not start the HTTP server on port %d", self.settings.port)
            self.scheduler.stop()
            return 1

        self.root = tkinter.Tk()
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        uibus.start(self.root)
        print(f"assistant {__version__} listening on http://127.0.0.1:{self.settings.port}", flush=True)
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            log.info("interrupted")
        finally:
            self.shutdown()
        return 0

    def shutdown(self) -> None:
        if self.stop.is_set():
            return
        self.stop.set()
        log.info("shutting down")
        uibus.stop()
        self.scheduler.stop()
        if self.server is not None:
            self.server.stop()
        if self.root is not None:
            try:
                self.root.destroy()
            except Exception:
                pass


def main() -> int:
    return App().run()
