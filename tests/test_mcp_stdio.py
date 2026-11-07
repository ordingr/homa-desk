"""The stdio shim for Claude Desktop (plan 7.2 M2-2; AC-MC-2).

``python -m assistant.mcp_stdio`` is started as a real subprocess exactly as Claude Desktop would, pointed at an
in-process server (conftest ``mcp_live``) through ASSISTANT_HOME. Besides raw JSON-RPC lines, the ``mcp``
package's own stdio client is used, since that is the closest stand-in for Claude Desktop's behaviour.
"""
import asyncio
import json
import sys

import pytest

from conftest import REPO_ROOT, run_stdio_shim, wait_until

pytest.importorskip("assistant.mcp_stdio", reason="assistant/mcp_stdio.py (M2-2) not present")

TOOLS = {"send_card", "update_card", "dismiss_card", "add_reminder"}


def rpc(id_, method, params=None):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


INIT = rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "qa", "version": "0"}})
INITIALIZED = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})


def parse(lines):
    out = []
    for line in lines:
        out.append(json.loads(line))  # every stdout line must be a JSON document, nothing else
    return out


def test_initialize_is_answered_with_the_assistant_server_info(mcp_live):
    lines, err = run_stdio_shim(mcp_live.home, [INIT])
    (reply,) = parse(lines)
    assert reply["id"] == 1 and "result" in reply, (reply, err)
    assert reply["result"]["serverInfo"]["name"] == "assistant"


def test_notification_produces_no_output_and_tools_list_returns_the_four_tools(mcp_live):
    lines, _ = run_stdio_shim(mcp_live.home, [INIT, INITIALIZED, rpc(2, "tools/list")])
    replies = parse(lines)
    assert [r["id"] for r in replies] == [1, 2], replies
    assert {t["name"] for t in replies[1]["result"]["tools"]} == TOOLS


def test_tools_call_is_forwarded_and_the_card_appears_in_the_app(mcp_live):
    call = rpc(3, "tools/call", {"name": "send_card", "arguments": {"title": "via shim", "id": "shim-card"}})
    lines, _ = run_stdio_shim(mcp_live.home, [INIT, INITIALIZED, call])
    replies = {r["id"]: r for r in parse(lines)}
    assert not replies[3]["result"].get("isError"), replies[3]
    assert wait_until(lambda: "shim-card" in [c.id for c in mcp_live.store.list()], timeout=2)
    assert mcp_live.store.get("shim-card").source == "mcp"


def test_a_tool_error_comes_back_as_a_result_with_iserror_not_a_dead_shim(mcp_live):
    call = rpc(4, "tools/call", {"name": "add_reminder", "arguments": {"text": "nonsense"}})
    lines, _ = run_stdio_shim(mcp_live.home, [INIT, call])
    replies = {r["id"]: r for r in parse(lines)}
    assert replies[4]["result"]["isError"] is True


def test_garbage_input_gets_a_json_rpc_error_and_the_shim_keeps_serving(mcp_live):
    lines, _ = run_stdio_shim(mcp_live.home, ["this is not json", rpc(5, "tools/list")], expect=2)
    replies = parse(lines)
    assert "error" in replies[0] and replies[0].get("id") is None
    assert replies[1]["id"] == 5 and "result" in replies[1]


def test_app_not_running_gives_an_error_with_the_request_id_not_a_crash_or_hang(tmp_path):
    import secrets
    home = tmp_path / "ghost"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"schema": 1, "port": 1999}), encoding="utf-8")
    (home / "token").write_text(secrets.token_urlsafe(32), encoding="ascii")
    lines, err = run_stdio_shim(home, [rpc(7, "tools/list")], timeout=15)
    (reply,) = parse(lines)
    assert reply["id"] == 7 and "error" in reply, (reply, err)
    assert "not running" in reply["error"]["message"].lower() or "port" in reply["error"]["message"].lower()


def test_missing_token_gives_an_error_naming_the_fix(tmp_path):
    home = tmp_path / "notoken"
    home.mkdir()
    lines, _ = run_stdio_shim(home, [rpc(8, "tools/list")], timeout=15)
    (reply,) = parse(lines)
    assert reply["id"] == 8 and "token" in reply["error"]["message"].lower()


def test_the_mcp_stdio_client_can_list_and_call_through_the_shim(mcp_live):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "assistant.mcp_stdio"],
                                       env={"ASSISTANT_HOME": str(mcp_live.home)}, cwd=str(REPO_ROOT))
        async with Client(params) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            result = await client.call_tool("send_card", {"title": "via stdio client", "id": "stdio-client-card"})
            return tools, result

    tools, result = asyncio.run(asyncio.wait_for(go(), 60))
    assert tools == TOOLS and not result.is_error
    assert "stdio-client-card" in [c.id for c in mcp_live.store.list()]
