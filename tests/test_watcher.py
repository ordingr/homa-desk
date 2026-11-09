"""Index manager + watcher (plan 7.3 M3a-2; AC-FI-2 .. AC-FI-5, AC-SH-9 built_at source).

Real IndexManager on a private copy of the search tree, real ReadDirectoryChangesW watcher, real Scheduler; results
come from search.run over ``manager.current()``. Tests that must bypass the watcher stop it first and call the
manager's overflow handler (``_on_overflow``) directly; that is the only private name used.
"""
import os
import shutil
import threading
import time

import pytest

from conftest import BULK_COUNT, build_search_tree, default_exclude_patterns, wait_until

manager_mod = pytest.importorskip("homa.index.manager", reason="homa/index/manager.py (M3a-2) not present")
store_mod = pytest.importorskip("homa.index.store", reason="homa/index/store.py (M3a-1) not present")
pytest.importorskip("homa.index.excludes", reason="homa/index/excludes.py (M3a-1) not present")
query = pytest.importorskip("homa.index.query", reason="homa/index/query.py (M3a-3) not present")
search = pytest.importorskip("homa.index.search", reason="homa/index/search.py (M3a-3) not present")
from homa.scheduler import Scheduler  # noqa: E402


class FakeSettings:
    def __init__(self, **index):
        self.index = index
        self.callbacks = []

    def get(self, key, default=None):
        return self.index if key == "index" else default

    def on_change(self, key, fn):
        self.callbacks.append((key, fn))

    def update(self, **index):
        self.index.update(index)
        for key, fn in self.callbacks:
            if key == "index":
                fn("index", self.index)


class Rig:
    def __init__(self, root, store_path, **extra):
        self.root, self.store_path = root, store_path
        self.settings = FakeSettings(roots=[str(root)], excludes=default_exclude_patterns(), **extra)
        self.scheduler = Scheduler()
        self.scheduler.start()
        self.manager = manager_mod.IndexManager(self.settings, self.scheduler, store_path)

    def start(self):
        self.manager.start()
        return self

    def stop(self):
        self.manager.stop()
        self.scheduler.stop()

    def first_walk(self, timeout=90):
        assert wait_until(lambda: self.manager.status()["built_at"] and not self.manager.status()["walking"], timeout, 0.1), \
            self.manager.status()

    def names(self, text, **kw):
        store, delta = self.manager.current()
        res = search.run(query.parse(text, {"md", "txt", "py"}), store, delta, **kw)
        return [r.name for r in res.rows]

    def has(self, text):
        return text in self.names(text)


@pytest.fixture
def rig(tmp_path):
    root = build_search_tree(tmp_path / "root")
    r = Rig(root, tmp_path / "index" / "files.idx").start()
    r.first_walk()
    yield r
    r.stop()


# ---- first walk ------------------------------------------------------------------------------------------------------

def test_first_walk_indexes_the_tree_and_reports_status(rig):
    st = rig.manager.status()
    assert st["entries"] > BULK_COUNT and st["built_at"] > 0 and st["walking"] is False
    assert st["roots"][0]["state"] == "ok" and st["roots"][0]["entries"] > 0
    assert st["excludes"], "per-rule exclude counts are exposed for Settings (AC-FI-7)"
    assert rig.has("designer.md") and not rig.names("probe_nm")
    assert (rig.store_path).is_file(), "the finished walk is persisted"


# ---- AC-FI-3: live changes within 5 s -------------------------------------------------------------------------------------

def test_create_appears_within_5_seconds(rig):
    started = time.monotonic()
    (rig.root / "docs" / "fresh-created.txt").write_text("x")
    assert wait_until(lambda: rig.has("fresh-created.txt"), 5.0, 0.05), "create not visible within 5 s"
    print(f"\n[watcher] create visible after {time.monotonic() - started:.2f}s")


def test_rename_and_delete_are_reflected_within_5_seconds(rig):
    target = rig.root / "docs" / "to-rename.txt"
    target.write_text("x")
    assert wait_until(lambda: rig.has("to-rename.txt"), 5.0)
    target.rename(rig.root / "docs" / "renamed-now.txt")
    assert wait_until(lambda: rig.has("renamed-now.txt") and not rig.has("to-rename.txt"), 5.0), \
        (rig.names("rename"), rig.names("to-rename"))
    (rig.root / "docs" / "renamed-now.txt").unlink()
    assert wait_until(lambda: not rig.has("renamed-now.txt"), 5.0)
    existing = rig.root / "designer.md"
    existing.unlink()
    assert wait_until(lambda: not rig.names("designer.md"), 5.0), "deleting an originally indexed file"


def test_new_directory_with_files_is_scanned_in_and_deleting_it_removes_the_children(rig):
    folder = rig.root / "brand_new_dir"
    folder.mkdir()
    for n in range(5):
        (folder / f"inside_{n}.txt").write_text("x")
    assert wait_until(lambda: len(rig.names("inside_")) == 5, 5.0), rig.names("inside_")
    shutil.rmtree(folder)
    assert wait_until(lambda: not rig.names("inside_") and not rig.names("brand_new_dir"), 5.0)


def test_changes_under_excluded_directories_and_deny_listed_names_never_surface(rig):
    (rig.root / "node_modules" / "late-probe.js").write_text("x")
    (rig.root / "newfolder.vhdx").write_text("x")
    (rig.root / "docs" / "late.env").write_text("x")
    (rig.root / "docs" / "visible-marker.txt").write_text("x")
    assert wait_until(lambda: rig.has("visible-marker.txt"), 5.0)
    assert not rig.names("late-probe") and not rig.names("newfolder.vhdx")
    (rig.root / ".env.production").write_text("x")
    (rig.root / "id_rsa_backup").write_text("x")
    time.sleep(1.0)
    try:
        from homa.security import denylist  # noqa: F401
    except ImportError:
        return
    assert not rig.names(".env.production") and not rig.names("id_rsa_backup")


# ---- freshness honesty (AC-FI-3, AC-SH-9) ----------------------------------------------------------------------------------

def test_hints_never_change_built_at_only_a_finished_walk_does(rig):
    before = rig.manager.status()["built_at"]
    (rig.root / "docs" / "hint-only.txt").write_text("x")
    assert wait_until(lambda: rig.has("hint-only.txt"), 5.0)
    assert rig.manager.status()["built_at"] == before
    time.sleep(1.1)
    rig.manager.request_rescan("test")
    assert wait_until(lambda: rig.manager.status()["built_at"] > before and not rig.manager.status()["walking"], 60, 0.1)
    assert rig.has("hint-only.txt"), "a rescan keeps the file"
    store, _ = rig.manager.current()
    assert store.built_at == rig.manager.status()["built_at"]


def test_overflow_ends_with_a_correct_index_after_the_rescan(rig):
    """A lost-notification overflow (ERROR_NOTIFY_ENUM_DIR) must end in a rescan that repairs the index."""
    for w in list(rig.manager._watchers.values()):
        w.stop()
    rig.manager._watchers.clear()
    before = rig.manager.status()["built_at"]
    bulk = rig.root / "overflow"
    bulk.mkdir()
    for n in range(2000):
        (bulk / f"quick_{n:05d}.txt").write_text("")
    (rig.root / "designer.md").unlink()
    assert not rig.names("quick_00001"), "no watcher is running, so the index is stale on purpose"
    time.sleep(1.1)
    rig.manager._on_overflow()
    assert wait_until(lambda: rig.manager.status()["built_at"] > before and not rig.manager.status()["walking"], 60, 0.1)
    assert rig.manager.status()["overflow_count"] >= 1
    store, delta = rig.manager.current()
    assert len(search.run(query.parse("quick_", set()), store, delta, limit=5000).rows) == 2000
    assert not rig.names("designer.md")
    assert rig.manager.status()["built_at"] > before


def test_a_burst_of_real_creates_converges(rig):
    folder = rig.root / "flooddir"
    folder.mkdir()
    for n in range(4000):
        (folder / f"zxflood_{n:05d}.txt").write_text("")

    def count():
        store, delta = rig.manager.current()
        return search.run(query.parse("zxflood_", set()), store, delta, limit=1).total
    assert wait_until(lambda: count() == 4000, 90, 0.5), f"index shows {count()} of 4000 after the burst"


# ---- persistence (AC-FI-2) ----------------------------------------------------------------------------------------------------

def test_persisted_index_serves_immediately_with_its_old_built_at_then_rescans(tmp_path):
    root = build_search_tree(tmp_path / "root")
    first = Rig(root, tmp_path / "idx" / "files.idx").start()
    first.first_walk()
    old_built_at = first.manager.status()["built_at"]
    first.stop()
    time.sleep(1.1)
    # M3b-0 (b): a persisted index younger than index.scan_if_older_hours is
    # served without a start scan, so age it past the threshold on disk first.
    from homa.index.store import IndexStore  # noqa: E402

    aged = IndexStore.load(str(tmp_path / "idx" / "files.idx"))
    old_built_at = aged.built_at - 13 * 3600
    aged.built_at = old_built_at
    aged.save(str(tmp_path / "idx" / "files.idx"))
    second = Rig(root, tmp_path / "idx" / "files.idx")
    try:
        second.manager.start()
        store, _ = second.manager.current()
        assert store.entry_count > BULK_COUNT, "search works at once from the persisted index"
        st = second.manager.status()
        assert st["loaded_from_disk"] is True
        assert st["built_at"] == pytest.approx(old_built_at, abs=1.0), "freshness must not claim a newer index"
        assert wait_until(lambda: second.manager.status()["built_at"] > old_built_at + 1 and not second.manager.status()["walking"], 90, 0.1)
        assert second.manager.status()["loaded_from_disk"] is False
    finally:
        second.stop()


def test_corrupt_persisted_index_falls_back_to_a_fresh_walk(tmp_path):
    root = build_search_tree(tmp_path / "root", bulk=5)
    store_path = tmp_path / "idx" / "files.idx"
    store_path.parent.mkdir()
    store_path.write_bytes(b"garbage, not an index")
    r = Rig(root, store_path).start()
    try:
        r.first_walk()
        assert r.has("designer.md")
    finally:
        r.stop()


# ---- settings live apply, dead share, empty roots ---------------------------------------------------------------------------------

def test_adding_a_root_in_settings_starts_a_crawl_without_restart(tmp_path):
    root_a = build_search_tree(tmp_path / "a", bulk=5)
    other = tmp_path / "b"
    other.mkdir()
    (other / "second-root-file.txt").write_text("x")
    r = Rig(root_a, tmp_path / "idx" / "f.idx").start()
    try:
        r.first_walk()
        assert not r.names("second-root-file")
        r.settings.update(roots=[str(root_a), str(other)])
        assert wait_until(lambda: r.has("second-root-file.txt"), 30, 0.2)
        r.settings.update(roots=[str(other)])
        assert wait_until(lambda: not r.names("designer.md") and r.has("second-root-file.txt"), 30, 0.2)
    finally:
        r.stop()


def test_no_roots_means_an_idle_empty_index(tmp_path):
    r = Rig(tmp_path / "root", tmp_path / "idx" / "f.idx")
    r.settings.index["roots"] = []
    r.start()
    try:
        assert wait_until(lambda: not r.manager.status()["walking"], 10, 0.1)
        assert r.manager.status()["entries"] == 0
    finally:
        r.stop()


def test_unreachable_unc_root_is_skipped_after_the_timeout_and_status_stays_responsive(tmp_path):
    root = build_search_tree(tmp_path / "root", bulk=5)
    unc = "\\\\192.0.2.1\\share"
    r = Rig(root, tmp_path / "idx" / "f.idx", root_timeout_s=2)
    r.settings.index["roots"] = [str(root), unc]
    r.start()
    try:
        slowest, deadline = 0.0, time.monotonic() + 60
        while time.monotonic() < deadline:
            t0 = time.perf_counter()
            st = r.manager.status()
            slowest = max(slowest, time.perf_counter() - t0)
            states = {x["path"]: x["state"] for x in st["roots"]}
            if st["built_at"] and not st["walking"] and states.get(unc) not in (None, "pending", "walking"):
                break
            time.sleep(0.2)
        assert states[unc] in ("timeout", "missing"), states
        assert states[str(root)] == "ok" and r.has("designer.md")
        assert slowest < 0.5, f"status() blocked for {slowest:.2f}s while the share hung"
    finally:
        r.stop()


def test_stop_joins_threads_promptly(tmp_path):
    root = build_search_tree(tmp_path / "root", bulk=5)
    r = Rig(root, tmp_path / "idx" / "f.idx").start()
    r.first_walk()
    before = {t.name for t in threading.enumerate()}
    started = time.perf_counter()
    r.stop()
    assert time.perf_counter() - started < 10
    assert wait_until(lambda: not {t.name for t in threading.enumerate()} & {"index-walk", "index-hints", "index-watcher"}, 5)
    assert before & {"index-walk", "index-hints"}
