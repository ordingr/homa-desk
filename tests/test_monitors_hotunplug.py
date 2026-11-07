"""Monitor hot-unplug (plan 7.5 M4-2; AC-MM-4).

Unit layer: the real `monitors` module and `DisplayWatcher.check` over a faked two-monitor Win32 enumeration and a
fake icon (no desktop windows). Live layer: a private test-mode app whose icon is saved on a secondary monitor,
then `POST /v1/_debug/monitors` removes that monitor; skipped on a single-monitor machine.
"""
import json

import pytest

from conftest import wait_until

monitors = pytest.importorskip("assistant.overlay.monitors", reason="monitors.py not present")

M1 = (0, 0, 1920, 1080)
M2 = (1920, 0, 3840, 1080)


@pytest.fixture
def two_monitors(monkeypatch):
    table = {101: {"Monitor": M1, "Work": (0, 0, 1920, 1040), "Flags": 1},
             202: {"Monitor": M2, "Work": (1920, 0, 3840, 1040), "Flags": 0}}
    monkeypatch.setattr(monitors.win32api, "EnumDisplayMonitors", lambda *a: [(h, None, None) for h in table])
    monkeypatch.setattr(monitors.win32api, "GetMonitorInfo", lambda h: table[h])
    monkeypatch.setattr(monitors, "_dpi_for", lambda h: 96)
    monitors._hidden.clear()
    yield table
    monitors._hidden.clear()


@pytest.fixture
def test_mode(monkeypatch):
    monkeypatch.setenv("ASSISTANT_TEST_MODE", "1")


class FakeIcon:
    size = 48

    def __init__(self, x, y):
        self.x, self.y = x, y
        self.resets = 0

    def position(self):
        return self.x, self.y

    def reset_position(self):
        self.resets += 1
        l, t, r, b = monitors.primary_monitor().work
        self.x, self.y = r - self.size - 16, b - self.size - 16


def watcher(icon, moved=None):
    return monitors.DisplayWatcher(icon, root=None, on_moved=moved)


# ---- simulate_remove / simulate_restore -----------------------------------------------------------------------------

def test_simulate_remove_hides_the_monitor_and_restore_brings_it_back(two_monitors, test_mode):
    assert [m.handle for m in monitors.list_monitors()] == [101, 202]
    gone = monitors.simulate_remove(1)
    assert gone.handle == 202 and monitors.simulated_hidden() == [202]
    assert [m.handle for m in monitors.list_monitors()] == [101]
    monitors.simulate_restore()
    assert monitors.simulated_hidden() == [] and [m.handle for m in monitors.list_monitors()] == [101, 202]


def test_refuses_to_remove_the_only_monitor(two_monitors, test_mode):
    monitors.simulate_remove(1)
    with pytest.raises(ValueError, match="only monitor"):
        monitors.simulate_remove(0)
    assert [m.handle for m in monitors.list_monitors()] == [101]


@pytest.mark.parametrize("idx", [-1, 2, 99])
def test_out_of_range_index_is_an_index_error(two_monitors, test_mode, idx):
    with pytest.raises(IndexError):
        monitors.simulate_remove(idx)
    assert monitors.simulated_hidden() == []


def test_seams_raise_outside_test_mode(two_monitors, monkeypatch):
    monkeypatch.delenv("ASSISTANT_TEST_MODE", raising=False)
    with pytest.raises(RuntimeError, match="ASSISTANT_TEST_MODE"):
        monitors.simulate_remove(1)
    with pytest.raises(RuntimeError, match="ASSISTANT_TEST_MODE"):
        monitors.simulate_restore()
    monkeypatch.setenv("ASSISTANT_TEST_MODE", "0")
    with pytest.raises(RuntimeError):
        monitors.simulate_remove(1)
    assert monitors.simulated_hidden() == []


# ---- DisplayWatcher.check -----------------------------------------------------------------------------------------------

def test_icon_on_the_removed_monitor_moves_to_the_primary_work_area(two_monitors, test_mode):
    moved = []
    icon = FakeIcon(2500, 300)
    w = watcher(icon, lambda x, y: moved.append((x, y)))
    assert w.check() is False and icon.resets == 0, "monitor still present: no move"
    monitors.simulate_remove(1)
    assert w.check() is True
    assert icon.resets == 1 and moved == [icon.position()] and w.moves == 1
    l, t, r, b = two_monitors[101]["Work"]
    assert l <= icon.x < r and t <= icon.y < b and icon.y + icon.size <= b


def test_icon_on_a_surviving_monitor_stays_put(two_monitors, test_mode):
    icon = FakeIcon(100, 100)
    w = watcher(icon)
    monitors.simulate_remove(1)
    assert w.check() is False and icon.position() == (100, 100) and w.moves == 0


def test_restoring_the_monitor_does_not_move_the_icon_back(two_monitors, test_mode):
    icon = FakeIcon(2500, 300)
    w = watcher(icon)
    monitors.simulate_remove(1)
    w.check()
    parked = icon.position()
    monitors.simulate_restore()
    assert w.check() is False and icon.position() == parked and w.moves == 1


def test_icon_whose_centre_is_off_every_monitor_is_moved(two_monitors, test_mode):
    icon = FakeIcon(-5000, 9000)
    assert watcher(icon).check() is True and icon.resets == 1


def test_a_stopped_watcher_does_nothing(two_monitors, test_mode):
    icon = FakeIcon(2500, 300)
    w = watcher(icon)
    monitors.simulate_remove(1)
    w.stop()
    assert w.check() is False and icon.resets == 0


def test_an_icon_error_is_logged_not_raised(two_monitors, test_mode):
    class Broken(FakeIcon):
        def position(self):
            raise RuntimeError("window gone")

    assert watcher(Broken(0, 0)).check() is False


def test_watcher_registers_for_simulated_changes(two_monitors, test_mode):
    w = watcher(FakeIcon(0, 0))
    monitors._watchers.append(w)
    posted = []
    try:
        import assistant.uibus as uibus
        orig = uibus.post
        uibus.post = lambda fn, *a: posted.append(fn)
        try:
            monitors.simulate_remove(1)
        finally:
            uibus.post = orig
    finally:
        monitors._watchers.remove(w)
    assert w.check in posted


def test_real_display_watcher_window_starts_and_stops():
    w = monitors.DisplayWatcher(FakeIcon(0, 0), root=None)
    started = w.start()
    try:
        assert started is True and w.debug_state()["listening"] is True
    finally:
        w.stop()
    assert w not in monitors._watchers


# ---- live app ----------------------------------------------------------------------------------------------------------------

def test_live_app_icon_follows_the_unplug(app_factory, tmp_path):
    real = monitors.list_monitors()
    if len(real) < 2:
        pytest.skip("needs a second physical monitor")
    target = real[1]
    l, t, r, b = target.work
    home = tmp_path / "live_home"
    home.mkdir()
    (home / "state_ui.json").write_text(json.dumps({"icon": {"x": l + 60, "y": t + 60}}), encoding="utf-8")
    with app_factory(test_mode=True, home=home) as (base, token):
        from conftest import Api
        api = Api(base, token)

        def icon_rect():
            status, ui = api.get("/v1/_debug/ui")
            assert status == 200, ui
            return ui["icon"]["rect"], ui

        rect, ui = icon_rect()
        assert l <= rect[0] < r and t <= rect[1] < b, f"icon not on the second monitor: {rect} vs {target.work}"
        assert ui["monitors"]["listening"] is True

        status, body = api.post("/v1/_debug/monitors", {"idx": 1})
        assert status == 200 and body == {"ok": True}, (status, body)
        pl, pt, pr, pb = monitors.primary_monitor().work
        assert wait_until(lambda: pl <= icon_rect()[0][0] < pr and pt <= icon_rect()[0][1] < pb, timeout=5), icon_rect()
        assert icon_rect()[1]["monitors"]["moves"] >= 1

        parked = icon_rect()[0]
        assert api.post("/v1/_debug/monitors", {"restore": True})[0] == 200
        assert icon_rect()[0] == parked, "icon must not jump back on restore"

        assert api.post("/v1/_debug/monitors", {"idx": 99})[0] == 422
        assert api.post("/v1/_debug/monitors", {})[0] == 422
        assert api.post("/v1/_debug/monitors", {"idx": 1}, token=False)[0] == 401


def test_debug_monitors_route_absent_outside_test_mode(app_factory):
    from conftest import Api
    with app_factory(test_mode=False) as (base, token):
        api = Api(base, token)
        assert api.post("/v1/_debug/monitors", {"idx": 1})[0] in (404, 405)
        assert api.get("/v1/_debug/ui")[0] == 404
