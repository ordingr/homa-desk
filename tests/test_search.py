"""Search executor over the fixture tree (plan 7.3 M3a-3; AC-FI-8 .. AC-FI-18, SEC-3).

The store is built by conftest.walk_store from tests/fixtures/tree_search.json: the real
homa.index.store when it exists, otherwise the 4b-API stand-in in conftest. node_modules and $RECYCLE.BIN are
pruned and *.vhdx skipped by the helper, as the default excludes would.
"""
import time
from datetime import datetime

import pytest

from conftest import BULK_COUNT, row_names, row_paths, walk_store

query = pytest.importorskip("homa.index.query", reason="homa/index/query.py (M3a-3) not present")
search = pytest.importorskip("homa.index.search", reason="homa/index/search.py (M3a-3) not present")
KNOWN = {"md", "py", "html", "txt", "yml", "pdf", "docx", "xlsx", "csv", "png", "json", "js", "ts"}
DOT = "·"


@pytest.fixture(scope="module")
def store(search_tree):
    return walk_store(search_tree)


def run(store, text, **kw):
    return search.run(query.parse(text, KNOWN), store, **kw)


def names(store, text, **kw):
    return row_names(run(store, text, **kw).rows)


# ---- the five reference examples ------------------------------------------------------------------------------------

def test_design_md(store):
    res = run(store, "design md")
    assert row_names(res.rows) == ["designer.md", "x.md"], "the name hit first, then x.md under docs/design"
    assert res.interpretation == f'name or path contains "design" {DOT} ext: md'


def test_design_doc_finds_the_design_folder_under_docs_then_what_lives_in_it(store):
    res = run(store, "design doc")
    assert row_names(res.rows) == ["design", "x.md"]
    assert all("docs" in p.lower().replace("\\", "/").split("/") for p in row_paths(res.rows))
    assert res.interpretation == f'name or path contains "design" {DOT} name or path contains "doc"'


def test_config_dash_e_yml(store):
    res = run(store, "config -e yml")
    assert row_names(res.rows) == ["config.yml"]
    assert res.interpretation == f'name or path contains "config" {DOT} ext: yml'


def test_report_dash_p_documents(store):
    res = run(store, "report -p Documents")
    assert row_names(res.rows) == ["report.pdf"]
    assert "Documents" in row_paths(res.rows)[0]
    assert res.interpretation == f'name or path contains "report" {DOT} path contains "Documents"'


def test_plain_design_returns_name_matches_best_first_then_path_only_rows(store):
    res = run(store, "design")
    got = row_names(res.rows)
    assert sorted(got, key=str.casefold) == sorted(
        ["Design", "design", "designer.md", "design-system.html", "stepper-designs.html", "logo.png", "x.md"],
        key=str.casefold)
    assert set(got[:2]) == {"Design", "design"}, "exact folder names first"
    assert got[2:5] == ["designer.md", "design-system.html", "stepper-designs.html"]
    assert set(got[5:]) == {"logo.png", "x.md"}, "files that only sit inside a design folder rank last"
    assert res.total == 7 and res.interpretation == 'name or path contains "design"'


# ---- grammar features end to end ----------------------------------------------------------------------------------------

def test_quoted_md_is_a_substring_term_unquoted_md_after_a_term_is_a_filter(store):
    assert sorted(names(store, 'notes "md"')) == ["md-notes.txt", "notes.md"]
    assert names(store, "notes md") == ["notes.md"]
    assert {"md-notes.txt", "designer.md", "x.md"} <= set(names(store, "md"))


def test_dash_x_foo_is_literal_terms_not_a_not(store):
    assert names(store, "-x foo") == ["my-x foo.txt"]


def test_bang_excludes_by_name_or_path(store):
    assert sorted(names(store, "plan")) == ["plan-draft.md", "plan-final.md"]
    assert names(store, "plan !draft") == ["plan-final.md"]
    assert "x.md" in names(store, "x.md") and "x.md" not in names(store, "x.md !design")


def test_ext_long_form_path_long_form(store):
    got = names(store, "ext:md,py")
    assert {"designer.md", "tool.py", "x.md"} <= set(got)
    assert all(n.rsplit(".", 1)[-1] in ("md", "py") for n in got)
    docs = row_paths(run(store, "path:docs", limit=500).rows)
    assert docs and all("docs" in p.lower() for p in docs)
    assert run(store, "readme path:docs").total == 1


def test_wildcards(store):
    assert "Design" in names(store, "des*gn")
    assert "designer.md" in names(store, "desig?")
    assert "config.yml" not in names(store, "desig?")
    assert names(store, "confi?.yml") == ["config.yml"]
    assert names(store, "*.yml") == ["config.yml"]
    assert run(store, "*").total > 100


def test_unicode_spaces_and_phrases(store):
    assert names(store, "résumé") == ["résumé.txt"]
    assert names(store, "RÉSUMÉ") == ["résumé.txt"]
    assert names(store, "日本語") == ["日本語メモ.txt"]
    assert names(store, '"report final"') == ["my report final.txt"]
    assert names(store, "my report final") == ["my report final.txt"]


def test_every_term_may_match_the_name_or_the_path_in_either_order(store):
    assert names(store, "design doc") == ["design", "x.md"]
    assert names(store, "doc design") == ["design", "x.md"], "term order no longer matters"
    assert "x.md" in names(store, "design md"), "x.md matches through its path, a plain term is no longer name-only"
    assert names(store, "x.md design")[0] == "x.md", "the name hit still outranks path-only rows"


# ---- chips and dates ------------------------------------------------------------------------------------------------------------

def test_chips_restrict_and_or_together_and_appear_in_the_interpretation(store):
    docs = run(store, "design", chips=["Docs"])
    assert row_names(docs.rows) == ["designer.md", "x.md"] and "Docs" in docs.interpretation
    assert sorted(names(store, "design", chips=["code"])) == ["design-system.html", "stepper-designs.html"]
    folders = run(store, "design", chips=["Folders"])
    assert sorted(row_names(folders.rows)) == ["Design", "design"] and "Folders" in folders.interpretation
    both = names(store, "design", chips=["Docs", "Folders"])
    assert sorted(both, key=str.casefold) == sorted(["Design", "design", "designer.md", "x.md"], key=str.casefold)
    assert sorted(names(store, "report", chips=["PDF"])) == ["report-old.pdf", "report.pdf"]
    assert "sheet.xlsx" in names(store, "sheet", chips=["Sheets"])
    assert "logo.png" in names(store, "logo", chips=["Images"])


def test_chips_alone_without_a_query_filter_everything(store):
    res = run(store, "", chips=["PDF"], limit=500)
    assert {"report.pdf", "report-old.pdf"} <= set(row_names(res.rows))
    assert all(n.lower().endswith(".pdf") for n in row_names(res.rows))


def test_unknown_chip_is_rejected(store):
    with pytest.raises(ValueError):
        run(store, "design", chips=["Videos"])


def test_date_presets_and_custom_range(store):
    recent = names(store, "design", date_filter="7d")
    assert "stepper-designs.html" in recent and "designer.md" not in recent and "x.md" not in recent
    assert "stepper-designs.html" in names(store, "design", date_filter="today")
    lo, hi = datetime(2024, 1, 25).timestamp(), datetime(2024, 3, 2).timestamp()
    got = names(store, "design", date_filter=(lo, hi))
    assert sorted(got) == ["design-system.html", "designer.md", "x.md"]
    assert "modified" in run(store, "design", date_filter="30d").interpretation.lower()


def test_date_filter_composes_with_chips(store):
    lo, hi = datetime(2024, 1, 25).timestamp(), datetime(2024, 3, 2).timestamp()
    assert names(store, "design", date_filter=(lo, hi), chips=["Docs"]) == ["designer.md", "x.md"]


# ---- deny-list, paging, result shape ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("q", [".env", "id_rsa", "id_ed25519", "kdbx", "server.pem", "credentials"])
def test_deny_listed_files_are_dropped_unless_show_denied(store, q):
    assert names(store, q) == [], q
    assert names(store, q, show_denied=True), f"{q}: flagged entries must exist in the store and appear with show_denied"


def test_deny_listed_never_survive_a_star_query(store):
    everything = row_paths(run(store, "*", limit=5000).rows)
    for bad in (".env", "id_rsa", "vault.kdbx", "server.pem", "credentials"):
        assert not [p for p in everything if p.endswith(bad)], bad


def test_limit_offset_total(store):
    res = run(store, "item_", limit=50)
    assert res.total == BULK_COUNT and len(res.rows) == 50
    assert len(run(store, "item_", limit=50, offset=590).rows) == 10
    beyond = run(store, "item_", limit=50, offset=5000)
    assert beyond.rows == [] and beyond.total == BULK_COUNT
    assert run(store, "item_", limit=0).rows == []
    with pytest.raises(ValueError):
        run(store, "item_", limit=-1)
    with pytest.raises(ValueError):
        run(store, "item_", sort="bogus")


def test_row_fields_match_the_documented_shape(store):
    row = run(store, "design md").rows[0]
    assert (row.name, row.ext, row.is_dir) == ("designer.md", "md", False)
    assert row.path.endswith("\\designer.md") and row.parent == row.path[: -len("\\designer.md")]
    assert row.size == 800
    assert abs(row.mtime - datetime(2024, 2, 1, 9).timestamp()) < 2
    assert row.spans and row.name[row.spans[0][0]:row.spans[0][1]].lower() == "design"
    assert row.to_dict()["name"] == "designer.md"
    folder = run(store, "Design", chips=["Folders"]).rows[0]
    assert folder.is_dir and folder.ext == ""


def test_elapsed_ms_and_built_at_and_interpretation_are_populated(store):
    res = run(store, "design md")
    assert isinstance(res.elapsed_ms, float) and 0 < res.elapsed_ms < 1000
    assert res.built_at == store.built_at and res.built_at > 0
    assert run(store, "").interpretation


def test_pruned_material_is_not_in_the_store(store):
    for q in ("probe_nm", "recycled", "disk2"):
        assert names(store, q) == [], q


def test_hostile_queries_finish_quickly_and_do_not_raise(store):
    started = time.perf_counter()
    for text in ("*" * 600, "?*" * 300 + "x", "a*" * 40, '"' * 100, "!" * 100 + "a", " ".join("*a*" for _ in range(40))):
        run(store, text, limit=10)
    assert time.perf_counter() - started < 5.0


def test_delta_removal_and_addition_are_honoured(search_tree, store):
    mod = pytest.importorskip("homa.index.store", reason="real IndexStore/IndexDelta (M3a-1) not present")
    delta = mod.IndexDelta()
    target = row_paths(run(store, "designer.md").rows)[0]
    delta.remove(target)
    assert names(store, "designer.md", delta=delta) == []
    delta.add(str(search_tree / "brand-new-design.txt"), 5, time.time(), 0)
    assert "brand-new-design.txt" in names(store, "brand-new", delta=delta), "AC-FI-3: live adds must be searchable"
