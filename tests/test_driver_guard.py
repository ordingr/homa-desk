"""tools/driver.py hardening from the rename review's R15 notes (docs/security/2026-11-08-homa-rename-review.md):

* R15-N1: ``start --home`` refuses a folder that is, is inside, or contains the real ``~/.homa`` or ``~/.assistant``.
* R15-N2: the child app gets no ``ASSISTANT_*`` / ``HOMA_*`` variable from the parent except ``HOMA_HOME`` (and
  ``HOMA_TEST_MODE`` only when ``--test-mode`` asks for it).
* R15-N3: ``http()`` never goes through an HTTP proxy from the environment.

Nothing here starts an app: ``subprocess.Popen`` inside the driver is replaced by a recorder that stops ``start`` right
after it has built the child's environment, and the driver's shared state file (``%TEMP%\\homa-driver``) is never read
or written. ``Path.home()`` is the session's scratch profile (conftest), so the "real" folders below are scratch too.
"""
import http.server
import json
import os
import threading
import types
from pathlib import Path

import pytest

from tools import driver


class Stop(Exception):
    pass


@pytest.fixture
def no_state(monkeypatch):
    written = []
    monkeypatch.setattr(driver, "load_state", lambda: {})
    monkeypatch.setattr(driver, "save_state", lambda state: written.append(state))
    return written


def args(home, port=50999, test_mode=False):
    return types.SimpleNamespace(home=str(home), port=port, test_mode=test_mode, console=True, allow_live_port=False)


# ---- R15-N1 -------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("make", [
    lambda h: h / ".homa",
    lambda h: h / ".homa" / "sub" / "deeper",
    lambda h: h / ".assistant",
    lambda h: h / ".assistant" / "x",
    lambda h: h,
    lambda h: Path(str(h / ".homa").upper()),
    lambda h: h / "Documents" / ".." / ".homa",
])
def test_overlap_with_the_real_home_is_detected(make):
    assert Path.home().resolve().is_relative_to(Path(os.environ["USERPROFILE"]).resolve())
    assert driver._real_home_overlap(make(Path.home())) != ""


@pytest.mark.parametrize("make", [
    lambda h, t: t / "scratch-home",
    lambda h, t: h / ".homa-scratch",
    lambda h, t: h / "work" / "homa",
])
def test_unrelated_folders_pass(tmp_path, make):
    assert driver._real_home_overlap(make(Path.home(), tmp_path)) == ""


def test_start_refuses_the_real_home_before_writing_anything(no_state, monkeypatch):
    real = Path.home() / ".homa"
    target = real / "qa-sub"
    monkeypatch.setattr(driver.subprocess, "Popen", lambda *a, **k: pytest.fail("no app may start"))
    with pytest.raises(SystemExit) as exc:
        driver.cmd_start(args(target))
    assert "overlaps the real" in str(exc.value)
    assert not target.exists() and not (real / "settings.json").exists() and no_state == []


def test_start_still_refuses_the_live_port(no_state, tmp_path, monkeypatch):
    monkeypatch.setattr(driver.subprocess, "Popen", lambda *a, **k: pytest.fail("no app may start"))
    with pytest.raises(SystemExit) as exc:
        driver.cmd_start(args(tmp_path / "h", port=17171))
    assert "live instance" in str(exc.value)


# ---- R15-N2 -------------------------------------------------------------------------------------------------------------

def run_start(tmp_path, monkeypatch, test_mode):
    seen = {}

    def recorder(cmd, cwd=None, env=None, stdout=None, **kw):
        seen["cmd"], seen["env"] = cmd, dict(env)
        stdout.close()
        raise Stop()

    monkeypatch.setattr(driver.subprocess, "Popen", recorder)
    with pytest.raises(Stop):
        driver.cmd_start(args(tmp_path / "scratch", test_mode=test_mode))
    return seen


LEAKY = {"ASSISTANT_TEST_MODE": "1", "ASSISTANT_HOME": "C:\\leak\\home", "ASSISTANT_STATIC_DIR": "C:\\leak\\static",
         "ASSISTANT_NOTES_DIR": "C:\\leak\\notes", "HOMA_TEST_MODE": "1", "HOMA_STATIC_DIR": "C:\\leak\\static2",
         "HOMA_NOTES_DIR": "C:\\leak\\notes2", "HOMA_MIGRATE_SHORTCUT_DIRS": "C:\\leak\\desk"}


@pytest.mark.parametrize("test_mode", [False, True])
def test_the_child_env_drops_inherited_prefixed_variables(no_state, tmp_path, monkeypatch, test_mode):
    for k, v in LEAKY.items():
        monkeypatch.setenv(k, v)
    env = run_start(tmp_path, monkeypatch, test_mode)["env"]
    scratch = (tmp_path / "scratch").resolve()
    prefixed = {k: v for k, v in env.items() if k.upper().startswith(("ASSISTANT_", "HOMA_"))}
    expected = {"HOMA_HOME": str(scratch)}
    if test_mode:
        expected["HOMA_TEST_MODE"] = "1"
    assert prefixed == expected, prefixed
    for var in ("USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA"):
        assert Path(env[var]).resolve().is_relative_to(scratch), (var, env[var])
    assert "HOMEDRIVE" not in env and "HOMEPATH" not in env
    assert json.loads((scratch / "settings.json").read_text(encoding="utf-8"))["port"] == 50999


# ---- R15-N3 -------------------------------------------------------------------------------------------------------------

class _Recorder(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.hits.append(self.path)
        body = json.dumps({"seen": self.path, "auth": self.headers.get("Authorization", "")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _serve():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    server.hits = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_http_ignores_proxy_variables(tmp_path, monkeypatch):
    target, proxy = _serve(), _serve()
    try:
        proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"
        for var in ("HTTP_PROXY", "http_proxy", "ALL_PROXY"):
            monkeypatch.setenv(var, proxy_url)
        for var in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(var, raising=False)
        (tmp_path / "token").write_text("scratch-token\n", encoding="ascii")
        state = {"port": target.server_address[1], "home": str(tmp_path)}
        status, body = driver.http("GET", "/v1/health", state=state)
        assert status == 200 and body == {"seen": "/v1/health", "auth": "Bearer scratch-token"}
        assert target.hits == ["/v1/health"] and proxy.hits == [], "the bearer must never travel via a proxy"
    finally:
        target.shutdown()
        proxy.shutdown()


# ---- R15C-N6 ------------------------------------------------------------------------------------------------------------

class _Redirector(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.hits.append((self.path, self.headers.get("Authorization", "")))
        if self.path == "/v1/hop":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.other_port}/landing")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b'{"landed": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_http_does_not_follow_a_redirect_or_forward_the_bearer(tmp_path):
    other, first = _serve(), None
    try:
        first = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Redirector)
        first.hits, first.other_port = [], other.server_address[1]
        threading.Thread(target=first.serve_forever, daemon=True).start()
        (tmp_path / "token").write_text("scratch-token\n", encoding="ascii")
        state = {"port": first.server_address[1], "home": str(tmp_path)}
        status, body = driver.http("GET", "/v1/hop", state=state)
        assert (status, body) == (302, None)
        assert first.hits == [("/v1/hop", "Bearer scratch-token")], "the request itself did carry the bearer"
        assert other.hits == [], "the redirect target was never contacted, so it never saw the Authorization header"
    finally:
        other.shutdown()
        if first is not None:
            first.shutdown()


def test_the_driver_opener_has_a_no_redirect_handler_and_no_default_opener():
    kinds = [type(h) for h in driver._OPENER.handlers]
    assert driver._NoRedirect in kinds and not any(k is __import__("urllib.request").request.HTTPRedirectHandler
                                                    for k in kinds)
