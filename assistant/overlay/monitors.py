import ctypes
from dataclasses import dataclass

import win32api

Rect = tuple[int, int, int, int]
_MONITORINFOF_PRIMARY = 1


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
