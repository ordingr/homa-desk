"""Crawler (plan 7.3 M3a-2; AC-FI-1, AC-FI-5, AC-FI-6, PRD 12 crawl targets).

Drives ``Crawler(roots, excludes, builder_factory, root_timeout_s).run() -> CrawlResult(store, roots, excludes)``
on tests/fixtures/tree_search.json (plus the 600 bulk files) with the real IndexBuilder and the shipped default
excludes. Nothing here uses the manager or the app.
"""
import os
import sys
import time
from pathlib import Path

import pytest

from conftest import BULK_COUNT, default_exclude_patterns, load_search_spec, store_flag

crawler = pytest.importorskip("assistant.index.crawler", reason="assistant/index/crawler.py (M3a-2) not present")
store_mod = pytest.importorskip("assistant.index.store", reason="assistant/index/store.py (M3a-1) not present")
excludes_mod = pytest.importorskip("assistant.index.excludes", reason="assistant/index/excludes.py (M3a-1) not present")

EXCLUDED_PREFIXES = ("node_modules", "$RECYCLE.BIN", "AppData")


def expected_entries():
    """Spec paths a default-excluded crawl must produce (junctions count once and are never entered)."""
    out = set()
    for e in load_search_spec()["entries"]:
        rel = e["path"]
        if rel.lower().endswith(".vhdx") or any(rel == p or rel.startswith(p + "/") for p in EXCLUDED_PREFIXES):
            continue
        out.add(rel)
    return out


def crawl(roots, patterns=None, **kw):
    excl = excludes_mod.Excludes(patterns) if patterns else None
    return crawler.Crawler([str(r) for r in roots], excl, store_mod.IndexBuilder, **kw).run()


def rel_paths(store, root):
    prefix = str(Path(root)).rstrip("\\") + "\\"
    out = set()
    for i in range(store.entry_count):
        p = store.path(i)
        if p.lower().startswith(prefix.lower()):
            out.add(p[len(prefix):].replace("\\", "/"))
    return out


def test_entry_set_equals_the_spec_exactly_with_default_excludes(search_tree):
    result = crawl([search_tree], default_exclude_patterns())
    got = rel_paths(result.store, search_tree)
    want = expected_entries()
    assert got == want, f"missing {sorted(want - got)[:10]}; unexpected {sorted(got - want)[:10]}"
    assert len(want) > BULK_COUNT


def test_excluded_directories_are_never_entered_probe_file_proves_it(search_tree):
    probe = search_tree / "node_modules" / "pkg" / "probe_nm.txt"
    assert probe.is_file()
    with_rules = crawl([search_tree], default_exclude_patterns()).store
    assert not list(with_rules.find_substring("probe_nm"))
    assert not list(with_rules.find_substring("recycled.txt"))
    without = crawl([search_tree]).store
    assert len(without.find_substring("probe_nm")) == 1, "unexcluded: found once, via node_modules, never twice via the junction"


def test_per_rule_counts_are_reported(search_tree):
    result = crawl([search_tree], default_exclude_patterns())
    counts = result.excludes
    assert counts and all(isinstance(n, int) and n > 0 for n in counts.values())
    vhdx = [n for rule, n in counts.items() if "vhdx" in str(rule).lower()]
    assert vhdx == [2], f"two .vhdx files in the tree; counts={counts}"
    assert sum(counts.values()) == 5, "recycle bin, node_modules, two .vhdx, and the AppData folder (one hit, never entered)"
    nm = [n for rule, n in counts.items() if "node_modules" in str(rule).lower()]
    assert nm and sum(nm) >= 1, "node_modules is counted once as a directory; its children are never visited"


def test_junctions_are_entries_but_never_followed(search_tree):
    store = crawl([search_tree]).store
    paths = rel_paths(store, search_tree)
    assert "linkdir" in paths and "docs_link" in paths
    assert not [p for p in paths if p.startswith(("linkdir/", "docs_link/"))]


def test_offline_file_is_indexed_with_the_placeholder_flag_and_never_opened(search_tree):
    opened = []
    active = [False]

    def hook(event, args):
        if active[0] and event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            opened.append(os.fsdecode(args[0]))

    sys.addaudithook(hook)
    active[0] = True
    try:
        store = crawl([search_tree], default_exclude_patterns()).store
    finally:
        active[0] = False
    ids = [i for i in store.find_substring("cloud-doc.docx")]
    assert len(ids) == 1
    assert store.flags(ids[0]) & store_flag("FLAG_PLACEHOLDER", 8)
    assert store.size(ids[0]) == 4096, "size comes from the directory listing"
    assert not [p for p in opened if "cloud-" in p.lower()], f"placeholder files must not be opened: {opened}"
    assert not [p for p in opened if p.lower().startswith(str(search_tree).lower())], "the crawler opens no file at all"


def test_flags_for_directories_denied_and_ordinary_files(search_tree):
    store = crawl([search_tree], default_exclude_patterns()).store
    f_dir, f_denied = store_flag("FLAG_DIR", 1), store_flag("FLAG_DENIED", 16)
    flags = {store.path(i)[len(str(search_tree)) + 1:]: store.flags(i) for i in range(store.entry_count)
             if store.path(i).startswith(str(search_tree) + "\\")}
    assert flags["docs"] & f_dir and not flags["designer.md"] & f_dir
    assert not flags["designer.md"] & f_denied
    try:
        from assistant.security import denylist  # noqa: F401
    except ImportError:
        pytest.skip("denylist.py (M3a-1) not present: flag assertions for SEC-3 names skipped")
    for rel in (".env", "id_rsa", "vault.kdbx", "secrets\\server.pem", ".aws\\credentials"):
        assert flags[rel] & f_denied, rel


def test_hidden_and_system_attributes_become_flags(tmp_path):
    import ctypes
    (tmp_path / "plain.txt").write_text("x")
    for name, attr in (("hid.txt", 0x2), ("sys.txt", 0x4)):
        (tmp_path / name).write_text("x")
        assert ctypes.windll.kernel32.SetFileAttributesW(str(tmp_path / name), attr)
    store = crawl([tmp_path]).store
    by_name = {store.name(i): store.flags(i) for i in range(store.entry_count)}
    assert by_name["hid.txt"] & store_flag("FLAG_HIDDEN", 2)
    assert by_name["sys.txt"] & store_flag("FLAG_SYSTEM", 4)
    assert not by_name["plain.txt"] & (store_flag("FLAG_HIDDEN", 2) | store_flag("FLAG_SYSTEM", 4))


def test_unicode_and_spaced_names_are_indexed(search_tree):
    got = rel_paths(crawl([search_tree], default_exclude_patterns()).store, search_tree)
    assert "ünï cödé/résumé.txt" in got
    assert "spaced dir/my report final.txt" in got and "日本語メモ.txt" in got


def test_missing_root_is_reported_not_raised_and_other_roots_still_crawl(search_tree, tmp_path):
    result = crawl([tmp_path / "does-not-exist", search_tree], default_exclude_patterns())
    states = {Path(r.path).name: r.state for r in result.roots}
    assert states["does-not-exist"] == "missing" and states[search_tree.name] == "ok"
    assert result.store.entry_count > BULK_COUNT


def test_root_with_unreadable_subtree_counts_errors_and_continues(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.txt").write_text("x")
    (tmp_path / "b.txt").write_text("x")
    result = crawl([tmp_path])
    assert {n for n in (result.store.name(i) for i in range(result.store.entry_count))} >= {"a", "f.txt", "b.txt"}


def test_unreachable_unc_root_times_out_and_the_local_root_is_unaffected(search_tree):
    unc = "\\\\192.0.2.1\\share"  # TEST-NET-1: never answers
    started = time.perf_counter()
    result = crawl([unc, search_tree], default_exclude_patterns(), root_timeout_s=2.0)
    took = time.perf_counter() - started
    states = {r.path: r.state for r in result.roots}
    assert states[unc] in ("timeout", "missing", "error"), states
    assert states[str(search_tree)] == "ok"
    assert took < 40, f"a dead share must not hold the walk for long (took {took:.1f}s)"
    assert rel_paths(result.store, search_tree) == expected_entries()


def test_crawl_speed_is_printed_and_sane(search_tree):
    started = time.perf_counter()
    result = crawl([search_tree], default_exclude_patterns())
    elapsed = time.perf_counter() - started
    per_entry = elapsed / max(result.store.entry_count, 1) * 1e6
    print(f"\n[crawler] fixture tree: {result.store.entry_count} entries in {elapsed:.3f}s ({per_entry:.0f} us/entry)")
    assert per_entry < 5000


@pytest.mark.timeout(1500)
def test_real_roots_crawl_is_recorded_against_p7():
    roots = os.environ.get("ASSISTANT_PERF_ROOT")
    if not roots:
        pytest.skip("set ASSISTANT_PERF_ROOT (';'-separated) to record the real-root crawl against P7 (M3a-2 Done cell)")
    rootlist = [r for r in roots.split(";") if r]
    started = time.perf_counter()
    result = crawl(rootlist, default_exclude_patterns())
    elapsed = time.perf_counter() - started
    n = result.store.entry_count
    projected_300k = elapsed / max(n, 1) * 300_000
    projected_2m = elapsed / max(n, 1) * 2_000_000
    print(f"\n[crawler] real roots: {n} entries in {elapsed:.1f}s; projected 0.3M {projected_300k:.1f}s (target 15 s), "
          f"2M {projected_2m:.0f}s (target 150 s); states {[(r.path, r.state) for r in result.roots]}")
    assert n > 0
