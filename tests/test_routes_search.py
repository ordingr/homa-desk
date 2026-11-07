"""HTTP search routes (plan 7.3 M3a-5; PRD section 8: GET /v1/search, GET /v1/roots; SEC-1, SEC-3).

Runs against a full private app (conftest.search_app) whose one root is the fixture tree. Skips until the index
manager is wired (M3a-7) and the route exists (M3a-5). Query-string encodings the plan leaves open are assumed:
``chips`` is a comma-separated list, ``modified`` is today|7d|30d, ``sort`` is best|name|ext|date.
"""
import pytest

from conftest import BULK_COUNT, row_names, row_paths, walk_store

query = pytest.importorskip("assistant.index.query", reason="assistant/index/query.py (M3a-3) not present")
search = pytest.importorskip("assistant.index.search", reason="assistant/index/search.py (M3a-3) not present")
KNOWN = {"md", "py", "html", "txt", "yml", "pdf", "docx", "xlsx", "csv", "png", "json", "js", "ts"}
DOT = "·"


def ok(app, q, **params):
    status, body = app.search(q, **params)
    assert status == 200, (status, body)
    return body


@pytest.fixture(scope="module")
def local(search_app):
    """The in-process answer for the same tree: pruned like the default excludes."""
    return walk_store(search_app.root)


def local_paths(store, text, **kw):
    return [p.lower() for p in row_paths(search.run(query.parse(text, KNOWN), store, **kw).rows)]


@pytest.mark.parametrize("q", ["design md", "design doc", "config -e yml", "report -p Documents", "design", "notes",
                               '"md"', "-x foo", "plan !draft", "ext:md,py", "*.yml"])
def test_search_equals_the_in_process_result(search_app, local, q):
    body = ok(search_app, q)
    expected = search.run(query.parse(q, KNOWN), local)
    assert [p.lower() for p in row_paths(body["rows"])] == [p.lower() for p in row_paths(expected.rows)], q
    assert body["total"] == expected.total
    assert body["interpretation"] == expected.interpretation


def test_response_carries_the_documented_fields(search_app):
    body = ok(search_app, "design md")
    assert set(body) >= {"rows", "total", "elapsed_ms", "interpretation", "built_at"}
    assert body["interpretation"] == f'name contains "design" {DOT} ext: md'
    assert isinstance(body["elapsed_ms"], (int, float)) and body["built_at"] > 0
    row = body["rows"][0]
    assert {"name", "path", "size", "mtime", "is_dir"} <= set(row)
    assert row["name"] == "designer.md" and row["is_dir"] is False


def test_chips_modified_sort_limit_offset_are_applied(search_app):
    assert sorted(row_names(ok(search_app, "design", chips="folders")["rows"])) == ["Design", "design"]
    both = ok(search_app, "design", chips="docs,folders")
    assert "designer.md" in row_names(both["rows"]) and "Folders" in both["interpretation"]
    recent = row_names(ok(search_app, "design", modified="7d")["rows"])
    assert "stepper-designs.html" in recent and "designer.md" not in recent
    by_name = row_names(ok(search_app, "item_", sort="name", limit=20)["rows"])
    assert by_name == sorted(by_name)
    page = ok(search_app, "item_", limit=50, offset=0)
    assert page["total"] == BULK_COUNT and len(page["rows"]) == 50
    assert len(ok(search_app, "item_", limit=50, offset=590)["rows"]) == 10


def test_limit_is_capped(search_app):
    status, body = search_app.search("item_", limit=100000)
    assert status in (200, 422)
    if status == 200:
        assert len(body["rows"]) <= 10000


@pytest.mark.parametrize("params", [{"limit": "abc"}, {"limit": -1}, {"offset": -5}, {"sort": "bogus"},
                                    {"chips": "videos"}, {"modified": "yesterday"}])
def test_bad_parameters_are_a_422_with_the_error_envelope(search_app, params):
    status, body = search_app.search("design", **params)
    assert status == 422, (params, status, body)
    assert body["error"]["code"] and body["error"]["message"]


def test_missing_q_is_422_or_an_empty_listing_never_a_500(search_app):
    status, body = search_app.api.get("/v1/search")
    assert status in (200, 422), (status, body)


def test_deny_listed_files_are_absent_by_default(search_app):
    for q in (".env", "id_rsa", "kdbx", "server.pem", "credentials", ".env.local"):
        assert ok(search_app, q)["rows"] == [], q
    everything = row_paths(ok(search_app, "*", limit=5000)["rows"])
    assert not [p for p in everything if p.lower().endswith((".env", "id_rsa", ".kdbx", ".pem", "credentials"))]


def test_protected_files_are_listed_by_name_only_when_the_setting_is_on(search_app_protected):
    names = row_names(ok(search_app_protected, "id_rsa")["rows"])
    assert "id_rsa" in names, "show_protected_by_name = true lists them in the local UI (PM 13)"


def test_excluded_material_is_not_searchable(search_app):
    for q in ("probe_nm", "recycled", "disk2", "leftpad"):
        assert ok(search_app, q)["rows"] == [], q


def test_roots_route_lists_the_configured_root(search_app):
    status, body = search_app.api.get("/v1/roots")
    assert status == 200
    roots = body["roots"] if isinstance(body, dict) else body
    listed = [r["path"] if isinstance(r, dict) else r for r in roots]
    assert any(str(search_app.root).lower() == p.lower() for p in listed), listed


def test_health_reports_the_index(search_app):
    status, body = search_app.api.get("/v1/health")
    idx = body["index"]
    assert idx["entries"] > BULK_COUNT and idx["built_at"] and idx["rescanning"] is False


def test_search_requires_the_token_and_a_loopback_host(search_app):
    assert search_app.api.get("/v1/search?q=design", token=False)[0] == 401
    assert search_app.api.get("/v1/search?q=design", token="wrong")[0] == 401
    assert search_app.api.get("/v1/roots", token=False)[0] == 401
    assert search_app.api.get("/v1/search?q=design", headers={"Host": "evil.example"})[0] == 403


def test_hostile_query_strings_do_not_500(search_app):
    for q in ("*" * 600, '"' * 50, "!" * 50, "ext:" * 80, "\\", "..\\..\\windows", "a" * 5000):
        status, _ = search_app.search(q)
        assert status in (200, 422), (q[:20], status)
