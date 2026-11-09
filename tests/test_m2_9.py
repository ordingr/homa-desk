"""M2-9: security review R5 fixes (R5-F1..F5, R5-N1, N2, N3, N4, N5, N7, N11).

Real components: real ICS parser and IcsSource against local HTTP servers, a real token file and hooks installer
in private homes, real Settings, an in-process MCP server (mcp_live) and the shared running app. Nothing touches the
real ~/.claude or ~/.homa.
"""
import http.client
import http.server
import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import REPO_ROOT, build_ics, launch_app, wait_until, _free_port, Api

from homa import paths
from homa.calendar import ics, meetings
from homa.calendar.meetings import find_join_url

HOOK = "/v1/hooks/claude-code"


# ---- R5-F1: join-URL host check --------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "https://zoom.us@evil.example/phish",
    "https://teams.microsoft.com@evil.example/l/x",
    "https://zoom.us:443.evil.example/x",
    "http://zoom.us/j/1",
    "https://zoom.us:8443/j/1",
    "https://user:pw@zoom.us/j/1",
    "https://evilzoom.us/j/1",
    "https://zoom.us.evil.example/j/1",
])
def test_f1_lookalike_join_urls_are_rejected(bad):
    assert find_join_url(bad) is None


@pytest.mark.parametrize("good", [
    "https://us02web.zoom.us/j/123?pwd=abc",
    "https://teams.microsoft.com/l/meetup-join/xyz",
    "https://meet.google.com/abc-defg-hij",
    "https://zoom.us:443/j/1",
])
def test_f1_real_join_urls_are_returned(good):
    assert find_join_url(good) == good
    assert find_join_url("see " + good + " thanks") == good


def test_f1_mixed_text_returns_the_good_link_not_the_first_match():
    text = ("https://zoom.us@evil.example/phish and then https://zoom.us:443.evil.example/x "
            "and finally https://us02web.zoom.us/j/123?pwd=abc.")
    assert find_join_url(text) == "https://us02web.zoom.us/j/123?pwd=abc"
    assert find_join_url(None, "", "http://zoom.us/j/1", text) == "https://us02web.zoom.us/j/123?pwd=abc"


# ---- R5-F2: per-event isolation and clamps -----------------------------------------------------------

def _ts(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _vevent(uid, summary, start_line, extra=()):
    return ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{summary}", start_line, *extra, "END:VEVENT"]


def _hostile_feed(now):
    soon = now + 600
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//QA//EN"]
    lines += _vevent("good1@example.test", "Good one", f"DTSTART:{_ts(soon)}", [f"DTEND:{_ts(soon + 1800)}"])
    lines += _vevent("huge@example.test", "Huge duration", f"DTSTART:{_ts(soon)}", ["DURATION:P9999999999D"])
    lines += _vevent("ancient@example.test", "Ancient", "DTSTART;VALUE=DATE:00010101")
    lines += _vevent("good2@example.test", "Good two", f"DTSTART:{_ts(soon + 3600)}", [f"DTEND:{_ts(soon + 5400)}"])
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def test_f2_hostile_events_do_not_silence_the_feed_and_source_is_marked_ok(tmp_path, ics_server, caplog):
    from homa.cards.store import CardStore
    from homa.scheduler import Scheduler
    from homa.settings import Settings
    now = time.time()
    source = ics.IcsSource(ics_server.serve("/hostile.ics", _hostile_feed(now)), "Hostile")
    scheduler = Scheduler()
    scheduler.start()
    settings = Settings()
    settings.load()
    ms = meetings.MeetingScheduler([source], CardStore(path=tmp_path / "state.json"), scheduler, settings)
    try:
        with caplog.at_level(logging.WARNING):
            ms.poll()
            status = ms.status()["calendars"][0]
        assert status["last_ok"], status
        assert not status["last_error"], status
        assert status["events"] == 3, status
    finally:
        ms.stop()
        scheduler.stop()
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING and "could not be read" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert "uid length" in warnings[0].getMessage()
    assert "ancient" not in warnings[0].getMessage().lower() and "example.test" not in warnings[0].getMessage()


def test_f2_parse_level_events_and_clamps():
    now = datetime.now(timezone.utc)
    events = ics.parse_ics(_hostile_feed(now.timestamp()), now, now + timedelta(days=2), "x")
    assert sorted(e.subject for e in events) == ["Good one", "Good two", "Huge duration"]
    huge = next(e for e in events if e.subject == "Huge duration")
    assert huge.end - huge.start == ics.MAX_DURATION == timedelta(days=366)
    assert ics._parse_time("19691231T000000Z", {}) is None
    assert ics._parse_time("21010101T000000Z", {}) is None
    assert ics._parse_time("19700101T000000Z", {}) is not None
    assert ics._parse_time("21001231T000000Z", {}) is not None
    assert ics._parse_duration("P99999999999999999999D") == ics.MAX_DURATION


# ---- R5-N3: redirects ------------------------------------------------------------------------------

class _RedirServer:
    """Serves /feed/N redirect chains: routes maps path -> (status, location or None, body)."""

    def __init__(self):
        self.routes = {}
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, location, body = outer.routes.get(self.path, (404, None, b""))
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def redir():
    r = _RedirServer()
    yield r
    r.close()


FEED = build_ics([{"uid": "r@example.test", "subject": "R", "start": time.time() + 600, "end": time.time() + 1200}])


def _fetch(url):
    now = datetime.now(timezone.utc)
    return ics.IcsSource(url, "r").fetch(now, now + timedelta(hours=1))


def test_n3_redirect_to_ftp_is_refused(redir):
    redir.routes["/a"] = (302, "ftp://example.test/feed.ics", b"")
    with pytest.raises(meetings.SourceError):
        _fetch(redir.base + "/a")


def test_n3_four_hop_chain_is_refused_and_two_hop_is_followed(redir):
    redir.routes["/h0"] = (302, "/h1", b"")
    redir.routes["/h1"] = (302, "/h2", b"")
    redir.routes["/h2"] = (302, "/h3", b"")
    redir.routes["/h3"] = (302, "/h4", b"")
    redir.routes["/h4"] = (200, None, FEED.encode())
    with pytest.raises(meetings.SourceError):
        _fetch(redir.base + "/h0")
    redir.routes["/t0"] = (302, "/t1", b"")
    redir.routes["/t1"] = (301, redir.base + "/t2", b"")
    redir.routes["/t2"] = (200, None, FEED.encode())
    assert [e.subject for e in _fetch(redir.base + "/t0")] == ["R"]


def test_n3_handler_allows_https_targets_and_refuses_other_schemes():
    import urllib.request
    handler = ics._HttpRedirects()
    req = urllib.request.Request("http://feeds.example.test/a")
    assert handler.redirect_request(req, None, 302, "Found", {}, "https://feeds.example.test/b") is not None
    for target in ("ftp://x.example/f", "file:///c:/x", "gopher://x.example/"):
        assert handler.redirect_request(req, None, 302, "Found", {}, target) is None
    assert handler.max_redirections == 3


# ---- R5-N4: TZID warn cap -------------------------------------------------------------------------

def test_n4_unknown_tzid_warnings_are_capped_with_one_closing_line(caplog):
    saved = set(ics._WARNED_TZIDS)
    ics._WARNED_TZIDS.clear()
    try:
        with caplog.at_level(logging.WARNING, logger="homa.calendar.ics"):
            for i in range(80):
                assert ics._tz_for(f"Bogus/Zone{i}") is timezone.utc
        assert len(ics._WARNED_TZIDS) == 50
        msgs = [r.getMessage() for r in caplog.records]
        assert sum("cannot be resolved" in m for m in msgs) == 50
        assert sum("further unknown calendar time zones are not logged" in m for m in msgs) == 1
    finally:
        ics._WARNED_TZIDS.clear()
        ics._WARNED_TZIDS.update(saved)


# ---- R5-F3: token regenerate vs readers ----------------------------------------------------------------

@pytest.fixture
def private_home(tmp_path, monkeypatch):
    home = tmp_path / "ah"
    home.mkdir()
    monkeypatch.setenv("HOMA_HOME", str(home))
    paths.ensure_layout()
    return home


def _regenerate_under_readers(pause):
    from homa.security import token as token_mod
    token_mod.ensure()
    stop = threading.Event()
    reader_errors = []

    def reader():
        while not stop.is_set():
            try:
                token_mod.read()
            except (OSError, ValueError) as exc:
                reader_errors.append(type(exc).__name__)
            if pause:
                time.sleep(pause)

    threads = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    failures, seen = [], set()
    try:
        for _ in range(60):
            try:
                seen.add(token_mod.regenerate().token)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        stop.set()
        for t in threads:
            t.join(5)
    assert failures == []
    assert len(seen) == 60
    assert token_mod.read() in seen
    assert not (paths.home() / "token.tmp").exists()


def test_f3_sixty_regenerations_under_four_polling_readers_never_fail(private_home):
    _regenerate_under_readers(0.001)


def test_f3_sixty_regenerations_under_four_spinning_readers_never_fail(private_home):
    _regenerate_under_readers(0)


def test_f3_replace_retries_a_transient_permission_error(private_home, monkeypatch):
    from homa.security import token as token_mod
    real, calls = os.replace, []

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 4:
            raise PermissionError("busy")
        return real(a, b)

    monkeypatch.setattr(token_mod.os, "replace", flaky)
    new = token_mod.regenerate().token
    assert len(calls) == 4 and token_mod.read() == new


def test_f3_live_app_serves_the_new_token_on_the_next_request(tmp_path):
    from homa.security import token as token_mod
    home = tmp_path / "live_home"
    with launch_app(home, _free_port()) as (base, old):
        old_api = Api(base, old)
        assert old_api.get("/v1/health")[0] == 200
        mp = pytest.MonkeyPatch()
        mp.setenv("HOMA_HOME", str(home))
        try:
            new = token_mod.regenerate().token
        finally:
            mp.undo()
        assert new != old
        assert Api(base, new).get("/v1/health")[0] == 200
        assert old_api.get("/v1/health")[0] == 401
        assert Api(base, new).get("/v1/health")[0] == 200, "stamp cache must keep serving the new token"


# ---- R5-F4: hooks remove after regenerate -------------------------------------------------------------

def _hooks_env(tmp_path):
    user = tmp_path / "user"
    (user / ".claude").mkdir(parents=True)
    ah = tmp_path / "ah4"
    ah.mkdir()
    (ah / "settings.json").write_text(json.dumps({"schema": 1, "port": 17333}), encoding="utf-8")
    drive, rest = os.path.splitdrive(str(user))
    env = dict(os.environ, USERPROFILE=str(user), HOME=str(user), HOMA_HOME=str(ah), HOMEDRIVE=drive, HOMEPATH=rest)
    return env, user / ".claude" / "settings.json"


def _cli(env, *args):
    r = subprocess.run([sys.executable, "-m", "homa", *args], cwd=REPO_ROOT, env=env, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def _regen(env):
    r = subprocess.run([sys.executable, "-c",
                        "from homa.security import token; r=token.regenerate(); print(r.hooks)"],
                       cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_f4_install_edit_regenerate_remove_leaves_no_bearer(tmp_path):
    env, settings = _hooks_env(tmp_path)
    settings.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "echo mine"}]}]}}), encoding="utf-8")
    _cli(env, "hooks", "install")
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["theme"] = "dark"
    data["permissions"] = {"allow": ["Bash(git status)"]}
    settings.write_text(json.dumps(data, indent=2), encoding="utf-8")
    assert _regen(env) == "updated"
    assert "Bearer" in settings.read_text(encoding="utf-8")
    _cli(env, "hooks", "remove")
    text = settings.read_text(encoding="utf-8")
    assert "Bearer" not in text and "/v1/hooks/claude-code" not in text
    after = json.loads(text)
    assert after["model"] == "opus" and after["theme"] == "dark"
    assert after["permissions"] == {"allow": ["Bash(git status)"]}
    assert after["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "echo mine"}]}]
    code = subprocess.run([sys.executable, "-c", "from homa import hooks_installer as h; print(h.installed())"],
                          cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert code.stdout.strip() == "no", code.stdout + code.stderr


# ---- R5-F5 / R5-N7: settings ------------------------------------------------------------------------

def _load_settings(home, monkeypatch, content):
    monkeypatch.setenv("HOMA_HOME", str(home))
    (home / "settings.json").write_text(json.dumps(content), encoding="utf-8")
    from homa.settings import Settings
    s = Settings()
    s.load()
    return s


@pytest.fixture
def shome(tmp_path):
    h = tmp_path / "sh"
    h.mkdir()
    return h


def test_f5_a_bad_lead_does_not_discard_the_urls_and_outlook_defaults_off(shome, monkeypatch):
    urls = ["https://one.example/a.ics", "https://two.example/b.ics"]
    s = _load_settings(shome, monkeypatch, {"schema": 1, "calendars": {"lead_minutes": 999, "ics_urls": urls}})
    cal = s.get("calendars")
    assert cal["ics_urls"] == urls and cal["lead_minutes"] == 5 and cal["outlook"] is False and cal["poll_minutes"] == 5


def test_f5_default_outlook_is_off_with_no_settings_file(shome, monkeypatch):
    monkeypatch.setenv("HOMA_HOME", str(shome))
    from homa.settings import DEFAULTS, Settings
    s = Settings()
    s.load()
    assert s.get("calendars")["outlook"] is False and DEFAULTS["calendars"]["outlook"] is False


def test_n7_external_edit_with_25_urls_keeps_20_and_warns(shome, monkeypatch, caplog):
    urls = [f"https://h{i}.example/c.ics" for i in range(25)]
    with caplog.at_level(logging.WARNING):
        s = _load_settings(shome, monkeypatch, {"schema": 1, "calendars": {"ics_urls": urls}})
    assert s.get("calendars")["ics_urls"] == urls[:20]
    assert any("trimmed" in r.getMessage() for r in caplog.records)
    assert not any("h3.example" in r.getMessage() for r in caplog.records), "URLs are credentials"


def test_n7_overlong_url_is_dropped_and_set_rejects_21_and_long(shome, monkeypatch):
    long_url = "https://x.example/" + "a" * 2100
    s = _load_settings(shome, monkeypatch, {"schema": 1, "calendars": {"ics_urls": ["https://ok.example/a", long_url]}})
    assert s.get("calendars")["ics_urls"] == ["https://ok.example/a"]
    with pytest.raises(ValueError):
        s.set("calendars", {**s.get("calendars"), "ics_urls": [f"https://h{i}.example/" for i in range(21)]})
    with pytest.raises(ValueError):
        s.set("calendars", {**s.get("calendars"), "ics_urls": [long_url]})
    s.set("calendars", {**s.get("calendars"), "ics_urls": [f"https://h{i}.example/" for i in range(20)]})
    assert len(s.get("calendars")["ics_urls"]) == 20


# ---- R5-N1 / R5-N2: MCP log and verbs ------------------------------------------------------------------

def _mcp_log(live):
    path = live.home / "logs" / "mcp.log"
    if not path.is_file():
        return []
    return [line.rstrip("\r") for line in path.read_bytes().decode("utf-8").split("\n")]


def test_n1_control_characters_in_tool_name_and_keys_stay_on_one_log_line(mcp_live):
    marker = "inj" + uuid.uuid4().hex[:6]
    before = len(_mcp_log(mcp_live))
    mcp_live.mcp().call(f"{marker}\n2026-01-01T00:00:00 tool=FORGED\r\x1b[31m", {f"k\nFORGED{marker}": "v"})
    assert wait_until(lambda: any(marker in l for l in _mcp_log(mcp_live)), timeout=5)
    new = [l for l in _mcp_log(mcp_live)[before:] if l.strip()]
    mine = [l for l in new if marker in l]
    assert len(mine) == 1, new
    assert not any(l.lstrip().startswith("2026-01-01") for l in new)
    assert "\r" not in mine[0] and "\x1b" not in mine[0], repr(mine[0])
    assert "tool=" + marker in mine[0] and "FORGED" in mine[0], repr(mine[0])


def _raw(live, method, path, body=None):
    conn = http.client.HTTPConnection(live.host, int(live.port), timeout=10)
    try:
        headers = {"Authorization": f"Bearer {live.token}", "Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse()
        return r.status, r.getheader("Allow"), r.read()
    finally:
        conn.close()


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH"])
def test_n2_non_post_on_mcp_is_405_allow_post(api, method, path):
    status, allow, _ = _raw(api, method, path, body=b"{}" if method != "GET" else None)
    assert status == 405 and allow == "POST"


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_n2_post_initialize_is_still_200(api, path):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "qa", "version": "1"}}}).encode()
    status, _, data = _raw(api, "POST", path, body)
    assert status == 200, (status, data[:200])


def test_n2_real_client_still_lists_tools(mcp_live):
    assert {t.name for t in mcp_live.mcp().list_tools()} >= {"send_card"}


# ---- R5-N5: masking in hook cards --------------------------------------------------------------------

BEARER43 = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcde"[:43]


@pytest.fixture
def hook_session(api):
    sid, project = str(uuid.uuid4()), "m29-" + uuid.uuid4().hex[:8]
    cwd = rf"C:\Users\<user>\projects\{project}"

    def send(event, **fields):
        status, body = api.post(HOOK, {"hook_event_name": event, "session_id": sid, "cwd": cwd, **fields})
        assert status == 200
        return body

    def cards():
        return [c for c in api.get("/v1/cards")[1] if project in (c.get("title") or "")]

    yield send, cards
    for c in cards():
        api.request("DELETE", f"/v1/cards/{c['id']}")


def _detail_of(cards, kind):
    got = wait_until(lambda: [c for c in cards() if c["kind"] == kind], timeout=3)
    assert got, cards()
    return got[0].get("detail") or ""


def test_n5_stop_message_masks_a_bare_43_char_token(hook_session):
    send, cards = hook_session
    assert len(BEARER43) == 43
    send("Stop", last_assistant_message=f"deploy key is {BEARER43} and Authorization: Bearer {BEARER43}")
    detail = _detail_of(cards, "done")
    assert BEARER43 not in detail and "***" in detail


def test_n5_notification_message_masks_a_bearer(hook_session):
    send, cards = hook_session
    send("Notification", notification_type="permission_prompt", message=f"Bearer {BEARER43} was used")
    detail = _detail_of(cards, "waiting")
    assert BEARER43 not in detail and "***" in detail


def test_n5_stop_failure_error_masks_a_bearer(hook_session):
    send, cards = hook_session
    send("StopFailure", error=f"Bearer {BEARER43}")
    detail = _detail_of(cards, "waiting")
    assert BEARER43 not in detail and "***" in detail


def test_n5_normal_prose_is_not_masked(hook_session):
    send, cards = hook_session
    send("Stop", last_assistant_message="All tests pass, 12 files changed")
    assert "All tests pass, 12 files changed" in _detail_of(cards, "done")


# ---- R5-N11: desktop snippet -------------------------------------------------------------------------

def test_n11_snippet_uses_the_interpreter_path_not_bare_python():
    settings_ui = pytest.importorskip("homa.overlay.settings_ui")
    server = json.loads(settings_ui.desktop_snippet())["mcpServers"]["homa"]
    assert server["command"] != "python"
    assert server["command"] == sys.executable
    assert os.path.isfile(server["command"]) and server["command"].lower().endswith("python.exe")
    assert server["args"] == ["-m", "homa.mcp_stdio"]


def test_n11_pythonw_is_swapped_for_the_console_python(tmp_path, monkeypatch):
    settings_ui = pytest.importorskip("homa.overlay.settings_ui")
    (tmp_path / "pythonw.exe").write_bytes(b"")
    (tmp_path / "python.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    assert json.loads(settings_ui.desktop_snippet())["mcpServers"]["homa"]["command"] == str(tmp_path / "python.exe")


# ---- --doctor ---------------------------------------------------------------------------------------

def test_doctor_says_outlook_off_when_the_key_is_absent(tmp_path):
    ah = tmp_path / "doc"
    ah.mkdir()
    (ah / "settings.json").write_text(json.dumps({"schema": 1, "port": _free_port(), "calendars": {"ics_urls": []}}),
                                      encoding="utf-8")
    env = dict(os.environ, HOMA_HOME=str(ah))
    r = subprocess.run([sys.executable, "-m", "homa", "--doctor"], cwd=REPO_ROOT, env=env, capture_output=True,
                       text=True, timeout=90)
    assert "0 ICS feed(s), Outlook off" in r.stdout, r.stdout + r.stderr
    (ah / "settings.json").write_text(json.dumps({"schema": 1, "port": _free_port(), "calendars": {"outlook": True}}),
                                      encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "homa", "--doctor"], cwd=REPO_ROOT, env=env, capture_output=True,
                       text=True, timeout=90)
    assert "Outlook on" in r.stdout, r.stdout
