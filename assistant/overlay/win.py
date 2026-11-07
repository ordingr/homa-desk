import ctypes
import logging

log = logging.getLogger("assistant.overlay.win")

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

OVERLAY_EXSTYLE = WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_LAYERED
DIALOG_EXSTYLE = WS_EX_TOOLWINDOW | WS_EX_TOPMOST

_PER_MONITOR_AWARE_V2 = -4
_user32 = ctypes.windll.user32


def set_dpi_aware() -> bool:
    """Must run before the first Tk root is created."""
    try:
        if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(_PER_MONITOR_AWARE_V2)):
            return True
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return True
    except (AttributeError, OSError):
        log.warning("could not set per-monitor DPI awareness")
        return False


def hwnd_of(widget) -> int:
    """Top-level window handle for a Tk widget (Tk's wrapper, not its client child)."""
    widget.update_idletasks()
    inner = widget.winfo_id()
    outer = _user32.GetParent(inner)
    return int(outer or inner)


def get_exstyle(hwnd: int) -> int:
    return _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF


def set_exstyle_bits(hwnd: int, bits: int) -> int:
    current = get_exstyle(hwnd)
    _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, current | bits)
    return get_exstyle(hwnd)


def apply_overlay_styles(widget) -> int:
    """Always on top, no taskbar button, never takes focus."""
    hwnd = hwnd_of(widget)
    return set_exstyle_bits(hwnd, OVERLAY_EXSTYLE)


def apply_dialog_styles(widget) -> int:
    """Topmost, no taskbar button, but can take keyboard focus."""
    hwnd = hwnd_of(widget)
    return set_exstyle_bits(hwnd, DIALOG_EXSTYLE)


def describe_exstyle(value: int) -> list[str]:
    names = {
        "WS_EX_TOPMOST": WS_EX_TOPMOST,
        "WS_EX_TOOLWINDOW": WS_EX_TOOLWINDOW,
        "WS_EX_LAYERED": WS_EX_LAYERED,
        "WS_EX_NOACTIVATE": WS_EX_NOACTIVATE,
    }
    return [n for n, bit in names.items() if value & bit]


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    import win32gui
    return win32gui.GetWindowRect(hwnd)
