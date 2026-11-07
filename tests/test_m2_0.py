"""M2-0: PRD-alignment and M1 leftovers (plan 16.3 M2-0, items a-h).

Live checks use the shared running app from conftest; ASGI checks drive create_app in-process.
"""
import asyncio
import copy
import json
import os
import re
import subprocess
import sys
import time
import uuid

import pytest

from assistant import paths
from assistant.cards.model import Card, CardValidationError, strip_control, validate_fields
from assistant.cards.store import FADE_SECONDS, CardStore
from assistant.server import app as server_app

from conftest import REPO_ROOT, wait_until

TOKEN = "T" * 43
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "hook_payloads"
HOOK = "/v1/hooks/claude-code"


# ---- helpers ---------------------------------------------------------------------------------

def _asgi(app, method, path, headers=None, body=b"", token=True):
    hdrs = [(b"host", b"127.0.0.1")]
    if token:
        hdrs.append((b"authorization", b"Bearer " + TOKEN.encode()))
    hdrs.extend((k.lower().encode(), v.encode()) for k, v in (headers or {}).items())
    queue = [body]
    sent = []

    async def receive():
        if queue:
            return {"type": "http.request", "body": queue.pop(0), "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": hdrs, "http_version": "1.1", "scheme": "http", "server": ("127.0.0.1", 1),
             "client": ("127.0.0.1", 2), "root_path": ""}
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    out_headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], out_headers, data


def _shape(data, code, field_key=True):
    parsed = json.loads(data)
    assert set(parsed) == {"error"}, parsed
    err = parsed["error"]
    assert isinstance(err, dict), parsed
    assert err["code"] == code, parsed
    assert isinstance(err["message"], str) and err["message"]
    if field_key:
        assert "field" in err, parsed
    return err


async def _dummy_mcp(scope, receive, send):
    assert scope["type"] == "http"
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"mcp-ok"})


@pytest.fixture(scope="module")
def mcp_app():
    return server_app.create_app(lambda: TOKEN, mcp_app=_dummy_mcp)


@pytest.fixture
def uniq():
    return "m20-" + uuid.uuid4().hex[:8]


# ---- (a) error shape --------------------------------------------------------------------------

def test_a_401_live_has_object_shape_www_authenticate_and_close(api):
    import http.client
    conn = http.client.HTTPConnection(api.host, int(api.port), timeout=10)
    try:
        conn.request("GET", "/v1/cards")
        r = conn.getresponse()
        data = r.read()
        assert r.status == 401
        err = _shape(data, "unauthorized")
        assert err["field"] is None
        assert r.getheader("WWW-Authenticate")
    finally:
        conn.close()


def test_a_403_host_and_origin_live(api):
    s, body = api.get("/v1/cards", headers={"Host": "evil.example"})
    assert s == 403 and body["error"]["code"] == "forbidden_host" and body["error"]["field"] is None
    s, body = api.get("/v1/cards", headers={"Origin": "http://evil.example"})
    assert s == 403 and body["error"]["code"] == "forbidden_origin" and body["error"]["field"] is None
    s, body = api.get("/v1/cards", headers={"Origin": "http://evil.example"}, token=False)
    assert s == 403 and body["error"]["code"] == "forbidden_origin"


def test_a_413_live_object_shape_message_unchanged(api):
    import socket
    request = (f"POST /v1/cards HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {api.token}\r\n"
               f"Content-Type: application/json\r\nContent-Length: {server_app.MAX_BODY_BYTES + 10}\r\n\r\n")
    with socket.create_connection((api.host, int(api.port)), timeout=10) as sock:
        sock.sendall(request.encode("ascii"))
        data = b""
        while b"\r\n\r\n" not in data:
            data += sock.recv(4096)
        head, _, rest = data.partition(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.1 413"), head
        assert b"connection: close" in head.lower()
        length = int(re.search(rb"content-length: (\d+)", head, re.I).group(1))
        while len(rest) < length:
            rest += sock.recv(4096)
    assert json.loads(rest) == {"error": {"code": "payload_too_large", "message": "payload too large", "field": None}}


def test_a_413_asgi_middleware_path_object_shape():
    app = server_app.create_app(lambda: TOKEN)
    status, headers, data = _asgi(app, "POST", "/v1/health", {"content-length": str(server_app.MAX_BODY_BYTES + 1)})
    assert status == 413
    err = _shape(data, "payload_too_large")
    assert err["message"] == "payload too large" and err["field"] is None
    assert headers.get("connection") == "close"


def test_a_router_404_and_405_live(api):
    s, body = api.get("/v1/no-such-route")
    assert s == 404 and body["error"]["code"] == "not_found" and body["error"]["field"] is None
    s, body = api.request("DELETE", "/v1/health")
    assert s == 405 and body["error"]["code"] == "method_not_allowed" and body["error"]["field"] is None


def test_a_405_keeps_allow_header():
    app = server_app.create_app(lambda: TOKEN)
    status, headers, data = _asgi(app, "POST", "/v1/health")
    assert status == 405
    _shape(data, "method_not_allowed")
    assert "GET" in headers.get("allow", "")


def test_a_unknown_card_id_404_is_also_object_shaped(api):
    s, body = api.get("/v1/cards/does-not-exist")
    assert s == 404 and isinstance(body["error"], dict) and body["error"]["code"]


def test_a_validation_error_field_is_null_or_named(api):
    s, body = api.post("/v1/cards", {"title": "x", "kind": "nonsense"})
    assert s == 422 and "field" in body["error"] and body["error"]["field"] == "kind"
    s, body = api.post("/v1/cards", raw=b"not json", headers={"Content-Type": "application/json"})
    assert s == 400 and "field" in body["error"]


# ---- (b) mcp_app mount -----------------------------------------------------------------------

def test_b_mcp_mount_with_token_is_200(mcp_app):
    status, _, data = _asgi(mcp_app, "GET", "/mcp/anything")
    assert (status, data) == (200, b"mcp-ok")


def test_b_mcp_mount_without_token_is_401(mcp_app):
    status, headers, data = _asgi(mcp_app, "GET", "/mcp/anything", token=False)
    assert status == 401
    _shape(data, "unauthorized")
    assert headers.get("www-authenticate")


def test_b_mcp_mount_bad_origin_is_403_and_bad_host_too(mcp_app):
    status, _, data = _asgi(mcp_app, "GET", "/mcp/x", {"origin": "http://evil.example"})
    assert status == 403
    _shape(data, "forbidden_origin")


def test_b_mcp_mount_oversize_body_is_413(mcp_app):
    status, _, data = _asgi(mcp_app, "POST", "/mcp/x", {"content-length": str(server_app.MAX_BODY_BYTES + 1)})
    assert status == 413


def test_b_without_mcp_app_no_mcp_route():
    app = server_app.create_app(lambda: TOKEN)
    status, _, data = _asgi(app, "GET", "/mcp/x")
    assert status == 404
    _shape(data, "not_found")


# ---- (c) control characters + token masking ---------------------------------------------------

def test_c_strip_control_unit():
    assert strip_control("a\u200bb\x07c\u202ed\x1be") == "abcde"
    assert strip_control("l1\n\tl2") == "l1\n\tl2"
    assert strip_control("caf\u00e9 \u0645") == "caf\u00e9 \u0645"


def test_c_live_title_detail_label_stripped(api, uniq):
    s, body = api.post("/v1/cards", {"title": f"{uniq}\u200b\x07T", "detail": "de\u202etail",
                                     "actions": [{"id": "a1", "label": "Go\u200b"}]})
    assert s in (200, 201), body
    try:
        assert body["title"] == f"{uniq}T"
        assert body["detail"] == "detail"
        assert body["actions"][0]["label"] == "Go"
    finally:
        api.request("DELETE", f"/v1/cards/{body['id']}")


def test_c_live_all_control_title_is_422(api):
    s, body = api.post("/v1/cards", {"title": "\u200b\x07\u202e"})
    assert s == 422
    assert body["error"]["field"] == "title"


def test_c_patch_title_and_detail_also_stripped(api, uniq):
    s, body = api.post("/v1/cards", {"title": uniq})
    cid = body["id"]
    try:
        s, patched = api.request("PATCH", f"/v1/cards/{cid}", {"title": uniq + "\u200bZ", "detail": "x\x07y"})
        assert s == 200
        assert patched["title"] == uniq + "Z" and patched["detail"] == "xy"
        s, patched = api.request("PATCH", f"/v1/cards/{cid}", {"title": "\u200b"})
        assert s == 422
    finally:
        api.request("DELETE", f"/v1/cards/{cid}")


def test_c_all_control_action_label_rejected():
    with pytest.raises(CardValidationError):
        validate_fields({"title": "t", "actions": [{"id": "a", "label": "\u200b\x07"}]}, require_title=True)


def _read_state(home):
    return json.loads((home / "state.json").read_text(encoding="utf-8"))


def test_c_permission_summary_masks_bearer_authorization_and_long_runs(api, assistant_home, uniq):
    longrun = "A1b2C3d4E5" * 5  # 50 chars, token-like
    cmd = (f"curl -H 'Authorization: Bearer abc.DEF-123' https://x.example/{uniq} "
           f"--data {longrun} -H \"Authorization: Basic dXNlcjpwYXNz\"")
    payload = json.loads((FIXTURES / "permission_request_bash.json").read_text(encoding="utf-8"))
    payload.update(session_id=str(uuid.uuid4()), cwd=rf"C:\Users\<user>\projects\{uniq}")
    payload["tool_input"] = {"command": cmd}
    s, _ = api.post(HOOK, payload)
    assert s == 200

    def find():
        s2, cards = api.get("/v1/cards")
        return [c for c in cards if uniq in (c.get("title") or "")]

    cards = wait_until(find, timeout=3)
    assert cards, "no permission card appeared"
    try:
        for c in cards:
            detail = c.get("detail") or ""
            assert "abc.DEF" not in detail and "abc.DEF-123" not in detail
            assert longrun not in detail
            assert "dXNlcjpwYXNz" not in detail
            assert "***" in detail
        time.sleep(0.6)
        raw = (assistant_home / "state.json").read_text(encoding="utf-8")
        wait_until(lambda: uniq in (assistant_home / "state.json").read_text(encoding="utf-8"), timeout=3)
        raw = (assistant_home / "state.json").read_text(encoding="utf-8")
        assert uniq in raw
        assert "abc.DEF" not in raw and longrun not in raw and "dXNlcjpwYXNz" not in raw
    finally:
        for c in cards:
            api.request("DELETE", f"/v1/cards/{c['id']}")


def test_c_masking_leaves_ordinary_text_alone():
    from assistant.cards.claude_sessions import _tool_summary
    assert _tool_summary({"command": "git status --short"}) == "git status --short"
    path = r"C:\Users\<user>\projects\my-project\src\a_rather_long_but_spaced path.py"
    assert _tool_summary({"file_path": path}) == path
    assert "bearer" not in _tool_summary({"command": "echo BEARER   tok123"}).lower().replace("bearer ***", "")
    # 39-char run is below the threshold and survives
    assert _tool_summary({"command": "x" * 39}) == "x" * 39
    assert _tool_summary({"command": "x" * 40}) == "***"


def test_c_text_strips_control_in_session_cards():
    from assistant.cards.claude_sessions import _text
    assert _text("pro\u200bject\x07", 50) == "project"


# ---- (d) expired vs dismissed ----------------------------------------------------------------

class _Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def _types(store, cid):
    return [e["type"] for e in store.events_since(cid, 0)]


def test_d_fade_records_expired(tmp_path):
    clock = _Clock()
    store = CardStore(tmp_path / "state.json", clock=clock)
    card = store.upsert(Card(id="c1", title="t", kind="done"), "http")
    store.mark_seen("c1")
    clock.t += FADE_SECONDS + 1
    store.tick()
    assert _types(store, "c1")[-1] == "expired"
    assert "dismissed" not in _types(store, "c1")


def test_d_expires_at_records_expired(tmp_path):
    clock = _Clock()
    store = CardStore(tmp_path / "state.json", clock=clock)
    store.upsert(Card(id="c1", title="t", kind="custom", expires_at=clock.t + 5), "http")
    clock.t += 6
    store.tick()
    assert _types(store, "c1")[-1] == "expired"


def test_d_user_dismiss_records_dismissed(tmp_path):
    clock = _Clock()
    store = CardStore(tmp_path / "state.json", clock=clock)
    store.upsert(Card(id="c1", title="t", kind="custom"), "http")
    store.dismiss("c1")
    assert _types(store, "c1")[-1] == "dismissed"
    assert "expired" not in _types(store, "c1")


def test_d_live_expires_at_then_delete(api, uniq):
    s, body = api.post("/v1/cards", {"title": uniq, "expires_at": time.time() + 1.5})
    assert s in (200, 201), body
    cid = body["id"]

    def types():
        s2, ev = api.get(f"/v1/cards/{cid}/events")
        return [e["type"] for e in ev] if s2 == 200 else []

    assert wait_until(lambda: "expired" in types(), timeout=6), types()
    assert "dismissed" not in types()

    s, body = api.post("/v1/cards", {"title": uniq + "b"})
    cid = body["id"]
    s, _ = api.request("DELETE", f"/v1/cards/{cid}")
    assert s in (200, 204)
    s, ev = api.get(f"/v1/cards/{cid}/events")
    assert [e["type"] for e in ev][-1] == "dismissed"
    assert "expired" not in [e["type"] for e in ev]


# ---- (e) send --id ----------------------------------------------------------------------------

def _send(assistant_env, *args):
    return subprocess.run([sys.executable, "-m", "assistant", "send", *args], cwd=REPO_ROOT, env=assistant_env,
                          capture_output=True, text=True, timeout=30)


def test_e_send_id_updates_in_place(api, assistant_env, uniq):
    cid = "m20-" + uuid.uuid4().hex[:8]
    try:
        r1 = _send(assistant_env, "--id", cid, "--title", uniq)
        assert r1.returncode == 0, (r1.stdout, r1.stderr)
        r2 = _send(assistant_env, "--id", cid, "--title", uniq + "-v2")
        assert r2.returncode == 0, (r2.stdout, r2.stderr)
        s, cards = api.get("/v1/cards")
        mine = [c for c in cards if c["id"] == cid]
        assert len(mine) == 1 and mine[0]["title"] == uniq + "-v2"
        assert len([c for c in cards if uniq in c["title"]]) == 1
    finally:
        api.request("DELETE", f"/v1/cards/{cid}")


def test_e_send_without_id_still_creates_distinct_cards(api, assistant_env, uniq):
    _send(assistant_env, "--title", uniq)
    _send(assistant_env, "--title", uniq)
    s, cards = api.get("/v1/cards")
    mine = [c for c in cards if c["title"] == uniq]
    try:
        assert len(mine) == 2
    finally:
        for c in mine:
            api.request("DELETE", f"/v1/cards/{c['id']}")


def test_e_send_parser_accepts_id_flag():
    from assistant.__main__ import _build_parser
    args = _build_parser().parse_args(["send", "--id", "abc", "--title", "t"])
    assert args.id == "abc"
    assert _build_parser().parse_args(["send", "--title", "t"]).id is None


# ---- (f) _KNOWN_FILES --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["state_ui.json.tmp", "state_ui.json", "state.json.tmp"])
def test_f_known_files_accepts(name):
    assert paths._KNOWN_FILES.fullmatch(name)


def test_f_known_files_still_rejects_strangers():
    assert not paths._KNOWN_FILES.fullmatch("state_ui.json.tmp.evil")
    assert not paths._KNOWN_FILES.fullmatch("other.tmp")


def test_f_home_with_state_ui_tmp_counts_as_known_layout(tmp_path, caplog, monkeypatch):
    home = tmp_path / "h"
    home.mkdir()
    (home / "state_ui.json.tmp").write_text("{}", encoding="utf-8")
    (home / "state_ui.json").write_text("{}", encoding="utf-8")
    assert paths._known_layout_only(home)
    (home / "stranger.txt").write_text("x", encoding="utf-8")
    assert not paths._known_layout_only(home)
    (home / "stranger.txt").unlink()
    monkeypatch.setenv("ASSISTANT_HOME", str(home))
    paths._fresh_roots.discard(home)
    import logging
    with caplog.at_level(logging.DEBUG, logger="assistant.paths"):
        paths.secure_home()
    assert not [r for r in caplog.records if "not restricting" in r.getMessage()]


# ---- (g) pyproject ----------------------------------------------------------------------------

def test_g_pyproject_pins_starlette_and_uvicorn_to_installed_versions():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"starlette==1.10.0"' in text and '"uvicorn==0.54.0"' in text
    assert '"mcp==' in text and '"pywin32==' in text
    import importlib.metadata as md
    assert md.version("starlette") == "1.10.0" and md.version("uvicorn") == "0.54.0"


def test_g_pip_check_is_clean():
    r = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


# ---- (h) uninstall.ps1 ------------------------------------------------------------------------

UNINSTALL = REPO_ROOT / "uninstall.ps1"


def test_h_uninstall_parses():
    cmd = ("$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
           f"'{UNINSTALL}',[ref]$null,[ref]$e); if($e.Count){{$e | % {{$_.Message}}; exit 1}}")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_h_uninstall_prompt_pip_line_and_image_check():
    text = UNINSTALL.read_text(encoding="utf-8")
    assert ("(settings, token, card state, reminders, hooks-backup, logs; "
            "index and plugins once they exist)? [y/N]") in text
    assert "pip uninstall" in text
    # printed, not run: no bare invocation of pip uninstall outside a Write-Host
    for line in text.splitlines():
        if "pip uninstall" in line:
            assert line.lstrip().startswith("Write-Host"), line
    stop = text.index("Stop-Process")
    assert text.rindex("Get-Process -Id", 0, stop) < stop
    assert "'python', 'pythonw'" in text[:stop + 200]
    assert text.count("Stop-Process") == 1
