"""Edge app-mode launcher for the full page (AC-SH-7, plan 7.4 M3b-3, PR7).

``open(query, port, mint)`` mints a launch nonce, starts ``msedge.exe --app=http://127.0.0.1:<port>/app/?launch=<nonce>&q=...``
with its own ``--user-data-dir`` (so the window owns its process and is separate from the user's browsing), and
remembers the window it opened. ``close()`` posts WM_CLOSE to that window only.

The profile folder is ``%LOCALAPPDATA%\\Homa\\edge-profile`` (plan PR7), not a folder inside HOMA_HOME:
``paths.secure_home`` skips re-restricting a home that holds folders it does not know.
"""
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

import win32con
import win32gui
import win32process

from .. import paths
from . import monitors

log = logging.getLogger("homa.overlay.fullpage")

WINDOW_CLASS = "Chrome_WidgetWin_1"
FIND_TIMEOUT_S = 15.0
FIND_POLL_S = 0.15
WINDOW_TITLE = "Homa - file search"
PROFILE_FLAGS = ("--disable-sync", "--disable-extensions", "--disable-background-networking",
                 "--no-service-autorun")
WINDOW_FRACTION = 0.85
_EDGE_PATHS = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
               r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")

_lock = threading.Lock()
_state: dict = {"hwnd": None, "pid": None, "reason": ""}


def edge_path() -> Optional[str]:
    return next((p for p in _EDGE_PATHS if os.path.exists(p)), None)


def profile_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "Homa" / "edge-profile" if base else paths.home() / "edge-profile"


def window_flags() -> list[str]:
    """--window-size/--window-position for ~85 % of the work area of the monitor holding the foreground window
    (the search popup while it is open), else the one under the cursor; centred."""
    try:
        fg = win32gui.GetForegroundWindow()
        if fg:
            l, t, r, b = win32gui.GetWindowRect(fg)
            anchor = ((l + r) // 2, (t + b) // 2)
        else:
            anchor = win32gui.GetCursorPos()
        wl, wt, wr, wb = monitors.monitor_at(*anchor).work
    except Exception:
        return []
    w, h = int((wr - wl) * WINDOW_FRACTION), int((wb - wt) * WINDOW_FRACTION)
    return [f"--window-size={w},{h}", f"--window-position={wl + (wr - wl - w) // 2},{wt + (wb - wt - h) // 2}"]


def build_url(query: str, port: int, nonce: str) -> str:
    return f"http://127.0.0.1:{int(port)}/app/?launch={quote(nonce, safe='')}&q={quote(query or '', safe='')}"


def edge_windows() -> dict:
    """{hwnd: pid} of visible top-level Edge windows."""
    found: dict = {}

    def visit(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == WINDOW_CLASS \
                    and win32gui.GetWindowText(hwnd):
                found[hwnd] = win32process.GetWindowThreadProcessId(hwnd)[1]
        except Exception:
            pass
        return True

    win32gui.EnumWindows(visit, None)
    return found


def _alive(hwnd, pid) -> bool:
    try:
        return bool(hwnd) and win32gui.IsWindow(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] == pid
    except Exception:
        return False


def _is_ours(hwnd) -> bool:
    """The window is Chromium's top-level class and carries the page title; never just 'a new Edge window'."""
    try:
        return win32gui.GetClassName(hwnd) == WINDOW_CLASS and win32gui.GetWindowText(hwnd).startswith(WINDOW_TITLE)
    except Exception:
        return False


def _find_window(before: dict, launched_pid: int) -> Optional[tuple]:
    deadline = time.monotonic() + FIND_TIMEOUT_S
    while time.monotonic() < deadline:
        now = edge_windows()
        ours = [(h, p) for h, p in now.items() if h not in before and _is_ours(h)]
        for hwnd, pid in ours:
            if pid == launched_pid:
                return hwnd, pid
        if ours:
            return ours[0]
        time.sleep(FIND_POLL_S)
    return None


def last_failure() -> str:
    """One line saying why the last open() returned False; empty after a success."""
    with _lock:
        return str(_state.get("reason") or "")


def is_open() -> bool:
    """True while the window this module opened is still on screen."""
    with _lock:
        return _alive(_state["hwnd"], _state["pid"])


def close() -> bool:
    """Close the window this module opened. False when there is none (or it is already gone)."""
    with _lock:
        hwnd, pid = _state["hwnd"], _state["pid"]
        _state["hwnd"] = _state["pid"] = None
    if not _alive(hwnd, pid):
        return False
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    return True


def _fail(reason: str) -> bool:
    with _lock:
        _state["reason"] = reason
    return False


def open(query: str, port: int, mint: Callable[[], str], profile: Optional[Path] = None) -> bool:  # noqa: A001
    """Launch the full page for `query`. A window opened earlier is closed first. True once the new window is
    found; False (with ``last_failure()`` set) when Edge is missing, will not start, or shows no window titled
    ``Homa - file search`` within FIND_TIMEOUT_S."""
    edge = edge_path()
    if edge is None:
        log.warning("msedge.exe not found; the full page cannot open")
        return _fail("Microsoft Edge was not found on this PC.")
    close()
    target = Path(profile) if profile else profile_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("could not create the Edge profile folder (%s)", type(exc).__name__)
        return _fail("The full page could not create its Edge profile folder.")
    before = edge_windows()
    url = build_url(query, port, mint())
    try:
        proc = subprocess.Popen([edge, f"--app={url}", f"--user-data-dir={target}", "--no-first-run",
                                 "--no-default-browser-check", *PROFILE_FLAGS, *window_flags()],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                close_fds=True)
    except OSError as exc:
        log.warning("could not start Edge (%s)", type(exc).__name__)
        return _fail("Microsoft Edge could not be started.")
    found = _find_window(before, proc.pid)
    if found is None:
        log.warning("Edge started but no window titled %r appeared in %.0f s", WINDOW_TITLE, FIND_TIMEOUT_S)
        return _fail("Edge started but the full page window did not appear.")
    with _lock:
        _state["hwnd"], _state["pid"] = found
        _state["reason"] = ""
    return True
