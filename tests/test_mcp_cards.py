"""MCP card and reminder tools over Streamable HTTP (plan 7.2 M2-2; AC-MC-1, AC-MC-6, AC-MC-8, AC-MC-9, AC-RM-7, SEC-1).

Uses the ``mcp`` package's own client against a real uvicorn server running ``create_app(..., mcp_app=...)``
in this process (conftest ``mcp_live``), with a private ASSISTANT_HOME. The final test additionally checks the
full app once M2-4 mounts /mcp there.
"""
import json
import time

import pytest

from conftest import Api, McpHttp, wait_until

pytest.importorskip("assistant.server.mcp", reason="assistant/server/mcp.py (M2-2) not present")

TOOLS = {"send_card", "update_card", "dismiss_card", "add_reminder"}
HELP = "didn't understand"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "qa", "version": "0"}}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def open_ids(live):
    return [c.id for c in live.store.list(open_only=True)]


# ---- tools/list, send/update/dismiss ---------------------------------------------------------------

def test_tools_list_is_exactly_the_four_card_and_reminder_tools(mcp_live):
    tools = mcp_live.mcp().list_tools()
    assert {t.name for t in tools} == TOOLS and len(tools) == 4


def test_send_card_shows_a_card_with_source_mcp_and_returns_its_id(mcp_live):
    result = mcp_live.mcp().call("send_card", {"title": "from claude", "detail": "details", "kind": "done", "priority": 2})
    assert not result.is_error, result.text
    card_id = result.card_id()
    assert card_id in open_ids(mcp_live)
    card = mcp_live.store.get(card_id)
    assert (card.title, card.detail, card.kind, card.priority, card.source) == ("from claude", "details", "done", 2, "mcp")
    status, body = mcp_live.api().get(f"/v1/cards/{card_id}")
    assert status == 200 and body["title"] == "from claude"


def test_send_card_with_an_existing_id_updates_in_place_instead_of_duplicating(mcp_live):
    first = mcp_live.mcp().call("send_card", {"title": "v1", "id": "mcp-fixed"})
    second = mcp_live.mcp().call("send_card", {"title": "v2", "id": "mcp-fixed"})
    assert first.card_id() == second.card_id() == "mcp-fixed"
    assert open_ids(mcp_live).count("mcp-fixed") == 1
    assert mcp_live.store.get("mcp-fixed").title == "v2"


def test_update_card_changes_only_the_given_fields_and_dismiss_card_closes_it(mcp_live):
    card_id = mcp_live.mcp().call("send_card", {"title": "before", "detail": "keep me", "kind": "waiting"}).card_id()
    assert not mcp_live.mcp().call("update_card", {"id": card_id, "title": "after"}).is_error
    card = mcp_live.store.get(card_id)
    assert (card.title, card.detail, card.kind) == ("after", "keep me", "waiting")
    assert not mcp_live.mcp().call("dismiss_card", {"id": card_id}).is_error
    assert card_id not in open_ids(mcp_live)
    again = mcp_live.mcp().call("dismiss_card", {"id": card_id})
    assert not again.is_error, "dismissing a dismissed card is not an error"


def test_invalid_input_and_unknown_ids_are_tool_errors_that_name_the_field(mcp_live):
    bad = mcp_live.mcp().call("send_card", {"title": "x", "priority": 9})
    assert bad.is_error and "priority" in bad.text
    long = mcp_live.mcp().call("send_card", {"title": "x", "detail": "d" * 501})
    assert long.is_error and "detail" in long.text
    assert mcp_live.mcp().call("update_card", {"id": "no-such-card", "title": "t"}).is_error
    assert mcp_live.mcp().call("dismiss_card", {"id": "no-such-card"}).is_error


# ---- auth, Host, Origin, size ------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/mcp/", "/mcp"])
def test_no_token_or_wrong_token_is_401_and_creates_nothing(mcp_live, path):
    api = mcp_live.api()
    before = open_ids(mcp_live)
    assert api.request("POST", path, INIT, token=False, headers=MCP_HEADERS)[0] == 401
    assert api.request("POST", path, INIT, token="x" * 43, headers=MCP_HEADERS)[0] == 401
    with pytest.raises(Exception):
        McpHttp(mcp_live.base_url, "").list_tools()
    assert open_ids(mcp_live) == before


def test_non_loopback_origin_and_host_are_403_even_with_a_valid_token(mcp_live):
    api = mcp_live.api()
    assert api.request("POST", "/mcp/", INIT, headers={**MCP_HEADERS, "Origin": "http://evil"})[0] == 403
    assert api.request("POST", "/mcp/", INIT, headers={**MCP_HEADERS, "Host": "evil.test"})[0] == 403
    assert api.request("POST", "/mcp/", INIT, headers={**MCP_HEADERS, "Origin": "http://localhost:3000"})[0] == 200, \
        "a loopback Origin is allowed"
    with pytest.raises(Exception):
        mcp_live.mcp(headers={"Origin": "http://evil"}).list_tools()


def test_the_no_slash_path_works_through_the_redirect_for_a_real_client(mcp_live):
    status, _ = mcp_live.api().request("POST", "/mcp", INIT, headers=MCP_HEADERS)
    assert status in (200, 307, 308)
    assert {t.name for t in McpHttp(mcp_live.base_url, mcp_live.token).list_tools()} == TOOLS


def test_oversize_declared_body_is_413_not_a_crash(mcp_live):
    import socket
    crlf = chr(13) + chr(10)
    request = crlf.join([
        "POST /mcp/ HTTP/1.1", f"Host: 127.0.0.1:{mcp_live.port}", f"Authorization: Bearer {mcp_live.token}",
        "Content-Type: application/json", "Accept: application/json, text/event-stream",
        "Content-Length: 2000000", "", ""]).encode("ascii")
    with socket.create_connection(("127.0.0.1", mcp_live.port), timeout=10) as sock:
        sock.sendall(request)
        chunks = []
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
        reply = b"".join(chunks).decode("latin-1")
    assert reply.startswith("HTTP/1.1 413"), reply[:80]
    assert "payload_too_large" in reply
    assert mcp_live.api().get("/v1/health")[0] == 200


# ---- add_reminder ---------------------------------------------------------------------------------------

def test_add_reminder_returns_the_interpretation_and_arms_the_reminder(mcp_live):
    result = mcp_live.mcp().call("add_reminder", {"text": "qa mcp reminder in 10m"})
    assert not result.is_error, result.text
    data = result.structured.get("result", result.structured) if isinstance(result.structured, dict) else json.loads(result.text)
    assert "Reminder 'qa mcp reminder' at " in data["interpretation"]
    assert data["id"] in [r.id for r in mcp_live.reminders.list()]
    mcp_live.reminders.cancel(data["id"])


def test_add_reminder_nonsense_returns_the_same_didnt_understand_error_as_http(mcp_live):
    result = mcp_live.mcp().call("add_reminder", {"text": "nonsense"})
    status, body = mcp_live.api().post("/v1/reminders", {"text": "nonsense"})
    assert result.is_error and HELP in result.text
    assert status in (400, 422) and HELP in json.dumps(body["error"]["message"]).lower().replace("’", "'")
    assert mcp_live.reminders.list() == [] or all("nonsense" not in r.title for r in mcp_live.reminders.list())


# ---- mcp.log and /v1/mcp/log (AC-MC-8) ------------------------------------------------------------------

def log_lines(live):
    path = live.home / "logs" / "mcp.log"
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def test_each_tool_call_writes_exactly_one_log_line_without_the_title(mcp_live):
    marker = "PRIVATE-TITLE-93471"
    before = len([l for l in log_lines(mcp_live) if "tool=send_card" in l])
    result = mcp_live.mcp().call("send_card", {"title": marker, "detail": marker})
    assert not result.is_error
    assert wait_until(lambda: len([l for l in log_lines(mcp_live) if "tool=send_card" in l]) == before + 1, timeout=3)
    text = "\n".join(log_lines(mcp_live))
    assert marker not in text, "titles and details must not be logged (AC-MC-8: arguments with path only)"
    line = [l for l in log_lines(mcp_live) if "tool=send_card" in l][-1]
    assert "result_bytes=" in line and line.split()[0][:4].isdigit(), line


def test_log_endpoint_returns_recent_lines_and_needs_the_token(mcp_live):
    mcp_live.mcp().call("dismiss_card", {"id": "nope"})
    api = mcp_live.api()
    assert api.get("/v1/mcp/log", token=False)[0] == 401
    status, body = api.get("/v1/mcp/log?lines=5")
    assert status == 200 and 1 <= len(body["lines"]) <= 5
    assert any("dismiss_card" in l for l in body["lines"])


def test_health_has_the_m2_fields(mcp_live):
    status, body = mcp_live.api().get("/v1/health")
    assert status == 200
    assert isinstance(body["uptime_s"], int) and body["uptime_s"] >= 0
    assert body["index"] is None and body["plugins"] == [] and isinstance(body["calendars"], list)


# ---- token regeneration (AC-MC-9) ------------------------------------------------------------------------

@pytest.fixture
def fake_user_profile(tmp_path, monkeypatch):
    """Point Path.home() at a scratch folder so hook re-installation can never touch the real ~/.claude."""
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setenv("HOME", str(profile))
    from assistant import hooks_installer
    assert str(hooks_installer.settings_path()).startswith(str(profile))
    return profile


def test_regenerating_the_token_makes_the_old_one_401_immediately_everywhere(mcp_live, fake_user_profile):
    from assistant.security import token
    old = mcp_live.token
    assert mcp_live.mcp().call("send_card", {"title": "pre-regen", "id": "regen-a"}).card_id() == "regen-a"
    result = token.regenerate()
    new = result.token
    assert new != old and mcp_live.token == new
    assert result.hooks == "not installed"
    assert not (fake_user_profile / ".claude" / "settings.json").exists(), "no hooks installed: no settings file may appear"
    api = Api(mcp_live.base_url, old)
    assert api.request("POST", "/mcp/", INIT, headers=MCP_HEADERS)[0] == 401
    assert api.get("/v1/health")[0] == 401
    with pytest.raises(Exception):
        McpHttp(mcp_live.base_url, old).list_tools()
    assert {t.name for t in McpHttp(mcp_live.base_url, new).list_tools()} == TOOLS


def test_regenerating_with_hooks_installed_rewrites_them_with_the_new_token(mcp_live, fake_user_profile):
    from assistant import hooks_installer
    from assistant.security import token
    assert hooks_installer.install() == 0
    settings_file = hooks_installer.settings_path()
    old = mcp_live.token
    assert old in settings_file.read_text(encoding="utf-8"), "hook entries embed the token"
    result = token.regenerate()
    assert result.hooks == "updated"
    text = settings_file.read_text(encoding="utf-8")
    assert result.token in text and old not in text
    assert hooks_installer.remove() == 0


def test_regenerating_with_a_malformed_claude_settings_file_reports_failure_and_leaves_it_alone(mcp_live, fake_user_profile):
    from assistant import hooks_installer
    from assistant.security import token
    target = hooks_installer.settings_path()
    target.parent.mkdir(parents=True)
    target.write_bytes(b"{ not json")
    result = token.regenerate()
    assert result.hooks.startswith("failed") or result.hooks == "not installed", result.hooks
    assert target.read_bytes() == b"{ not json"
    assert result.token == mcp_live.token


# ---- the full application ---------------------------------------------------------------------------------

def test_running_app_serves_the_same_four_tools_once_m2_4_has_mounted_mcp(running_app):
    base, token = running_app
    status, _ = Api(base, token).request("POST", "/mcp/", INIT, headers=MCP_HEADERS)
    if status == 404:
        pytest.skip("the full app does not mount /mcp yet (M2-4 wiring in assistant/app.py)")
    assert status == 200
    assert {t.name for t in McpHttp(base, token).list_tools()} == TOOLS
