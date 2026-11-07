import asyncio
import logging
import logging.handlers
import math
import os
import sys
import threading
import time
from typing import Optional

from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import __version__, netinfo, paths, uibus
from .calendar.ics import IcsSource
from .calendar.meetings import MeetingScheduler
from .calendar.outlook import OutlookSource
from .cards.model import Card
from .cards.store import CardStore
from .reminders.store import Reminder, ReminderStore
from .scheduler import Handle, Scheduler
from .security import token as token_store
from .server.app import ServerThread, create_app
from .server.mcp import build_mcp
from .settings import Settings

log = logging.getLogger("assistant.app")

TEST_MODE_VAR = "ASSISTANT_TEST_MODE"
DEBUG_TIMEOUT_S = 3.0
TICK_SLACK_S = 0.05
MCP_PATH = "/mcp"


class _ProactorResetFilter(logging.Filter):
    """Drops the proactor transport's ConnectionResetError traceback from _call_connection_lost."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if not isinstance(exc, ConnectionResetError):
            return True
        context = getattr(record, "message", None) or record.getMessage()
        return "_call_connection_lost" not in context


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
    logging.getLogger("asyncio").addFilter(_ProactorResetFilter())


class _ExactMount:
    """Serves the mounted app for the path without its trailing slash instead of a 307 redirect."""

    def __init__(self, app, prefix: str) -> None:
        self._app = app
        self._prefix = prefix

    async def __call__(self, scope, receive, send):
        inner = dict(scope, path="/", raw_path=b"/", root_path=scope.get("root_path", "") + self._prefix)
        await self._app(inner, receive, send)


def pid_file():
    return paths.home() / "assistant.pid"


class App:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.settings = Settings()
        self.scheduler = Scheduler()
        self.store: Optional[CardStore] = None
        self.reminders: Optional[ReminderStore] = None
        self.server: Optional[ServerThread] = None
        self.root = None
        self.icon = None
        self.cards_ui = None
        self.meetings: Optional[MeetingScheduler] = None
        self.settings_window = None
        self._started_at = time.monotonic()
        self._token = ""
        self._token_stamp = None
        self._sources_key: tuple = ()
        self._shown: set[str] = set()
        self._tick_lock = threading.Lock()
        self._tick_handle: Optional[Handle] = None
        self._tick_for: Optional[float] = None
        self._test_mode = os.environ.get(TEST_MODE_VAR) == "1"
        self._windowed = sys.stderr is None

    def run(self) -> int:
        paths.ensure_layout()
        _setup_logging()
        paths.secure_home()
        self.settings.load()
        if self._another_instance_running():
            return 1
        self._token = token_store.ensure()
        log.info("starting assistant %s, home %s", __version__, paths.home())

        self.store = CardStore()
        self.scheduler.start()
        self.settings.watch(self.scheduler)
        self.reminders = ReminderStore(self.scheduler, fire=self._fire_reminder)
        self.reminders.start()
        self.store.subscribe(self._on_store_event_any_thread)
        self.meetings = MeetingScheduler(self._calendar_sources(), self.store, self.scheduler, self.settings)
        self.settings.on_change("calendars", self._on_calendars_changed)
        self.settings.on_change("mute_sources", self._on_mute_sources_changed)
        self.meetings.start()

        _, mcp_asgi = build_mcp(self.store, self.reminders)
        web_app = create_app(self._current_token, self.store, self.reminders, self.scheduler, mcp_app=mcp_asgi)
        web_app.router.routes.insert(0, Route("/v1/health", self._health_route, methods=["GET"]))
        web_app.router.routes.append(Route(MCP_PATH, _ExactMount(mcp_asgi, MCP_PATH)))
        web_app.router.routes.append(Route("/v1/shutdown", self._shutdown_route, methods=["POST"]))
        if self._test_mode:
            log.warning("%s=1: serving /v1/_debug/ui", TEST_MODE_VAR)
            web_app.router.routes.append(Route("/v1/_debug/ui", self._debug_route, methods=["GET"]))
        self.server = ServerThread(web_app, self.settings.port)
        if not self.server.start():
            log.error("could not start the HTTP server on port %d", self.settings.port)
            self.meetings.stop()
            self.scheduler.stop()
            self._report_bind_failure()
            return 1
        self._write_pid()

        try:
            self._build_ui()
        except Exception:
            log.exception("could not build the overlay")
            self.shutdown()
            return 1
        print(f"assistant {__version__} listening on http://127.0.0.1:{self.settings.port}", flush=True)
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            log.info("interrupted")
        finally:
            self.shutdown()
        return 0

    def _notify_user(self, text: str) -> None:
        if sys.stderr is not None:
            print(text, file=sys.stderr, flush=True)
        if self._windowed:
            netinfo.message_box("Assistant", text)

    def _another_instance_running(self) -> bool:
        path = pid_file()
        try:
            fields = path.read_text(encoding="ascii").split()
            pid = int(fields[0])
            port = int(fields[1])
        except (OSError, ValueError, IndexError):
            return False
        if pid == os.getpid():
            return False
        image = netinfo.process_image(pid)
        alive = image is not None and os.path.basename(image).lower().startswith("python")
        if not alive or netinfo.listener_pid(port) != pid:
            log.warning("removing stale pid file %s (pid %d is not the Assistant listening on port %d)",
                        path, pid, port)
            try:
                path.unlink()
            except OSError:
                pass
            return False
        message = f"Assistant is already running (pid {pid}; pid file {path}). This second copy will exit."
        log.error(message)
        self._notify_user(message)
        return True

    def _report_bind_failure(self) -> None:
        port = self.settings.port
        owner = netinfo.listener_pid(port)
        who = ""
        if owner is not None:
            image = netinfo.process_image(owner)
            who = f" It is held by process {owner}" + (f" ({os.path.basename(image)})." if image else ".")
        message = (f"Assistant could not start: 127.0.0.1:{port} is not available.{who} "
                   f"Close that program or change the port in settings.json.")
        log.error(message)
        self._notify_user(message)

    def _build_ui(self) -> None:
        from .overlay import cards_ui as cards_mod, icon as icon_mod

        self.root = icon_mod.create_root()
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        uibus.start(self.root)
        actions = icon_mod.IconActions(
            search=lambda: log.info("search not yet available"),
            remind_submit=self._remind_submit,
            mute=self._mute,
            unmute=lambda: self.store.set_mute(None),
            settings=self._open_settings,
            quit=self.shutdown,
        )
        self.icon = icon_mod.Icon(self.root, actions)
        self.cards_ui = cards_mod.CardsUI(
            self.root, self.icon, cards_mod.callbacks_for_store(self.store, self._open_url), self.settings,
            on_close_request=self.shutdown)
        self._sync_indicators()
        self._sync_visible()
        self._arm_tick()

    def _current_token(self) -> str:
        try:
            st = paths.token_file().stat()
            stamp = (st.st_mtime_ns, st.st_size)
            if stamp != self._token_stamp:
                self._token = token_store.read()
                self._token_stamp = stamp
        except (OSError, ValueError):
            pass
        return self._token

    async def _health_route(self, request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__,
                             "uptime_s": int(time.monotonic() - self._started_at),
                             "index": None, "plugins": [],
                             "calendars": self.meetings.status()["calendars"] if self.meetings else []})

    def _calendar_sources(self) -> list:
        cal = self.settings.get("calendars") or {}
        sources: list = []
        for url in cal.get("ics_urls", []):
            try:
                sources.append(IcsSource.from_url(url))
            except ValueError:
                log.warning("ignoring a calendar address that is not an http(s) URL")
        if cal.get("outlook", False):
            outlook = OutlookSource()
            if outlook.available():
                sources.append(outlook)
        self._sources_key = (tuple(cal.get("ics_urls", [])), bool(cal.get("outlook", False)))
        return sources

    def _on_calendars_changed(self, key: str, value) -> None:
        old = self._sources_key
        sources = self._calendar_sources()
        if self._sources_key != old and self.meetings is not None:
            self.meetings.set_sources(sources)

    def _source_muted(self, source: Optional[str]) -> bool:
        muted = self.settings.get("mute_sources") or {}
        return bool(source) and muted.get(source) is True

    def _on_mute_sources_changed(self, key: str, value) -> None:
        if self.cards_ui is not None and not self.stop.is_set():
            self._sync_visible()
            self._sync_indicators()

    def _open_settings(self) -> None:
        from .overlay import settings_ui
        win = self.settings_window
        if win is not None and win.open:
            try:
                win.win.deiconify()
                win.win.lift()
                win.win.focus_force()
                return
            except Exception:
                self.settings_window = None
        try:
            self.settings_window = settings_ui.SettingsWindow(
                self.root, self.settings, self._current_token, self.server.port, scale=self.icon.scale)
        except Exception:
            log.exception("could not open the settings window")
            self.settings_window = None

    def _open_url(self, url: str) -> None:
        if not url.lower().startswith(("http://", "https://")):
            log.warning("not opening a card url that is not http(s)")
            return
        import webbrowser
        webbrowser.open(url)

    def _mute(self, until: Optional[float]) -> None:
        self.store.set_mute(math.inf if until is None else until)

    def _remind_submit(self, parsed, sticky: bool) -> None:
        self.reminders.add(parsed.title, parsed.at, getattr(parsed, "detail", "") or "", bool(sticky))

    def _fire_reminder(self, reminder: Reminder) -> None:
        card = Card(id="reminder-" + reminder.id, kind="reminder", title=reminder.title,
                    detail=reminder.detail or None, sticky=reminder.sticky)
        uibus.post(self.store.upsert, card, "reminders")

    def _on_store_event_any_thread(self, event: str, card: Optional[Card]) -> None:
        self._arm_tick()
        uibus.post(self._on_store_event, event, card)

    def _on_store_event(self, event: str, card: Optional[Card]) -> None:
        if self.stop.is_set() or self.cards_ui is None:
            return
        if event in ("muted", "unmuted"):
            self._sync_visible()
        elif card is not None:
            if event in ("added", "updated", "snooze_ended"):
                if (not self.store.is_muted() and not self._source_muted(card.source)
                        and card.dismissed_at is None and card.snoozed_until is None):
                    self._shown.add(card.id)
                    self.cards_ui.on_store_event(event, card)
            else:
                if event in ("snoozed", "dismissed", "expired"):
                    self._shown.discard(card.id)
                if card.id in self._shown or event in ("snoozed", "dismissed", "expired"):
                    self.cards_ui.on_store_event(event, card)
        self._sync_indicators()

    def _sync_indicators(self) -> None:
        self.icon.set_count(self.store.open_count())
        self.icon.set_muted(self.store.is_muted(), self.store.mute_until())

    def _sync_visible(self) -> None:
        wanted = {c.id: c for c in self.store.visible() if not self._source_muted(c.source)}
        for card_id in list(self._shown - wanted.keys()):
            self._shown.discard(card_id)
            self.cards_ui.remove(card_id)
        for card_id, card in wanted.items():
            if card_id not in self._shown:
                self._shown.add(card_id)
                self.cards_ui.show(card)

    def _arm_tick(self) -> None:
        if self.stop.is_set():
            return
        deadline = self.store.next_deadline()
        with self._tick_lock:
            if deadline == self._tick_for and (deadline is None or self._tick_handle is not None):
                return
            if self._tick_handle is not None:
                self._tick_handle.cancel()
                self._tick_handle = None
            self._tick_for = deadline
            if deadline is not None:
                self._tick_handle = self.scheduler.at(max(deadline, time.time()) + TICK_SLACK_S, self._run_tick)

    def _run_tick(self) -> None:
        with self._tick_lock:
            self._tick_handle = None
            self._tick_for = None
        self.store.tick()
        self._arm_tick()

    async def _shutdown_route(self, request: Request) -> JSONResponse:
        log.info("shutdown requested over HTTP")
        return JSONResponse({"status": "shutting down"}, status_code=202,
                            background=BackgroundTask(uibus.post, self.shutdown))

    def _debug_snapshot(self) -> dict:
        snapshot = self.icon.debug_state()
        snapshot["cards"] = self.cards_ui.debug_state()
        snapshot["strips"] = self.cards_ui.strip_state()
        return snapshot

    async def _debug_route(self, request: Request) -> JSONResponse:
        try:
            data = await asyncio.wait_for(asyncio.wrap_future(uibus.submit(self._debug_snapshot)),
                                          DEBUG_TIMEOUT_S)
        except asyncio.TimeoutError:
            return JSONResponse({"error": "ui thread busy"}, status_code=503)
        return JSONResponse(data)

    def _write_pid(self) -> None:
        try:
            pid_file().write_text(f"{os.getpid()}\n{self.settings.port}\n", encoding="ascii")
        except OSError:
            log.exception("could not write the pid file")

    def _remove_pid(self) -> None:
        path = pid_file()
        try:
            first = path.read_text(encoding="ascii").split()[0]
            if first == str(os.getpid()):
                path.unlink()
        except (OSError, IndexError):
            pass

    def shutdown(self) -> None:
        if self.stop.is_set():
            return
        self.stop.set()
        log.info("shutting down")
        uibus.stop()
        if self.meetings is not None:
            self.meetings.stop()
        self.scheduler.stop()
        if self.server is not None:
            self.server.stop()
        self._remove_pid()
        if self.cards_ui is not None:
            self.cards_ui.close()
        if self.icon is not None:
            self.icon.destroy()
        if self.root is not None:
            try:
                self.root.destroy()
            except Exception:
                pass


def main() -> int:
    return App().run()
