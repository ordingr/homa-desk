"""Light / dark / system colour themes for the overlay, popup and settings window (AC-PS-1).

`gfx` and `panel` look their palette names up here on every access, so anything drawn after a switch
uses the new colours; `recolour` repaints what is already on screen."""
import logging
import sys
import threading
from typing import Any, Callable, Optional

log = logging.getLogger("homa.overlay.theme")

MODES = ("light", "dark", "system")
DEFAULT_MODE = "dark"
POLL_SECONDS = 5.0
PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

DARK = {
    "PANEL_BG": "#22252B",
    "PANEL_FG": "#F2F2F2",
    "PANEL_DIM": "#9AA0A6",
    "PANEL_ERR": "#F28B82",
    "PANEL_OK": "#A8D5A2",
    "INPUT_BG": "#2F333A",
    "INPUT_BORDER": "#444A53",
    "PANEL_BG_BLINK": "#3A3F48",
    "PANEL_DIM_CARD": "#A9AFB8",
    "PANEL_LINK": "#7FB6FF",
    "BUTTON_BG": "#343942",
    "BUTTON_BG_HOVER": "#4A505B",
}
LIGHT = {
    "PANEL_BG": "#F4F5F7",
    "PANEL_FG": "#1F2328",
    "PANEL_DIM": "#5C6570",
    "PANEL_ERR": "#B3261E",
    "PANEL_OK": "#1E7A3C",
    "INPUT_BG": "#FDFDFE",
    "INPUT_BORDER": "#BFC5CD",
    "PANEL_BG_BLINK": "#E6E9ED",
    "PANEL_DIM_CARD": "#505A66",
    "PANEL_LINK": "#0B5CC4",
    "BUTTON_BG": "#E1E4E8",
    "BUTTON_BG_HOVER": "#CED3DA",
}
PALETTES = {"dark": DARK, "light": LIGHT}
NAMES = frozenset(DARK)

_COLOUR_OPTIONS = ("background", "foreground", "activebackground", "activeforeground", "selectcolor",
                   "insertbackground", "highlightbackground", "highlightcolor", "disabledbackground",
                   "disabledforeground", "selectbackground", "selectforeground", "troughcolor")

_lock = threading.Lock()
_mode = DEFAULT_MODE
_resolved = DEFAULT_MODE
_listeners: list = []
_poll_handle = None


def _read_dword(name: str) -> Optional[int]:
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY) as key:
            value, kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return int(value) if kind == winreg.REG_DWORD else None


def system_theme() -> str:
    """'light' or 'dark' as Windows reports it for apps; dark when the registry says nothing."""
    for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
        value = _read_dword(name)
        if value is not None:
            return "light" if value else "dark"
    return DEFAULT_MODE


def normalise(mode: Any) -> str:
    return mode if isinstance(mode, str) and mode in MODES else DEFAULT_MODE


def resolve(mode: Any) -> str:
    mode = normalise(mode)
    return system_theme() if mode == "system" else mode


def mode() -> str:
    return _mode


def current() -> str:
    return _resolved


def palette(name: Optional[str] = None) -> dict:
    return PALETTES[name or _resolved]


def colour(name: str) -> str:
    return PALETTES[_resolved][name]


def on_change(fn: Callable[[str, str], None]) -> None:
    """fn(old_resolved, new_resolved), called on whichever thread changed the theme."""
    with _lock:
        _listeners.append(fn)


def _apply(resolved: str) -> bool:
    global _resolved
    with _lock:
        old = _resolved
        if resolved == old:
            return False
        _resolved = resolved
        listeners = list(_listeners)
    for fn in listeners:
        try:
            fn(old, resolved)
        except Exception:
            log.exception("theme listener failed")
    return True


def set_mode(value: Any) -> bool:
    """Set the configured mode and resolve it; True when the resolved theme changed."""
    global _mode
    _mode = normalise(value)
    return _apply(resolve(_mode))


def refresh() -> bool:
    """Re-read the Windows setting when the mode is 'system'."""
    return _apply(resolve(_mode)) if _mode == "system" else False


def recolour(root: Any, old: str, new: str) -> int:
    """Swap every widget, canvas item and text tag colour that equals an `old` palette entry for the `new`
    one. Returns how many values changed."""
    src, dst = PALETTES[old], PALETTES[new]
    remap = {src[n].lower(): dst[n] for n in NAMES if src[n].lower() != dst[n].lower()}
    return _walk(root, remap, top=True)


def _walk(widget: Any, remap: dict, top: bool = False) -> int:
    changed = 0
    if not top:
        changed += _recolour_widget(widget, remap)
    try:
        children = widget.winfo_children()
    except Exception:
        return changed
    for child in children:
        changed += _walk(child, remap)
    return changed


def _recolour_widget(widget: Any, remap: dict) -> int:
    changed = 0
    try:
        options = set(widget.keys())
    except Exception:
        return 0
    for option in _COLOUR_OPTIONS:
        if option in options:
            changed += _swap(lambda o=option: str(widget.cget(o)),
                             lambda v, o=option: widget.configure(**{o: v}), remap)
    kind = widget.winfo_class()
    if kind == "Text":
        for tag in widget.tag_names():
            for option in ("background", "foreground"):
                changed += _swap(lambda t=tag, o=option: str(widget.tag_cget(t, o)),
                                 lambda v, t=tag, o=option: widget.tag_configure(t, **{o: v}), remap)
    elif kind == "Canvas":
        for item in widget.find_all():
            for option in ("fill", "outline"):
                changed += _swap(lambda i=item, o=option: str(widget.itemcget(i, o)),
                                 lambda v, i=item, o=option: widget.itemconfigure(i, **{o: v}), remap)
    return changed


def _swap(read: Callable[[], str], write: Callable[[str], Any], remap: dict) -> int:
    try:
        target = remap.get(read().lower())
        if target is None:
            return 0
        write(target)
        return 1
    except Exception:
        return 0


def _poll() -> None:
    if _mode != "system":
        return
    resolved = system_theme()
    if resolved != _resolved:
        from .. import uibus
        uibus.post(refresh)


def _arm_poll(scheduler: Any) -> None:
    """The 5-second registry check runs only while the mode is 'system'."""
    global _poll_handle
    if _mode == "system" and _poll_handle is None:
        _poll_handle = scheduler.every(POLL_SECONDS, _poll)
    elif _mode != "system" and _poll_handle is not None:
        _poll_handle.cancel()
        _poll_handle = None


def install(settings: Any, scheduler: Any, root: Any) -> None:
    """Resolve the saved theme, follow later changes to it (settings window, edited settings.json) and,
    while the mode is 'system', the Windows setting."""
    set_mode(settings.get("theme", DEFAULT_MODE))
    on_change(lambda old, new: recolour(root, old, new))

    def changed(_key: str, value: Any) -> None:
        set_mode(value)
        _arm_poll(scheduler)

    settings.on_change("theme", changed)
    _arm_poll(scheduler)


def debug_state() -> dict:
    return {"mode": _mode, "resolved": _resolved, "polling": _poll_handle is not None,
            "panel_bg": colour("PANEL_BG")}
