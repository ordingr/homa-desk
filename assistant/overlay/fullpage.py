"""Edge app-mode launcher for the full page (AC-SH-7, plan 7.4 M3b-3, PR7).

``open(query, port, mint)`` mints a launch nonce, starts ``msedge.exe --app=http://127.0.0.1:<port>/app/?launch=<nonce>&q=...``
with its own ``--user-data-dir`` (so the window owns its process and is separate from the user's browsing), and
remembers the window it opened. ``close()`` posts WM_CLOSE to that window only.

The profile folder is ``%LOCALAPPDATA%\\Assistant\\edge-profile`` (plan PR7), not a folder inside ASSISTANT_HOME:
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

log = logging.getLogger("assistant.overlay.fullpage")

WINDOW_CLASS = "Chrome_WidgetWin_1"
FIND_TIMEOUT_S = 15.0
FIND_POLL_S = 0.15
_EDGE_PATHS = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
               r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")

_lock = threading.Lock()
_state: dict = {"hwnd": None, "pid": None}


def edge_path() -> Optional[str]:
    return next((p for p in _EDGE_PATHS if os.path.exists(p)), None)


def profile_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "Assistant" / "edge-profile" if base else paths.home() / "edge-profile"


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


def _find_window(before: dict, launched_pid: int) -> Optional[tuple]:
    deadline = time.monotonic() + FIND_TIMEOUT_S
    while time.monotonic() < deadline:
        now = edge_windows()
        new = {h: p for h, p in now.items() if h not in before}
        for hwnd, pid in new.items():
            if pid == launched_pid:
                return hwnd, pid
        if new:
            hwnd = next(iter(new))
            return hwnd, new[hwnd]
        time.sleep(FIND_POLL_S)
    return None


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


def open(query: str, port: int, mint: Callable[[], str], profile: Optional[Path] = None) -> bool:  # noqa: A001
    """Launch the full page for `query`. A window opened earlier is closed first. True once the new window is found."""
    edge = edge_path()
    if edge is None:
        log.warning("msedge.exe not found; the full page cannot open")
        return False
    close()
    target = Path(profile) if profile else profile_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("could not create the Edge profile folder (%s)", type(exc).__name__)
        return False
    before = edge_windows()
    url = build_url(query, port, mint())
    try:
        proc = subprocess.Popen([edge, f"--app={url}", f"--user-data-dir={target}", "--no-first-run",
                                 "--no-default-browser-check"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                close_fds=True)
    except OSError as exc:
        log.warning("could not start Edge (%s)", type(exc).__name__)
        return False
    found = _find_window(before, proc.pid)
    if found is None:
        log.warning("Edge started but its window was not found; Esc in the page cannot close it")
        return True
    with _lock:
        _state["hwnd"], _state["pid"] = found
    return True
