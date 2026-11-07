"""MCP file tools over the one /mcp mount (plan 7.3 M3a-5 + 17.4 item 1; AC-MC-3, AC-MC-4, AC-MC-5, AC-MC-7, SEC-3, SEC-4).

Full private app (conftest.search_app_protected: the 'show protected files by name' setting is ON, which must make no
difference over MCP). Tool argument names follow PRD AC-MC-3. Result shapes are read tolerantly: any list of dicts that
carry ``path`` counts as rows; refusals are an error result or a payload whose text names a reason.
"""
import json
import os

import pytest

from conftest import BULK_COUNT

pytest.importorskip("assistant.server.mcp", reason="assistant/server/mcp.py not present")

FILE_TOOLS = {"search_files", "get_file_info", "read_text_preview", "list_indexed_roots"}
CARD_TOOLS = {"send_card", "update_card", "dismiss_card", "add_reminder"}
DENIED_NAMES = [".env", ".env.local", "id_rsa", "id_ed25519", "vault.kdbx", "server.pem", "credentials"]


@pytest.fixture(scope="module")
def app(search_app_protected):
    return search_app_protected


def payload(result):
    """Everything the tool returned as one structure."""
    if result.structured is not None:
        return result.structured
    try:
        return json.loads(result.text)
    except ValueError:
        return result.text


def find_rows(data):
    if isinstance(data, list):
        if data and all(isinstance(x, dict) and "path" in x for x in data):
            return data
        for x in data:
            found = find_rows(x)
            if found:
                return found
    elif isinstance(data, dict):
        for v in data.values():
            found = find_rows(v)
            if found:
                return found
    return []


def rows_of(result):
    assert not result.is_error, result.text
    return find_rows(payload(result))


def refused(result) -> bool:
    """True when the tool said no: an error result, or a reason in the payload."""
    if result.is_error:
        return True
    text = (result.text + json.dumps(payload(result), default=str)).lower()
    return any(w in text for w in ("refus", "denied", "protected", "not allowed", "outside", "not in", "binary",
                                   "placeholder", "not downloaded", "error", "unavailable"))


def leaked(result, needle: str) -> bool:
    return needle.lower() in (result.text + json.dumps(payload(result), default=str)).lower()


# ---- tools/list -------------------------------------------------------------------------------------------------------------

def test_tools_list_has_the_four_file_tools_read_only_and_not_open_world(app):
    tools = {t.name: t for t in app.mcp.list_tools()}
    assert FILE_TOOLS | CARD_TOOLS <= set(tools)
    for name in FILE_TOOLS:
        ann = tools[name].annotations
        assert ann is not None, name
        assert ann.read_only_hint is True and ann.open_world_hint is False, name
        assert ann.destructive_hint in (False, None), name


def test_no_tool_can_open_reveal_rename_delete_or_execute(app):
    names = {t.name.lower() for t in app.mcp.list_tools()}
    assert not [n for n in names if any(w in n for w in ("open", "reveal", "rename", "delete", "remove", "exec", "run", "write", "move"))]


# ---- search_files -----------------------------------------------------------------------------------------------------------------

def test_search_files_uses_the_files_mode_grammar_and_returns_compact_rows(app):
    rows = rows_of(app.mcp.call("search_files", {"query": "design md"}))
    assert [r["name"] for r in rows] == ["designer.md", "x.md"], "the name hit first, then the file under docs/design"
    row = rows[0]
    assert row["path"].lower().startswith(str(app.root).lower()) and row["size"] == 800
    assert {"path", "name", "size"} <= set(row) and ("modified" in row or "mtime" in row)
    assert "config.yml" in [r["name"] for r in rows_of(app.mcp.call("search_files", {"query": "config -e yml"}))]


def test_search_files_default_limit_is_50_and_500_is_the_ceiling(app):
    assert len(rows_of(app.mcp.call("search_files", {"query": "item_"}))) == 50
    assert len(rows_of(app.mcp.call("search_files", {"query": "item_", "limit": 500}))) == min(500, BULK_COUNT)
    over = app.mcp.call("search_files", {"query": "item_", "limit": 501})
    assert over.is_error or len(find_rows(payload(over))) <= 500, "limit > 500 must be refused or clamped, never honoured"
    small = rows_of(app.mcp.call("search_files", {"query": "item_", "limit": 5, "offset": 10}))
    first = rows_of(app.mcp.call("search_files", {"query": "item_", "limit": 15}))
    assert [r["path"] for r in small] == [r["path"] for r in first][10:15]


def test_search_files_filters_and_sort(app):
    rows = rows_of(app.mcp.call("search_files", {"query": "design", "filters": {"folders_only": True}}))
    assert sorted(r["name"] for r in rows) == ["Design", "design"]
    rows = rows_of(app.mcp.call("search_files", {"query": "design", "filters": {"ext": ["md", "html"]}}))
    assert sorted(r["name"] for r in rows) == ["design-system.html", "designer.md", "stepper-designs.html", "x.md"]
    rows = rows_of(app.mcp.call("search_files", {"query": "item_", "limit": 10, "sort": "name"}))
    names = [r["name"] for r in rows]
    assert names == sorted(names)
    rows = rows_of(app.mcp.call("search_files", {"query": "design", "filters": {"modified_after": "2025-01-01"}}))
    assert "designer.md" not in [r["name"] for r in rows] and "stepper-designs.html" in [r["name"] for r in rows]


@pytest.mark.parametrize("name", DENIED_NAMES)
def test_deny_listed_names_appear_in_no_result_even_with_show_protected_on(app, name):
    for q in (name, name.lstrip("."), "*"):
        res = app.mcp.call("search_files", {"query": q, "limit": 500})
        paths = [r["path"].lower() for r in find_rows(payload(res))]
        assert not [p for p in paths if p.endswith("\\" + name.lower())], (q, name)


def test_the_same_protected_name_is_listed_over_http_when_the_setting_is_on(app):
    """Contrast: the local UI route lists it (setting on), MCP never does."""
    status, body = app.search("id_rsa")
    if status != 200:
        pytest.skip("GET /v1/search not available")
    assert "id_rsa" in [r["name"] for r in body["rows"]]
    assert not [r for r in find_rows(payload(app.mcp.call("search_files", {"query": "id_rsa"}))) if r["name"] == "id_rsa"]


def test_excluded_material_is_not_searchable_over_mcp(app):
    for q in ("probe_nm", "recycled", "disk2"):
        assert rows_of(app.mcp.call("search_files", {"query": q})) == [], q
    assert rows_of(app.mcp.call("search_files", {"query": "secret.txt"})) == [], "the junction out of the root is not followed"


# ---- get_file_info --------------------------------------------------------------------------------------------------------------------

def test_get_file_info_returns_size_and_mtime_for_indexed_files(app):
    p = str(app.root / "designer.md")
    res = app.mcp.call("get_file_info", {"paths": [p, str(app.root / "docs")]})
    assert not res.is_error, res.text
    text = json.dumps(payload(res), default=str)
    assert "800" in text and "designer.md" in text


def test_get_file_info_refuses_denied_outside_and_oversized_requests(app):
    for bad in (str(app.root / ".env"), str(app.root / "id_rsa"), str(app.root / ".aws" / "credentials"), "C:\\Windows\\win.ini"):
        res = app.mcp.call("get_file_info", {"paths": [bad]})
        info = json.dumps(payload(res), default=str) + res.text
        assert refused(res) or "size" not in info, bad
    too_many = [str(app.root / "designer.md")] * 21
    assert app.mcp.call("get_file_info", {"paths": too_many}).is_error, "paths[] is capped at 20"
    ok20 = app.mcp.call("get_file_info", {"paths": [str(app.root / "designer.md")] * 20})
    assert not ok20.is_error, ok20.text


# ---- read_text_preview ------------------------------------------------------------------------------------------------------------------

def preview(app, rel_or_abs, **kw):
    path = rel_or_abs if os.path.isabs(rel_or_abs) else str(app.root / rel_or_abs)
    return app.mcp.call("read_text_preview", {"path": path, **kw})


def test_text_file_returns_content_inside_an_untrusted_delimiter_with_max_bytes_honoured(app):
    res = preview(app, "docs/readme.txt", max_bytes=1000)
    assert not res.is_error, res.text
    text = res.text or json.dumps(payload(res))
    low = text.lower()
    assert "untrusted" in low, "content must be wrapped in a labelled 'untrusted file content' delimiter"
    assert low.index("untrusted") < text.index("line 0001"), "the label comes before the content"
    assert "line 0001" in text and "line 1400" not in text
    body_bytes = len(text.encode("utf-8"))
    assert body_bytes < 1000 + 600, f"1000-byte cap plus delimiter overhead, got {body_bytes}"
    default = preview(app, "docs/readme.txt")
    assert 10_000 < len(default.text.encode("utf-8")) < 17_500, "default max_bytes is 16 KB"
    big = preview(app, "docs/readme.txt", max_bytes=1_000_000)
    assert big.is_error or len(big.text.encode("utf-8")) < 66_000, "max_bytes is capped at 64 KB"


def test_max_lines_is_honoured(app):
    res = preview(app, "docs/readme.txt", max_lines=3)
    assert not res.is_error and "line 0003" in res.text and "line 0004" not in res.text


def test_hostile_file_content_is_returned_inside_the_label_not_obeyed(app):
    res = preview(app, "docs/hostile-note.txt")
    assert not res.is_error, res.text
    assert "IGNORE ALL PREVIOUS" in res.text and "untrusted" in res.text.lower()
    assert res.text.lower().index("untrusted") < res.text.index("IGNORE ALL PREVIOUS")


@pytest.mark.parametrize("rel,why", [
    ("tools/setup.exe", "executable"),
    ("data/binary.txt", "binary (NUL bytes, content sniff, not extension)"),
    (".env", "deny-listed name"),
    ("id_rsa", "deny-listed name"),
    ("secrets/server.pem", "deny-listed name"),
    (".aws/credentials", "deny-listed name"),
    ("vault.kdbx", "deny-listed name"),
    ("cloud/cloud-doc.docx", "OFFLINE placeholder"),
    ("cloud/cloud-note.md", "OFFLINE placeholder"),
    ("notes/keybackup.txt", "private key content in an innocent name"),
])
def test_preview_refuses_with_a_reason_and_leaks_no_content(app, rel, why):
    res = preview(app, rel)
    assert refused(res), f"{rel} ({why}) must be refused; got {res.text[:120]!r}"
    assert not leaked(res, "BEGIN RSA PRIVATE KEY") and not leaked(res, "MIIBOgIBAAJBAKj34")
    assert not leaked(res, "not-a-real-token") and not leaked(res, "This program cannot be run")
    assert res.text.strip() or res.is_error, "a refusal must say something"


def test_offline_placeholder_preview_does_not_recall_the_file(app):
    import ctypes
    target = str(app.root / "cloud" / "cloud-doc.docx")
    before = ctypes.windll.kernel32.GetFileAttributesW(target)
    preview(app, "cloud/cloud-doc.docx")
    assert ctypes.windll.kernel32.GetFileAttributesW(target) == before and before & 0x1000, "still OFFLINE afterwards"


# ---- AC-MC-5: every tool resolves the path first ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("tool,args", [
    ("read_text_preview", lambda app: {"path": "C:\\Windows\\win.ini"}),
    ("read_text_preview", lambda app: {"path": str(app.root / "docs" / ".." / ".." / (app.root.name + "_outside") / "secret.txt")}),
    ("read_text_preview", lambda app: {"path": str(app.root / "escape" / "secret.txt")}),
    ("get_file_info", lambda app: {"paths": [str(app.root / "escape" / "secret.txt")]}),
    ("get_file_info", lambda app: {"paths": ["C:\\Windows\\win.ini"]}),
    ("read_text_preview", lambda app: {"path": str(app.root / "linkdir" / "pkg" / "probe_nm.txt")}),
])
def test_paths_outside_the_roots_junction_escapes_and_excluded_trees_are_refused(app, tool, args):
    res = app.mcp.call(tool, args(app))
    assert refused(res), (tool, args(app), res.text[:150])
    assert not leaked(res, "outside the root") and not leaked(res, "win.ini]") and not leaked(res, "[fonts]")


def test_a_junction_inside_the_root_resolves_to_its_target(app):
    res = preview(app, "docs_link/readme.txt", max_bytes=200)
    assert not res.is_error and "line 0001" in res.text


def test_missing_and_malformed_paths_are_errors_not_crashes(app):
    for bad in (str(app.root / "no-such-file.txt"), "", "\x00", "relative\\path.txt", "\\\\192.0.2.1\\share\\x.txt"):
        res = app.mcp.call("read_text_preview", {"path": bad})
        assert refused(res), repr(bad)
    assert app.mcp.call("search_files", {"query": "design"}).is_error is False, "server still healthy"


# ---- list_indexed_roots, logging ---------------------------------------------------------------------------------------------------------------

def test_list_indexed_roots(app):
    res = app.mcp.call("list_indexed_roots", {})
    assert not res.is_error, res.text
    assert str(app.root).lower() in (res.text + json.dumps(payload(res), default=str)).lower().replace("\\\\", "\\")


def test_mcp_log_records_calls_by_shape_only_never_queries_or_paths(app):
    app.mcp.call("search_files", {"query": "zebra-distinct-query-9137"})
    app.mcp.call("read_text_preview", {"path": str(app.root / "docs" / "readme.txt")})
    status, body = app.api.get("/v1/mcp/log?lines=200")
    assert status == 200
    blob = json.dumps(body)
    assert "search_files" in blob and "read_text_preview" in blob
    assert "zebra-distinct-query-9137" not in blob and "readme.txt" not in blob and str(app.root).replace("\\", "\\\\") not in blob


def test_mcp_requires_the_token(app):
    from conftest import McpHttp
    try:
        res = McpHttp(app.base_url, "wrong-token").call("search_files", {"query": "design"})
    except BaseException as exc:  # the client refuses at initialize; the exception is the refusal
        assert not isinstance(exc, (KeyboardInterrupt, SystemExit))
        return
    assert res.is_error
