"""DWM rounded corners on the live overlay, and the pythonw (no stderr) start (dev change M4 overlay polish).

A private test-mode app on its own port and ASSISTANT_HOME (never the user's instance). ``/v1/_debug/ui`` reports
``rounded`` and ``corner_preference`` for the icon and every card window; the "+N more" strip is not in that payload, so
its window is found by rectangle and the DWM attribute is read straight from it. DWM_WINDOW_CORNER_PREFERENCE: 2 = ROUND
(icon, cards), 3 = ROUNDSMALL (strip). Needs Windows 11 and a desktop session; skips otherwise.

This file does not touch tests/e2e/test_overlay.py.
"""
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import REPO_ROOT, STARTUP_TIMEOUT, Api, _free_port, _health_ok, _stop, launch_app, wait_until

if sys.getwindowsversion().build < 22000:
    pytest.skip("DWM rounded corners need Windows 11", allow_module_level=True)

from assistant.overlay import win  # noqa: E402

win32gui = pytest.importorskip("win32gui")


def ui_of(api, timeout=40.0):
    """GET /v1/_debug/ui, retrying while the UI thread is busy (503)."""
    end = time.monotonic() + timeout
    status, body = 0, None
    while time.monotonic() < end:
        status, body = api.get("/v1/_debug/ui")
        if status == 200:
            return body
        time.sleep(0.5)
    raise AssertionError(f"/v1/_debug/ui never answered 200: {status} {body}")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    base = tmp_path_factory.mktemp("corners")
    with launch_app(base / "home", _free_port(), None, test_mode=True) as (url, token):
        api = Api(url, token)
        ui = ui_of(api)
        if "icon" not in ui:
            pytest.skip("debug payload has no icon")
        if ui["icon"].get("rounded") is not True:
            pytest.skip(f"this machine's DWM refused rounding (icon.rounded={ui['icon'].get('rounded')}): "
                        "the chroma-key path is covered in tests/test_overlay_polish.py")
        yield api


@pytest.fixture(autouse=True)
def clean_cards(app):
    def wipe():
        status, cards = app.get("/v1/cards")
        for card in (cards or []) if status == 200 else []:
            app.request("DELETE", f"/v1/cards/{card['id']}")

    wipe()
    yield
    wipe()


def test_icon_reports_rounded_with_corner_preference_2(app):
    icon = ui_of(app)["icon"]
    assert icon["rounded"] is True and icon["corner_preference"] == 2, icon
    assert win.corner_preference(icon["hwnd"]) == 2, "and DWM itself agrees, read from this process"


def test_every_card_window_is_rounded_with_corner_preference_2(app):
    for n in range(4):
        status, body = app.post("/v1/cards", {"id": f"corner-{n}", "title": f"card {n}", "kind": ["waiting", "done", "meeting", "reminder"][n]})
        assert status in (200, 201), body
    assert wait_until(lambda: len(ui_of(app)["cards"]) >= 4, timeout=20)
    cards = ui_of(app)["cards"]
    assert cards, "no card windows reported"
    for card in cards:
        assert card["rounded"] is True and card["corner_preference"] == 2, card
        assert win.corner_preference(card["hwnd"]) == 2


def strip_windows(rects):
    """hwnds of visible top-level windows whose rectangle matches a reported strip rectangle."""
    found = {}

    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            rect = win32gui.GetWindowRect(hwnd)
            for want in rects:
                if all(abs(a - b) <= 2 for a, b in zip(rect, want)):
                    found[tuple(want)] = hwnd
        return True

    user32 = ctypes.windll.user32  # rectangles are in physical pixels only for a DPI-aware thread, as the app reports them
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        win32gui.EnumWindows(visit, None)
    finally:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))
    return found


def test_the_more_strip_uses_corner_preference_3_when_a_stack_overflows(app):
    for n in range(45):
        status, body = app.post("/v1/cards", {"id": f"flood-{n}", "title": f"flood card {n}", "kind": "custom"})
        assert status in (200, 201), body
    assert wait_until(lambda: ui_of(app).get("strips"), timeout=30), "45 cards never produced a '+N more' strip"
    ui = ui_of(app)
    strips = ui["strips"]
    assert strips and all(s["count"] > 0 for s in strips)
    for card in ui["cards"]:
        assert card["rounded"] is True and card["corner_preference"] == 2, "cards stay ROUND next to the strip"
    wins = strip_windows([s["rect"] for s in strips])
    assert len(wins) == len(strips), f"could not find every strip window by rectangle: {strips} -> {wins}"
    for rect, hwnd in wins.items():
        assert win.corner_preference(hwnd) == 3, (rect, hwnd, win.corner_preference(hwnd))
    assert ui["icon"]["corner_preference"] == 2


# ---- pythonw: sys.stderr is None -----------------------------------------------------------------------------------------

def test_the_app_starts_serves_and_stops_cleanly_under_pythonw(tmp_path):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pytest.skip("pythonw.exe not next to the interpreter")
    home, port = tmp_path / "home", _free_port()
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"schema": 1, "port": port}), encoding="utf-8")
    env = dict(os.environ, ASSISTANT_HOME=str(home), ASSISTANT_TEST_MODE="1")
    proc = subprocess.Popen([str(pythonw), "-m", "assistant"], cwd=REPO_ROOT, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    token = ""
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            if not token and (home / "token").is_file():
                token = (home / "token").read_text(encoding="ascii").strip()
            if token and _health_ok(port, token):
                break
            time.sleep(0.2)
        assert proc.poll() is None, f"pythonw app exited early with {proc.returncode}; app.log: " + (
            (home / "logs" / "app.log").read_text(errors="replace") if (home / "logs" / "app.log").is_file() else "none")
        assert _health_ok(port, token)
        ui = ui_of(Api(f"http://127.0.0.1:{port}", token))
        assert ui["icon"]["rounded"] in (True, False) and "corner_preference" in ui["icon"]
    finally:
        _stop(proc, port, token)
    log = (home / "logs" / "app.log").read_text(encoding="utf-8", errors="replace")
    assert "Traceback" not in log, log[-1500:]
    assert "shutting down" in log
