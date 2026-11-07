"""Watcher churn and delta-fold behaviour (plan 19.3 M4-0 item 1; AC-FI-3 idle).

The M3b measurement: while another process churned a big folder, the app burned 18.5 % of a core and ran one
7.7-second full walk, because ``_bump_delta`` crossed ``DELTA_FOLD_LIMIT`` and asked for a fold 1 s later. M4-0 makes the
fold wait for ``FOLD_QUIET_S`` of quiet after the last hint batch, resets the counter only when a walk finishes, lets a
manual/daily/overflow request take over a pending fold, and drops a MODIFIED that follows an ADDED for the same path in
one batch.

Fast tests drive the real ``IndexManager`` (real watcher, real scheduler, tiny tree) with the constants patched small.
The slow test is the plan's perf gate: a 400k-entry synthetic store, a ~1,000-op burst, CPU averaged over the next
minute, and no full walk in that minute.
"""
import os
import shutil
import threading
import time

import pytest

from conftest import wait_until

manager_mod = pytest.importorskip("assistant.index.manager")
store_mod = pytest.importorskip("assistant.index.store")
watcher_mod = pytest.importorskip("assistant.index.watcher")
from assistant.scheduler import Scheduler  # noqa: E402

ADDED, REMOVED, MODIFIED = watcher_mod.ACTION_ADDED, watcher_mod.ACTION_REMOVED, watcher_mod.ACTION_MODIFIED
RENAMED_NEW = watcher_mod.ACTION_RENAMED_NEW


class FakeSettings:
    def __init__(self, roots, excludes=None):
        self.index = {"roots": [str(r) for r in roots], "excludes": excludes if excludes is not None else [],
                      "scan_if_older_hours": 168}
        self.callbacks = []

    def get(self, key, default=None):
        return self.index if key == "index" else default

    def on_change(self, key, fn):
        self.callbacks.append((key, fn))


class Rig:
    """A started manager whose ``_walk`` calls are recorded as (monotonic time, reason)."""

    def __init__(self, root, store_path, excludes=None):
        self.root = root
        self.scheduler = Scheduler()
        self.scheduler.start()
        self.manager = manager_mod.IndexManager(FakeSettings([root], excludes), self.scheduler, store_path)
        self.walks = []
        real = self.manager._walk

        def recording(reason):
            self.walks.append((time.monotonic(), reason))
            real(reason)

        self.manager._walk = recording

    def start(self):
        self.manager.start()
        assert wait_until(lambda: self.manager.status()["built_at"] and not self.manager.status()["walking"], 60, 0.1), \
            self.manager.status()
        self.walks.clear()
        return self

    def stop(self):
        self.manager.stop()
        self.scheduler.stop()

    def reasons(self):
        return [r for _, r in self.walks]


@pytest.fixture
def small_rig(tmp_path):
    root = tmp_path / "root"
    (root / "work").mkdir(parents=True)
    (root / "work" / "seed.txt").write_text("seed")
    rig = Rig(root, tmp_path / "index" / "files.idx")
    yield rig
    rig.stop()


def _create_files(folder, count, prefix="n"):
    os.makedirs(folder, exist_ok=True)
    for i in range(count):
        with open(os.path.join(folder, f"{prefix}{i}.txt"), "w") as fh:
            fh.write("x")


# ---- the fold waits for quiet ----------------------------------------------------------------------------------------

def test_crossing_the_fold_limit_does_not_walk_until_the_quiet_window_has_passed(small_rig, monkeypatch):
    monkeypatch.setattr(manager_mod, "DELTA_FOLD_LIMIT", 20)
    monkeypatch.setattr(manager_mod, "FOLD_QUIET_S", 3.0)
    rig = small_rig.start()
    _create_files(rig.root / "work" / "burst", 60)
    # the limit is crossed once the hints are applied
    assert wait_until(lambda: rig.manager.status()["delta_ops"] > 20, 15, 0.05), rig.manager.status()
    crossed = time.monotonic()
    last_hint = rig.manager._last_hint_at
    # the old code asked for the fold OVERFLOW_DEBOUNCE_S (1 s) later: nothing may walk inside the quiet window
    time.sleep(1.8)
    assert rig.reasons() == [], f"a walk ran {time.monotonic() - crossed:.1f}s after the limit was crossed: {rig.walks}"
    assert rig.manager.status()["delta_ops"] > 20, "the delta counter must survive until a walk finishes"
    # ...and it does fold once the quiet window is over
    assert wait_until(lambda: "delta-fold" in rig.reasons(), 15, 0.1), (rig.walks, rig.manager.status())
    started = next(t for t, r in rig.walks if r == "delta-fold")
    assert started - rig.manager._last_hint_at >= 3.0 - 0.05 or started - last_hint >= 3.0 - 0.05, \
        (started - last_hint, "the fold must be at least FOLD_QUIET_S after the last hint batch")
    assert wait_until(lambda: not rig.manager.status()["walking"] and rig.manager.status()["delta_ops"] == 0, 30, 0.1), \
        rig.manager.status()
    # the folded index has the new files in its base store, no delta needed
    store, _ = rig.manager.current()
    names = {store.name(i) if hasattr(store, "name") else None for i in range(store.entry_count)}
    assert "n59.txt" in names


def test_a_continuing_burst_keeps_pushing_the_fold_back(small_rig, monkeypatch):
    monkeypatch.setattr(manager_mod, "DELTA_FOLD_LIMIT", 10)
    monkeypatch.setattr(manager_mod, "FOLD_QUIET_S", 2.5)
    rig = small_rig.start()
    t0 = time.monotonic()
    for wave in range(5):                       # activity every ~0.8 s for ~4 s, longer than FOLD_QUIET_S
        _create_files(rig.root / "work" / f"wave{wave}", 12)
        time.sleep(0.8)
    assert rig.manager.status()["delta_ops"] > 10
    assert rig.reasons() == [], f"folded while the folder was still busy ({time.monotonic() - t0:.1f}s): {rig.walks}"
    assert wait_until(lambda: "delta-fold" in rig.reasons(), 15, 0.1), rig.walks


# ---- a real request takes over a pending fold -----------------------------------------------------------------------

def test_manual_request_upgrades_a_pending_delta_fold_and_runs_without_the_quiet_wait():
    mgr = manager_mod.IndexManager.__new__(manager_mod.IndexManager)
    mgr._cond = threading.Condition()
    mgr._requested, mgr._due = None, 0.0
    now = time.monotonic()
    mgr.request_rescan("delta-fold", delay=90.0)
    assert mgr._requested == "delta-fold" and mgr._due >= now + 89
    mgr.request_rescan("manual")
    assert mgr._requested == "manual", "a manual request must replace the pending fold's reason"
    assert mgr._due <= time.monotonic() + 0.5, "...and its due time (the fold's long delay must not survive)"


@pytest.mark.parametrize("pending", ["manual", "daily", "overflow"])
def test_a_pending_real_reason_is_not_downgraded_to_delta_fold(pending):
    mgr = manager_mod.IndexManager.__new__(manager_mod.IndexManager)
    mgr._cond = threading.Condition()
    mgr._requested, mgr._due = None, 0.0
    mgr.request_rescan(pending)
    mgr.request_rescan("delta-fold", delay=90.0)
    assert mgr._requested == pending
    assert mgr._due <= time.monotonic() + 0.5


def test_manual_rescan_walks_immediately_even_while_a_fold_is_waiting_out_the_quiet_window(small_rig, monkeypatch):
    monkeypatch.setattr(manager_mod, "FOLD_QUIET_S", 120.0)
    rig = small_rig.start()
    rig.manager._last_hint_at = time.monotonic()        # busy right now
    rig.manager.request_rescan("delta-fold")            # due now, but the quiet window holds it
    time.sleep(1.2)
    assert rig.reasons() == [], rig.walks
    rig.manager.request_rescan("manual")
    assert wait_until(lambda: rig.reasons() == ["manual"], 10, 0.1), rig.walks


def test_a_finished_walk_resets_the_delta_counter(small_rig):
    rig = small_rig.start()
    rig.manager._delta_ops = 1234
    rig.manager.request_rescan("manual")
    assert wait_until(lambda: rig.reasons() == ["manual"] and not rig.manager.status()["walking"], 20, 0.1)
    assert rig.manager.status()["delta_ops"] == 0


# ---- per-batch work -------------------------------------------------------------------------------------------------

class _HintHarness:
    """The real ``_hint_loop`` with ``_apply_hint`` replaced by a recorder (no store needed)."""

    def __init__(self, monkeypatch):
        monkeypatch.setattr(manager_mod, "HINT_IDLE_WAIT_S", 0.1)
        self.mgr = manager_mod.IndexManager.__new__(manager_mod.IndexManager)
        mgr = self.mgr
        mgr._stop = threading.Event()
        mgr._hints = manager_mod.queue.SimpleQueue()
        mgr._replay = manager_mod.collections.deque()
        mgr._replay_lock = threading.Lock()
        mgr._replay_overflowed = False
        mgr._walking = False
        mgr._last_hint_at = float("-inf")
        mgr._batch_scan_left = manager_mod.BATCH_SCAN_BUDGET
        self.applied = []
        mgr._apply_hint = lambda action, path, root: self.applied.append((action, path))
        self.thread = threading.Thread(target=mgr._hint_loop, daemon=True)

    def run(self, hints):
        for h in hints:
            self.mgr._hints.put(h)                       # all queued before the loop starts: one batch
        self.thread.start()
        assert wait_until(lambda: len(self.applied) >= 1 and self.mgr._hints.empty(), 5, 0.02)
        time.sleep(0.5)
        self.mgr._stop.set()
        self.thread.join(5)
        return self.applied


def test_modified_after_added_for_the_same_path_in_one_batch_is_dropped(monkeypatch):
    root = r"C:\r"
    a, b = r"C:\r\a.txt", r"C:\r\b.txt"
    applied = _HintHarness(monkeypatch).run([
        (ADDED, a, root), (MODIFIED, a, root), (MODIFIED, a, root),     # create + write storm for one file
        (MODIFIED, b, root),                                            # a plain write is kept (once)
        (MODIFIED, b, root),
        (RENAMED_NEW, a, root), (MODIFIED, a, root),                    # rename target written to: dropped
    ])
    assert applied == [(ADDED, a), (MODIFIED, b), (RENAMED_NEW, a)], applied


def test_a_modified_after_a_removal_is_kept_because_the_file_came_back(monkeypatch):
    root, a = r"C:\r", r"C:\r\a.txt"
    applied = _HintHarness(monkeypatch).run([(ADDED, a, root), (REMOVED, a, root), (MODIFIED, a, root)])
    assert applied == [(ADDED, a), (REMOVED, a), (MODIFIED, a)], applied


def test_one_batch_cannot_scan_more_than_the_batch_budget(tmp_path, monkeypatch):
    """Ten new directories of 40 files each in one batch with a budget of 100: the mini-scans stop early and a
    delta-fold (quiet-delayed) is requested for the rest."""
    monkeypatch.setattr(manager_mod, "BATCH_SCAN_BUDGET", 100)
    root = tmp_path / "root"
    dirs = []
    for d in range(10):
        folder = root / f"d{d}"
        _create_files(folder, 40)
        dirs.append(str(folder))
    builder = store_mod.IndexBuilder()
    builder.add_dir(str(root), -1)
    store = builder.finish()
    mgr = manager_mod.IndexManager.__new__(manager_mod.IndexManager)
    mgr._stop = threading.Event()
    mgr._cond = threading.Condition()
    mgr._requested, mgr._due = None, 0.0
    mgr._delta_ops = 0
    mgr._delta_paths = set()
    mgr._excludes = None
    mgr._matches = lambda p: False
    mgr._batch_scan_left = manager_mod.BATCH_SCAN_BUDGET
    delta = manager_mod._default_delta_factory()
    mgr._state = (store, delta)
    for d in dirs:
        mgr._apply_hint(ADDED, d, str(root))
    scanned = len([p for p in mgr._delta_paths if p.endswith(".txt")])
    assert scanned <= 100 + 40, f"{scanned} files scanned in one batch with a budget of 100"
    assert scanned < 400
    assert mgr._requested == "delta-fold"
    assert mgr._due >= time.monotonic() + manager_mod.FOLD_QUIET_S - 5


# ---- the plan's perf gate -------------------------------------------------------------------------------------------

def _build_big_store(root):
    b = store_mod.IndexBuilder()
    rid = b.add_dir(str(root), -1)
    count = 1

    def mkdir(path, parent):
        nonlocal count
        count += 1
        return b.add_dir(path, parent)

    big = mkdir(os.path.join(root, "bigtree"), rid)
    for i in range(60):
        sid = mkdir(os.path.join(root, "bigtree", f"s{i}"), big)
        for j in range(1000):
            b.add(sid, f"file{j}.dat", 10, 1.0, 0)
            count += 1
    bulk = mkdir(os.path.join(root, "bulk"), rid)
    for i in range(3000):
        sid = mkdir(os.path.join(root, "bulk", f"d{i}"), bulk)
        for j in range(110):
            b.add(sid, f"n{j}.txt", 10, 1.0, 0)
            count += 1
    store = b.finish()
    store.built_at = time.time()
    store.roots = (str(root),)
    return store


@pytest.mark.slow
@pytest.mark.timeout(420)
@pytest.mark.parametrize("fold_limit", [manager_mod.DELTA_FOLD_LIMIT, 500], ids=["default-limit", "limit-500-crossed"])
def test_a_thousand_op_burst_on_a_400k_store_costs_under_2_percent_and_never_walks(tmp_path, monkeypatch, fold_limit):
    monkeypatch.setattr(manager_mod, "DELTA_FOLD_LIMIT", fold_limit)
    root = tmp_path / "root"
    work = root / "work"
    for i in range(200):
        d = work / f"d{i}"
        d.mkdir(parents=True)
        for j in range(5):
            (d / f"f{j}.txt").write_text("x")
    store = _build_big_store(str(root))
    assert store.entry_count >= 390_000
    idx = tmp_path / "idx" / "files.idx"
    idx.parent.mkdir()
    store.save(str(idx))

    scheduler = Scheduler()
    scheduler.start()
    mgr = manager_mod.IndexManager(FakeSettings([root]), scheduler, str(idx))
    walks = []
    real = mgr._walk
    mgr._walk = lambda reason: (walks.append((round(time.monotonic() - t_burst, 1), reason)), real(reason))[1]
    t_burst = time.monotonic()
    try:
        mgr.start()
        assert wait_until(lambda: mgr.status()["entries"] >= 390_000, 60, 0.2), mgr.status()
        time.sleep(3)
        walks.clear()

        t_burst = time.monotonic()
        cpu0 = time.process_time()
        ops = 0
        for k in range(600):                                     # 600 modifications
            with open(work / f"d{k % 200}" / f"f{k % 5}.txt", "a") as fh:
                fh.write("y")
            ops += 1
        for k in range(80):                                      # 80 new dirs x 3 files
            d = work / f"new{k}"
            d.mkdir()
            ops += 1
            for j in range(3):
                (d / f"a{j}.txt").write_text("z")
                ops += 1
        for k in range(40):                                      # 40 of them removed again
            shutil.rmtree(work / f"new{k}")
            ops += 1
        assert ops >= 900
        time.sleep(max(0.0, 60.0 - (time.monotonic() - t_burst)))
        elapsed = time.monotonic() - t_burst
        cpu = time.process_time() - cpu0
        pct = cpu / elapsed * 100
        status = mgr.status()
        print(f"\n[perf] limit={fold_limit} ops={ops} window={elapsed:.1f}s cpu={cpu:.2f}s = {pct:.2f}% of one core, "
              f"delta_ops={status['delta_ops']}, walks={walks}")
        assert walks == [], f"a full walk ran within a minute of the burst: {walks}"
        assert not status["walking"]
        assert pct < 2.0, f"{pct:.2f}% of one core over the minute after the burst"
        if fold_limit == 500:
            assert status["delta_ops"] > 500, "the variant is only meaningful if the fold limit was really crossed"
    finally:
        mgr.stop()
        scheduler.stop()
