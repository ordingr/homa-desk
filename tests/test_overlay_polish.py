"""Overlay polish: DWM rounded corners with the chroma-key fallback, PNG icon/badge assets, the full-page window
geometry, and the pythonw (no stderr) start.

In-process, with a real Tk root; Windows facts (the DWM attribute) are read back from the real window. Needs a desktop
session for Tk. Nothing here starts the overlay app (see tests/e2e/test_overlay_corners.py for that).
"""
import logging
import logging.handlers
import struct
import sys
import time
import tkinter as tk
import types
import zlib

import pytest

from homa import paths
from homa.overlay import cards_ui, fullpage, gfx, icon as icon_mod, monitors, win



def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x8e\x5b\xd6\xff" * 2 for _ in range(2))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


PNG_1X1 = _png()
WIN11 = sys.getwindowsversion().build >= 22000


def warm(png) -> None:
    """The first Tk load of a file that was never read raises an empty TclError here (cold file); load it once, with retries."""
    for _ in range(5):
        try:
            tk.PhotoImage(file=str(png))
            return
        except tk.TclError:
            time.sleep(0.05)


@pytest.fixture(scope="module")
def root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no Tk display: {exc}")
    r.withdraw()
    for png in gfx.ASSETS.glob("*.png"):
        warm(png)
    yield r
    r.destroy()


@pytest.fixture(autouse=True)
def retrying_png_loads(monkeypatch):
    """Under pytest's fd capture Tk's PNG reader fails now and then with an empty TclError on a cold file (not seen with
    the app's own stdio, 300 loads in a plain process, or under pythonw). Retry so the tests judge gfx, not that."""
    real = gfx.load_png

    def load(prefix, px):
        for _ in range(6):
            found = real(prefix, px)
            if found is not None:
                return found
            time.sleep(0.02)
        return None

    monkeypatch.setattr(gfx, "load_png", load)


@pytest.fixture(autouse=True)
def scratch_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMA_HOME", str(tmp_path / "home"))


# ---- fullpage.window_flags --------------------------------------------------------------------------------------------

def mon(work, rect=None, handle=1):
    return monitors.Monitor(handle=handle, rect=rect or work, work=work, primary=True, dpi=96)


@pytest.fixture
def fake_desktop(monkeypatch):
    state = {"fg": 0, "rect": (0, 0, 0, 0), "cursor": (10, 10), "monitor": mon((0, 0, 1920, 1040)), "asked": []}
    monkeypatch.setattr(fullpage.win32gui, "GetForegroundWindow", lambda: state["fg"])
    monkeypatch.setattr(fullpage.win32gui, "GetWindowRect", lambda h: state["rect"])
    monkeypatch.setattr(fullpage.win32gui, "GetCursorPos", lambda: state["cursor"])

    def at(x, y):
        state["asked"].append((x, y))
        return state["monitor"]

    monkeypatch.setattr(fullpage.monitors, "monitor_at", at)
    return state


def test_window_flags_is_85_percent_of_the_work_area_and_centred(fake_desktop):
    fake_desktop["fg"], fake_desktop["rect"] = 77, (100, 100, 300, 200)
    flags = fullpage.window_flags()
    assert flags == ["--window-size=1632,884", "--window-position=144,78"], flags
    assert fake_desktop["asked"] == [(200, 150)], "the monitor holding the foreground window (its centre)"


def test_window_flags_on_a_second_monitor_with_a_taskbar_offset(fake_desktop):
    fake_desktop["fg"], fake_desktop["rect"] = 5, (2000, 10, 2200, 110)
    fake_desktop["monitor"] = mon((1920, 40, 4480, 1440), rect=(1920, 0, 4480, 1440))
    assert fullpage.window_flags() == ["--window-size=2176,1190", "--window-position=2112,145"]


def test_window_flags_on_a_monitor_left_of_the_primary_has_negative_coordinates(fake_desktop):
    fake_desktop["fg"], fake_desktop["rect"] = 5, (-1800, 0, -1600, 100)
    fake_desktop["monitor"] = mon((-1920, 0, 0, 1080))
    assert fullpage.window_flags() == ["--window-size=1632,918", "--window-position=-1776,81"]


def test_window_flags_without_a_foreground_window_uses_the_cursor(fake_desktop):
    fake_desktop["fg"], fake_desktop["cursor"] = 0, (1500, 900)
    assert fullpage.window_flags()[0].startswith("--window-size=")
    assert fake_desktop["asked"] == [(1500, 900)]


def test_window_flags_gives_nothing_when_the_desktop_cannot_be_read(fake_desktop, monkeypatch):
    def boom(*a):
        raise OSError("no desktop")

    monkeypatch.setattr(fullpage.win32gui, "GetForegroundWindow", boom)
    assert fullpage.window_flags() == []


def test_window_flags_real_desktop_stays_inside_the_work_area():
    flags = fullpage.window_flags()
    assert len(flags) == 2
    w, h = (int(v) for v in flags[0].split("=")[1].split(","))
    x, y = (int(v) for v in flags[1].split("=")[1].split(","))
    assert w > 0 and h > 0
    fg = fullpage.win32gui.GetForegroundWindow()
    anchor = fullpage.win32gui.GetCursorPos() if not fg else tuple(
        (a + b) // 2 for a, b in zip(fullpage.win32gui.GetWindowRect(fg)[:2], fullpage.win32gui.GetWindowRect(fg)[2:]))
    l, t, r, b = monitors.monitor_at(*anchor).work
    assert l <= x and t <= y and x + w <= r and y + h <= b


# ---- gfx.load_png -------------------------------------------------------------------------------------------------------

@pytest.fixture
def assets(tmp_path, monkeypatch, root):
    folder = tmp_path / "assets"
    folder.mkdir()
    for name in ("icon_60", "icon_90", "icon_180", "icon-muted_75", "badge_plus_60", "badge_plus_120"):
        (folder / f"{name}.png").write_bytes(PNG_1X1)
    for png in folder.glob("*.png"):
        warm(png)
    monkeypatch.setattr(gfx, "ASSETS", folder)
    return folder


@pytest.mark.parametrize("want, size", [(1, 60), (60, 60), (70, 60), (80, 90), (89, 90), (140, 180), (150, 180), (500, 180)])
def test_load_png_picks_the_nearest_size(assets, want, size):
    found = gfx.load_png("icon", want)
    assert found is not None and found[1] == size and isinstance(found[0], tk.PhotoImage)


def test_load_png_matches_the_prefix_exactly(assets):
    assert gfx.load_png("icon-muted", 200)[1] == 75, "icon_* must not swallow icon-muted_*"
    assert gfx.load_png("icon", 75)[1] in (60, 90)
    assert gfx.load_png("badge_plus", 100)[1] == 120
    assert gfx.load_png("badge_3", 60) is None
    assert gfx.load_png("badge", 60) is None


def test_load_png_returns_none_for_missing_or_corrupt_assets(assets, tmp_path, monkeypatch):
    (assets / "icon_60.png").write_bytes(b"not a png")
    (assets / "icon_90.png").unlink()
    (assets / "icon_180.png").unlink()
    assert gfx.load_png("icon", 60) is None
    monkeypatch.setattr(gfx, "ASSETS", tmp_path / "does-not-exist")
    assert gfx.load_png("icon", 60) is None


def test_the_shipped_assets_cover_icon_muted_and_every_badge_at_every_size():
    real = gfx.ASSETS
    sizes = {60, 75, 90, 105, 120, 180}
    for prefix in ["icon", "icon-muted", "badge_plus"] + [f"badge_{n}" for n in range(1, 10)]:
        for size in sizes:
            assert (real / f"{prefix}_{size}.png").is_file(), f"{prefix}_{size}.png is missing"
    assert (real / "homa.ico").is_file()


# ---- the icon: assets, badges, fallbacks --------------------------------------------------------------------------------

def make_icon(root, rounded=None, monkeypatch=None):
    if rounded is not None:
        monkeypatch.setattr(win, "round_corners", lambda hwnd, small=False: rounded)
    return icon_mod.Icon(root, icon_mod.IconActions(quit=lambda: None))


def items(ic, tag):
    return [(ic.canvas.type(i), ic.canvas.itemcget(i, "text") if ic.canvas.type(i) == "text" else None)
            for i in ic.canvas.find_withtag(tag)]


@pytest.fixture
def made(root):
    icons = []
    yield icons
    for ic in icons:
        ic.win.destroy()


@pytest.mark.parametrize("count, key", [(1, "badge_1"), (9, "badge_9"), (10, "badge_plus"), (150, "badge_plus")])
def test_rounded_icon_uses_the_png_badge_and_9_plus_for_anything_over_9(root, made, monkeypatch, count, key):
    asked = []
    real = gfx.load_png
    monkeypatch.setattr(gfx, "load_png", lambda prefix, px: (asked.append(prefix), real(prefix, px))[1])
    ic = make_icon(root, True, monkeypatch)
    made.append(ic)
    ic.set_count(count)
    assert key in asked and sum(1 for p in asked if p.startswith("badge")) >= 1
    assert not [p for p in asked if p.startswith("badge") and p != key], asked
    kinds = [kind for kind, _ in items(ic, "badge")]
    assert kinds == ["image"], "one PNG badge image and no drawn circle or text"
    assert [kind for kind, _ in items(ic, "glyph")] == ["image"]


def test_no_badge_at_zero_and_it_goes_away_again(root, made, monkeypatch):
    ic = make_icon(root, True, monkeypatch)
    made.append(ic)
    assert items(ic, "badge") == []
    ic.set_count(3)
    assert len(items(ic, "badge")) == 1
    ic.set_count(0)
    assert items(ic, "badge") == []
    ic.set_count(-4)
    assert ic.count == 0 and items(ic, "badge") == []


def test_muted_icon_loads_the_muted_png(root, made, monkeypatch):
    asked = []
    real = gfx.load_png
    monkeypatch.setattr(gfx, "load_png", lambda prefix, px: (asked.append(prefix), real(prefix, px))[1])
    ic = make_icon(root, True, monkeypatch)
    made.append(ic)
    ic.set_muted(True)
    assert asked[-1] == "icon-muted" or "icon-muted" in asked
    assert ic.debug_state()["icon"]["muted"] is True and ic.debug_state()["icon"]["body_colour"] == gfx.GREY
    ic.set_muted(False)
    assert asked[-1] == "icon"


@pytest.mark.parametrize("rounded", [True, False])
def test_missing_pngs_fall_back_to_the_old_bell_and_drawn_badge(root, made, monkeypatch, tmp_path, rounded):
    empty = tmp_path / "no-assets"
    empty.mkdir()
    monkeypatch.setattr(gfx, "ASSETS", empty)
    ic = make_icon(root, rounded, monkeypatch)
    made.append(ic)
    assert items(ic, "glyph") == [] and len(items(ic, "bell")) >= 3, "the bell is drawn from primitives"
    ic.set_count(7)
    badge = items(ic, "badge")
    assert ("oval", None) in badge and ("text", "7") in badge
    ic.set_count(10)
    assert ("text", "10") in items(ic, "badge")
    ic.set_muted(True)
    assert any(kind == "line" for kind, _ in items(ic, "bell")), "the muted cross is drawn"
    assert ic.debug_state()["icon"]["rounded"] is rounded


def test_fallback_badge_text_keeps_the_old_99_plus_rule(root, made, monkeypatch, tmp_path):
    """Observation recorded as a test: the drawn (fallback) badge still caps at 99+, unlike the PNG badge's 9+."""
    empty = tmp_path / "no-assets"
    empty.mkdir()
    monkeypatch.setattr(gfx, "ASSETS", empty)
    ic = make_icon(root, True, monkeypatch)
    made.append(ic)
    ic.set_count(150)
    assert ("text", "99+") in items(ic, "badge")


def test_when_dwm_rounding_fails_the_icon_takes_the_chroma_key_path_and_still_draws(root, made, monkeypatch):
    ic = make_icon(root, False, monkeypatch)
    made.append(ic)
    assert ic.rounded is False
    assert str(ic.win.attributes("-transparentcolor")) == gfx.CHROMA_KEY
    assert ic.canvas.cget("bg") == gfx.CHROMA_KEY
    assert [kind for kind, _ in items(ic, "body")] == ["oval"], "the round body is drawn by hand"
    assert [kind for kind, _ in items(ic, "glyph")] == ["image"], "the PNG glyph is still used (cropped)"
    ic.set_count(5)
    badge = items(ic, "badge")
    assert ("text", "5") in badge and ("oval", None) in badge, "the PNG badge is only for the rounded path"
    state = ic.debug_state()["icon"]
    assert state["rounded"] is False and "corner_preference" in state


def test_when_dwm_rounding_works_the_icon_is_solid_with_no_transparent_colour(root, made, monkeypatch):
    ic = make_icon(root, True, monkeypatch)
    made.append(ic)
    assert ic.canvas.cget("bg") == gfx.PURPLE
    assert not ic.win.attributes("-transparentcolor")
    assert items(ic, "body") == []


def test_more_strip_draws_on_both_paths(root, monkeypatch):
    ui = types.SimpleNamespace(root=root, adopt_close=lambda w: None,
                               font=lambda size, weight="normal": ("Segoe UI", -size, weight))
    for rounded in (True, False):
        monkeypatch.setattr(win, "round_corners", lambda hwnd, small=False, r=rounded: r)
        strip = cards_ui.MoreStrip(ui, monitors.primary_monitor())
        try:
            strip.show(4, 50, 50)
            assert strip.rounded is rounded
            texts = [strip.canvas.itemcget(i, "text") for i in strip.canvas.find_all() if strip.canvas.type(i) == "text"]
            assert texts == ["+4 more"]
            assert strip.canvas.cget("bg") == (cards_ui.panel.TEXT_DIM if rounded else gfx.CHROMA_KEY)
            assert bool(strip.win.attributes("-transparentcolor")) is (not rounded)
        finally:
            strip.destroy()


# ---- win.round_corners / corner_preference ------------------------------------------------------------------------------

@pytest.mark.skipif(not WIN11, reason="DWM rounded-corner attribute needs Windows 11")
@pytest.mark.parametrize("small, expected", [(False, 2), (True, 3)])
def test_round_corners_sets_the_dwm_preference_and_it_reads_back(root, small, expected):
    top = tk.Toplevel(root)
    try:
        top.overrideredirect(True)
        top.geometry("80x80+200+200")
        top.update()
        hwnd = win.hwnd_of(top)
        assert win.round_corners(hwnd, small=small) is True
        assert win.corner_preference(hwnd) == expected
    finally:
        top.destroy()


def test_round_corners_is_false_for_a_window_that_does_not_exist():
    assert win.round_corners(0xDEAD0) is False
    assert win.corner_preference(0xDEAD0) == -1


def test_round_corners_is_false_when_dwmapi_lacks_the_attribute(monkeypatch):
    class Dwm:
        def __getattr__(self, name):
            raise AttributeError(name)

    monkeypatch.setattr(win.ctypes, "windll", types.SimpleNamespace(dwmapi=Dwm()), raising=False)
    assert win.round_corners(123) is False
    assert win.corner_preference(123) == -1


def test_create_root_sets_the_application_icon(monkeypatch):
    calls = []
    monkeypatch.setattr(win, "set_dpi_aware", lambda: None)
    monkeypatch.setattr(win, "set_app_icon", lambda r: calls.append(r) or True)
    try:
        r = icon_mod.create_root()
    except tk.TclError as exc:
        pytest.skip(f"no Tk display: {exc}")
    try:
        assert calls == [r]
        assert r.state() == "withdrawn"
    finally:
        r.destroy()


def test_set_app_icon_applies_the_ico_and_survives_a_missing_file(root, monkeypatch, tmp_path):
    assert win.ICON_FILE.is_file()
    assert win.set_app_icon(root) is True
    monkeypatch.setattr(win, "ICON_FILE", tmp_path / "gone.ico")
    assert win.set_app_icon(root) is False


# ---- pythonw: no stderr --------------------------------------------------------------------------------------------------

@pytest.fixture
def clean_root_logger():
    log = logging.getLogger()
    before, level = list(log.handlers), log.level
    yield log
    for handler in list(log.handlers):
        if handler not in before:
            log.removeHandler(handler)
            handler.close()
    log.setLevel(level)


def test_logging_setup_without_stderr_attaches_only_the_file_handler(monkeypatch, clean_root_logger):
    from homa import app
    paths.ensure_layout()
    before = list(clean_root_logger.handlers)
    monkeypatch.setattr(sys, "stderr", None)
    app._setup_logging()
    added = [h for h in clean_root_logger.handlers if h not in before]
    assert len(added) == 1 and isinstance(added[0], logging.handlers.RotatingFileHandler)
    logging.getLogger("homa.qa").info("written without a console")
    added[0].flush()
    assert "written without a console" in (paths.logs_dir() / "app.log").read_text(encoding="utf-8")


def test_logging_setup_with_stderr_also_attaches_the_console_handler(clean_root_logger):
    from homa import app
    paths.ensure_layout()
    before = list(clean_root_logger.handlers)
    app._setup_logging()
    added = [h for h in clean_root_logger.handlers if h not in before]
    kinds = {type(h) for h in added}
    assert kinds == {logging.handlers.RotatingFileHandler, logging.StreamHandler}


def test_the_app_object_can_be_built_with_stderr_none(monkeypatch):
    from homa import app, netinfo
    shown = []
    monkeypatch.setattr(netinfo, "message_box", lambda title, text: shown.append((title, text)))
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdout", None)
    built = app.App()
    assert built._windowed is True
    built._notify_user("hello")
    assert shown == [("Homa", "hello")], "with no console the message goes to a box, and nothing raises"
