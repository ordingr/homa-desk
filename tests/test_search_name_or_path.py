"""Files-mode search: every plain term matches the name OR the path; name hits rank above path-only rows.

Hand-built stores (real ``IndexBuilder``), no crawler. The path-only tier is rank.PATH (4) and carries no highlight
spans. When every candidate chunk is too common (more than search.VIA_DIRS_MAX entries) the executor falls back to
the names containing the longest literal and says ``truncated``.
"""
import pytest

from conftest import ROOT_PARENT, row_names, row_paths

query = pytest.importorskip("homa.index.query")
search = pytest.importorskip("homa.index.search")
store_mod = pytest.importorskip("homa.index.store")
KNOWN = {"md", "py", "txt", "pdf"}
MTIME = 1.7e9


def run(store, text, **kw):
    return search.run(query.parse(text, KNOWN), store, **kw)


@pytest.fixture(scope="module")
def store():
    b = store_mod.IndexBuilder()
    root = b.add_dir("C:\\proj", ROOT_PARENT)
    widget = b.add_dir("C:\\proj\\widget", root)
    other = b.add_dir("C:\\proj\\other", root)
    deep = b.add_dir("C:\\proj\\widget\\deep", widget)
    for d, name in [(root, "widget.txt"), (root, "widget-notes.md"), (root, "my widget plan.pdf"),
                    (widget, "alpha.txt"), (widget, "beta.md"), (widget, "plan draft.txt"), (widget, "final plan.txt"),
                    (deep, "gamma.py"), (deep, "widget_helper.py"), (other, "delta.txt"), (other, "plan.md"),
                    (other, "unrelated.pdf")]:
        b.add(d, name, 10, MTIME, 0)
    return b.finish()


def test_name_hits_come_first_then_path_only_rows(store):
    res = run(store, "widget")
    got = row_names(res.rows)
    name_hits = {"widget", "widget.txt", "widget-notes.md", "my widget plan.pdf", "widget_helper.py"}
    path_only = {"alpha.txt", "beta.md", "plan draft.txt", "final plan.txt", "deep", "gamma.py"}
    assert set(got) == name_hits | path_only
    assert set(got[:len(name_hits)]) == name_hits, got
    assert set(got[len(name_hits):]) == path_only, got
    tiers = {r.name: r.tier for r in res.rows}
    assert all(tiers[n] < 4 for n in name_hits) and all(tiers[n] == 4 for n in path_only)
    assert res.interpretation == 'name or path contains "widget"'
    assert not res.truncated


def test_path_only_rows_have_no_highlight_spans_and_name_rows_do(store):
    rows = {r.name: r for r in run(store, "widget").rows}
    for name in ("alpha.txt", "beta.md", "gamma.py"):
        assert rows[name].spans == [], name
        assert rows[name].to_dict()["spans"] == []
    hit = rows["widget-notes.md"]
    assert hit.spans and hit.name[hit.spans[0][0]:hit.spans[0][1]].lower() == "widget"


def test_second_plain_term_may_match_the_other_side(store):
    assert set(row_names(run(store, "widget plan").rows)) == {
        "my widget plan.pdf", "plan draft.txt", "final plan.txt"}
    # order of terms does not change the set
    assert set(row_names(run(store, "plan widget").rows)) == set(row_names(run(store, "widget plan").rows))
    both = row_names(run(store, "widget plan").rows)
    assert both[0] == "my widget plan.pdf", "the only row whose name carries both terms ranks first"


def test_case_insensitive_terms_use_the_path_too(store):
    assert set(row_names(run(store, "WIDGET plan").rows)) == set(row_names(run(store, "widget plan").rows))


def test_phrase_matches_name_or_path(store):
    assert row_names(run(store, '"plan draft"').rows) == ["plan draft.txt"]
    got = set(row_names(run(store, '"widget\\deep"').rows))
    assert got == {"deep", "gamma.py", "widget_helper.py"}, "a phrase containing a separator matches through the path"


def test_not_word_excludes_on_name_and_path(store):
    got = set(row_names(run(store, "plan !draft").rows))
    assert "plan draft.txt" not in got and {"final plan.txt", "plan.md"} <= got
    assert "gamma.py" not in row_names(run(store, "widget !deep").rows)


def test_ext_filter_and_path_part_compose_with_anywhere_terms(store):
    assert set(row_names(run(store, "widget ext:py").rows)) == {"gamma.py", "widget_helper.py"}
    assert set(row_names(run(store, "plan ext:md").rows)) == {"plan.md"}
    assert set(row_names(run(store, "plan path:other").rows)) == {"plan.md"}
    assert set(row_names(run(store, "txt path:deep").rows)) == set()


def test_wildcard_term_matches_the_path(store):
    got = row_names(run(store, "wid*et").rows)
    assert "alpha.txt" in got and "widget.txt" in got
    assert got.index("widget.txt") < got.index("alpha.txt")


def test_no_match_anywhere_is_empty_and_not_truncated(store):
    res = run(store, "zzqq")
    assert res.rows == [] and res.total == 0 and not res.truncated


def test_every_row_path_contains_every_term(store):
    for text in ("widget", "widget deep", "plan widget"):
        terms = text.split()
        for path in row_paths(run(store, text).rows):
            assert all(t in path.lower() for t in terms), (text, path)


# ---- the too-common fallback ---------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def big_store():
    """60,000 files inside one folder whose name contains ``commonfolder`` (> VIA_DIRS_MAX), plus a few marked ones."""
    b = store_mod.IndexBuilder()
    root = b.add_dir("C:\\big", ROOT_PARENT)
    common = b.add_dir("C:\\big\\commonfolder", root)
    elsewhere = b.add_dir("C:\\big\\elsewhere", root)
    for i in range(search.VIA_DIRS_MAX + 10_000):
        b.add(common, f"f{i:06d}.dat", 1, MTIME, 0)
    b.add(common, "rare-target.txt", 1, MTIME, 0)
    b.add(elsewhere, "commonfolder-readme.txt", 1, MTIME, 0)
    b.add(elsewhere, "another-commonfolder.md", 1, MTIME, 0)
    return b.finish()


def test_too_common_chunk_falls_back_to_name_hits_and_says_truncated(big_store):
    res = run(big_store, "commonfolder")
    assert res.truncated is True
    names = set(row_names(res.rows))
    assert names == {"commonfolder", "commonfolder-readme.txt", "another-commonfolder.md"}, names
    assert all(r.tier < 4 for r in res.rows), "the fallback is name-only, no path-only rows"


def test_a_selective_second_chunk_keeps_the_result_complete(big_store):
    res = run(big_store, "commonfolder rare")
    assert row_names(res.rows) == ["rare-target.txt"]
    assert not res.truncated, "one selective chunk is enough to enumerate every matching path"


def test_selective_path_chunk_lists_everything_below_it(big_store):
    res = run(big_store, "elsewhere")
    assert set(row_names(res.rows)) == {"elsewhere", "commonfolder-readme.txt", "another-commonfolder.md"}
    assert not res.truncated
    assert res.rows[0].name == "elsewhere"
