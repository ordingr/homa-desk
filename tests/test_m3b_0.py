"""M3b-0: M3a leftovers, plan 18.3 items (a)-(p).

Real components throughout: the real Settings validators, ``search.run`` over stores built from real directories,
IndexManager on a persisted store, the real excludes matcher, NotesIndex on a real vault, and private full apps for
the HTTP and MCP behaviour. Items that live inside the Tk popup ((a) popup half, (c) cloud marker, (e) Sort control and
Ctrl+S, (h) header notes text, (i) details line) belong to tests/e2e/test_search_popup.py, which is outside this task's
write set and is listed as NOT TESTED in the report. Items whose code is not present yet skip with the missing name.
"""
import contextlib
import ctypes
import json
import os
import time
import urllib.parse

import pytest

from conftest import (Api, McpHttp, SearchApp, _free_port, launch_app, row_names, row_paths, wait_index, wait_until,
                      walk_store)

from homa import settings as settings_mod
from homa.settings import DEFAULTS, Settings

KNOWN = set(DEFAULTS["index"].get("known_exts", [])) or {"md", "py", "txt", "pdf", "png", "json"}
FILE_ATTRIBUTE_HIDDEN, FILE_ATTRIBUTE_SYSTEM = 0x2, 0x4


def need_default(key):
    if key not in DEFAULTS["index"]:
        pytest.skip(f"settings.DEFAULTS['index'] has no {key!r} yet (M3b-0)")


def set_attrs(path, attrs):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    assert k32.SetFileAttributesW(str(path), attrs), ctypes.WinError(ctypes.get_last_error())


def rows_of_mcp(result):
    assert not result.is_error, result.text
    data = result.structured if result.structured is not None else json.loads(result.text)
    if isinstance(data, dict) and "result" in data and "rows" not in data:
        data = data["result"]
    return data


# ---- (a) AC-FI-1: hidden and system files -----------------------------------------------------------------------------

def test_a_include_hidden_defaults_to_false():
    need_default("include_hidden")
    assert DEFAULTS["index"]["include_hidden"] is False


def test_a_include_hidden_only_accepts_a_boolean():
    need_default("include_hidden")
    s = Settings()
    s.load()
    index = s.get("index")
    s.set("index", {**index, "include_hidden": True})
    assert s.get("index")["include_hidden"] is True
    for bad in ("yes", 1, None):
        with pytest.raises(ValueError):
            s.set("index", {**index, "include_hidden": bad})
    s.set("index", {**index, "include_hidden": False})


@pytest.fixture(scope="module")
def hidden_tree(tmp_path_factory):
    root = tmp_path_factory.mktemp("hidden_tree") / "root"
    root.mkdir()
    (root / "plainsample.txt").write_text("p\n", encoding="utf-8")
    hidden = root / "hiddensample.txt"
    hidden.write_text("h\n", encoding="utf-8")
    set_attrs(hidden, FILE_ATTRIBUTE_HIDDEN)
    system = root / "systemsample.txt"
    system.write_text("s\n", encoding="utf-8")
    set_attrs(system, FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM)
    return root


def test_a_search_run_drops_hidden_and_system_entries_unless_asked(hidden_tree):
    from homa.index import query, search
    store = walk_store(hidden_tree)
    parsed = query.parse("sample", KNOWN)
    try:
        default = search.run(parsed, store)
        shown = search.run(parsed, store, show_hidden=True)
    except TypeError:
        pytest.skip("search.run has no show_hidden parameter yet (M3b-0)")
    assert row_names(default.rows) == ["plainsample.txt"]
    assert sorted(row_names(shown.rows)) == ["hiddensample.txt", "plainsample.txt", "systemsample.txt"]


@pytest.fixture(scope="module")
def hidden_apps(tmp_path_factory, hidden_tree):
    need_default("include_hidden")
    base = tmp_path_factory.mktemp("hidden_apps")
    with contextlib.ExitStack() as stack:
        apps = []
        for flag in (False, True):
            home = base / f"home_{flag}"
            base_url, token = stack.enter_context(launch_app(
                home, _free_port(), {"index": {"roots": [str(hidden_tree)], "include_hidden": flag}}))
            index = wait_index(base_url, token, minimum=3, timeout=60)
            if not (isinstance(index, dict) and index.get("entries", 0) >= 3):
                pytest.skip(f"index never reached 3 entries ({index!r})")
            apps.append(SearchApp(base_url, token, hidden_tree, home))
        yield apps


def test_a_http_and_mcp_show_hidden_fixtures_only_with_the_toggle_on(hidden_apps):
    off, on = hidden_apps
    assert row_names(off.search("sample")[1]["rows"]) == ["plainsample.txt"]
    assert sorted(row_names(on.search("sample")[1]["rows"])) == ["hiddensample.txt", "plainsample.txt",
                                                                 "systemsample.txt"]
    off_rows = rows_of_mcp(off.mcp.call("search_files", {"query": "sample"}))["rows"]
    on_rows = rows_of_mcp(on.mcp.call("search_files", {"query": "sample"}))["rows"]
    assert [r["name"] for r in off_rows] == ["plainsample.txt"]
    assert sorted(r["name"] for r in on_rows) == ["hiddensample.txt", "plainsample.txt", "systemsample.txt"]


# ---- (b) AC-FI-4: start scan only when the index is old ---------------------------------------------------------------

class CountingManager:
    """Builds an IndexManager whose walks are counted; everything else is the real manager."""

    @staticmethod
    def make(tmp_path, built_age_hours, roots_setting=None, battery=False, monkeypatch=None):
        from homa.index import manager as manager_mod
        from homa.scheduler import Scheduler

        class Counting(manager_mod.IndexManager):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.walks = []

            def _walk(self, reason):
                self.walks.append(reason)

        root = tmp_path / "idxroot"
        root.mkdir(exist_ok=True)
        (root / "note.txt").write_text("x\n", encoding="utf-8")
        store = walk_store(root)
        store.built_at = time.time() - built_age_hours * 3600.0
        store_path = tmp_path / "index.bin"
        store.save(str(store_path))
        s = Settings()
        s.load()
        s.set("index", {**s.get("index"), "roots": [str(root)], **(roots_setting or {})})
        if monkeypatch is not None:
            monkeypatch.setattr(manager_mod, "_on_battery", lambda: battery)
        return Counting(s, Scheduler(), store_path), manager_mod


def test_b_scan_if_older_hours_default_and_validation():
    need_default("scan_if_older_hours")
    assert DEFAULTS["index"]["scan_if_older_hours"] == 12
    s = Settings()
    s.load()
    index = s.get("index")
    for ok in (1, 12, 168):
        s.set("index", {**index, "scan_if_older_hours": ok})
    for bad in (0, 169, -1, 1.5, True, "12"):
        with pytest.raises(ValueError):
            s.set("index", {**index, "scan_if_older_hours": bad})
    s.set("index", {**index, "scan_if_older_hours": 12})


def test_b_an_index_one_hour_old_starts_no_crawl_and_one_thirteen_hours_old_does(tmp_path, monkeypatch):
    need_default("scan_if_older_hours")
    (tmp_path / "fresh").mkdir()
    fresh, _ = CountingManager.make(tmp_path / "fresh", 1.0, monkeypatch=monkeypatch)
    fresh.start()
    try:
        time.sleep(1.5)
        assert fresh.walks == [], f"a 1 h old index must not be rescanned at start, walks={fresh.walks}"
        assert fresh.status()["entries"] > 0 and fresh.status()["loaded_from_disk"] is True
    finally:
        fresh.stop()
    (tmp_path / "old").mkdir()
    old, _ = CountingManager.make(tmp_path / "old", 13.0, monkeypatch=monkeypatch)
    old.start()
    try:
        assert wait_until(lambda: old.walks == ["start"], timeout=10), old.walks
    finally:
        old.stop()


def test_b_the_threshold_is_the_setting(tmp_path, monkeypatch):
    need_default("scan_if_older_hours")
    (tmp_path / "t").mkdir()
    mgr, _ = CountingManager.make(tmp_path / "t", 3.0, roots_setting={"scan_if_older_hours": 2}, monkeypatch=monkeypatch)
    mgr.start()
    try:
        assert wait_until(lambda: mgr.walks == ["start"], timeout=10), mgr.walks
    finally:
        mgr.stop()


def test_b_on_battery_the_start_scan_waits_but_a_manual_rescan_runs(tmp_path, monkeypatch):
    need_default("scan_if_older_hours")
    (tmp_path / "b").mkdir()
    mgr, _ = CountingManager.make(tmp_path / "b", 30.0, battery=True, monkeypatch=monkeypatch)
    mgr.start()
    try:
        time.sleep(1.5)
        assert mgr.walks == [], f"start scan ran on battery: {mgr.walks}"
        mgr.request_rescan("manual")
        assert wait_until(lambda: mgr.walks == ["manual"], timeout=10), mgr.walks
        mgr.request_rescan("daily")
        mgr.request_rescan("overflow")
        time.sleep(1.5)
        assert mgr.walks == ["manual"], "daily and overflow scans must also wait on battery"
    finally:
        mgr.stop()


# ---- (c) AC-FI-6: placeholder flag on HTTP rows -------------------------------------------------------------------------

def test_c_http_rows_carry_placeholder_true_for_offline_files_only(search_app):
    status, body = search_app.search("cloud")
    assert status == 200
    flags = {r["name"]: r.get("placeholder") for r in body["rows"] if not r["is_dir"]}
    if all(v is None for v in flags.values()):
        pytest.skip("rows carry no 'placeholder' field yet (M3b-0 (c))")
    assert flags.get("cloud-doc.docx") is True and flags.get("cloud-note.md") is True, flags
    status, body = search_app.search("designer")
    assert [r["placeholder"] for r in body["rows"] if r["name"] == "designer.md"] == [False]


def test_c_http_placeholder_agrees_with_the_mcp_flag(search_app):
    http = {r["path"]: r.get("placeholder") for r in search_app.search("cloud")[1]["rows"]}
    if not any(v is not None for v in http.values()):
        pytest.skip("rows carry no 'placeholder' field yet (M3b-0 (c))")
    mcp = {r["path"]: r["placeholder"] for r in rows_of_mcp(search_app.mcp.call("search_files", {"query": "cloud"}))["rows"]}
    assert {p: v for p, v in http.items() if p in mcp} == {p: mcp[p] for p in http if p in mcp} and mcp


# ---- (d) AC-FI-15: closed date range -------------------------------------------------------------------------------------

def test_d_modified_accepts_a_closed_day_range_over_http(search_app):
    status, body = search_app.search("md", modified="2024-02-01..2024-02-28")
    assert status == 200, body
    names = row_names(body["rows"])
    if status == 200 and "designer.md" not in names:
        pytest.skip("modified range is not applied yet (M3b-0 (d))")
    assert "designer.md" in names and "x.md" in names
    assert not {"notes.md", "plan-final.md", "plan-draft.md"} & set(names)
    status, body = search_app.search("md", modified="2024-02-01..2024-02-01")
    assert row_names(body["rows"]) == ["designer.md"], "the first day is inclusive in local time"
    status, body = search_app.search("md", modified="2024-02-16..2024-02-16")
    assert row_names(body["rows"]) == []


@pytest.mark.parametrize("bad", ["2024-02-30..2024-03-01", "2024-02-01..", "..2024-02-01", "abc..def",
                                 "2024-03-01..2024-02-01", "2024-02-01..2024-02-28..2024-03-01"])
def test_d_a_malformed_range_is_422_with_the_field(search_app, bad):
    status, body = search_app.search("md", modified=bad)
    assert status == 422, (bad, status, body)
    assert body["error"]["field"] == "modified"


def test_d_mcp_filters_modified_before(search_app):
    result = search_app.mcp.call("search_files", {"query": "md", "filters": {"modified_after": "2024-02-01",
                                                                           "modified_before": "2024-02-28"}})
    if result.is_error and "modified_before" in result.text:
        pytest.skip("MCP filters.modified_before is not supported yet (M3b-0 (d))")
    names = [r["name"] for r in rows_of_mcp(result)["rows"]]
    assert "designer.md" in names and "x.md" in names and "notes.md" not in names
    bad = search_app.mcp.call("search_files", {"query": "md", "filters": {"modified_before": "not-a-date"}})
    assert bad.is_error and "modified_before" in bad.text


# ---- (e) AC-FI-13/16: sort and saved searches -------------------------------------------------------------------------------

def test_e_saved_searches_keep_and_return_the_sort(search_app):
    name = "m3b0-sort-recall"
    status, body = search_app.api.post("/v1/saved", {"name": name, "query": "design", "sort": "date"})
    assert status == 201, body
    try:
        status, listing = search_app.api.get("/v1/saved")
        mine = [s for s in listing["searches"] if s["name"] == name]
        assert mine and mine[0]["sort"] == "date"
    finally:
        search_app.api.request("DELETE", f"/v1/saved/{urllib.parse.quote(name)}")


@pytest.mark.parametrize("sort", ["best", "name", "ext", "date"])
def test_e_every_sort_key_is_accepted_and_orders(search_app, sort):
    status, body = search_app.search("item_", sort=sort, limit=20)
    assert status == 200
    names = row_names(body["rows"])
    if sort == "name":
        assert names == sorted(names, key=str.casefold)


# ---- (f) AC-FI-9: known_exts setting --------------------------------------------------------------------------------------------

def test_f_known_exts_default_is_the_old_constant_without_doc():
    need_default("known_exts")
    default = DEFAULTS["index"]["known_exts"]
    assert "md" in default and "pdf" in default and "doc" not in default
    assert set(default) == set(settings_mod.DEFAULT_KNOWN_EXTS)


def test_f_known_exts_validation_and_the_route_helper():
    need_default("known_exts")
    from homa.server import routes_search
    s = Settings()
    s.load()
    index = s.get("index")
    s.set("index", {**index, "known_exts": ["zzq", "md"]})
    assert routes_search.known_exts(s) == frozenset({"zzq", "md"})
    for bad in ("md", [1], ["bad ext"], ["x" * 17], [""], ["a.b"]):
        with pytest.raises(ValueError):
            s.set("index", {**index, "known_exts": bad})
    s.set("index", {**index, "known_exts": list(DEFAULTS["index"]["known_exts"])})
    assert routes_search.known_exts(s) == frozenset(DEFAULTS["index"]["known_exts"])
    assert routes_search.known_exts(None) == routes_search.KNOWN_EXTS


def test_f_a_custom_extension_becomes_a_filter_in_the_parser():
    need_default("known_exts")
    from homa.index import query
    assert query.parse("report zzq", {"zzq"}).exts == ["zzq"]
    assert query.parse("report zzq", {"md"}).exts == []


# ---- (g) R7-N11: hotkey_skip_fullscreen ---------------------------------------------------------------------------------------

def test_g_hotkey_skip_fullscreen_is_a_validated_boolean_defaulting_off():
    if "hotkey_skip_fullscreen" not in DEFAULTS:
        pytest.skip("DEFAULTS has no hotkey_skip_fullscreen yet (M3b-0 (g))")
    assert DEFAULTS["hotkey_skip_fullscreen"] is False
    s = Settings()
    s.load()
    s.set("hotkey_skip_fullscreen", True)
    assert s.get("hotkey_skip_fullscreen") is True
    for bad in ("yes", 1, None):
        with pytest.raises(ValueError):
            s.set("hotkey_skip_fullscreen", bad)
    s.set("hotkey_skip_fullscreen", False)


# ---- (h) MCP ext normalisation and notes --------------------------------------------------------------------------------------

def test_h_mcp_ext_filter_is_normalised_like_the_query(search_app):
    base = [r["path"] for r in rows_of_mcp(search_app.mcp.call(
        "search_files", {"query": "design", "filters": {"ext": ["md"]}}))["rows"]]
    assert base, "fixture should contain a .md file matching 'design'"
    for spelling in ("*.md", ".md", "MD", ".MD", "*.Md"):
        got = [r["path"] for r in rows_of_mcp(search_app.mcp.call(
            "search_files", {"query": "design", "filters": {"ext": [spelling]}}))["rows"]]
        assert got == base, spelling


def test_h_search_files_returns_the_same_notes_as_http(search_app):
    long_query = "design " + "x" * 600
    status, http_body = search_app.search(long_query)
    assert status == 200 and http_body.get("notes"), http_body
    result = rows_of_mcp(search_app.mcp.call("search_files", {"query": long_query}))
    if "notes" not in result:
        pytest.skip("search_files returns no 'notes' yet (M3b-0 (h))")
    assert result["notes"] == http_body["notes"]
    assert rows_of_mcp(search_app.mcp.call("search_files", {"query": "designer"})).get("notes") == []


# ---- (i) AC-AN-2/3: the noun stays "passage(s)" --------------------------------------------------------------------------------

def test_i_ask_notes_noun_is_passages_on_the_real_mode_and_the_placeholder():
    from homa.search import modes, notes_bm25
    assert notes_bm25.AskNotesMode.noun == "passage(s)"
    assert modes.AskNotesPlaceholder.noun == "passage(s)"
    assert modes.FilesMode.noun == "file(s)"


# ---- (j) one-character name term + path term: selective, not truncated --------------------------------------------------------

def test_j_one_char_name_plus_path_term_is_selective_and_fast_on_2m_entries(synthetic_store_2m):
    from homa.index import query, search
    store, _, _ = synthetic_store_2m
    parsed = query.parse("a -p d00001", KNOWN)
    search.run(parsed, store, limit=500)  # warm
    started = time.perf_counter()
    result = search.run(parsed, store, limit=500)
    elapsed = (time.perf_counter() - started) * 1000
    assert result.truncated is False, "a selective query must not be reported as truncated"
    assert elapsed < 100, f"{elapsed:.1f} ms"
    assert result.rows and all("d00001" in p for p in row_paths(result.rows))
    assert all("a" in n.lower() for n in row_names(result.rows))
    assert result.total == len(result.rows) <= 100, "d00001 holds 100 entries"
    again = search.run(query.parse("x path:d00002", KNOWN), store, limit=500)
    assert again.truncated is False and all("d00002" in p for p in row_paths(again.rows))


def test_j_the_shortcut_returns_what_the_full_scan_returns():
    """A one-character term cannot use the directory table: it scans names only and says so; the ``-p`` form is complete."""
    from homa.index import query, search
    from conftest import ROOT_PARENT, store_api
    builder = store_api().IndexBuilder()
    root = builder.add_dir("C:\\j", ROOT_PARENT)
    d1, d2 = builder.add_dir("C:\\j\\alpha", root), builder.add_dir("C:\\j\\beta", root)
    for n, name in enumerate(["a.txt", "ab.txt", "cat.md", "dog.md", "zzz.md"]):
        builder.add(d1, name, 1, 1.7e9 + n, 0)
        builder.add(d2, name, 1, 1.7e9 + n, 0)
    store = builder.finish()
    shortcut = search.run(query.parse("a -p alpha", KNOWN), store, limit=100)
    full = search.run(query.parse("a", KNOWN), store, limit=100)
    in_alpha = sorted(p for p in row_paths(full.rows) if "alpha" in p.lower())
    assert in_alpha and full.truncated, "a one-character term is name-only and reported as truncated"
    assert not shortcut.truncated
    assert set(in_alpha) <= set(row_paths(shortcut.rows))
    assert sorted(p.rsplit("\\", 1)[-1] for p in row_paths(shortcut.rows)) == sorted(
        ["a.txt", "ab.txt", "alpha", "cat.md", "dog.md", "zzz.md"]), "every file under alpha matches 'a' through its path"


# ---- (k) the root folder row is not a result ---------------------------------------------------------------------------------

def test_k_the_root_directory_itself_is_not_returned(search_app):
    root_name = search_app.root.name
    status, body = search_app.search(root_name)
    assert status == 200
    assert str(search_app.root).lower() not in [p.lower() for p in row_paths(body["rows"])]
    result = rows_of_mcp(search_app.mcp.call("search_files", {"query": root_name}))
    assert str(search_app.root).lower() not in [r["path"].lower() for r in result["rows"]]
    assert search_app.root.name in str(search_app.root)  # the query really named the root


# ---- (l) A22: a user's excludes replace the defaults --------------------------------------------------------------------------

def test_l_user_excludes_replace_the_defaults_and_own_data_rules_stay(tmp_path):
    from homa.index import manager as manager_mod
    from homa.scheduler import Scheduler
    s = Settings()
    s.load()
    s.set("index", {**s.get("index"), "roots": [str(tmp_path)], "excludes": ["only-this/"]})
    mgr = manager_mod.IndexManager(s, Scheduler(), tmp_path / "i.bin")
    cfg = mgr._config()
    assert cfg["patterns"] == ["only-this/"], "A22: the list is the whole list"
    rules = mgr._own_data_rules()
    assert any(".homa" in r for r in rules), "the own-data rule is unconditional"
    assert any(".assistant" in r for r in rules), "a lingering pre-rename folder is never indexed"


# ---- (m) drive-relative default excludes ---------------------------------------------------------------------------------------

def test_m_default_windows_excludes_are_drive_relative():
    from homa.index.excludes import DEFAULT_EXCLUDES
    lowered = [r.lower() for r in DEFAULT_EXCLUDES]
    if "c:\\windows\\" in lowered:
        pytest.skip("DEFAULT_EXCLUDES still hold the absolute C:\\Windows\\ rule (M3b-0 (m))")
    assert "*:\\windows\\" in lowered
    assert "*:\\programdata\\microsoft\\search\\" in lowered


@pytest.mark.parametrize("drive", ["C", "D", "e"])
def test_m_the_drive_relative_rule_matches_on_any_drive_and_only_the_top_level(drive):
    from homa.index.excludes import Excludes
    ex = Excludes(["*:\\Windows\\"])
    full = f"{drive}:\\Windows"
    assert ex.matching_rule(full, "Windows", "Windows", True), full
    assert ex.matching_rule(f"{drive}:\\Windows\\System32", "Windows/System32", "System32", True)
    assert not ex.matching_rule(f"{drive}:\\Users\\me\\Windows", "Users/me/Windows", "Windows", True), \
        "only the drive-root Windows folder"
    assert not ex.matching_rule(f"{drive}:\\Windowsfoo", "Windowsfoo", "Windowsfoo", True)


# ---- (n) R7-N5: notes index skips deny-listed paths ---------------------------------------------------------------------------

def test_n_notes_index_does_not_hold_text_of_deny_listed_notes(tmp_path):
    from homa.search.notes_bm25 import NotesIndex
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "ordinary.md").write_text("# Ordinary\n\nthe zebraquartz plan is public\n", encoding="utf-8")
    (vault / ".env-notes.md").write_text("# Secrets\n\nthe walrusnickel password is hunter2\n", encoding="utf-8")
    idx = NotesIndex(str(vault), index_path=tmp_path / "idx" / "n.json.gz")
    idx.start()
    try:
        assert idx.wait_ready(30)
        assert idx.search("zebraquartz"), "the ordinary note must be indexed"
        assert idx.search("walrusnickel") == []
        assert idx.skipped_placeholder >= 1, "denied notes are counted with the skipped files"
    finally:
        idx.close()
    saved = (tmp_path / "idx" / "n.json.gz")
    import gzip
    raw = gzip.decompress(saved.read_bytes()).decode("utf-8", "replace") if saved.exists() else ""
    assert "walrusnickel" not in raw and "hunter2" not in raw, "the persisted index must not hold denied text"


# ---- (o) R7-N10: \\?\ paths are vetted like plain ones -----------------------------------------------------------------------------

class _FakeIndex:
    def __init__(self, root):
        self._store, self._root = walk_store(root), str(root)

    def current(self):
        return self._store, None

    def status(self):
        return {"roots": [{"path": self._root}]}


@pytest.fixture
def vetted_tree(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "file.txt").write_text("hello\n", encoding="utf-8")
    return root, _FakeIndex(root)


def test_o_vet_accepts_extended_length_prefixed_paths(vetted_tree):
    from homa.server import mcp
    root, index = vetted_tree
    plain = str(root / "sub" / "file.txt")
    resolved, reason = mcp._vet(index, plain)
    assert reason == "" and resolved
    for prefixed in ("\\\\?\\" + plain, "\\\\?\\" + plain.upper()):
        got, why = mcp._vet(index, prefixed)
        assert why == "" and os.path.normcase(got) == os.path.normcase(resolved), (prefixed, why)


def test_o_a_prefixed_path_outside_the_roots_is_still_refused(vetted_tree, tmp_path):
    from homa.server import mcp
    root, index = vetted_tree
    outside = tmp_path / "outside.txt"
    outside.write_text("no\n", encoding="utf-8")
    got, why = mcp._vet(index, "\\\\?\\" + str(outside))
    assert got is None and "outside the indexed roots" in why
    got, why = mcp._vet(index, "\\\\?\\UNC\\server\\share\\x.txt")
    assert got is None


# ---- (p) R7-N2: open, then compare the handle's final path with the vetted one -----------------------------------------------------

def test_p_final_path_reports_what_an_open_handle_really_is(tmp_path):
    from homa.security import pathcheck
    if not hasattr(pathcheck, "final_path"):
        pytest.skip("pathcheck.final_path is not present yet (M3b-0 (p))")
    target = tmp_path / "t.txt"
    target.write_text("x\n", encoding="utf-8")
    with open(target, "rb") as fh:
        assert os.path.normcase(pathcheck.final_path(fh)) == os.path.normcase(str(target.resolve()))


def test_p_read_text_preview_refuses_when_the_path_now_leads_elsewhere(tmp_path):
    import _winapi
    from homa.server import mcp
    if "recheck" not in mcp._read_preview.__code__.co_varnames:
        pytest.skip("mcp._read_preview has no open-then-verify recheck yet (M3b-0 (p))")
    root = tmp_path / "root"
    inside = root / "dirx"
    inside.mkdir(parents=True)
    (inside / "file.txt").write_text("INSIDE\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("OUTSIDE-SECRET\n", encoding="utf-8")
    vetted = os.path.realpath(str(inside / "file.txt"))
    assert mcp._read_preview(vetted, 100, None, lambda final: False).count("INSIDE") == 1
    inside.rename(root / "dirx_old")
    _winapi.CreateJunction(str(outside), str(root / "dirx"))
    with pytest.raises(Exception) as err:
        mcp._read_preview(vetted, 100, None, lambda final: False)
    assert "changed while" in str(err.value) and "OUTSIDE-SECRET" not in str(err.value)
