"""Columnar index store (plan 4b / 7.3 M3a-1; AC-FI-2, PRD 12 query and RAM targets).

Builder contract: ``add_dir(path, parent_id) -> id`` (a root gets parent_id -1), ``add(parent_id, name, size, mtime,
flags) -> id``, ``finish() -> IndexStore``; flags are the module's FLAG_* constants; ``mtime`` is epoch seconds.
"""
import os
import pickle
import statistics
import time

import pytest

from conftest import ROOT_PARENT, percentile, row_paths

store_mod = pytest.importorskip("assistant.index.store", reason="assistant/index/store.py (M3a-1) not present")
FLAG_DIR = getattr(store_mod, "FLAG_DIR", 1)
FLAG_DENIED = getattr(store_mod, "FLAG_DENIED", 16)
FLAG_PLACEHOLDER = getattr(store_mod, "FLAG_PLACEHOLDER", 8)


def small_store():
    b = store_mod.IndexBuilder()
    root = b.add_dir("C:\\proj", ROOT_PARENT)
    docs = b.add_dir("C:\\proj\\docs", root)
    ids = {
        "readme": b.add(root, "README.md", 1200, 1_700_000_000.0, 0),
        "report": b.add(docs, "Report 2024.docx", 52000, 1_710_000_000.5, 0),
        "cafe": b.add(docs, "caf\u00e9.txt", 77, 1_720_000_000.0, 0),
        "cloud": b.add(docs, "cloud.docx", 4096, 1_730_000_000.0, FLAG_PLACEHOLDER),
        "secret": b.add(root, ".env", 64, 1_700_000_001.0, FLAG_DENIED),
    }
    return b.finish(), root, docs, ids


def test_accessors_return_what_the_builder_was_given():
    store, root, docs, ids = small_store()
    assert store.entry_count >= 5
    assert store.name(ids["report"]) == "Report 2024.docx"
    assert store.path(ids["report"]) == "C:\\proj\\docs\\Report 2024.docx"
    assert store.parent_path(ids["report"]) == "C:\\proj\\docs"
    assert store.size(ids["report"]) == 52000
    assert store.mtime(ids["report"]) == pytest.approx(1_710_000_000.5, abs=1e-3)
    assert store.flags(ids["cloud"]) & FLAG_PLACEHOLDER
    assert store.flags(ids["secret"]) & FLAG_DENIED
    assert not store.flags(ids["readme"]) & (FLAG_DIR | FLAG_DENIED)
    assert store.flags(docs) & FLAG_DIR and store.name(docs) == "docs" and store.path(docs) == "C:\\proj\\docs"
    assert store.built_at > 0


def test_find_substring_is_casefolded_and_returns_every_entry_once():
    store, _, _, ids = small_store()
    hits = set(store.find_substring("report"))
    assert ids["report"] in hits
    assert set(store.find_substring("README".casefold())) == {ids["readme"]}
    assert set(store.find_substring("caf\u00e9")) == {ids["cafe"]}
    assert list(store.find_substring("zzzz-not-present")) == []
    hits = list(store.find_substring("."))
    assert len(hits) == len(set(hits)), "an entry with two matches must be reported once"
    assert set(store.find_substring("docx")) >= {ids["report"], ids["cloud"]}


def test_find_substring_does_not_match_across_names():
    b = store_mod.IndexBuilder()
    root = b.add_dir("C:\\r", ROOT_PARENT)
    b.add(root, "abc", 1, 1.0, 0)
    b.add(root, "def", 1, 1.0, 0)
    assert list(b.finish().find_substring("cd")) == [], "names are separated, a term can never span two"


def test_iter_in_dir_lists_direct_children_only():
    store, root, docs, ids = small_store()
    assert {store.name(i) for i in store.iter_in_dir("C:\\proj\\docs")} == {"Report 2024.docx", "caf\u00e9.txt", "cloud.docx"}
    top = {store.name(i) for i in store.iter_in_dir("C:\\proj")}
    assert {"README.md", ".env", "docs"} <= top and "caf\u00e9.txt" not in top
    assert list(store.iter_in_dir("C:\\nope")) == []


def test_empty_store_is_valid():
    store = store_mod.IndexBuilder().finish()
    assert store.entry_count == 0 and list(store.find_substring("a")) == []


def test_save_load_round_trips_byte_identically(tmp_path):
    store, _, _, ids = small_store()
    first, second = tmp_path / "a.idx", tmp_path / "b.idx"
    store.save(str(first))
    loaded = store_mod.IndexStore.load(str(first))
    assert loaded.entry_count == store.entry_count
    assert loaded.built_at == pytest.approx(store.built_at)
    for i in range(store.entry_count):
        assert (loaded.name(i), loaded.path(i), loaded.size(i), loaded.mtime(i), loaded.flags(i)) == \
               (store.name(i), store.path(i), store.size(i), store.mtime(i), store.flags(i))
    assert list(loaded.find_substring("report")) == list(store.find_substring("report"))
    loaded.save(str(second))
    assert first.read_bytes() == second.read_bytes(), "save -> load -> save must be byte-identical"
    assert not list(tmp_path.glob("*.tmp")), "no temp file left behind"


def test_save_replaces_an_existing_file_atomically(tmp_path):
    store, _, _, _ = small_store()
    target = tmp_path / "idx.bin"
    target.write_bytes(b"old content")
    store.save(str(target))
    assert store_mod.IndexStore.load(str(target)).entry_count == store.entry_count


@pytest.mark.parametrize("junk", [b"", b"not an index", b"\x00" * 64, os.urandom(256)])
def test_loading_a_corrupt_or_foreign_file_raises_instead_of_returning_garbage(tmp_path, junk):
    bad = tmp_path / "bad.idx"
    bad.write_bytes(junk)
    with pytest.raises((ValueError, EOFError, pickle.UnpicklingError)):
        store_mod.IndexStore.load(str(bad))


def test_a_pickle_that_would_run_code_is_refused_without_running_it(tmp_path):
    """Loading must not execute arbitrary pickle payloads (restricted unpickler)."""
    import pickle
    marker = tmp_path / "pwned.txt"

    class Evil:
        def __reduce__(self):
            return (open, (str(marker), "w"))

    evil = tmp_path / "evil.idx"
    evil.write_bytes(pickle.dumps(Evil(), protocol=5))
    with pytest.raises(Exception):
        store_mod.IndexStore.load(str(evil))
    assert not marker.exists(), "the payload ran"
    wrapped = tmp_path / "evil2.idx"
    wrapped.write_bytes(pickle.dumps({"magic": store_mod.FORMAT_MAGIC, "version": store_mod.FORMAT_VERSION,
                                      "byteorder": __import__("sys").byteorder, "x": Evil()}, protocol=5))
    with pytest.raises(Exception):
        store_mod.IndexStore.load(str(wrapped))
    assert not marker.exists(), "the payload ran from inside a well-formed header"


def test_wrong_version_or_byte_order_is_a_value_error(tmp_path):
    import pickle
    import sys
    other = "big" if sys.byteorder == "little" else "little"
    for header in ({"magic": store_mod.FORMAT_MAGIC, "version": 9999, "byteorder": sys.byteorder},
                   {"magic": store_mod.FORMAT_MAGIC, "version": store_mod.FORMAT_VERSION, "byteorder": other},
                   {"magic": "something-else", "version": store_mod.FORMAT_VERSION, "byteorder": sys.byteorder}):
        bad = tmp_path / "h.idx"
        bad.write_bytes(pickle.dumps(header, protocol=5))
        with pytest.raises(ValueError):
            store_mod.IndexStore.load(str(bad))


def test_missing_file_raises_oserror_not_garbage(tmp_path):
    with pytest.raises(OSError):
        store_mod.IndexStore.load(str(tmp_path / "nope.idx"))


def test_truncated_index_file_is_rejected(tmp_path):
    store, _, _, _ = small_store()
    good = tmp_path / "g.idx"
    store.save(str(good))
    data = good.read_bytes()
    cut = tmp_path / "cut.idx"
    cut.write_bytes(data[: len(data) // 2])
    with pytest.raises((ValueError, EOFError, pickle.UnpicklingError)):
        store_mod.IndexStore.load(str(cut))


def test_walk_store_matches_the_real_tree(search_tree):
    from conftest import walk_store
    store = walk_store(search_tree)
    assert "designer.md" in [store.name(i) for i in store.find_substring("designer")]
    assert not list(store.find_substring("probe_nm")), "pruned directories are never entered"


# ---- IndexDelta ---------------------------------------------------------------------------------------------------

def test_delta_hides_removed_entries_and_is_thread_safe():
    import threading
    store, _, _, ids = small_store()
    delta = store_mod.IndexDelta()
    hits = store.find_substring("report")
    assert ids["report"] in hits
    delta.remove(store.path(ids["report"]))
    assert ids["report"] not in list(delta.apply_to(store.find_substring("report")))

    def hammer(n):
        for k in range(500):
            delta.add(f"C:\\proj\\t{n}_{k}.txt", 1, 1.0, 0)
            delta.remove(f"C:\\proj\\t{n}_{k - 1}.txt")
    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert not any(t.is_alive() for t in threads)


def test_delta_remove_replace_and_add_with_ids_past_entry_count():
    store, _, _, ids = small_store()
    base = store.entry_count
    delta = store_mod.IndexDelta()
    report = "C:\\proj\\docs\\Report 2024.docx"

    def hits(term):
        return list(delta.apply_to(store.find_substring(term)))

    assert hits("report") == [ids["report"]]

    delta.remove(report)
    assert hits("report") == [], "remove hides the indexed entry"

    delta.add(report, 999, 1_800_000_000.0, 0)
    got = hits("report")
    assert len(got) == 1 and got[0] >= base and got[0] != ids["report"], "replace: old id dropped, one new id past entry_count"
    assert store.size(got[0]) == 999 and store.mtime(got[0]) == pytest.approx(1_800_000_000.0)
    assert store.name(got[0]) == "Report 2024.docx" and store.path(got[0]).lower() == report.lower()

    delta.add("C:\\proj\\docs\\brand-new-report.txt", 5, 1.0, 0)
    got = hits("report")
    assert len(got) == 2 and all(g >= base for g in got)
    assert {store.name(g) for g in got} == {"Report 2024.docx", "brand-new-report.txt"}

    delta.remove("C:\\proj\\docs\\brand-new-report.txt")
    assert [store.name(g) for g in hits("report")] == ["Report 2024.docx"], "an add that is then removed is gone"
    assert hits("zzz-nothing") == []


def test_delta_paths_are_case_insensitive_and_unrelated_hits_are_untouched():
    store, _, _, ids = small_store()
    delta = store_mod.IndexDelta()
    delta.remove("C:\\PROJ\\DOCS\\REPORT 2024.DOCX")
    assert ids["report"] not in list(delta.apply_to(store.find_substring("report")))
    assert list(delta.apply_to(store.find_substring("readme"))) == [ids["readme"]]
    assert len(delta) == 1


def test_an_empty_delta_returns_the_hits_unchanged():
    store, _, _, _ = small_store()
    hits = store.find_substring("docx")
    assert list(store_mod.IndexDelta().apply_to(hits)) == list(hits)


def test_find_substring_limit_returns_the_first_ids_in_ascending_order(synthetic_store_2m):
    store, _, _ = synthetic_store_2m
    full = store.find_substring("inv")
    top = store.find_substring("inv", limit=500)
    assert list(top) == list(full[:500]) and list(top) == sorted(top)


# ---- PRD 12: 2,000,000 names -----------------------------------------------------------------------------------------------

def timed(store, term, **kw):
    t0 = time.perf_counter()
    hits = store.find_substring(term, **kw)
    return (time.perf_counter() - t0) * 1000, hits


def test_two_million_names_query_time_and_memory(synthetic_store_2m):
    """Tech-lead decision: the 50 ms gate applies to selective needles (cold); a dense needle is reported cold and warm
    (small result cache) and, with limit=500, must come back in < 5 ms."""
    store, build_s, private_delta = synthetic_store_2m
    assert store.entry_count >= 2_000_000
    selective_cold = []
    for n in range(50):
        ms, hits = timed(store, f"_{1_000_003 + n * 37}.")
        selective_cold.append(ms)
        assert len(hits) <= 1
    dense_cold, dense_hits = timed(store, "rep")
    dense_warm = [timed(store, "rep")[0] for _ in range(50)]
    limited = [timed(store, "rep", limit=500)[0] for _ in range(50)]
    assert len(dense_hits) > 100_000
    print(f"\n[index_store] 2M names: build {build_s:.1f}s; private working set delta {private_delta / 1048576:.0f} MB\n"
          f"[index_store]   selective needle cold: median {statistics.median(selective_cold):.1f} ms, "
          f"p95 {percentile(selective_cold, 95):.1f} ms (50 distinct needles)\n"
          f"[index_store]   dense needle 'rep' ({len(dense_hits)} hits): cold {dense_cold:.1f} ms, warm median "
          f"{statistics.median(dense_warm):.2f} ms, limit=500 median {statistics.median(limited):.2f} ms")
    assert statistics.median(selective_cold) < 50, "PRD 12: substring over 2M names <= 50 ms"
    assert percentile(selective_cold, 95) < 100
    assert statistics.median(limited) < 5, "dense needle with limit=500 must be < 5 ms"
    assert private_delta < 300 * 1048576, "PRD 12: index RAM target <= 300 MB"


def test_two_million_store_round_trips_and_names_survive(synthetic_store_2m, tmp_path):
    store, _, _ = synthetic_store_2m
    path = tmp_path / "big.idx"
    started = time.perf_counter()
    store.save(str(path))
    loaded = store_mod.IndexStore.load(str(path))
    print(f"\n[index_store] 2M save+load {time.perf_counter() - started:.1f}s, {path.stat().st_size / 1048576:.0f} MB on disk")
    assert loaded.entry_count == store.entry_count
    for i in (0, 1, 999_999, store.entry_count - 1):
        assert loaded.path(i) == store.path(i) and loaded.size(i) == store.size(i)
    assert len(loaded.find_substring("rep")) == len(store.find_substring("rep"))
