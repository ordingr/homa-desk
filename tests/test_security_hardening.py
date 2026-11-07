"""Regression tests for the M1-0 security hardening (Review 2: R2-1, R2-2, R2-4, R2-5, R2-7, R2-8, R2-12).

Everything here runs in-process against the real ``create_app()`` and the real Windows ACL APIs; no
server process is started. Every home folder is a pytest temp dir.
"""
import asyncio
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.responses import JSONResponse
from starlette.routing import Route

from assistant import paths
from assistant.security import acl, token
from assistant.server import app as server_app

from conftest import REPO_ROOT

TOKEN = "T" * 43
MAX = server_app.MAX_BODY_BYTES


async def _swallowing_json(request):
    try:
        await request.json()
    except Exception as exc:
        return JSONResponse({"error": "bad json", "swallowed": type(exc).__name__}, status_code=400)
    return JSONResponse({"ok": True})


async def _read_body(request):
    data = await request.body()
    return JSONResponse({"got": len(data)})


@pytest.fixture
def asgi_app():
    app = server_app.create_app(lambda: TOKEN)
    app.router.routes.append(Route("/swallow", _swallowing_json, methods=["POST"]))
    app.router.routes.append(Route("/echo", _read_body, methods=["POST"]))
    return app


def _call(app, path, chunks, *, content_length=None, auth=True):
    """Drive the ASGI app with one POST; returns (status, body bytes). ``chunks`` are streamed as given."""
    headers = [(b"host", b"127.0.0.1")]
    if auth:
        headers.append((b"authorization", b"Bearer " + TOKEN.encode()))
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    queue = list(chunks)
    sent = []

    async def receive():
        if queue:
            body = queue.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(queue)}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": path, "raw_path": path.encode("utf-8"),
             "query_string": b"", "headers": headers, "http_version": "1.1", "scheme": "http",
             "server": ("127.0.0.1", 1), "client": ("127.0.0.1", 2)}
    asyncio.run(app(scope, receive, send))
    status = [m["status"] for m in sent if m["type"] == "http.response.start"]
    assert len(status) == 1, f"expected exactly one response start, got {sent!r}"
    return status[0], b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def _records(caplog, name):
    return [r for r in caplog.records if r.name == name]


# ----- 413 body cap (R2-1, R2-7) ---------------------------------------------------------------

def test_declared_oversize_body_is_413_with_one_warning(asgi_app, caplog):
    caplog.set_level(logging.DEBUG)
    status, body = _call(asgi_app, "/echo", [b""], content_length=2_000_000)
    assert status == 413
    assert b"payload too large" in body
    warnings = _records(caplog, "assistant.server.app")
    assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
    assert warnings[0].getMessage() == "rejected http '/echo' with 413 (payload too large)"


def test_streamed_oversize_body_is_413(asgi_app):
    status, body = _call(asgi_app, "/echo", [b"x" * 600_000, b"x" * 600_000])
    assert status == 413
    assert b"payload too large" in body


def test_streamed_body_lying_about_content_length_is_still_413(asgi_app):
    status, _ = _call(asgi_app, "/echo", [b"x" * 600_000, b"x" * 600_000], content_length=10)
    assert status == 413


def test_route_that_catches_exception_still_gets_413(asgi_app):
    status, body = _call(asgi_app, "/swallow", [b"x" * 600_000, b"x" * 600_000])
    assert status == 413, body
    assert b"swallowed" not in body


def test_body_exactly_at_cap_passes_and_one_over_is_413(asgi_app):
    status, body = _call(asgi_app, "/echo", [b"x" * MAX])
    assert status == 200 and b'"got":1048576' in body.replace(b" ", b"")
    assert _call(asgi_app, "/echo", [b"x" * MAX, b"y"])[0] == 413


def test_oversize_without_token_is_401_before_the_cap(asgi_app, caplog):
    caplog.set_level(logging.DEBUG)
    status, _ = _call(asgi_app, "/echo", [b""], content_length=2_000_000, auth=False)
    assert status == 401
    assert _records(caplog, "assistant.server.app") == []
    assert len(_records(caplog, "assistant.server.auth")) == 1


# ----- log injection (F1, R2-7) -----------------------------------------------------------------

def test_413_log_line_repr_escapes_newlines_in_path(asgi_app, caplog):
    caplog.set_level(logging.DEBUG)
    _call(asgi_app, "/echo\nWARNING forged line\r\x1b[31m", [b""], content_length=2_000_000)
    (record,) = _records(caplog, "assistant.server.app")
    message = record.getMessage()
    assert "\n" not in message and "\r" not in message and "\x1b" not in message
    assert "\\n" in message


def test_logged_path_is_capped_at_200_chars_on_413_and_401(asgi_app, caplog):
    caplog.set_level(logging.DEBUG)
    long_path = "/" + "a" * 500
    _call(asgi_app, long_path, [b""], content_length=2_000_000)
    _call(asgi_app, long_path, [b""], auth=False)
    for name in ("assistant.server.app", "assistant.server.auth"):
        (record,) = _records(caplog, name)
        message = record.getMessage()
        run = max(len(m) for m in re.findall(r"a+", message))
        assert run == 199, (name, run)  # "/" + 199 a's == the first 200 characters of the path


# ----- home folder ACL (R2-2) -------------------------------------------------------------------

def _aces(path):
    out = subprocess.run(["icacls", str(path)], capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if ":(" in line]


@pytest.fixture
def fresh_state(monkeypatch):
    monkeypatch.setattr(paths, "_fresh_roots", set())


def test_fresh_home_gets_a_single_inheriting_user_only_ace(tmp_path, monkeypatch, fresh_state):
    root = tmp_path / "assistant-home"
    monkeypatch.setenv("ASSISTANT_HOME", str(root))
    paths.ensure_layout()
    aces = _aces(root)
    assert len(aces) == 1, aces
    assert "(OI)(CI)(F)" in aces[0]
    (child,) = _aces(root / "logs")
    assert "(I)" in child
    assert len(_aces(root / "logs")) == 1


def test_second_ensure_layout_on_own_folder_keeps_the_ace(tmp_path, monkeypatch, fresh_state):
    root = tmp_path / "assistant-home"
    monkeypatch.setenv("ASSISTANT_HOME", str(root))
    paths.ensure_layout()
    first = _aces(root)
    paths.ensure_layout()  # now pre-existing, but contents are the known layout only
    assert _aces(root) == first


def test_preexisting_foreign_folder_is_left_alone_with_a_warning(tmp_path, monkeypatch, fresh_state, caplog):
    root = tmp_path / "shared"
    report = root / "other-tool" / "data" / "report.txt"
    report.parent.mkdir(parents=True)
    report.write_text("not ours", encoding="utf-8")
    before = (_aces(report), _aces(root))
    monkeypatch.setenv("ASSISTANT_HOME", str(root))
    caplog.set_level(logging.DEBUG)
    paths.ensure_layout()
    assert (_aces(report), _aces(root)) == before
    assert len(before[0]) > 1  # the foreign file really does carry inherited SYSTEM/Administrators ACEs
    warnings = [r for r in _records(caplog, "assistant.paths") if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "not restricting" in warnings[0].getMessage()


def test_drive_root_and_user_profile_are_refused_without_touching_acls(tmp_path, monkeypatch, fresh_state, caplog):
    calls = []
    monkeypatch.setattr(acl, "restrict_to_current_user", lambda *a, **k: calls.append(a))
    caplog.set_level(logging.DEBUG)

    monkeypatch.setenv("ASSISTANT_HOME", Path(tmp_path.anchor).as_posix())
    paths.secure_home()

    fake_profile = tmp_path / "profile"
    fake_profile.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_profile))
    monkeypatch.setenv("ASSISTANT_HOME", str(fake_profile))
    paths.secure_home()

    assert calls == []
    warnings = [r for r in _records(caplog, "assistant.paths") if r.levelno == logging.WARNING]
    assert len(warnings) == 2 and all("refusing to restrict" in w.getMessage() for w in warnings)


# ----- token file (R2-4, R2-5, N3) --------------------------------------------------------------

@pytest.fixture
def token_home(tmp_path, monkeypatch, fresh_state):
    root = tmp_path / "assistant-home"
    monkeypatch.setenv("ASSISTANT_HOME", str(root))
    paths.ensure_layout()
    return root


def test_existing_token_with_everyone_read_is_restored_to_a_single_user_ace(token_home):
    first = token.ensure()
    subprocess.run(["icacls", str(paths.token_file()), "/grant", "*S-1-1-0:(R)"], check=True, capture_output=True)
    assert any("Everyone" in line or "S-1-1-0" in line for line in _aces(paths.token_file()))
    second = token.ensure()
    aces = _aces(paths.token_file())
    assert second == first
    assert len(aces) == 1 and "Everyone" not in aces[0] and "(F)" in aces[0], aces


def test_token_securing_failure_refuses_to_start_with_the_reason(token_home, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise OSError(50, "The request is not supported")
    monkeypatch.setattr(acl, "restrict_to_current_user", boom)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SystemExit) as excinfo:
        token.ensure()
    assert excinfo.value.code == 1
    errors = [r for r in _records(caplog, "assistant.security.token") if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].getMessage() == "could not secure the token file (OSError); refusing to start"
    assert errors[0].exc_info is None
    assert not paths.token_file().exists()
    assert not paths.token_file().with_name("token.tmp").exists()


def test_existing_token_that_cannot_be_secured_also_refuses_to_start(token_home, monkeypatch, caplog):
    token.ensure()
    monkeypatch.setattr(acl, "restrict_to_current_user", lambda *a, **k: (_ for _ in ()).throw(OSError(5, "denied")))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SystemExit) as excinfo:
        token.ensure()
    assert excinfo.value.code == 1
    assert "(OSError)" in caplog.text


# ----- doctor without pywin32 (R2-8) ------------------------------------------------------------

_BLOCK = "import sys; sys.modules['win32security'] = None; "


def _run_py(args, tmp_path):
    env = dict(os.environ, ASSISTANT_HOME=str(tmp_path / "doctor-home"))
    return subprocess.run([sys.executable, *args], cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                          timeout=60)


def test_doctor_module_imports_without_win32security(tmp_path):
    result = _run_py(["-c", _BLOCK + "import assistant.doctor; print('imported')"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_doctor_runs_without_win32security_and_prints_no_traceback(tmp_path):
    code = (_BLOCK + "import runpy; sys.argv = ['assistant', '--doctor']; "
            "runpy.run_module('assistant', run_name='__main__')")
    result = _run_py(["-c", code], tmp_path)
    assert "Traceback" not in result.stderr, result.stderr
    assert "pywin32" in result.stdout
    assert "token file" in result.stdout
    assert result.returncode in (0, 1)
    assert not (tmp_path / "doctor-home").exists(), "--doctor must be read-only"
