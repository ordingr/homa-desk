import ctypes
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

import win32api
import win32con
import win32gui

from .. import paths, uibus

log = logging.getLogger("homa.overlay.monitors")

Rect = tuple[int, int, int, int]
_MONITORINFOF_PRIMARY = 1
TEST_MODE_VAR = "HOMA_TEST_MODE"
RECHECK_MS = 500

_hidden: set = set()
_watchers: list = []


@dataclass(frozen=True)
class Monitor:
    handle: int
    rect: Rect
    work: Rect
    primary: bool
    dpi: int

    @property
    def scale(self) -> float:
        return self.dpi / 96.0


def _dpi_for(handle: int) -> int:
    dpix = ctypes.c_uint(0)
    dpiy = ctypes.c_uint(0)
    try:
        res = ctypes.windll.shcore.GetDpiForMonitor(
            ctypes.c_void_p(int(handle)), 0, ctypes.byref(dpix), ctypes.byref(dpiy))
        if res == 0 and dpix.value:
            return int(dpix.value)
    except (AttributeError, OSError):
        pass
    return 96


def list_monitors() -> list[Monitor]:
    result = []
    for handle, _dc, _rect in win32api.EnumDisplayMonitors(None, None):
        info = win32api.GetMonitorInfo(handle)
        result.append(Monitor(
            handle=int(handle),
            rect=tuple(info["Monitor"]),
            work=tuple(info["Work"]),
            primary=bool(info["Flags"] & _MONITORINFOF_PRIMARY),
            dpi=_dpi_for(handle),
        ))
    result.sort(key=lambda m: (not m.primary, m.rect[0], m.rect[1]))
    if _hidden:
        result = [m for m in result if m.handle not in _hidden] or result
    return result


def primary_monitor() -> Monitor:
    monitors = list_monitors()
    for m in monitors:
        if m.primary:
            return m
    return monitors[0]


def monitor_at(x: int, y: int) -> Monitor:
    """Monitor containing the point, else the nearest by centre distance."""
    monitors = list_monitors()
    for m in monitors:
        l, t, r, b = m.rect
        if l <= x < r and t <= y < b:
            return m

    def dist(m: Monitor) -> int:
        l, t, r, b = m.rect
        cx, cy = (l + r) // 2, (t + b) // 2
        return (cx - x) ** 2 + (cy - y) ** 2

    return min(monitors, key=dist)


def is_visible(x: int, y: int, w: int, h: int) -> bool:
    """True if the whole rectangle sits inside one monitor's bounds."""
    for m in list_monitors():
        l, t, r, b = m.rect
        if x >= l and y >= t and x + w <= r and y + h <= b:
            return True
    return False


def default_icon_position(size: int, margin: int) -> tuple[int, int]:
    """Bottom-right of the primary monitor's work area (clear of the taskbar)."""
    l, t, r, b = primary_monitor().work
    return r - size - margin, b - size - margin


def simulate_remove(idx: int) -> Monitor:
    """Test seam: act as if monitor `idx` (index into list_monitors()) was unplugged. Refuses to remove the last one."""
    if paths.env("TEST_MODE") != "1":
        raise RuntimeError(f"simulate_remove needs {TEST_MODE_VAR}=1")
    current = list_monitors()
    if not 0 <= idx < len(current):
        raise IndexError(f"no monitor {idx}; {len(current)} present")
    if len(current) == 1:
        raise ValueError("cannot remove the only monitor")
    gone = current[idx]
    _hidden.add(gone.handle)
    _notify()
    return gone


def simulate_restore() -> None:
    """Test seam: plug every simulated-removed monitor back in."""
    if paths.env("TEST_MODE") != "1":
        raise RuntimeError(f"simulate_restore needs {TEST_MODE_VAR}=1")
    _hidden.clear()
    _notify()


def simulated_hidden() -> list[int]:
    return sorted(_hidden)


def _notify() -> None:
    for watcher in list(_watchers):
        watcher.request_check()


def _centre_on_a_monitor(x: int, y: int, size: int) -> bool:
    cx, cy = x + size // 2, y + size // 2
    return any(m.rect[0] <= cx < m.rect[2] and m.rect[1] <= cy < m.rect[3] for m in list_monitors())


class DisplayWatcher:
    """Moves the icon to the primary work area when the monitor holding it disappears (AC-MM-4).

    A hidden top-level window on its own thread receives WM_DISPLAYCHANGE, so nothing runs while the display
    set is unchanged. Reconnecting a monitor never moves the icon back."""

    def __init__(self, icon: Any, root: Any = None, on_moved: Optional[Callable[[int, int], None]] = None) -> None:
        self.icon = icon
        self.root = root
        self.on_moved = on_moved
        self.moves = 0
        self.events = 0
        self._hwnd: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stopped = False

    def start(self) -> bool:
        _watchers.append(self)
        self._thread = threading.Thread(target=self._pump, name="display-watch", daemon=True)
        self._thread.start()
        if not self._ready.wait(2.0) or self._hwnd is None:
            log.warning("display change notifications are unavailable; the icon will not follow monitor unplugs")
            return False
        return True

    def stop(self) -> None:
        self._stopped = True
        if self in _watchers:
            _watchers.remove(self)
        if self._hwnd:
            try:
                win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)
            except win32gui.error:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _pump(self) -> None:
        try:
            wc = win32gui.WNDCLASS()
            wc.lpszClassName = f"HomaDisplayWatch{id(self)}"
            wc.hInstance = win32api.GetModuleHandle(None)
            wc.lpfnWndProc = {win32con.WM_DISPLAYCHANGE: self._on_display_change,
                              win32con.WM_DESTROY: self._on_destroy}
            atom = win32gui.RegisterClass(wc)
            self._hwnd = win32gui.CreateWindow(atom, "", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None)
        except Exception:
            log.exception("could not create the display watcher window")
            self._hwnd = None
            return
        finally:
            self._ready.set()
        win32gui.PumpMessages()

    def _on_display_change(self, hwnd, msg, wparam, lparam) -> int:
        self.events += 1
        self.request_check()
        return 0

    def _on_destroy(self, hwnd, msg, wparam, lparam) -> int:
        win32gui.PostQuitMessage(0)
        return 0

    def request_check(self) -> None:
        if self._stopped:
            return
        uibus.post(self.check)
        uibus.post(self._recheck_later)

    def _recheck_later(self) -> None:
        if self.root is not None and not self._stopped:
            try:
                self.root.after(RECHECK_MS, self.check)
            except Exception:
                pass

    def check(self) -> bool:
        """UI thread. True when the icon was moved."""
        if self._stopped:
            return False
        try:
            x, y = self.icon.position()
            size = self.icon.size
            if _centre_on_a_monitor(x, y, size):
                return False
            self.icon.reset_position()
            nx, ny = self.icon.position()
        except Exception:
            log.exception("could not move the icon after a display change")
            return False
        self.moves += 1
        log.info("icon monitor disappeared; moved to the primary work area at %d,%d", nx, ny)
        if self.on_moved is not None:
            self.on_moved(nx, ny)
        return True

    def debug_state(self) -> dict:
        return {"events": self.events, "moves": self.moves, "hidden": simulated_hidden(),
                "monitors": len(list_monitors()), "listening": self._hwnd is not None}
