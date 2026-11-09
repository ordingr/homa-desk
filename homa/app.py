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

from . import __version__, hooks_installer, migrate, netinfo, paths, uibus, update
from .calendar.ics import IcsSource
from .calendar.meetings import MeetingScheduler
from .calendar.outlook import OutlookSource
from .cards.model import MAX_DETAIL, MAX_TITLE, Card, strip_control
from .cards.store import CardStore
from .index.manager import IndexManager
from .reminders.store import Reminder, ReminderStore
from .scheduler import Handle, Scheduler
from .search import modes as search_modes
from .search.notes_bm25 import AskNotesMode
from .search.saved import SavedSearches
from .security import token as token_store
from .actions import shell
from .server import auth, routes_actions, routes_settings
from .server.app import ServerThread, create_app
from .server.mcp import build_mcp
from .server.routes_files import register_files_routes
from .server.routes_search import known_exts
from .server.static import register_static
from .overlay import fullpage, monitors, sounds, theme
from .settings import Settings

log = logging.getLogger("homa.app")

TEST_MODE_VAR = "HOMA_TEST_MODE"
DEBUG_TIMEOUT_S = 3.0
TICK_SLACK_S = 0.05
MCP_PATH = "/mcp"
SHUTDOWN_STEPS_S = 3.0
HARD_EXIT_S = 10.0



class _ProactorResetFilter(logging.Filter):
    """Drops the proactor transport's ConnectionResetError traceback from _call_connection_lost."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if not isinstance(exc, ConnectionResetError):
            return True
        context = getattr(record, "message", None) or record.getMessage()
        return "_call_connection_lost" not in context and "_loop_reading" not in context


class _ShutdownCancelFilter(logging.Filter):
    """Drops the traceback of a request that uvicorn cancelled because it outlived the graceful-shutdown timeout."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not isinstance(exc, asyncio.CancelledError)


def _setup_logging() -> None:
    handler = logging.handlers.RotatingFileHandler(
        paths.logs_dir() / "app.log", maxBytes=2 * 1024 * 1024, backupCount=4, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(console)
    logging.getLogger("asyncio").addFilter(_ProactorResetFilter())
    logging.getLogger("uvicorn.error").addFilter(_ShutdownCancelFilter())


class _ExactMount:
    """Serves the mounted app for the path without its trailing slash instead of a 307 redirect."""

    def __init__(self, app, prefix: str) -> None:
        self._app = app
        self._prefix = prefix

    async def __call__(self, scope, receive, send):
        inner = dict(scope, path="/", raw_path=b"/", root_path=scope.get("root_path", "") + self._prefix)
        await self._app(inner, receive, send)


def _clean(text: str, limit: int) -> str:
    return strip_control(text)[:limit]


def pid_file():
    return paths.home() / "homa.pid"


def index_store_path():
    return paths.home() / "index" / "files.idx"


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
        self.index: Optional[IndexManager] = None
        self.notes_mode: Optional[AskNotesMode] = None
        self.saved: Optional[SavedSearches] = None
        self.search_modes = None
        self.popup = None
        self.hotkey = None
        self.display_watch = None
        self.settings_window = None
        self._started_at = time.monotonic()
        self._token = ""
        self._token_stamp = None
        self._sources_key: tuple = ()
        self._shown: set[str] = set()
        self._tick_lock = threading.Lock()
        self._tick_handle: Optional[Handle] = None
        self._tick_for: Optional[float] = None
        self._test_mode = paths.env("TEST_MODE") == "1"
        self._windowed = sys.stderr is None
        self.hard_exit = False

    def run(self) -> int:
        paths.ensure_layout()
        _setup_logging()
        paths.secure_home()
        self.settings.load()
        if self._another_instance_running():
            return 1
        self._token = token_store.ensure()
        log.info("starting homa %s, home %s", __version__, paths.home())

        self.store = CardStore()
        self.scheduler.start()
        self.settings.watch(self.scheduler)
        self.reminders = ReminderStore(self.scheduler, fire=self._fire_reminder)
        self.reminders.start()
        self.store.subscribe(self._on_store_event_any_thread)
        self.meetings = MeetingScheduler(self._calendar_sources(), self.store, self.scheduler, self.settings)
        self.settings.on_change("calendars", self._on_calendars_changed)
        self.settings.on_change("mute_sources", self._on_mute_sources_changed)
        sounds.set_enabled(self.settings.get("sound_on", True) is not False)
        self.settings.on_change("sound_on", lambda _key, value: sounds.set_enabled(value is not False))
        self.meetings.start()

        self.index = IndexManager(self.settings, self.scheduler, index_store_path())
        self.saved = SavedSearches()
        self.notes_mode = AskNotesMode(self.settings, self.scheduler)
        self.search_modes = search_modes.default_registry(self._files_source, self.notes_mode, self.settings)
        self.settings.on_change("notes", lambda _key, _value: self._start_notes_index())

        _, mcp_asgi = build_mcp(self.store, self.reminders, index=self.index)
        web_app = create_app(self._current_token, self.store, self.reminders, self.scheduler, mcp_app=mcp_asgi,
                             index=self.index, settings=self.settings, saved=self.saved)
        register_files_routes(web_app, self.index, self.settings, closer=fullpage.close,
                              opener=self._open_fullpage)
        register_static(web_app)
        web_app.router.routes.extend(routes_actions.routes)
        web_app.router.routes.extend(routes_settings.routes)
        web_app.router.routes.insert(0, Route("/v1/health", self._health_route, methods=["GET"]))
        web_app.router.routes.append(Route(MCP_PATH, _ExactMount(mcp_asgi, MCP_PATH)))
        web_app.router.routes.append(Route("/v1/shutdown", self._shutdown_route, methods=["POST"]))
        if self._test_mode:
            log.warning("%s=1: serving /v1/_debug/ui", TEST_MODE_VAR)
            web_app.router.routes.append(Route("/v1/_debug/ui", self._debug_route, methods=["GET"]))
            web_app.router.routes.append(Route("/v1/_debug/monitors", self._debug_monitors_route,
                                               methods=["POST"]))
        self.server = ServerThread(web_app, self.settings.port)
        self._write_pid()
        if not self.server.start():
            log.error("could not start the HTTP server on port %d", self.settings.port)
            self._remove_pid()
            self.meetings.stop()
            self.notes_mode.close()
            self.scheduler.stop()
            self._report_bind_failure()
            return 1

        try:
            self._build_ui()
        except Exception:
            log.exception("could not build the overlay")
            self.shutdown()
            return 1
        threading.Thread(target=self._start_index, name="index-start", daemon=True).start()
        print(f"homa {__version__} listening on http://127.0.0.1:{self.settings.port}", flush=True)
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
            netinfo.message_box("Homa", text)

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
            log.warning("removing stale pid file %s (pid %d is not Homa listening on port %d)",
                        path, pid, port)
            try:
                path.unlink()
            except OSError:
                pass
            return False
        message = f"Homa is already running (pid {pid}; pid file {path}). This second copy will exit."
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
        message = (f"Homa could not start: 127.0.0.1:{port} is not available.{who} "
                   f"Close that program or change the port in settings.json.")
        log.error(message)
        self._notify_user(message)

    def _build_ui(self) -> None:
        from .overlay import cards_ui as cards_mod, icon as icon_mod

        self.root = icon_mod.create_root()
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        uibus.start(self.root)
        theme.install(self.settings, self.scheduler, self.root)
        actions = icon_mod.IconActions(
            search=self._open_search,
            remind_submit=self._remind_submit,
            mute=self._mute,
            unmute=lambda: self.store.set_mute(None),
            settings=self._open_settings,
            set_sound=self._set_sound,
            quit=self.shutdown,
        )
        self.icon = icon_mod.Icon(self.root, actions)
        self.cards_ui = cards_mod.CardsUI(
            self.root, self.icon, cards_mod.callbacks_for_store(self.store, self._open_url), self.settings,
            on_close_request=self.shutdown)
        self.display_watch = monitors.DisplayWatcher(self.icon, self.root)
        self.display_watch.start()
        self._sync_indicators()
        self._sync_visible()
        self._arm_tick()
        self._build_search()
        self._show_update_notice()
        self._show_migration_cards()
        log.info("overlay ready")

    def _show_update_notice(self) -> None:
        notice = update.consume_notice()
        if notice is not None:
            self.store.upsert(Card(id="updated", kind="custom", title=_clean(notice["title"], MAX_TITLE),
                                   detail=_clean(notice["detail"], MAX_DETAIL)), "app")

    def _show_migration_cards(self) -> None:
        notice = migrate.consume_notice()
        if notice is not None:
            self.store.upsert(Card(id="migrated", kind="custom", title=_clean(notice["title"], MAX_TITLE),
                                   detail=_clean(notice["detail"], MAX_DETAIL), sticky=True), "app")
        port = self.settings.port
        try:
            if hooks_installer.mismatch(port, self._token) is None:
                return
            ports = hooks_installer.installed_ports()
        except Exception:
            log.warning("could not check the Claude Code hooks", exc_info=True)
            return
        where = ", ".join(str(p) for p in sorted(ports))
        title = (f"Claude Code hooks point at port {where}; Homa listens on {port}." if ports != {port}
                 else "Claude Code hooks carry a token that is not Homa's.")
        self.store.upsert(Card(id="hooks-mismatch", kind="custom", sticky=True, title=title,
                               detail="Open Settings > Claude Code and re-install them."), "app")

    def _files_source(self):
        store, delta = self.index.current()
        if store is None:
            return None
        return store, delta, known_exts(self.settings)

    def _start_index(self) -> None:
        started = time.monotonic()
        try:
            self.index.start()
            log.info("file index loaded: %d entries in %.2fs", self.index.status()["entries"],
                     time.monotonic() - started)
        except Exception:
            log.exception("could not start the file index")
        self._start_notes_index()

    def _start_notes_index(self) -> None:
        if self.stop.is_set():
            return
        try:
            self.notes_mode.index()
        except Exception:
            log.exception("could not start the notes index")

    def _build_search(self) -> None:
        try:
            from .overlay import hotkey as hotkey_mod, search_ui
            self.popup = search_ui.SearchPopup(self.root, self.icon, self.search_modes, self.settings,
                                               saved=self.saved, on_fullpage=self._on_fullpage,
                                               on_action=self._on_action, on_clipboard=self._on_clipboard,
                                               fullpage_is_open=self._fullpage_open,
                                               fullpage_close=fullpage.close)
            self.hotkey = hotkey_mod.Hotkey(
                str(self.settings.get("hotkey", "") or ""),
                lambda: uibus.post(self.popup.toggle, self.hotkey.last_press),
                skip_when=lambda: (self.settings.get("hotkey_skip_fullscreen", False) is True
                                   and hotkey_mod.foreground_is_fullscreen()))
            self.settings.on_change("hotkey", self._on_hotkey_changed)
            status = self.hotkey.start()
        except Exception:
            log.exception("could not build the search popup or hotkey")
            return
        if status != "registered":
            log.warning("hotkey %r: %s; the icon still opens search", self.hotkey.combo, status)
        else:
            log.info("hotkey %r registered", self.hotkey.combo)

    def _on_fullpage(self, query: str, handoff: Optional[dict] = None) -> None:
        threading.Thread(target=self._open_fullpage, args=(query, handoff), name="fullpage-open",
                         daemon=True).start()

    def _open_fullpage(self, query: str, handoff: Optional[dict] = None) -> bool:
        try:
            if handoff is not None:
                handoff = auth.check_handoff(handoff)
            opened = fullpage.open(query, self.server.port, lambda: auth.mint_nonce(handoff))
        except Exception:
            log.exception("could not open the full page")
            return False
        if not opened:
            reason = fullpage.last_failure() or "The full page did not open."
            log.warning("the full page did not open: %s", reason)
            if self.popup is not None:
                uibus.post(self.popup.show_note, reason)
        elif self.popup is not None:
            # The full page takes over the search; one search window is enough.
            uibus.post(self.popup.close)
        return opened

    def _on_action(self, verb: str, row, confirm: bool = False, new_name: Optional[str] = None,
                   whole_file: bool = False) -> dict:
        return shell.dispatch(verb, row.path, new_name=new_name, confirm=confirm, index=self.index,
                              settings=self.settings, whole_file=whole_file)

    def _on_clipboard(self, paths: list, op: str) -> dict:
        return shell.clipboard_files(paths, op, index=self.index)

    def _fullpage_open(self) -> bool:
        return fullpage.is_open()

    def _on_hotkey_changed(self, key: str, value) -> None:
        if self.hotkey is None or self.stop.is_set():
            return
        status = self.hotkey.set_combo(str(value or ""))
        log.info("hotkey remapped to %r: %s", self.hotkey.combo, status)

    def _open_search(self) -> None:
        if self.popup is None:
            log.info("search is not available")
            return
        self.popup.toggle(time.perf_counter())

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
                             "index": self._index_health(), "plugins": [],
                             "calendars": self.meetings.status()["calendars"] if self.meetings else []})

    def _index_health(self) -> Optional[dict]:
        if self.index is None:
            return None
        status = self.index.status()
        status["rescanning"] = bool(status.get("walking"))
        return status

    def _calendar_sources(self) -> list:
        cal = self.settings.get("calendars") or {}
        sources: list = []
        labels = cal.get("labels", {})
        for url in cal.get("ics_urls", []):
            try:
                sources.append(IcsSource.from_url(url, strip_control(labels.get(url, "")).strip() or None))
            except ValueError:
                log.warning("ignoring a calendar address that is not an http(s) URL")
        if cal.get("outlook", False):
            outlook = OutlookSource()
            if outlook.available():
                sources.append(outlook)
        self._sources_key = (tuple((u, labels.get(u, "")) for u in cal.get("ics_urls", [])),
                             bool(cal.get("outlook", False)))
        return sources

    def _calendar_status(self) -> list:
        return self.meetings.status()["calendars"] if self.meetings else []

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
                self.root, self.settings, self._current_token, self.server.port, scale=self.icon.scale,
                calendar_status=self._calendar_status)
            from .overlay import settings_pages_search
            settings_pages_search.install(
                self.settings_window, index_status=self.index.status, request_rescan=self.index.request_rescan,
                hotkey_info=self._hotkey_info, notes_info=self._notes_info)
        except Exception:
            log.exception("could not open the settings window")
            self.settings_window = None

    def _hotkey_info(self) -> dict:
        hk = self.hotkey
        if hk is None:
            return {"status": "unavailable", "combo": "", "presses": 0}
        return {"status": hk.status(), "combo": hk.combo, "presses": hk.presses}

    def _notes_info(self) -> dict:
        mode = self.notes_mode
        idx = mode._index
        return {"dir": mode.notes_dir(), "skipped_placeholder": mode.skipped_placeholder,
                "status": idx.status_text if idx is not None else ""}

    def _set_sound(self, enabled: bool) -> None:
        try:
            self.settings.set("sound_on", bool(enabled))
        except (ValueError, OSError):
            log.exception("could not save the sound setting")

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
        if self.popup is not None:
            snapshot["search"] = self.popup.debug_state()
        snapshot["actions"] = shell.action_log()[-50:]
        snapshot["fullpage"] = {"open": self._fullpage_open()}
        snapshot["theme"] = theme.debug_state()
        if self.display_watch is not None:
            snapshot["monitors"] = self.display_watch.debug_state()
        if self.hotkey is not None:
            snapshot["hotkey"] = self.hotkey.status()
            snapshot["hotkey_combo"] = self.hotkey.combo
            snapshot["hotkey_thread_id"] = self.hotkey.thread_id
            snapshot["hotkey_presses"] = self.hotkey.presses
        return snapshot

    async def _debug_route(self, request: Request) -> JSONResponse:
        try:
            data = await asyncio.wait_for(asyncio.wrap_future(uibus.submit(self._debug_snapshot)),
                                          DEBUG_TIMEOUT_S)
        except asyncio.TimeoutError:
            return JSONResponse({"error": "ui thread busy"}, status_code=503)
        return JSONResponse(data)

    async def _debug_monitors_route(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            if body.get("restore") is True:
                await asyncio.wrap_future(uibus.submit(monitors.simulate_restore))
            else:
                await asyncio.wrap_future(uibus.submit(lambda: monitors.simulate_remove(int(body["idx"]))))
        except (ValueError, KeyError, IndexError, RuntimeError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse({"ok": True})

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

    def _run_steps(self, steps: list) -> None:
        """Runs the teardown steps side by side; one that is stuck is logged and left behind after the shared
        deadline instead of holding up the server stop and the pid file release."""
        def run(name, fn) -> None:
            try:
                fn()
            except Exception:
                log.exception("shutdown step %s failed", name)

        started = [(name, threading.Thread(target=run, args=(name, fn), name=f"shutdown-{name}", daemon=True))
                   for name, fn in steps]
        for _, th in started:
            th.start()
        deadline = time.monotonic() + SHUTDOWN_STEPS_S
        for name, th in started:
            th.join(max(0.0, deadline - time.monotonic()))
            if th.is_alive():
                log.warning("shutdown step %s still running after %.0f s; moving on", name, SHUTDOWN_STEPS_S)

    def _hard_exit(self) -> None:
        time.sleep(HARD_EXIT_S)
        log.error("shutdown did not finish within %.0f s; exiting", HARD_EXIT_S)
        self._remove_pid()
        logging.shutdown()
        os._exit(1)

    def shutdown(self) -> None:
        if self.stop.is_set():
            return
        self.stop.set()
        log.info("shutting down")
        if self.hard_exit:
            threading.Thread(target=self._hard_exit, name="hard-exit", daemon=True).start()
        uibus.stop()
        if self.server is not None:
            self.server.request_stop()
        steps = [("calendar", lambda: self.meetings is not None and self.meetings.stop()),
                 ("hotkey", lambda: self.hotkey is not None and self.hotkey.stop()),
                 ("display watch", lambda: self.display_watch is not None and self.display_watch.stop()),
                 ("full page", fullpage.close),
                 ("index", lambda: self.index is not None and self.index.stop()),
                 ("notes", lambda: self.notes_mode is not None and self.notes_mode.close()),
                 ("scheduler", self.scheduler.stop)]
        self._run_steps(steps)
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
    app = App()
    app.hard_exit = True
    code = app.run()
    lingering = [t.name for t in threading.enumerate() if t is not threading.main_thread() and not t.daemon]
    if lingering:
        log.warning("exiting with non-daemon threads still running: %s", ", ".join(lingering))
        logging.shutdown()
        os._exit(code)
    return code
