"""Ranking (plan 7.3 M3a-3; AC-FI-13): exact > prefix > word boundary > substring > path-only, ties by shorter name."""
import os

import pytest

from conftest import ROOT_PARENT, store_api

query = pytest.importorskip("assistant.index.query", reason="assistant/index/query.py (M3a-3) not present")
search = pytest.importorskip("assistant.index.search", reason="assistant/index/search.py (M3a-3) not present")
KNOWN = {"md", "py", "html", "txt", "yml", "pdf"}


def make_store(files, dirs=("report",)):
    api = store_api()
    b = api.IndexBuilder()
    root = b.add_dir("C:\\rank", ROOT_PARENT)
    ids = {"": root}
    for d in dirs:
        ids[d] = b.add_dir(f"C:\\rank\\{d}", root)
    for rel in files:
        parent, _, name = rel.rpartition("/")
        b.add(ids[parent], name, 10, 1.7e9, 0)
    return b.finish()


def ranked(store, text, **kw):
    res = search.run(query.parse(text, KNOWN), store, **kw)
    return [r.name for r in res.rows], res


def test_tier_order_exact_prefix_word_boundary_substring():
    store = make_store(["xreport.txt", "old-report.txt", "report.txt.bak", "report.txt"], dirs=())
    names, _ = ranked(store, "report.txt")
    assert names == ["report.txt", "report.txt.bak", "old-report.txt", "xreport.txt"]


def test_exact_means_whole_name_or_name_without_extension_and_folders_count():
    store = make_store(["report.pdf", "reports.pdf", "annual-report.pdf", "annualreport.pdf"])
    names, _ = ranked(store, "report")
    assert set(names[:2]) == {"report", "report.pdf"}, names
    assert names[2] == "reports.pdf" and names[3] == "annual-report.pdf" and names[4] == "annualreport.pdf"


def test_ties_inside_a_tier_go_to_the_shorter_name():
    store = make_store(["report-aaaa.txt", "report-b.txt", "report-cc.txt"], dirs=())
    names, _ = ranked(store, "report")
    assert names == ["report-b.txt", "report-cc.txt", "report-aaaa.txt"]


def test_name_matches_beat_path_only_matches():
    store = make_store(["summary-report.txt", "report/summary.txt", "report/other.txt"])
    names, res = ranked(store, "summary report")
    assert names[0] == "summary-report.txt" and names[1] == "summary.txt"
    assert "other.txt" not in names, "first term must still match the name"


def test_ranking_is_case_insensitive():
    store = make_store(["REPORT.TXT", "Report-b.txt", "xreport.txt"], dirs=())
    names, _ = ranked(store, "report.txt")
    assert names[0] == "REPORT.TXT" and names[-1] == "xreport.txt"


def test_sort_buttons_override_ranking():
    store = make_store(["b-report.txt", "a-report.md", "report.pdf", "c-report.docx"], dirs=())
    by_name, _ = ranked(store, "report", sort="name")
    assert by_name == sorted(by_name, key=str.casefold)
    by_ext, _ = ranked(store, "report", sort="ext")
    exts = [os.path.splitext(n)[1] for n in by_ext]
    assert exts == sorted(exts)
    default, _ = ranked(store, "report")
    assert default[0] == "report.pdf" and default != by_name


def test_date_sort_is_newest_first():
    api = store_api()
    b = api.IndexBuilder()
    root = b.add_dir("C:\\rank", ROOT_PARENT)
    for n, ts in (("report-old.txt", 1.6e9), ("report-new.txt", 1.8e9), ("report-mid.txt", 1.7e9)):
        b.add(root, n, 1, ts, 0)
    names, _ = ranked(b.finish(), "report", sort="date")
    assert names == ["report-new.txt", "report-mid.txt", "report-old.txt"]


def test_paging_is_stable_across_pages():
    store = make_store([f"report-{n:03d}.txt" for n in range(120)], dirs=())
    first, res = ranked(store, "report", limit=50)
    second, _ = ranked(store, "report", limit=50, offset=50)
    third, _ = ranked(store, "report", limit=50, offset=100)
    everything, _ = ranked(store, "report", limit=500)
    assert res.total == 120 and first + second + third == everything and len(third) == 20
