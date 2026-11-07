"""On-screen overlay checks, driven through tools/driver.py and GET /v1/_debug/ui (plan 8.3, M1-3a/3b/6 Done cells).

The app runs with ASSISTANT_TEST_MODE=1 against a scratch ASSISTANT_HOME on its own port. Everything is
skipped until the test-mode debug route is served (M1-6 wiring + M1-3a icon). Keys that the plan leaves open in
the debug payload (card window list, timer text, button rectangles) are read tolerantly and the test says which
key it expected; adjust the reader, not the assertion, if the dev names it differently.

Needs a real desktop session (SendInput, GetWindowLong). Tests that wait for the 20-s fade or a 60-s reminder
carry their own timeout.
"""
import ctypes
import importlib.util
import json
import socket
import sys
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from conftest import REPO_ROOT, wait_until

spec = importlib.util.spec_from_file_location("assistant_driver", REPO_ROOT / "tools" / "driver.py")
driver = importlib.util.module_from_spec(spec)
sys.modules["assistant_driver"] = driver
spec.loader.exec_module(driver)

GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x8, 0x80, 0x40000, 0x08000000
SPI_GETWORKAREA = 0x0030
SM_CMONITORS = 80

MENU = ["Search", "Remind me…", ("Mute", "Unmute"), ("Sound off", "Sound on"), "Test sound",
        "Reset position", "Settings", "Quit"]

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _card_id(window):
    return str(window.get("id", window.get("card_id")))


class Overlay:
    def __init__(self, home: Path, port: int):
        self.home, self.port = home, port

    def start(self):
        args = type("A", (), dict(home=str(self.home), port=self.port, test_mode=True, console=False))
        driver.cmd_start(args)

    def stop(self):
        driver.cmd_stop(None)

    def restart(self):
        self.stop()
        self.start()
        wait_until(lambda: self.ui_or_none() is not None, timeout=10)

    def ui_or_none(self):
        try:
            status, body = driver.http("GET", "/v1/_debug/ui")
        except OSError:
            return None
        return body if status == 200 else None

    def ui(self) -> dict:
        ui = self.ui_or_none()
        assert ui is not None, "/v1/_debug/ui not available"
        return ui

    def post(self, **card):
        status, body = driver.http("POST", "/v1/cards", card)
        assert status in (200, 201), (status, body)
        return body

    def windows(self) -> list:
        return driver.card_windows(self.ui())

    def window(self, card_id):
        found = [w for w in self.windows() if _card_id(w) == card_id]
        return found[0] if found else None

    def dismiss_all(self):
        status, cards = driver.http("GET", "/v1/cards")
        if status == 200:
            for c in cards or []:
                driver.http("DELETE", f"/v1/cards/{c['id']}")


@pytest.fixture(scope="module")
def overlay(tmp_path_factory):
    driver.STATE_FILE = tmp_path_factory.mktemp("driver_state") / "state.json"
    home = tmp_path_factory.mktemp("overlay_home")
    ov = Overlay(home, _free_port())
    ov.start()
    try:
        ui = ov.ui_or_none()
        if ui is None:
            pytest.skip("GET /v1/_debug/ui is not served with ASSISTANT_TEST_MODE=1 yet (M1-6 wiring / M1-3a)")
        if "icon" not in ui:
            pytest.skip("debug payload has no 'icon' entry (M1-3a not wired into the app yet)")
        yield ov
    finally:
        ov.stop()


@pytest.fixture(autouse=True)
def clean_cards(overlay):
    overlay.dismiss_all()
    yield
    overlay.dismiss_all()


def _work_area():
    rect = wintypes.RECT()
    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    return rect.left, rect.top, rect.right, rect.bottom


def _centre(rect):
    return (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2


# ---- M1-3a: the icon ----------------------------------------------------------------

def test_debug_route_requires_the_token(overlay):
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{overlay.port}/v1/_debug/ui", timeout=5)
        status = 200
    except urllib.error.HTTPError as err:
        status = err.code
    assert status == 401


def test_icon_sits_bottom_right_of_the_primary_work_area_not_over_the_taskbar(overlay):
    left, top, right, bottom = overlay.ui()["icon"]["rect"]
    wl, wt, wr, wb = _work_area()
    assert wl <= left < right <= wr and wt <= top < bottom <= wb, "icon must be inside the work area"
    assert wr - right < 200 and wb - bottom < 200, f"icon {left, top, right, bottom} is not bottom-right of {wl, wt, wr, wb}"


def test_icon_window_styles_toolwindow_noactivate_topmost_and_no_taskbar_button(overlay):
    hwnd = overlay.ui()["icon"]["hwnd"]
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    assert style & WS_EX_TOOLWINDOW, hex(style)
    assert style & WS_EX_NOACTIVATE, hex(style)
    assert style & WS_EX_TOPMOST, hex(style)
    assert not style & WS_EX_APPWINDOW, "WS_EX_APPWINDOW would force a taskbar button"


def test_right_click_menu_items_are_in_ac_ov_11_order(overlay):
    labels = overlay.ui()["menu"]
    assert len(labels) == len(MENU), labels
    for got, want in zip(labels, MENU):
        assert got in (want if isinstance(want, tuple) else (want,)), (labels, MENU)


def test_dragging_200px_moves_the_icon_and_the_position_survives_a_restart_within_2px(overlay):
    before = overlay.ui()["icon"]["rect"]
    cx, cy = _centre(before)
    driver.drag(cx, cy, cx - 200, cy - 200)
    assert wait_until(lambda: overlay.ui()["icon"]["rect"] != before, timeout=2)
    moved = overlay.ui()["icon"]["rect"]
    assert abs((before[0] - moved[0]) - 200) <= 4 and abs((before[1] - moved[1]) - 200) <= 4, (before, moved)
    overlay.restart()
    after = overlay.ui()["icon"]["rect"]
    assert abs(after[0] - moved[0]) <= 2 and abs(after[1] - moved[1]) <= 2, (moved, after)


def _choose_menu_item(overlay, label_start: str):
    """Right-click the icon and pick an entry with the keyboard (tk popups are not addressable by pixel)."""
    labels = overlay.ui()["menu"]
    index = next(i for i, label in enumerate(labels) if label.startswith(label_start))
    driver.click_at(*_centre(overlay.ui()["icon"]["rect"]), right=True)
    time.sleep(0.3)
    for _ in range(index + 1):
        driver.press_keys(["down"])
        time.sleep(0.05)
    driver.press_keys(["enter"])
    time.sleep(0.4)


def test_reset_position_returns_the_icon_to_the_bottom_right(overlay):
    _choose_menu_item(overlay, "Reset position")
    left, top, right, bottom = overlay.ui()["icon"]["rect"]
    wl, wt, wr, wb = _work_area()
    assert wr - right < 200 and wb - bottom < 200 and bottom <= wb


def test_remind_me_shows_live_interpretation_and_the_didnt_understand_line(overlay):
    _choose_menu_item(overlay, "Remind me")
    assert wait_until(lambda: overlay.ui().get("reminder_input_open"), timeout=2), "input did not open"
    driver.type_text("driver selftest in 10m")
    assert wait_until(lambda: "Reminder 'driver selftest' at " in json.dumps(overlay.ui(), ensure_ascii=False), timeout=2)
    for _ in range(len("driver selftest in 10m")):
        driver.press_keys(["backspace"])
    driver.type_text("foo bar")
    assert wait_until(lambda: "didn't understand" in json.dumps(overlay.ui(), ensure_ascii=False), timeout=2)
    driver.press_keys(["enter"])  # does nothing on bad input (AC-RM-3)
    assert overlay.ui().get("reminder_input_open")
    driver.press_keys(["esc"])
    assert wait_until(lambda: not overlay.ui().get("reminder_input_open"), timeout=2)


# ---- M1-3b / M1-6: cards ------------------------------------------------------------

def _colours():
    gfx = pytest.importorskip("assistant.overlay.gfx", reason="assistant/overlay/gfx.py (M1-3a) not present")
    return {"waiting": gfx.AMBER, "done": gfx.GREEN, "meeting": gfx.BLUE, "reminder": gfx.PURPLE, "custom": gfx.PURPLE}


def test_card_appears_within_1s_of_the_post(overlay):
    card = overlay.post(id="e2e-fast", title="fast", kind="custom")
    assert wait_until(lambda: overlay.window(card["id"]), timeout=1.0), "no window within 1 s"


def _monitor_count() -> int:
    """Number of monitors the app enumerates, read in-process (the debug payload does not expose monitors)."""
    try:
        from assistant.overlay import monitors
        return len(monitors.list_monitors())
    except Exception:
        import win32api
        return len(win32api.EnumDisplayMonitors(None, None))


def _icon_monitor_index(overlay) -> int:
    """Index (in the order the app lists monitors, as used by the window 'monitor' key) of the icon's monitor."""
    from assistant.overlay import monitors
    left, top, right, bottom = overlay.ui()["icon"]["rect"]
    target = monitors.monitor_at((left + right) // 2, (top + bottom) // 2)
    return [m.handle for m in monitors.list_monitors()].index(target.handle)


def _set_every_monitor(overlay, value: bool):
    """The setting has no HTTP route yet: edit settings.json in the scratch home and restart the app."""
    path = overlay.home / "settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cards_every_monitor"] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    overlay.restart()


@pytest.fixture
def single_monitor_cards(overlay):
    _set_every_monitor(overlay, False)
    yield
    _set_every_monitor(overlay, True)


def _windows_by_card(overlay) -> dict:
    by_card: dict = {}
    for w in overlay.windows():
        by_card.setdefault(_card_id(w), []).append(w)
    return by_card


def test_five_kinds_show_one_window_per_monitor_with_kind_colours_stacked_newest_at_the_bottom_above_the_icon(overlay):
    kinds = ["waiting", "done", "meeting", "reminder", "custom"]
    monitors_n = _monitor_count()
    for k in kinds:
        overlay.post(id=f"e2e-{k}", title=k, kind=k)
        time.sleep(0.15)
    assert wait_until(lambda: len(overlay.windows()) == 5 * monitors_n, timeout=3), (monitors_n, overlay.windows())
    by_card = _windows_by_card(overlay)
    for k in kinds:
        assert sorted(w["monitor"] for w in by_card[f"e2e-{k}"]) == list(range(monitors_n)), (k, by_card[f"e2e-{k}"])
    colours = _colours()
    icon_monitor = _icon_monitor_index(overlay)
    wins = {cid: next(w for w in ws if w["monitor"] == icon_monitor) for cid, ws in by_card.items()}
    icon_top = overlay.ui()["icon"]["rect"][1]
    for k in kinds:
        w = wins[f"e2e-{k}"]
        assert str(w.get("colour", w.get("color"))).lower() == colours[k].lower(), (k, w)
        assert w["rect"][3] <= icon_top + 4, f"{k} card overlaps/below the icon"
    tops = [wins[f"e2e-{k}"]["rect"][1] for k in kinds]
    assert tops == sorted(tops), f"newest must be at the bottom (increasing y): {tops}"


def test_waiting_timer_advances_each_second(overlay):
    overlay.post(id="e2e-timer", title="timer", kind="waiting", timer_started_at=time.time())
    assert wait_until(lambda: overlay.window("e2e-timer"), timeout=2)

    def timer_text():
        w = overlay.window("e2e-timer") or {}
        return next((str(v) for k, v in w.items() if "timer" in k.lower() or "elapsed" in k.lower()), None)

    first = wait_until(timer_text, timeout=2)
    assert first, "expected a 'timer'/'elapsed' key on the waiting card window in /v1/_debug/ui"
    assert wait_until(lambda: timer_text() != first, timeout=2.5), f"timer stuck at {first}"


def test_hover_marks_seen_and_stops_blinking(overlay):
    overlay.post(id="e2e-hover", title="hover", kind="waiting")
    assert wait_until(lambda: overlay.window("e2e-hover"), timeout=2)
    assert overlay.window("e2e-hover").get("seen") in (False, None)
    driver.move_to(*_centre(overlay.window("e2e-hover")["rect"]))
    assert wait_until(lambda: overlay.window("e2e-hover").get("seen") is True, timeout=2)
    assert overlay.window("e2e-hover").get("blinking") is False


def test_done_card_disappears_20_s_after_it_is_seen(overlay):
    overlay.post(id="e2e-fade", title="fade", kind="done")
    assert wait_until(lambda: overlay.window("e2e-fade"), timeout=2)
    driver.move_to(*_centre(overlay.window("e2e-fade")["rect"]))
    assert wait_until(lambda: overlay.window("e2e-fade").get("seen") is True, timeout=2)
    t0 = time.monotonic()
    assert wait_until(lambda: overlay.window("e2e-fade") is None, timeout=24, step=0.2), "card never faded"
    assert 18 <= time.monotonic() - t0 <= 22.5


def test_mute_greys_the_icon_and_queues_cards(overlay):
    gfx = pytest.importorskip("assistant.overlay.gfx")
    _choose_menu_item(overlay, "Mute")  # lands on the submenu entry; Enter again picks its first choice
    driver.press_keys(["enter"])
    time.sleep(0.4)
    icon = overlay.ui()["icon"]
    if not icon["muted"]:
        pytest.skip("could not drive the Mute submenu by keyboard")
    assert icon["body_colour"].lower() == gfx.GREY.lower()
    overlay.post(id="e2e-muted", title="queued", kind="waiting")
    time.sleep(0.5)
    assert overlay.window("e2e-muted") is None, "muted cards must not be shown"
    assert overlay.ui()["icon"]["count"] >= 1, "muted cards are still counted on the icon"
    _choose_menu_item(overlay, "Unmute")
    assert wait_until(lambda: overlay.window("e2e-muted"), timeout=2), "queued card appears on unmute"


def test_cards_on_every_monitor(overlay):
    monitors_n = _monitor_count()
    if monitors_n < 2:
        pytest.skip("one monitor: per-monitor mirroring (AC-OV-3) is verified on a multi-monitor setup only")
    overlay.post(id="e2e-mirror", title="mirror", kind="custom")

    def mirrored():
        return sorted(w["monitor"] for w in overlay.windows() if _card_id(w) == "e2e-mirror")

    assert wait_until(lambda: mirrored() == list(range(monitors_n)), timeout=3), mirrored()


def test_cards_only_on_the_icons_monitor_when_every_monitor_is_off(overlay, single_monitor_cards):
    overlay.post(id="e2e-single", title="single", kind="custom")
    assert wait_until(lambda: overlay.window("e2e-single"), timeout=3)
    time.sleep(0.5)
    assert [w["monitor"] for w in overlay.windows() if _card_id(w) == "e2e-single"] == [_icon_monitor_index(overlay)]


# ---- M1-6: whole-app behaviour --------------------------------------------------------

def test_open_cards_survive_kill_and_relaunch(overlay):
    overlay.post(id="e2e-persist", title="persist", kind="waiting")
    assert wait_until(lambda: overlay.window("e2e-persist"), timeout=2)
    overlay.restart()
    assert wait_until(lambda: overlay.window("e2e-persist"), timeout=4), "card not restored after restart"


@pytest.mark.timeout(120)
def test_reminder_in_one_minute_fires_a_purple_card_at_plus_60s(overlay):
    status, body = driver.http("POST", "/v1/reminders", {"text": "e2e reminder in 1m"})
    if status == 404:
        pytest.skip("POST /v1/reminders not served yet (M1-4)")
    assert status == 201, body
    t0 = time.monotonic()
    colours = _colours()

    def matching():
        return [w for w in overlay.windows() if "e2e reminder" in json.dumps(w)]

    assert wait_until(matching, timeout=70, step=0.5), "reminder never fired"
    elapsed = time.monotonic() - t0
    assert 55 <= elapsed <= 65, elapsed
    window = matching()[0]
    assert str(window.get("colour", window.get("color"))).lower() == colours["reminder"].lower()
