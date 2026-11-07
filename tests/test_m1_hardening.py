"""Regression tests for the M1-9 hardening (Review 3: B1, F1-F4, N2, N4, uibus future race).

Process-level tests start the real ``python -m assistant`` / ``send`` / ``shutdown`` against a scratch
ASSISTANT_HOME; API-level tests drive the real ``create_app()`` as an ASGI callable (httpx is not installed).
Proxy variables are set only in the environment of the subprocess a test launches.
"""
import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from assistant import netinfo, paths, send, uibus
from assistant.cards import model
from assistant.cards.store import MAX_OPEN_CARDS, CardStore
from assistant.reminders.store import ReminderStore
from assistant.scheduler import Scheduler
from assistant.server import app as server_app

from conftest import REPO_ROOT, STARTUP_TIMEOUT, _free_port, wait_until

TOKEN = "T" * 43
DAY = 86400.0
_PROXY_VARS = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
               "NO_PROXY", "no_proxy")


# ----- helpers -------------------------------------------------------------------------------------

class Sink:
    """Loopback HTTP server that records every request; ``redirect_to`` makes it answer 307."""

    def __init__(self, redirect_to: str | None = None):
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.requests.append({"method": self.command, "path": self.path,
                                       "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                if redirect_to:
                    self.send_response(307)
                    self.send_header("Location", redirect_to)
                    self.send_header("Content-Length", "0")
                else:
                    payload = b'{"id": "sink", "status": "shutting down"}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.end_headers()

            do_GET = do_POST = do_PUT = do_DELETE = _handle

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def sink():
    s = Sink()
    yield s
    s.close()


def _env(home, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _PROXY_VARS}
    env["ASSISTANT_HOME"] = str(home)
    env.update(extra)
    return env


def _prepare_home(tmp_path, port: int):
    home = tmp_path / "home"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"schema": 1, "port": port}), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "assistant", "--init"], cwd=REPO_ROOT, env=_env(home),
                   capture_output=True, check=True, timeout=60)
    return home


def _cli(home, *args, **env_extra):
    return subprocess.run([sys.executable, "-m", "assistant", *args], cwd=REPO_ROOT, env=_env(home, **env_extra),
                          capture_output=True, text=True, timeout=60)


def _health(port: int, token: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/health",
                                     headers={"Authorization": f"Bearer {token}"})
    try:
        with opener.open(request, timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


class LiveApp:
    def __init__(self, home, port, **env_extra):
        self.home, self.port = home, port
        self.token = (home / "token").read_text(encoding="ascii").strip()
        self.proc = subprocess.Popen([sys.executable, "-m", "assistant"], cwd=REPO_ROOT,
                                     env=_env(home, **env_extra), stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"assistant exited early ({self.proc.returncode})")
            if _health(port, self.token):
                return
            time.sleep(0.2)
        self.stop()
        raise RuntimeError("assistant did not answer /v1/health in time")

    def get(self, path: str):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                         headers={"Authorization": f"Bearer {self.token}"})
        try:
            with opener.open(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except OSError:
            return None

    def stop(self):
        if self.proc.poll() is not None:
            return
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/shutdown", data=b"{}", method="POST",
                                         headers={"Authorization": f"Bearer {self.token}"})
        try:
            opener.open(request, timeout=5).close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)
            self.proc.wait(timeout=10)


@pytest.fixture
def live(tmp_path):
    port = _free_port()
    home = _prepare_home(tmp_path, port)
    app = LiveApp(home, port)
    yield app
    app.stop()


def _asgi(app, method, path, body=None, *, raw=None):
    """One request through the ASGI app; returns (status, parsed json or None)."""
    payload = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else b"")
    headers = [(b"host", b"127.0.0.1"), (b"authorization", b"Bearer " + TOKEN.encode()),
               (b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    queue = [payload]
    sent = []

    async def receive():
        if queue:
            return {"type": "http.request", "body": queue.pop(0), "more_body": False}
        return {"type": "http.disconnect"}

    async def send_(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": headers, "http_version": "1.1", "scheme": "http", "server": ("127.0.0.1", 1),
             "client": ("127.0.0.1", 2)}
    asyncio.run(app(scope, receive, send_))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, (json.loads(data) if data else None)


@pytest.fixture
def cards_app(tmp_path):
    store = CardStore(tmp_path / "state.json")
    return store, server_app.create_app(lambda: TOKEN, store)


def _post_card(app, **fields):
    body = {"title": "t", "kind": "custom"}
    body.update(fields)
    return _asgi(app, "POST", "/v1/cards", body)


def _field(response) -> str:
    return response[1]["error"].get("field", "")


# ----- B1: no proxy, no redirect -------------------------------------------------------------------

def test_send_with_proxy_env_and_nothing_listening_reaches_no_proxy(tmp_path, sink):
    home = _prepare_home(tmp_path, _free_port())
    result = _cli(home, "send", "--title", "x", HTTP_PROXY=sink.url, http_proxy=sink.url)
    assert result.returncode == 1
    assert "Assistant is not running" in result.stderr
    assert sink.requests == []


def test_send_and_shutdown_with_proxy_env_do_not_touch_the_proxy_when_app_runs(live, sink):
    env = {"HTTP_PROXY": sink.url, "http_proxy": sink.url}
    sent = _cli(live.home, "send", "--title", "x", **env)
    assert sent.returncode == 0, sent.stderr
    assert live.get("/v1/cards") == 200
    stopped = _cli(live.home, "shutdown", **env)
    assert stopped.returncode == 0, stopped.stderr
    assert "shutdown requested" in stopped.stdout
    assert live.proc.wait(timeout=20) == 0
    assert sink.requests == []


def test_shutdown_with_proxy_env_and_nothing_listening_reaches_no_proxy(tmp_path, sink):
    home = _prepare_home(tmp_path, _free_port())
    result = _cli(home, "shutdown", HTTP_PROXY=sink.url, http_proxy=sink.url)
    assert result.returncode == 0
    assert "not running" in result.stdout
    assert sink.requests == []


def test_redirect_is_not_followed_so_authorization_never_reaches_the_target(tmp_path):
    target = Sink()
    stub = Sink(redirect_to=target.url + "/stolen")
    try:
        home = _prepare_home(tmp_path, stub.port)
        result = _cli(home, "send", "--title", "x")
        assert result.returncode == 1
        assert "307" in result.stderr
        assert len(stub.requests) == 1 and "authorization" in stub.requests[0]["headers"]
        assert target.requests == []
    finally:
        stub.close()
        target.close()


def test_send_module_opener_ignores_env_proxies_and_never_redirects(monkeypatch):
    from urllib.request import HTTPRedirectHandler
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    # a proxy-aware opener registers a proxy_open hook on the http scheme's request path; ours must not
    assert "http" not in send._opener.handle_open or all(
        type(h).__name__ != "ProxyHandler" for h in send._opener.handle_open["http"])
    redirectors = [h for hs in send._opener.handle_error.get("http", {}).values() for h in hs
                   if isinstance(h, HTTPRedirectHandler)]
    assert redirectors and all(h.redirect_request(None, None, 307, "", {}, "http://x/") is None
                               for h in redirectors)


# ----- F3: listener identity, second instance, bind failure ---------------------------------------

def test_listener_pid_reports_the_owner_of_a_listening_socket():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        assert netinfo.listener_pid(sock.getsockname()[1]) == os.getpid()
    assert netinfo.listener_pid(_free_port()) is None


def test_process_image_names_python_and_returns_none_for_a_dead_pid():
    assert os.path.basename(netinfo.process_image(os.getpid())).lower().startswith("python")
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    assert netinfo.process_image(dead.pid) is None


@pytest.mark.parametrize("command", [("send", "--title", "x"), ("shutdown",)])
def test_cli_refuses_to_send_the_token_to_a_foreign_listener(tmp_path, command):
    squatter = Sink()
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        home = _prepare_home(tmp_path, squatter.port)
        (home / "assistant.pid").write_text(f"{other.pid}\n{squatter.port}\n", encoding="ascii")
        result = _cli(home, *command)
        assert result.returncode == 1
        assert f"held by process {os.getpid()}" in result.stderr
        assert f"pid {other.pid}" in result.stderr
        assert "nothing was sent" in result.stderr
        assert squatter.requests == []
    finally:
        other.kill()
        other.wait()
        squatter.close()


def test_cli_sends_when_the_listener_pid_matches_the_pid_file(tmp_path):
    ours = Sink()
    try:
        home = _prepare_home(tmp_path, ours.port)
        (home / "assistant.pid").write_text(f"{os.getpid()}\n{ours.port}\n", encoding="ascii")
        result = _cli(home, "send", "--title", "x")
        # the listener is this pytest process, which the pid file names, so the request goes through
        assert result.returncode == 0, result.stderr
        assert len(ours.requests) == 1
    finally:
        ours.close()


def test_second_instance_exits_with_already_running_and_first_keeps_serving(live):
    second = subprocess.run([sys.executable, "-m", "assistant"], cwd=REPO_ROOT, env=_env(live.home),
                            capture_output=True, text=True, timeout=60)
    assert second.returncode == 1
    assert "already running" in second.stderr
    assert (live.home / "assistant.pid").read_text().split()[0] == str(live.proc.pid) or live.proc.poll() is None
    assert live.get("/v1/health") == 200
    log = (live.home / "logs" / "app.log").read_text(encoding="utf-8")
    assert "already running" in log


def test_bind_failure_exits_1_and_names_the_port_and_its_owner(tmp_path):
    with socket.socket() as squat:
        squat.bind(("127.0.0.1", 0))
        squat.listen(1)
        port = squat.getsockname()[1]
        home = _prepare_home(tmp_path, port)
        result = _cli(home, ASSISTANT_TEST_MODE="")
        assert result.returncode == 1
        assert f"127.0.0.1:{port} is not available" in result.stderr
        assert f"held by process {os.getpid()}" in result.stderr
        assert not (home / "assistant.pid").exists()


def test_message_box_is_used_only_when_there_is_no_stderr(monkeypatch, capsys):
    from assistant.app import App
    shown = []
    monkeypatch.setattr(netinfo, "message_box", lambda title, text: shown.append((title, text)))
    app = App()
    app._windowed = False
    app._notify_user("with console")
    assert shown == []
    assert "with console" in capsys.readouterr().err
    app._windowed = True
    monkeypatch.setattr(sys, "stderr", None)
    app._notify_user("no console")
    assert shown == [("Assistant", "no console")]


# ----- F1: scheduler and far-future inputs --------------------------------------------------------

def test_scheduler_survives_far_future_jobs_and_a_raising_job(caplog):
    sched = Scheduler()
    sched.start()
    fired = []
    try:
        with caplog.at_level(logging.ERROR, logger="assistant.scheduler"):
            now = time.time()
            sched.at(now + 300 * DAY, lambda: fired.append("far"))
            sched.at(1e308, lambda: fired.append("inf-ish"))
            sched.at(now + 2, lambda: fired.append("two"))
            sched.at(now + 3, lambda: 1 / 0)
            sched.at(now + 4, lambda: fired.append("four"))
            assert wait_until(lambda: "four" in fired, timeout=7.0)
        assert fired == ["two", "four"]
        assert sched._thread.is_alive()
        errors = [r for r in caplog.records if r.name == "assistant.scheduler" and r.levelno >= logging.ERROR]
        assert len(errors) == 1 and "ZeroDivisionError" in (errors[0].exc_text or "") + str(errors[0].exc_info)
    finally:
        sched.stop()


def test_scheduler_wait_clamp_is_below_the_threading_timeout_limit():
    from assistant import scheduler
    assert scheduler.MAX_WAIT_S == 3600.0 < threading.TIMEOUT_MAX


@pytest.fixture
def reminders_app(tmp_path):
    store = CardStore(tmp_path / "state.json")
    sched = Scheduler()
    sched.start()
    reminders = ReminderStore(sched, fire=lambda r: None, path=tmp_path / "reminders.json")
    reminders.start()
    yield server_app.create_app(lambda: TOKEN, store, reminders), sched
    sched.stop()


def test_reminder_more_than_a_year_out_is_rejected_with_422(reminders_app):
    app, sched = reminders_app
    assert _asgi(app, "POST", "/v1/reminders", {"text": "x in 400d"})[0] == 422
    assert _asgi(app, "POST", "/v1/reminders", {"title": "x", "at": time.time() + 400 * DAY})[0] == 422
    assert sched._thread.is_alive()


def test_reminder_300_days_out_is_accepted_and_the_scheduler_lives_on(reminders_app):
    app, sched = reminders_app
    status, body = _asgi(app, "POST", "/v1/reminders", {"title": "far", "at": time.time() + 300 * DAY})
    assert status == 201, body
    soon = threading.Event()
    sched.at(time.time() + 0.3, soon.set)
    assert soon.wait(3.0), "scheduler stopped firing after a far-future reminder"


def test_card_expires_at_more_than_a_year_out_is_422(cards_app):
    _, app = cards_app
    response = _post_card(app, expires_at=time.time() + 400 * DAY)
    assert response[0] == 422 and _field(response) == "expires_at"
    assert _post_card(app, expires_at=time.time() + 300 * DAY)[0] == 201


# ----- F2: snooze bounds, no non-finite JSON ------------------------------------------------------

@pytest.mark.parametrize("minutes,ok", [(0, False), (0.5, False), (1, True), (10080, True), (10081, False),
                                        (1e308, False), (True, False), ("5", False), (-3, False),
                                        (float("nan"), False)])
def test_snooze_boundaries(cards_app, minutes, ok):
    store, app = cards_app
    card_id = _post_card(app)[1]["id"]
    raw = json.dumps({"minutes": minutes}).encode()  # json emits NaN literally; the route must still refuse it
    status, body = _asgi(app, "POST", f"/v1/cards/{card_id}/snooze", raw=raw)
    assert (status == 200) == ok, (minutes, status, body)
    if not ok:
        assert status in (400, 422)
        assert store.get(card_id).snoozed_until is None
    # whatever happened, the list API still works and state.json holds no non-finite literal
    assert _asgi(app, "GET", "/v1/cards")[0] == 200
    text = store.path.read_text(encoding="utf-8")
    assert "Infinity" not in text and "NaN" not in text


def test_state_with_a_non_finite_value_is_never_written(tmp_path, caplog):
    store = CardStore(tmp_path / "state.json")
    card = model.Card.from_dict({"title": "t"})
    created = store.upsert(card, "test")
    before = (tmp_path / "state.json").read_text(encoding="utf-8")
    with store._locked():
        store._cards[created.id].snoozed_until = float("inf")
        store._save()
    assert (tmp_path / "state.json").read_text(encoding="utf-8") == before
    assert "Infinity" not in before


def test_persisted_infinite_snooze_is_clamped_on_load():
    card = model.Card.from_dict({"title": "t", "snoozed_until": 1e308}, trusted=True)
    assert card.snoozed_until <= time.time() + model.MAX_AHEAD_SECONDS + 5


# ----- F4 / N2: field caps, open-card cap, unknown-field log cap ---------------------------------

@pytest.mark.parametrize("fields,limit", [
    ("url", 2048), ("icon", 256), ("title", 120), ("detail", 500)])
def test_string_caps_accept_the_limit_and_reject_one_more(cards_app, fields, limit):
    _, app = cards_app
    value = "https://e.example/" + "a" * (limit - 18) if fields == "url" else "a" * limit
    assert len(value) == limit
    assert _post_card(app, **{fields: value})[0] == 201
    over = _post_card(app, **{fields: value + "b"})
    assert over[0] == 422 and _field(over) == fields


@pytest.mark.parametrize("part,limit", [("id", 64), ("label", 40), ("shortcut", 16)])
def test_action_caps_accept_the_limit_and_reject_one_more(cards_app, part, limit):
    _, app = cards_app

    def action(n):
        a = {"id": "a", "label": "l"}
        a[part] = "x" * n
        return [a]

    assert _post_card(app, actions=action(limit))[0] == 201
    over = _post_card(app, actions=action(limit + 1))
    assert over[0] == 422 and _field(over) == "actions"
    assert part in over[1]["error"]["message"]


def test_205_non_sticky_posts_leave_200_open_and_log_a_warning(cards_app, caplog):
    store, app = cards_app
    with caplog.at_level(logging.WARNING, logger="assistant.cards.store"):
        for i in range(MAX_OPEN_CARDS + 5):
            assert _post_card(app, title=f"c{i}", sticky=False)[0] == 201
    assert MAX_OPEN_CARDS == 200
    assert store.open_count() == 200
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "open-card limit" in r.getMessage()]
    assert len(warnings) == 5
    titles = {c.title for c in store.list()}
    assert "c0" not in titles and "c204" in titles


def test_200_sticky_cards_then_one_more_is_422_too_many_cards(cards_app):
    store, app = cards_app
    for i in range(MAX_OPEN_CARDS):
        assert _post_card(app, title=f"s{i}", sticky=True)[0] == 201
    status, body = _post_card(app, title="one more", sticky=True)
    assert status == 422 and body["error"]["code"] == "too_many_cards"
    assert store.open_count() == 200


def test_150_unknown_fields_log_at_most_100_names_plus_one_cap_line(monkeypatch, caplog):
    monkeypatch.setattr(model, "_logged_unknown", set())
    body = {"title": "t", **{f"zz{i}": 1 for i in range(150)}}
    with caplog.at_level(logging.WARNING, logger="assistant.cards.model"):
        model.Card.from_dict(body)
        model.Card.from_dict(body)
    messages = [r.getMessage() for r in caplog.records if r.name == "assistant.cards.model"]
    ignored = [m for m in messages if m.startswith("ignoring unknown card field")]
    caps = [m for m in messages if "further unknown" in m]
    assert len(ignored) == 100, len(ignored)
    assert len(caps) == 1


# ----- uibus future race --------------------------------------------------------------------------

def _empty_uibus():
    while uibus.drain():
        pass


def test_cancelled_future_is_skipped_without_error_or_traceback(caplog):
    _empty_uibus()
    ran = []
    future = uibus.submit(lambda: ran.append(1))
    assert future.cancel()
    with caplog.at_level(logging.DEBUG):
        assert uibus.drain() is True
    assert ran == []
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_future_cancelled_while_its_callable_runs_does_not_raise(caplog):
    _empty_uibus()
    holder = {}

    def fn():
        holder["future"].cancel()
        return "late"

    holder["future"] = uibus.submit(fn)
    # a PENDING future refuses cancel() only once running; here cancel() wins and result/exception are skipped
    with caplog.at_level(logging.DEBUG):
        uibus.drain()
    assert holder["future"].cancelled() or holder["future"].result() == "late"
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_debug_ui_requests_racing_shutdown_leave_no_traceback(tmp_path):
    port = _free_port()
    home = _prepare_home(tmp_path, port)
    app = LiveApp(home, port, ASSISTANT_TEST_MODE="1")
    assert app.get("/v1/_debug/ui") == 200
    statuses = []

    def hammer():
        statuses.append(app.get("/v1/_debug/ui"))

    threads = [threading.Thread(target=hammer) for _ in range(20)]
    stopper = threading.Thread(target=app.stop)
    for t in threads[:10]:
        t.start()
    stopper.start()
    for t in threads[10:]:
        t.start()
    for t in threads + [stopper]:
        t.join(timeout=60)
    assert app.proc.poll() == 0
    log = (home / "logs" / "app.log").read_text(encoding="utf-8")
    # asyncio's proactor loop logs a ConnectionResetError traceback whenever a client resets a socket
    # (a client-side Windows artefact, seen in about 1 run in 20 under CPU load); nothing else may appear.
    blocks = [b for b in log.split("Traceback")[1:] if "_call_connection_lost" not in b]
    assert blocks == [], log[-2000:]
    assert "InvalidStateError" not in log and "ui call failed" not in log
    assert "shutting down" in log


# ----- N4: crash residue does not disable the home ACL --------------------------------------------

def _everyone_aces(path):
    out = subprocess.run(["icacls", str(path)], capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if "Everyone" in line or "S-1-1-0" in line]


@pytest.mark.parametrize("residue", ["state.json.tmp", "state.json.corrupt", "reminders.json.tmp",
                                     "reminders.json.corrupt", "settings.json.tmp", "assistant.pid",
                                     "hooks-install.json"])
def test_crash_residue_does_not_stop_secure_home(tmp_path, monkeypatch, caplog, residue):
    root = tmp_path / "assistant-home"
    root.mkdir()
    (root / "state.json").write_text("{}", encoding="utf-8")
    (root / residue).write_text("x", encoding="utf-8")
    monkeypatch.setenv("ASSISTANT_HOME", str(root))
    monkeypatch.setattr(paths, "_fresh_roots", set())
    subprocess.run(["icacls", str(root), "/grant", "*S-1-1-0:(R)"], check=True, capture_output=True)
    assert _everyone_aces(root)
    with caplog.at_level(logging.WARNING, logger="assistant.paths"):
        paths.secure_home()
    assert not [r for r in caplog.records if "not restricting" in r.getMessage()]
    assert _everyone_aces(root) == []


def test_a_foreign_file_still_stops_secure_home(tmp_path, monkeypatch, caplog):
    root = tmp_path / "assistant-home"
    root.mkdir()
    (root / "state.json.tmp").write_text("x", encoding="utf-8")
    (root / "holiday-photos.zip").write_text("x", encoding="utf-8")
    monkeypatch.setenv("ASSISTANT_HOME", str(root))
    monkeypatch.setattr(paths, "_fresh_roots", set())
    with caplog.at_level(logging.WARNING, logger="assistant.paths"):
        paths.secure_home()
    assert [r for r in caplog.records if "not restricting" in r.getMessage()]


# ----- R4: listener ownership, stale pid file, asyncio reset noise, doctor/bind messages -----------

def _foreign_listener(port: int):
    """A non-python process (PowerShell TcpListener) holding ``port``; it accepts and discards."""
    script = (f"$l=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,{port});$l.Start();"
              "while($true){if($l.Pending()){$l.AcceptTcpClient().Close()};Start-Sleep -Milliseconds 100}")
    proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", script], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    deadline = time.monotonic() + 30
    while netinfo.listener_pid(port) is None:
        assert time.monotonic() < deadline and proc.poll() is None, "foreign listener did not start"
        time.sleep(0.2)
    return proc


@pytest.mark.parametrize("command", [("send", "--title", "x"), ("shutdown",)])
def test_r41_no_pid_file_and_non_python_listener_is_refused(tmp_path, command):
    port = _free_port()
    home = _prepare_home(tmp_path, port)
    assert not (home / "assistant.pid").exists()
    squatter = _foreign_listener(port)
    try:
        result = _cli(home, *command)
        assert result.returncode == 1
        assert "No assistant.pid" in result.stderr
        assert "powershell" in result.stderr.lower()
        assert f"pid {netinfo.listener_pid(port)}" in result.stderr
        assert "nothing was sent" in result.stderr
    finally:
        squatter.kill()
        squatter.wait()


def test_r41_no_pid_file_and_python_listener_sends(tmp_path):
    ours = Sink()
    try:
        home = _prepare_home(tmp_path, ours.port)
        assert not (home / "assistant.pid").exists()
        result = _cli(home, "send", "--title", "x")
        assert result.returncode == 0, result.stderr
        assert len(ours.requests) == 1
    finally:
        ours.close()


def test_r41_no_pid_file_and_nothing_listening_is_not_a_listener_refusal(tmp_path):
    home = _prepare_home(tmp_path, _free_port())
    result = _cli(home, "send", "--title", "x")
    assert result.returncode != 0
    assert "No assistant.pid" not in result.stderr


def test_r41_no_pid_file_with_the_real_app_sends_and_shuts_down(live):
    (live.home / "assistant.pid").unlink()
    assert _cli(live.home, "send", "--title", "x").returncode == 0
    assert _cli(live.home, "shutdown").returncode == 0
    live.proc.wait(timeout=20)


def test_r42_stale_pid_file_with_a_reused_python_pid_is_removed_and_the_app_starts(tmp_path):
    port = _free_port()
    home = _prepare_home(tmp_path, port)
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        pid_file = home / "assistant.pid"
        pid_file.write_text(f"{unrelated.pid}\n{port}\n", encoding="ascii")
        app = LiveApp(home, port)
        try:
            recorded = pid_file.read_text(encoding="ascii").split()
            assert int(recorded[0]) != unrelated.pid
            assert netinfo.listener_pid(port) == int(recorded[0])
            log = (home / "logs" / "app.log").read_text(encoding="utf-8")
            assert log.count("removing stale pid file") == 1
            assert str(pid_file) in log
        finally:
            app.stop()
    finally:
        unrelated.kill()
        unrelated.wait()


def test_r42_pid_file_naming_a_dead_pid_is_removed_too(tmp_path):
    port = _free_port()
    home = _prepare_home(tmp_path, port)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (home / "assistant.pid").write_text(f"{dead.pid}\n{port}\n", encoding="ascii")
    app = LiveApp(home, port)
    try:
        assert (home / "assistant.pid").read_text(encoding="ascii").split()[0] != str(dead.pid)
        assert (home / "logs" / "app.log").read_text(encoding="utf-8").count("removing stale pid file") == 1
    finally:
        app.stop()


def test_r42_refusal_names_the_pid_file_and_leaves_it_alone(live):
    before = (live.home / "assistant.pid").read_text(encoding="ascii")
    second = subprocess.run([sys.executable, "-m", "assistant"], cwd=REPO_ROOT, env=_env(live.home),
                            capture_output=True, text=True, timeout=60)
    assert second.returncode == 1
    assert f"pid file {live.home / 'assistant.pid'}" in second.stderr
    assert (live.home / "assistant.pid").read_text(encoding="ascii") == before
    assert "removing stale" not in (live.home / "logs" / "app.log").read_text(encoding="utf-8")
    assert live.get("/v1/health") == 200


def _asyncio_record(exc, message, name="asyncio"):
    return logging.LogRecord(name, logging.ERROR, __file__, 1, message, None, (type(exc), exc, None))


def test_r43_filter_drops_only_the_proactor_connection_lost_reset():
    from assistant.app import _ProactorResetFilter
    flt = _ProactorResetFilter()
    lost = "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)"
    assert flt.filter(_asyncio_record(ConnectionResetError(10054, "reset"), lost)) is False
    assert flt.filter(_asyncio_record(ConnectionResetError(10054, "reset"), "peer reset", name="assistant.x")) is True
    assert flt.filter(_asyncio_record(ConnectionResetError(10054, "reset"), "Exception in callback other()")) is True
    assert flt.filter(_asyncio_record(ValueError("boom"), lost)) is True
    assert flt.filter(logging.LogRecord("asyncio", logging.ERROR, __file__, 1, lost, None, None)) is True


def test_r43_filter_installed_by_setup_logging_stops_a_real_loop_exception_report(tmp_path, monkeypatch):
    from assistant import app as app_module
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path))
    monkeypatch.setattr(paths, "_fresh_roots", set())
    paths.ensure_layout()
    root = logging.getLogger()
    asyncio_logger = logging.getLogger("asyncio")
    saved_handlers, saved_filters = list(root.handlers), list(asyncio_logger.filters)
    try:
        app_module._setup_logging()
        loop = asyncio.new_event_loop()
        try:
            loop.call_exception_handler({
                "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)",
                "exception": ConnectionResetError(10054, "reset")})
            loop.call_exception_handler({"message": "Exception in callback other()",
                                         "exception": ConnectionResetError(10054, "reset")})
        finally:
            loop.close()
        for handler in root.handlers:
            handler.flush()
        text = (paths.logs_dir() / "app.log").read_text(encoding="utf-8")
    finally:
        for handler in root.handlers[:]:
            if handler not in saved_handlers:
                root.removeHandler(handler)
                handler.close()
        asyncio_logger.filters[:] = saved_filters
    assert "_call_connection_lost" not in text
    assert "Exception in callback other()" in text


def test_r45_doctor_names_the_pid_and_image_holding_the_port(tmp_path):
    with socket.socket() as squat:
        squat.bind(("127.0.0.1", 0))
        squat.listen(1)
        port = squat.getsockname()[1]
        home = _prepare_home(tmp_path, port)
        result = _cli(home, "--doctor")
    line = next(l for l in result.stdout.splitlines() if l.startswith("port"))
    image = os.path.basename(netinfo.process_image(os.getpid()))
    assert line.split(None, 1)[1] == f"{port} in use by pid {os.getpid()} ({image})"


def test_r45_doctor_names_a_non_python_owner(tmp_path):
    port = _free_port()
    home = _prepare_home(tmp_path, port)
    squatter = _foreign_listener(port)
    try:
        line = next(l for l in _cli(home, "--doctor").stdout.splitlines() if l.startswith("port"))
        assert f"{port} in use by pid {netinfo.listener_pid(port)} (powershell.exe)" in line
    finally:
        squatter.kill()
        squatter.wait()


def test_r45_doctor_says_free_for_a_free_port(tmp_path):
    port = _free_port()
    home = _prepare_home(tmp_path, port)
    line = next(l for l in _cli(home, "--doctor").stdout.splitlines() if l.startswith("port"))
    assert line.split(None, 1)[1] == f"{port} free"


def test_r45_bind_failure_log_line_says_address_in_use_with_the_errno(tmp_path):
    with socket.socket() as squat:
        squat.bind(("127.0.0.1", 0))
        squat.listen(1)
        port = squat.getsockname()[1]
        home = _prepare_home(tmp_path, port)
        result = _cli(home)
    assert result.returncode == 1
    log = (home / "logs" / "app.log").read_text(encoding="utf-8")
    assert f"server failed to start on 127.0.0.1:{port} (address in use (10048))" in log
    assert "port in use?" not in log


def test_r45_bind_error_text_does_not_claim_address_in_use_when_the_port_is_bindable():
    text = server_app.ServerThread(None, _free_port())._bind_error()
    assert "address in use" not in text
    assert "bindable" in text
