"""Search popup (AC-SH-1..9, AC-FI-14..17): a rounded box that slides out left of the icon,
grows into results on Enter, and walks back out with Escape. tkinter, main thread only."""
import ctypes
import dataclasses
import logging
import os
import re
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont
from typing import Any, Callable, Optional

from .. import uibus
from ..actions import claude
from ..search import modes as search_modes
from ..search.modes import (MODIFIED_CHOICES, SORT_CHOICES, CHIP_LABELS, CHIPS, ModeUnavailable, Row, SearchResult,
                            valid_modified)
from ..search.saved import SavedSearches
from ..settings import DEFAULT_SHORTCUTS
from . import ask_claude_ui, gfx, monitors, panel, theme, win

log = logging.getLogger("homa.overlay.search_ui")

LIVE_DELAY_MS = 120
GHOST_WORD_END = " .-_()[]"
GHOST_ROWS = 10
FIELD = {
    "dark": {"bg": "#6A3DB8", "fg": "#FFFFFF", "dim": "#E4D8F7", "ghost": "#D2C6EC"},
    "light": {"bg": "#E3D7F6", "fg": "#2B1557", "dim": "#5A4A80", "ghost": "#6B6482"},
}


def field_colours() -> dict:
    return FIELD[theme.current()]


def _blend(fg: str, bg: str, a: float) -> str:
    f = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x * a + y * (1 - a)) for x, y in zip(f, b))


def pill_image(master: tk.Misc, w: int, h: int, fill: str, bg: str) -> tk.PhotoImage:
    """A w x h stadium (corner radius h/2) in ``fill`` whose edge pixels are pre-blended toward ``bg``."""
    img = tk.PhotoImage(master=master, width=w, height=h)
    r = h / 2.0
    cap = int(r) + 1
    img.put(bg, to=(0, 0, w, h))
    if w > 2 * cap:
        img.put(fill, to=(cap, 0, w - cap, h))
    for y in range(h):
        left, right = [], []
        for x in range(cap):
            d = ((x + 0.5 - r) ** 2 + (y + 0.5 - r) ** 2) ** 0.5
            colour = _blend(fill, bg, max(0.0, min(1.0, r - d + 0.5)))
            left.append(colour)
            right.append(colour)
        img.put("{" + " ".join(left) + "}", to=(0, y))
        img.put("{" + " ".join(reversed(right)) + "}", to=(w - cap, y))
    return img


def completion(prefix: str, name: str) -> str:
    """The rest of the word of ``name`` that starts with ``prefix`` (at the start or a word boundary), up to the
    next separator; a following extension comes whole. Empty when nothing continues the prefix."""
    low, want = name.casefold(), prefix.casefold()
    if not want or len(low) != len(name):
        return ""
    i = low.find(want)
    while i >= 0:
        if i == 0 or not name[i - 1].isalnum():
            rest = name[i + len(want):]
            if rest.startswith("."):
                return rest
            cut = next((k for k, ch in enumerate(rest) if ch in GHOST_WORD_END), len(rest))
            if cut:
                return rest[:cut]
        i = low.find(want, i + 1)
    return ""


def suggestion(text: str, names) -> str:
    """Ghost text for the last word of ``text`` from the first of ``names`` that continues it."""
    if not text or text[-1].isspace():
        return ""
    prefix = text.rsplit(None, 1)[-1]
    if prefix[0] in '"!' or any(ch in prefix for ch in "*?:"):
        return ""
    for name in names:
        rest = completion(prefix, name)
        if rest:
            return rest
    return ""

PLACEMENTS = ("icon", "mouse", "focused", "primary", "last")
STATE_KEY = "search_box"

PAD = 12
GAP = 10
BOX_W = 440
RES_W = 720
BAR_H = 36
HEADER_H = 22
CHIP_H = 26
ROW_H = 28
VISIBLE_ROWS = 9
DETAIL_H = 44
NOTE_DETAIL_H = 150
FOOTER_H = 20
BTN_H = 28
SAVED_ROW_H = 26
MAX_SAVED_SHOWN = 6
SLIDE_STEPS = 6
SLIDE_STEP_MS = 14
SEARCH_LIMIT_NOTE = 200

MENU_ITEMS = (
    ("open", "Open", "Enter"),
    ("open_with", "Open with…", ""),
    ("reveal", "Reveal in Explorer", "Ctrl+Enter"),
    ("copy_path", "Copy path", "Ctrl+C"),
    ("copy_path_quoted", "Copy path (quoted)", ""),
    None,
    ("rename", "Rename…", "F2"),
    ("properties", "Properties", "Alt+Enter"),
    ("md_vault", "Open in MD Vault", "Ctrl+M"),
    ("recycle", "Move to Recycle Bin", "Delete"),
    None,
    ("ask_claude", "Ask Claude about this file", ""),
)
ACTIONS = tuple(item for item in MENU_ITEMS if item)
BUTTONS = (
    ("open", "Open", "Enter"),
    ("md_vault", "MD Vault", "Ctrl+M"),
    ("reveal", "Explorer", "Ctrl+Enter"),
    ("folder_listing", "List folder", "Ctrl+F"),
)
LOCAL_MARK = "● "
CLAUDE_MARK = "☁ "
CLAUDE_SUFFIX = "  → Claude"

_EXT_GROUPS = {
    "docs": ("doc", "docx", "md", "txt", "rtf", "odt", "pptx", "ppt"),
    "sheets": ("xls", "xlsx", "csv", "ods", "tsv"),
    "pdf": ("pdf",),
    "images": ("png", "jpg", "jpeg", "gif", "bmp", "svg", "webp", "ico", "tif", "tiff"),
    "code": ("py", "js", "ts", "tsx", "jsx", "json", "yml", "yaml", "html", "css", "sql", "ps1",
             "sh", "c", "cpp", "h", "cs", "java", "go", "rs", "toml", "xml"),
}
_GROUP_COLOURS = {"docs": gfx.BLUE, "sheets": gfx.GREEN, "pdf": gfx.RED, "images": gfx.PURPLE,
                  "code": "#C98A12"}
_OTHER_TAG = "#5F6670"
_HIGHLIGHT = gfx.AMBER


def ext_colour(row: Row) -> str:
    if row.is_dir:
        return gfx.GREY
    ext = row.ext.lower()
    for group, exts in _EXT_GROUPS.items():
        if ext in exts:
            return _GROUP_COLOURS[group]
    return _OTHER_TAG


def format_built_at(built_at: Optional[float]) -> str:
    if not built_at:
        return ""
    try:
        return datetime.fromtimestamp(float(built_at)).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


_RANGE_TOKEN = re.compile(r"(?<!\S)(?:modified:)?(\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2})(?!\S)", re.IGNORECASE)


def extract_range(text: str) -> tuple:
    """(text without the token, 'YYYY-MM-DD..YYYY-MM-DD' or ''): the first valid `modified:A..B` (or bare A..B) token."""
    for found in _RANGE_TOKEN.finditer(text):
        if valid_modified(found.group(1)):
            return " ".join((text[:found.start()] + " " + text[found.end():]).split()), found.group(1)
    return text, ""


def format_modified(mtime: Optional[float]) -> str:
    if mtime is None:
        return ""
    try:
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


def details_line(row: Row) -> str:
    parts = [row.path]
    if row.size is not None and not row.is_dir:
        parts.append(f"{row.size:,} bytes")
    when = format_modified(row.mtime)
    if when:
        parts.append(f"modified {when}")
    line = " · ".join(parts)
    return f"{row.passage}\n{line}" if row.passage else line


def header_text(result: SearchResult, noun: str, elapsed_ms: Optional[float]) -> str:
    parts = [f"{result.total} {noun}"]
    built = format_built_at(result.built_at)
    if built:
        parts.append(f"index from {built}")
    if elapsed_ms is not None:
        parts.append(f"{elapsed_ms:.0f} ms")
    if result.interpretation:
        parts.append(result.interpretation)
    if result.truncated:
        parts.append("truncated — refine the query")
    parts.extend(result.notes)
    return " · ".join(parts)


_KEYSYMS = {"enter": ("Return", "KP_Enter"), "escape": ("Escape",), "delete": ("Delete",), "tab": ("Tab",),
            "space": ("space",), "backspace": ("BackSpace",), "insert": ("Insert",), "home": ("Home",),
            "end": ("End",), "pageup": ("Prior",), "pagedown": ("Next",), "up": ("Up",), "down": ("Down",),
            "left": ("Left",), "right": ("Right",), "menu": ("App",)}
_MODS = (("ctrl", "Control"), ("alt", "Alt"), ("shift", "Shift"))


def chord_sequences(chord: str) -> list:
    """Tk key sequences for a settings chord such as "Ctrl+Shift+S", "F2" or "Alt+Enter"; [] when unusable."""
    parts = [p for p in str(chord).strip().split("+") if p]
    if not parts:
        return []
    key, mods = parts[-1].casefold(), {p.casefold() for p in parts[:-1]}
    if not mods <= {"ctrl", "alt", "shift"}:
        return []
    if key in _KEYSYMS:
        names = _KEYSYMS[key]
    elif re.fullmatch(r"f(?:[1-9]|1[0-9]|2[0-4])", key):
        names = (key.upper(),)
    elif len(key) == 1 and key.isascii() and key.isalnum():
        names = (key.upper() if "shift" in mods and key.isalpha() else key,)
        if key.isalpha():
            mods.discard("shift")
    else:
        return []
    prefix = [name for m, name in _MODS if m in mods]
    return ["<" + "-".join(prefix + [name]) + ">" for name in names]


class _DictSettings:
    def __init__(self, data: Optional[dict] = None) -> None:
        self.data = data or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


class SearchPopup:
    ACTIONS = ACTIONS
    BUTTONS = BUTTONS

    def __init__(self, root: tk.Misc, icon: Any, modes: Any, settings: Any = None,
                 run_search: Optional[Callable[..., Any]] = None, saved: Optional[SavedSearches] = None,
                 on_fullpage: Optional[Callable[[str], None]] = None,
                 on_action: Optional[Callable[..., Any]] = None,
                 fullpage_is_open: Optional[Callable[[], bool]] = None,
                 fullpage_close: Optional[Callable[[], Any]] = None,
                 post: Callable[..., None] = uibus.post,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        self.root = root
        self.icon = icon
        self.modes = modes
        self.settings = settings if settings is not None else _DictSettings()
        self.run_search = run_search or search_modes.make_run_search(modes)
        self.saved = saved or SavedSearches()
        self.on_fullpage = on_fullpage
        self.on_action = on_action
        self.fullpage_is_open = fullpage_is_open
        self.fullpage_close = fullpage_close
        self.on_context: Optional[Callable[[Row, Any], None]] = self.show_context_menu
        self._post = post
        self._clock = clock

        self.state = "closed"
        self.mode_i = 0
        self.chips: set = set()
        self.modified = ""
        self.sort = ""
        self.result: Optional[SearchResult] = None
        self.rows: list = []
        self.sel = -1
        self.top = 0
        self.header = ""
        self.details = ""
        self.details_kind = ""
        self.error = ""
        self.pending = False
        self.last_text = ""
        self.result_noun = "result(s)"
        self.saved_sel = 0
        self.saved_note = ""
        self.press_ts: Optional[float] = None
        self.shown_ts: Optional[float] = None
        self.anim_done = True
        self.scale = 1.0
        self.exstyle = 0
        self.hwnd = 0
        self._token = 0
        self._live_job: Optional[str] = None
        self._ghost = ""
        self._field: Optional[dict] = None
        self._pill: Optional[tk.PhotoImage] = None
        self._pill_key: Optional[tuple] = None
        self._entry_x, self._entry_w, self._entry_cy = 0, 40, 0.0
        self._anchor: dict = {"x": 0, "cy": 0, "align": "right"}
        self._offset = 0
        self._fonts: dict = {}
        self._drag: Optional[dict] = None
        self._saved_cache: list = []
        self._passage: Optional[tk.Text] = None
        self._passage_dirty = True
        self._listing_stack: list = []
        self._menu: Optional[tk.Menu] = None
        self._menu_items: list = []
        self.menu_open = False
        self.last_action: Optional[dict] = None
        self._dialog: Optional[dict] = None
        self.answer_window: Optional[ask_claude_ui.AnswerWindow] = None
        self._bound_seqs: list = []
        self._chords_bound: Optional[dict] = None
        self.ask_thread: Optional[threading.Thread] = None

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.rounded = win.round_corners(win.hwnd_of(self.win))
        key = gfx.INPUT_BORDER if self.rounded else gfx.CHROMA_KEY
        self.win.configure(bg=key)
        self.win.attributes("-topmost", True)
        if not self.rounded:
            self.win.attributes("-transparentcolor", gfx.CHROMA_KEY)
        self.canvas = tk.Canvas(self.win, bg=key, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.var = tk.StringVar()
        look = field_colours()
        self.entry = tk.Entry(self.canvas, textvariable=self.var, bg=look["bg"], fg=look["fg"],
                              insertbackground=look["fg"], relief="flat", bd=0, highlightthickness=0,
                              selectbackground=gfx.BLUE, selectforeground=gfx.WHITE)
        self.ghost_label = tk.Label(self.canvas, text="", bg=look["bg"], fg=look["ghost"], bd=0, padx=0, pady=0)
        self.var.trace_add("write", lambda *_: self._on_text())
        for sequence in ("<KeyRelease>", "<ButtonRelease-1>"):
            self.entry.bind(sequence, lambda _e: self._place_ghost(), add="+")
        self._bind_keys()
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<ButtonPress-3>", self._context)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.win.bind("<Escape>", lambda _e: self._escape())
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        theme.on_change(self._on_theme)

    # ---- mode helpers -------------------------------------------------

    def _mode_list(self) -> list:
        items = self.modes.all() if hasattr(self.modes, "all") else list(self.modes)
        return items

    def _current_mode(self) -> Any:
        items = self._mode_list()
        if not items:
            raise ModeUnavailable("no search modes registered")
        self.mode_i %= len(items)
        return items[self.mode_i]

    def effective(self) -> tuple:
        """(mode, query, switched): the mode the current text will run in, honouring `?`-style prefixes."""
        current = self._current_mode()
        if hasattr(self.modes, "resolve"):
            return self.modes.resolve(self.var.get(), current)
        return current, self.var.get().strip(), False

    def cycle_mode(self, step: int = 1) -> Any:
        items = self._mode_list()
        if not items:
            return None
        mode, query, switched = self.effective()
        if switched:
            self.var.set(query)
            self.mode_i = items.index(mode)
        self.mode_i = (self.mode_i + step) % len(items)
        self._render()
        return items[self.mode_i]

    # ---- open / close -------------------------------------------------

    def toggle(self, press_ts: Optional[float] = None) -> None:
        if self.state == "closed":
            self.open(press_ts)
        else:
            self.close()

    def open(self, press_ts: Optional[float] = None) -> None:
        if self.state != "closed":
            self._focus()
            return
        self.press_ts = press_ts
        threading.Thread(target=claude.status, name="claude-probe", daemon=True).start()
        self._apply_shortcuts()
        self.mode_i = 0
        self.chips = set()
        self.modified = ""
        self.sort = ""
        self._reset_results()
        self.var.set("")
        self.state = "input"
        self.saved_sel = 0
        self._refresh_saved()
        self._place()
        self._render()
        self._offset = int(self._rect()[2] * 0.4)
        self.anim_done = False
        self.win.attributes("-alpha", 0.0)
        self._apply_geometry()
        self.win.deiconify()
        self.exstyle = win.apply_dialog_styles(self.win)
        self.hwnd = win.hwnd_of(self.win)
        self.win.update_idletasks()
        self.shown_ts = self._clock()
        self._focus()
        self._slide(0)

    def close(self) -> None:
        if self.state == "closed":
            return
        self._token += 1
        self._cancel_live()
        self.state = "closed"
        self.pending = False
        self.win.withdraw()

    def _focus(self) -> None:
        self.win.lift()
        try:
            self.win.focus_force()
            self.entry.focus_force()
        except tk.TclError:
            return
        try:
            import win32gui
            win32gui.SetForegroundWindow(self.hwnd or win.hwnd_of(self.win))
        except Exception:
            pass

    def _slide(self, step: int) -> None:
        if self.state == "closed":
            return
        p = min(1.0, (step + 1) / SLIDE_STEPS)
        ease = 1 - (1 - p) ** 2
        full = self._rect()[2] * 0.4
        self._offset = int(full * (1 - ease)) if p < 1.0 else 0
        try:
            self.win.attributes("-alpha", ease)
            self._apply_geometry()
        except tk.TclError:
            return
        if p < 1.0:
            self.win.after(SLIDE_STEP_MS, self._slide, step + 1)
        else:
            self.anim_done = True

    # ---- placement ----------------------------------------------------

    def _placement(self) -> str:
        try:
            value = self.settings.get("search_placement", "icon")
        except Exception:
            value = "icon"
        return value if value in PLACEMENTS else "icon"

    def _place(self) -> None:
        kind = self._placement()
        anchor = None
        if kind == "last":
            anchor = self._saved_anchor()
        elif kind in ("mouse", "focused", "primary"):
            anchor = self._centered_anchor(kind)
        if anchor is None:
            anchor = self._icon_anchor()
        self._anchor = anchor
        mon = monitors.monitor_at(anchor["x"], anchor["cy"])
        self.scale = mon.scale
        self._fonts = {}

    def _icon_anchor(self) -> dict:
        ix, iy = self.icon.position()
        size = getattr(self.icon, "size", gfx.px(60, 1.0))
        scale = monitors.monitor_at(ix + 1, iy + 1).scale
        return {"x": ix - gfx.px(GAP, scale), "cy": iy + size // 2, "align": "right"}

    def _centered_anchor(self, kind: str) -> Optional[dict]:
        try:
            if kind == "mouse":
                import win32api
                mon = monitors.monitor_at(*win32api.GetCursorPos())
            elif kind == "focused":
                import win32gui
                hwnd = win32gui.GetForegroundWindow()
                l, t, r, b = win32gui.GetWindowRect(hwnd)
                mon = monitors.monitor_at((l + r) // 2, (t + b) // 2)
            else:
                mon = monitors.primary_monitor()
        except Exception:
            return None
        l, t, r, b = mon.work
        return {"x": (l + r) // 2, "cy": t + int((b - t) * 0.28), "align": "center"}

    def _saved_anchor(self) -> Optional[dict]:
        from . import icon as icon_mod
        raw = icon_mod.load_state().get(STATE_KEY)
        if (isinstance(raw, dict) and isinstance(raw.get("x"), int) and isinstance(raw.get("cy"), int)
                and raw.get("align") in ("right", "center")
                and monitors.is_visible(raw["x"] - 1, raw["cy"] - 1, 2, 2)):
            return {"x": raw["x"], "cy": raw["cy"], "align": raw["align"]}
        return None

    def _sizes(self) -> tuple:
        s = self.scale
        if self.state == "results":
            h = (PAD + BAR_H + HEADER_H + CHIP_H + 6 + VISIBLE_ROWS * ROW_H + 4 + self._detail_h() + 6 + BTN_H + PAD
                 + (FOOTER_H if self._footer() else 0))
            return gfx.px(RES_W, s), gfx.px(h, s)
        h = PAD + BAR_H + PAD
        if self.state == "input" and self._saved_visible():
            n = min(len(self._saved_cache), MAX_SAVED_SHOWN)
            h += 20 + n * SAVED_ROW_H + 6
        return gfx.px(BOX_W, s), gfx.px(h, s)

    def _notes_view(self) -> bool:
        return bool(self.rows) and self.rows[0].kind == "note"

    def _detail_h(self) -> int:
        return NOTE_DETAIL_H if self._notes_view() else DETAIL_H

    def _footer(self) -> str:
        return self.result.footer if self.result and not self._listing_stack else ""

    def _rect(self) -> tuple:
        w, h = self._sizes()
        a = self._anchor
        bar = gfx.px(PAD * 2 + BAR_H, self.scale)
        mon = monitors.monitor_at(a["x"], a["cy"])
        l, t, r, b = mon.work
        x = a["x"] - w if a["align"] == "right" else a["x"] - w // 2
        if a["cy"] > (t + b) // 2:
            y = a["cy"] + bar // 2 - h
        else:
            y = a["cy"] - bar // 2
        x = max(l, min(x, r - w))
        y = max(t, min(y, b - h))
        return x, y, w, h

    def _apply_geometry(self) -> None:
        x, y, w, h = self._rect()
        self.win.geometry(f"{w}x{h}+{x + self._offset}+{y}")
        self.canvas.configure(width=w, height=h)

    # ---- state transitions -------------------------------------------

    def _reset_results(self) -> None:
        self.result = None
        self.rows = []
        self.sel = -1
        self.top = 0
        self.header = ""
        self.details = ""
        self.details_kind = ""
        self.error = ""
        self.pending = False
        self.last_text = ""
        self._listing_stack = []
        self._passage_dirty = True

    def _saved_visible(self) -> bool:
        return self.state == "input" and not self.var.get().strip() and bool(self._saved_cache)

    def _refresh_saved(self) -> None:
        try:
            self._saved_cache = self.saved.list()
        except Exception:
            log.exception("could not read saved searches")
            self._saved_cache = []

    def _on_text(self) -> None:
        if self.state == "closed":
            return
        self._cancel_live()
        text = self.var.get()
        if self.state == "results" and not text.strip():
            self._token += 1
            self._reset_results()
            self.state = "input"
            self._refresh_saved()
            self._apply_geometry()
        elif self.state == "input":
            self.saved_sel = min(self.saved_sel, max(0, len(self._saved_cache) - 1))
            self._apply_geometry()
        if text.strip():
            self._live_job = self.win.after(LIVE_DELAY_MS, self._live_search)
        self._render()

    def _on_theme(self, _old: str, _new: str) -> None:
        try:
            self._render()
        except tk.TclError:
            pass

    def _cancel_live(self) -> None:
        if self._live_job is not None:
            try:
                self.win.after_cancel(self._live_job)
            except tk.TclError:
                pass
            self._live_job = None

    def _live_search(self) -> None:
        self._live_job = None
        if self.state == "closed" or not self.var.get().strip():
            return
        if self.state == "results" and self.var.get() == self.last_text:
            return
        try:
            mode, _query, _switched = self.effective()
        except ModeUnavailable:
            return
        if not getattr(mode, "local", False):
            return
        self.search_now(keep=self.state == "results")

    def _escape(self) -> str:
        if self.menu_open:
            self._close_menu()
        elif self._fullpage_is_open():
            self._fullpage_close()
        elif self._listing_stack:
            self._leave_listing()
        elif self.state == "results":
            self._token += 1
            self._reset_results()
            self.state = "input"
            self._refresh_saved()
            self._apply_geometry()
            self._render()
            self.entry.focus_set()
        elif self.state == "input":
            self.close()
        return "break"

    def escape(self) -> str:
        """Esc chain step (menu -> full page -> folder listing -> results -> normal -> closed); returns the resulting state."""
        self._escape()
        return self.state

    def _fullpage_is_open(self) -> bool:
        if self.fullpage_is_open is None or self.fullpage_close is None:
            return False
        try:
            return bool(self.fullpage_is_open())
        except Exception:
            log.exception("could not ask whether the full page is open")
            return False

    def _fullpage_close(self) -> None:
        try:
            self.fullpage_close()
        except Exception:
            log.exception("could not close the full page")

    def show_note(self, text: str) -> None:
        """One line in the details area, for messages that arrive from outside the popup (e.g. the full page did not open)."""
        if self.state == "closed" or not text:
            return
        self.details, self.details_kind = text, "err"
        self._render()

    # ---- searching ----------------------------------------------------

    def submit(self) -> None:
        self._cancel_live()
        text = self.var.get()
        if self.state == "input" and not text.strip():
            if self._saved_visible() and self._saved_cache:
                self.run_saved(self._saved_cache[self.saved_sel].name)
            return
        if self.state == "results" and self.rows and text == self.last_text and not self.pending:
            self.invoke("open")
            return
        self.search_now()

    def search_now(self, keep: bool = False) -> None:
        mode, query, _switched = self.effective()
        query, typed_range = extract_range(query)
        if typed_range:
            self.modified = typed_range
        if not query:
            return
        self._token += 1
        token = self._token
        self.saved_note = ""
        self.state = "results"
        self.pending = True
        self.error = ""
        self._listing_stack = []
        if not keep:
            self.header = "searching…"
            self.rows, self.sel, self.top, self.details, self.details_kind = [], -1, 0, "", ""
        self.result_noun = getattr(mode, "noun", None) or "result(s)"
        self.last_text = self.var.get()
        self._apply_geometry()
        self._render()
        chips, modified, sort = sorted(self.chips), self.modified, self.sort
        started = self._clock()

        def work() -> None:
            try:
                res = self._call_run_search(mode, query, chips, modified, sort)
                self._post(self._finish, token, res, None, (self._clock() - started) * 1000.0)
            except Exception as exc:
                self._post(self._finish, token, None, exc, 0.0)

        threading.Thread(target=work, name="homa-search", daemon=True).start()

    def _call_run_search(self, mode: Any, query: str, chips: list, modified: str, sort: str = "") -> Any:
        try:
            return self.run_search(mode, query, chips=chips, modified=modified, sort=sort)
        except TypeError as exc:
            if "keyword argument" not in str(exc):
                raise
        try:
            return self.run_search(mode, query, chips=chips, modified=modified)
        except TypeError as exc:
            if "keyword argument" not in str(exc):
                raise
            return self.run_search(mode, query)

    def _finish(self, token: int, res: Any, exc: Optional[BaseException], wall_ms: float) -> None:
        if token != self._token or self.state != "results":
            return
        self.pending = False
        if exc is not None:
            self.error = str(exc) if isinstance(exc, ModeUnavailable) else f"search failed ({type(exc).__name__})"
            self.header = self.error
            if not isinstance(exc, ModeUnavailable):
                log.warning("search failed: %s", type(exc).__name__)
            self.rows, self.sel = [], -1
            self._render()
            return
        result = search_modes.to_result(res)
        self.result = result
        self.rows = list(result.rows)
        self.top = 0
        self.sel = 0 if self.rows else -1
        elapsed = result.elapsed_ms if result.elapsed_ms is not None else wall_ms
        self.header = result.notice or header_text(result, self.result_noun, elapsed)
        self._update_details()
        self._apply_geometry()
        self._render()

    def _update_details(self) -> None:
        self.details_kind = ""
        self.details = details_line(self.rows[self.sel]) if 0 <= self.sel < len(self.rows) else ""
        self._passage_dirty = True

    def run_saved(self, name: str) -> None:
        def apply(item) -> None:
            items = self._mode_list()
            for i, mode in enumerate(items):
                if mode.keyword == item.mode:
                    self.mode_i = i
            self.chips = {c for c in item.chips if c in CHIPS}
            self.modified = item.modified if valid_modified(item.modified) else ""
            self.sort = item.sort if item.sort in dict(SORT_CHOICES) else ""
            self.var.set(item.query)
            self.search_now()
        try:
            self.saved.run(name, apply)
        except KeyError:
            self.saved_note = "saved search not found"

    def save_current(self, name: Optional[str] = None) -> bool:
        mode, query, _ = self.effective()
        query = query or self.last_text.strip()
        if not query:
            return False
        try:
            self.saved.add((name or query), query, chips=self.chips, modified=self.modified, sort=self.sort,
                           mode=mode.keyword)
        except (OSError, ValueError):
            log.exception("could not save search")
            return False
        self._refresh_saved()
        self.saved_note = f"saved “{(name or query)[:40]}”"
        self._render()
        return True

    def remove_saved(self, name: str) -> bool:
        ok = self.saved.remove(name)
        self._refresh_saved()
        self.saved_sel = min(self.saved_sel, max(0, len(self._saved_cache) - 1))
        if self.state == "input":
            self._apply_geometry()
        self._render()
        return ok

    # ---- selection / filters / actions -------------------------------

    def move(self, delta: int) -> None:
        if self._saved_visible():
            self.saved_sel = max(0, min(len(self._saved_cache) - 1, self.saved_sel + delta))
        elif self.rows:
            self.select(self.sel + delta)
            return
        self._render()

    def select(self, index: int) -> None:
        if not self.rows:
            return
        self.sel = max(0, min(len(self.rows) - 1, index))
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + VISIBLE_ROWS:
            self.top = self.sel - VISIBLE_ROWS + 1
        self._update_details()
        self._render()

    def toggle_chip(self, name: str) -> None:
        if name not in CHIPS:
            return
        self.chips ^= {name}
        self._rerun_if_results()

    def set_modified(self, value: str) -> None:
        if not valid_modified(value):
            return
        self.modified = value
        self._rerun_if_results()

    def set_sort(self, value: str) -> None:
        if value not in dict(SORT_CHOICES):
            return
        self.sort = value
        self._rerun_if_results()

    def cycle_sort(self) -> None:
        keys = [k for k, _ in SORT_CHOICES]
        self.set_sort(keys[(keys.index(self.sort) + 1) % len(keys)])

    def cycle_modified(self) -> None:
        """Any time -> Today -> 7 days -> 30 days -> Custom (the range typed in the query bar) -> Any time.
        Custom is skipped when the bar holds no valid range."""
        keys = [k for k, _ in MODIFIED_CHOICES]
        if self.modified in keys:
            nxt = self.modified if keys.index(self.modified) == len(keys) - 1 else keys[keys.index(self.modified) + 1]
            if nxt != self.modified:
                self.set_modified(nxt)
                return
            clean, custom = extract_range(self.var.get())
            if custom:
                self.var.set(clean)
                self.set_modified(custom)
                return
        self.set_modified("")

    def _rerun_if_results(self) -> None:
        if self.state == "results" and self.var.get().strip():
            self.search_now()
        else:
            self._render()

    def selected_row(self) -> Optional[Row]:
        return self.rows[self.sel] if 0 <= self.sel < len(self.rows) else None

    def invoke(self, verb: str, row: Optional[Row] = None) -> bool:
        row = row or self.selected_row()
        if row is None or self.on_action is None:
            return False
        kwargs: dict = {}
        if verb == "rename":
            new_name = self._ask_text("Rename", "New name", row.name)
            if new_name is None or new_name == row.name:
                return True
            kwargs["new_name"] = new_name
        try:
            result = self.on_action(verb, row, **kwargs)
            if isinstance(result, dict) and result.get("needs_confirmation"):
                summary = result.get("summary")
                if verb == "ask_claude" and isinstance(summary, dict) and summary.get("available") is False:
                    result = {"ok": False, "message": str(summary.get("reason") or claude.REASON_ABSENT)}
                elif verb == "ask_claude":
                    return self._ask_claude_flow(row, result)
                elif self._ask_confirm(str(result.get("message", "")), False):
                    result = self.on_action(verb, row, **kwargs, confirm=True)
                else:
                    result = {"ok": False, "message": "Cancelled.", "cancelled": True}
        except Exception:
            log.exception("result action %s failed", verb)
            return False
        self._after_action(verb, row, result)
        return True

    def _ask_claude_flow(self, row: Row, prepared: dict) -> bool:
        summary = prepared.get("summary") or {}
        offer = not summary.get("whole_file") and summary.get("mode") != "whole_file"
        choice = self._ask_confirm(ask_claude_ui.confirm_text(prepared), True, whole=offer)
        whole = choice == "whole"
        if whole:
            prepared = self.on_action("ask_claude", row, whole_file=True)
            if not (isinstance(prepared, dict) and prepared.get("needs_confirmation")):
                self._after_action("ask_claude", row, prepared)
                return True
            choice = self._ask_confirm(ask_claude_ui.confirm_text(prepared), True)
        if not choice:
            self._after_action("ask_claude", row, {"ok": False, "message": "Cancelled.", "cancelled": True})
            return True
        return self._ask_claude_async(row, whole)

    def _ask_claude_async(self, row: Row, whole_file: bool = False) -> bool:
        if self.ask_thread is not None and self.ask_thread.is_alive():
            return True
        self.details, self.details_kind = f"Asking Claude about {row.name}…", "dim"
        self._render()

        def work() -> None:
            try:
                result = (self.on_action("ask_claude", row, confirm=True, whole_file=True) if whole_file
                          else self.on_action("ask_claude", row, confirm=True))
            except Exception:
                log.exception("result action ask_claude failed")
                result = {"ok": False, "message": "Asking Claude failed."}
            uibus.post(self._ask_claude_done, row, result)

        self.ask_thread = threading.Thread(target=work, name="ask-claude", daemon=True)
        self.ask_thread.start()
        return True

    def _ask_claude_done(self, row: Row, result: Any) -> None:
        self._after_action("ask_claude", row, result)
        if isinstance(result, dict) and result.get("ok") and isinstance(result.get("answer"), str):
            if self.answer_window is not None:
                self.answer_window.close()
            self.answer_window = ask_claude_ui.show_answer(self.win, row.name, row.path, result["answer"],
                                                           self.scale)

    def _after_action(self, verb: str, row: Row, result: Any) -> None:
        self.last_action = {"verb": verb, "name": row.name, "result": result}
        if not isinstance(result, dict):
            return
        if verb == "folder_listing" and result.get("ok") and isinstance(result.get("rows"), list):
            self._enter_listing(result)
            return
        message = str(result.get("message", ""))
        if verb == "rename" and result.get("ok") and 0 <= self.sel < len(self.rows):
            new_path = result.get("path")
            new_name = os.path.basename(str(new_path)) if new_path else ""
            if new_name:
                self.rows[self.sel] = dataclasses.replace(row, name=new_name, spans=())
            self._update_details()
        if message:
            self.details = message
            self.details_kind = "ok" if result.get("ok") else "dim" if result.get("cancelled") else "err"
        self._render()

    def _enter_listing(self, result: dict) -> None:
        folder = str(result.get("folder", ""))
        rows = []
        for item in result["rows"]:
            path = str(item.get("path", ""))
            name = str(item.get("name") or os.path.basename(path))
            is_dir = bool(item.get("is_dir"))
            rows.append(Row(name=name, folder=os.path.dirname(path) or folder,
                            ext="" if is_dir else os.path.splitext(name)[1].lstrip("."), size=item.get("size"),
                            mtime=item.get("mtime"), is_dir=is_dir, placeholder=bool(item.get("placeholder"))))
        self._listing_stack.append({"folder": folder, "rows": self.rows, "sel": self.sel, "top": self.top,
                                    "header": self.header, "details": self.details, "kind": self.details_kind})
        self.rows, self.top, self.sel = rows, 0, 0 if rows else -1
        self.header = f"Folder: {folder}"
        if result.get("truncated"):
            self.header += f" · showing {len(rows)} of {result.get('total', len(rows))}"
        self.saved_note = ""
        self._update_details()
        self._apply_geometry()
        self._render()

    def _leave_listing(self) -> None:
        snap = self._listing_stack.pop()
        self.rows, self.sel, self.top = snap["rows"], snap["sel"], snap["top"]
        self.header, self.details, self.details_kind = snap["header"], snap["details"], snap["kind"]
        self._passage_dirty = True
        self._apply_geometry()
        self._render()
        self.entry.focus_set()

    # ---- context menu and dialogs ------------------------------------

    def show_context_menu(self, row: Row, event: Any = None) -> None:
        if self.menu_open or self.on_action is None:
            return
        menu = tk.Menu(self.win, tearoff=0, bg=gfx.PANEL_BG, fg=gfx.PANEL_FG, activebackground=gfx.BUTTON_BG_HOVER,
                       activeforeground=gfx.WHITE, bd=1, relief="flat",
                       font=("Segoe UI", max(9, round(10 * self.scale))))
        items = []
        for item in MENU_ITEMS:
            if item is None:
                menu.add_separator()
                continue
            verb, label, shortcut = item
            shortcut = self.chord_label(verb, shortcut) if shortcut else shortcut
            is_claude = verb == "ask_claude"
            found = claude.cached_status() if is_claude else None
            reason = str(found["reason"]) if found and not found["available"] else None
            text = f"{CLAUDE_MARK}{label}{CLAUDE_SUFFIX}" if is_claude else f"{LOCAL_MARK}{label}"
            if reason:
                text = f"{text}  ({reason})"
            menu.add_command(label=text, accelerator=shortcut, state="disabled" if reason else "normal",
                             foreground=gfx.AMBER if is_claude else gfx.PANEL_OK,
                             command=lambda v=verb, r=row: self.invoke(v, r))
            items.append({"verb": verb, "label": text, "key": shortcut, "scope": "claude" if is_claude else "local",
                          "enabled": reason is None, "reason": reason})
        self._menu, self._menu_items, self.menu_open = menu, items, True
        x = getattr(event, "x_root", None)
        y = getattr(event, "y_root", None)
        if x is None or y is None:
            x, y = self.win.winfo_rootx() + 60, self.win.winfo_rooty() + 120
        try:
            menu.tk_popup(int(x), int(y))
        finally:
            menu.grab_release()
            self.menu_open, self._menu = False, None

    def _close_menu(self) -> None:
        if self._menu is not None:
            try:
                self._menu.unpost()
                ctypes.windll.user32.EndMenu()
            except (tk.TclError, AttributeError, OSError):
                pass
        self.menu_open, self._menu = False, None

    def _menu_at_selection(self) -> None:
        row = self.selected_row()
        if row is None or self.state != "results":
            return
        s = self.scale
        top = gfx.px(PAD + BAR_H + 4 + HEADER_H + CHIP_H + 6, s)
        y = self.win.winfo_rooty() + top + (self.sel - self.top) * gfx.px(ROW_H, s) + gfx.px(ROW_H, s)
        at = type("At", (), {"x_root": self.win.winfo_rootx() + gfx.px(120, s), "y_root": y})()
        self.show_context_menu(row, at)

    def _open_dialog(self, kind: str, title: str, message: str) -> tk.Toplevel:
        dlg = tk.Toplevel(self.win)
        dlg.withdraw()
        dlg.title(title)
        dlg.configure(bg=gfx.PANEL_BG, padx=16, pady=14)
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        self._dialog = {"kind": kind, "title": title, "message": message}
        return dlg

    def _run_dialog(self, dlg: tk.Toplevel, focus: tk.Misc) -> None:
        dlg.update_idletasks()
        x = self.win.winfo_rootx() + max(0, (self.win.winfo_width() - dlg.winfo_reqwidth()) // 2)
        y = self.win.winfo_rooty() + gfx.px(80, self.scale)
        dlg.geometry(f"+{x}+{y}")
        dlg.deiconify()
        dlg.lift()
        try:
            dlg.wait_visibility()
            dlg.grab_set()
            focus.focus_force()
        except tk.TclError:
            pass
        dlg.wait_window()
        self._dialog = None
        self._focus()

    def _dialog_button(self, parent: tk.Misc, text: str, command: Callable[[], None], accent: str = "") -> tk.Button:
        return tk.Button(parent, text=text, command=command, bg=accent or gfx.BUTTON_BG, fg=gfx.PANEL_FG,
                         activebackground=gfx.BUTTON_BG_HOVER, activeforeground=gfx.WHITE, relief="flat", bd=0,
                         padx=14, pady=5, font=("Segoe UI", 10))

    def _ask_confirm(self, message: str, claude: bool = False, whole: bool = False) -> Any:
        """True or False; "whole" (truthy) when the user chose to send the whole file instead."""
        dlg = self._open_dialog("confirm", "Confirm", message)
        answer: dict = {"ok": False}
        if claude:
            tk.Label(dlg, text=f"{CLAUDE_MARK}Sends to Claude", bg=gfx.PANEL_BG, fg=gfx.AMBER,
                     font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))
        tk.Label(dlg, text=message, bg=gfx.PANEL_BG, fg=gfx.PANEL_FG, font=("Segoe UI", 11), wraplength=380,
                 justify="left").pack(anchor="w")
        row = tk.Frame(dlg, bg=gfx.PANEL_BG)
        row.pack(anchor="e", pady=(12, 0))

        def finish(value: Any) -> None:
            answer["ok"] = value
            dlg.destroy()

        no = self._dialog_button(row, "Cancel", lambda: finish(False))
        yes = self._dialog_button(row, "Send" if claude else "OK", lambda: finish(True),
                                  gfx.AMBER if claude else gfx.BLUE)
        no.pack(side="right", padx=(8, 0))
        yes.pack(side="right")
        if whole:
            self._dialog_button(row, "Send whole file…", lambda: finish("whole")).pack(side="right", padx=(0, 8))
        dlg.bind("<Escape>", lambda _e: finish(False))
        dlg.bind("<Return>", lambda _e: finish(True))
        dlg.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        self._run_dialog(dlg, yes)
        return answer["ok"]

    def _ask_text(self, title: str, label: str, initial: str = "") -> Optional[str]:
        dlg = self._open_dialog("input", title, label)
        answer: dict = {"value": None}
        tk.Label(dlg, text=label, bg=gfx.PANEL_BG, fg=gfx.PANEL_DIM, font=("Segoe UI", 10)).pack(anchor="w")
        var = tk.StringVar(value=initial)
        entry = tk.Entry(dlg, textvariable=var, width=46, bg=gfx.INPUT_BG, fg=gfx.PANEL_FG,
                         insertbackground=gfx.PANEL_FG, relief="flat", highlightthickness=1,
                         highlightbackground=gfx.INPUT_BORDER, highlightcolor=gfx.PURPLE, font=("Segoe UI", 12))
        entry.pack(anchor="w", pady=(4, 0), ipady=3)
        stem = os.path.splitext(initial)[0]
        entry.selection_range(0, len(stem) if stem else "end")
        row = tk.Frame(dlg, bg=gfx.PANEL_BG)
        row.pack(anchor="e", pady=(12, 0))

        def finish(value: Optional[str]) -> None:
            answer["value"] = value
            dlg.destroy()

        self._dialog_button(row, "Cancel", lambda: finish(None)).pack(side="right", padx=(8, 0))
        self._dialog_button(row, "OK", lambda: finish(var.get()), gfx.BLUE).pack(side="right")
        dlg.bind("<Escape>", lambda _e: finish(None))
        dlg.bind("<Return>", lambda _e: finish(var.get()))
        dlg.protocol("WM_DELETE_WINDOW", lambda: finish(None))
        self._run_dialog(dlg, entry)
        return answer["value"]

    def fullpage(self) -> None:
        if self.on_fullpage is not None:
            self.on_fullpage(self.var.get().strip())

    # ---- keyboard / mouse --------------------------------------------

    def _chords(self) -> dict:
        chords = dict(DEFAULT_SHORTCUTS)
        try:
            custom = self.settings.get("shortcuts")
        except Exception:
            custom = None
        if isinstance(custom, dict):
            chords.update({k: v for k, v in custom.items()
                           if k in DEFAULT_SHORTCUTS and isinstance(v, str) and chord_sequences(v)})
        return chords

    def chord_label(self, action: str, fallback: str = "") -> str:
        return self._chords().get(action, fallback)

    def _bind_keys(self) -> None:
        self._apply_shortcuts()

    def _apply_shortcuts(self) -> None:
        chords = self._chords()
        if chords == self._chords_bound:
            return
        for seq in self._bound_seqs:
            self.entry.unbind(seq)
        self._bound_seqs = []
        claimed: set = set()

        def bind(sequence: str, handler: Callable[[Any], str]) -> None:
            self.entry.bind(sequence, handler)
            self._bound_seqs.append(sequence)

        def make(fn: Callable[[], Any], only_results: bool) -> Callable[[Any], str]:
            def handler(_event) -> str:
                if only_results and self.state != "results":
                    return ""
                fn()
                return "break"
            return handler

        def bind_action(action: str, handler: Callable[[Any], str]) -> None:
            try:
                for sequence in chord_sequences(chords[action]):
                    bind(sequence, handler)
                    claimed.add(sequence)
            except tk.TclError:
                log.warning("shortcut %r for %s is not usable; using the default", chords[action], action)
                for sequence in chord_sequences(DEFAULT_SHORTCUTS[action]):
                    bind(sequence, handler)
                    claimed.add(sequence)

        def key(action: str, fn: Callable[[], Any], only_results: bool = False) -> None:
            bind_action(action, make(fn, only_results))

        def raw(action: str, handler: Callable[[Any], str]) -> None:
            bind_action(action, handler)

        key("open", self.submit)
        key("reveal", lambda: self.invoke("reveal"), True)
        key("properties", lambda: self.invoke("properties"), True)
        key("rename", lambda: self.invoke("rename"), True)
        key("context_menu", self._menu_at_selection, True)
        tab_cycles = chords["cycle_mode"].casefold() == "tab"
        if tab_cycles:
            raw("cycle_mode", self._tab)
        else:
            key("cycle_mode", self.cycle_mode)
        key("md_vault", lambda: self.invoke("md_vault"), True)
        key("folder_listing", lambda: self.invoke("folder_listing"), True)
        key("cycle_sort", self.cycle_sort, True)
        key("save_search", self.save_current)
        key("cycle_date", self.cycle_modified, True)
        key("full_page", self.fullpage)
        key("close", self._escape)
        raw("recycle", self._delete_key)
        raw("copy_path", self._copy_key)

        def fixed(sequence: str, fn: Callable[[], Any], only_results: bool = False) -> None:
            if sequence not in claimed:
                bind(sequence, make(fn, only_results))

        fixed("<App>", self._menu_at_selection, True)
        if not tab_cycles and "<Tab>" not in claimed:
            bind("<Tab>", lambda _e: "break" if self.accept_suggestion() else "")
        if tab_cycles:
            fixed("<Shift-Tab>", lambda: self.cycle_mode(-1))
            fixed("<ISO_Left_Tab>", lambda: self.cycle_mode(-1))
        fixed("<Up>", lambda: self.move(-1))
        fixed("<Down>", lambda: self.move(1))
        fixed("<Prior>", lambda: self.move(-VISIBLE_ROWS))
        fixed("<Next>", lambda: self.move(VISIBLE_ROWS))
        fixed("<Home>", lambda: self.select(0), True)
        fixed("<End>", lambda: self.select(len(self.rows) - 1), True)
        fixed("<KP_Enter>", self.submit)
        self._chords_bound = chords

    def _row_keys_live(self) -> bool:
        return (self.state == "results" and bool(self.rows) and self.var.get() == self.last_text
                and not self.entry.selection_present())

    def _delete_key(self, _event) -> str:
        if self._saved_visible():
            self.remove_saved(self._saved_cache[self.saved_sel].name)
        elif self._row_keys_live():
            self.invoke("recycle")
        else:
            return ""
        return "break"

    def _copy_key(self, _event) -> str:
        if not self._row_keys_live():
            return ""
        self.invoke("copy_path")
        return "break"

    def _hit(self, event) -> str:
        items = self.canvas.find_overlapping(event.x, event.y, event.x, event.y)
        for item in reversed(items):
            for tag in self.canvas.gettags(item):
                if tag.startswith("hit:"):
                    return tag
        return ""

    def _press(self, event) -> None:
        hit = self._hit(event)
        self._drag = {"hit": hit, "rx": event.x_root, "ry": event.y_root, "ax": self._anchor["x"],
                      "ay": self._anchor["cy"], "moved": False}
        self.entry.focus_set()

    def _motion(self, event) -> None:
        d = self._drag
        if not d or d["hit"]:
            return
        dx, dy = event.x_root - d["rx"], event.y_root - d["ry"]
        if not d["moved"] and abs(dx) < 4 and abs(dy) < 4:
            return
        d["moved"] = True
        self._anchor = {**self._anchor, "x": d["ax"] + dx, "cy": d["ay"] + dy}
        self._apply_geometry()

    def _release(self, event) -> None:
        d, self._drag = self._drag, None
        if not d:
            return
        if d["moved"]:
            try:
                from . import icon as icon_mod
                icon_mod.save_state({STATE_KEY: dict(self._anchor)})
            except OSError:
                log.exception("could not save the search box position")
            return
        self.click(d["hit"])

    def click(self, hit: str) -> None:
        parts = hit.split(":")
        if len(parts) < 3:
            return
        kind, arg = parts[1], ":".join(parts[2:])
        if kind == "row":
            self.select(int(arg))
        elif kind == "chip":
            self.toggle_chip(arg)
        elif kind == "date":
            self.cycle_modified()
        elif kind == "sort":
            self.cycle_sort()
        elif kind == "act":
            self.invoke(arg)
        elif kind == "back":
            self.escape()
        elif kind == "full":
            self.fullpage()
        elif kind == "save":
            self.save_current()
        elif kind == "mode":
            self.cycle_mode()
        elif kind == "saved":
            self.run_saved(self._saved_cache[int(arg)].name)

    def _context(self, event) -> None:
        hit = self._hit(event)
        if self.on_context is not None and hit.startswith("hit:row:"):
            self.select(int(hit.split(":")[2]))
            row = self.selected_row()
            if row is not None:
                self.on_context(row, event)

    def _wheel(self, event) -> None:
        if self.state != "results" or not self.rows:
            return
        step = -1 if event.delta > 0 else 1
        self.top = max(0, min(max(0, len(self.rows) - VISIBLE_ROWS), self.top + step * 3))
        self._render()

    # ---- drawing ------------------------------------------------------

    def _font(self, key: str) -> tkfont.Font:
        if key not in self._fonts:
            sizes = {"base": (13, "normal"), "small": (11, "normal"), "bold": (13, "bold"),
                     "input": (15, "normal"), "tag": (10, "bold"), "title": (12, "bold")}
            size, weight = sizes[key]
            self._fonts[key] = tkfont.Font(root=self.root, family="Segoe UI", size=-gfx.px(size, self.scale),
                                           weight=weight)
        return self._fonts[key]

    def _fit(self, text: str, font: tkfont.Font, maxw: int, tail: bool = False) -> str:
        if maxw <= 0:
            return ""
        if font.measure(text) <= maxw:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            cand = ("…" + text[-mid:]) if tail else (text[:mid] + "…")
            if font.measure(cand) <= maxw:
                lo = mid
            else:
                hi = mid - 1
        return ("…" + text[-lo:]) if tail else (text[:lo] + "…")

    def _render(self) -> None:
        if self.state == "closed":
            return
        c, s = self.canvas, self.scale
        w, h = self._sizes()
        c.delete("all")
        c.configure(width=w, height=h)
        try:
            mode, query, switched = self.effective()
        except ModeUnavailable:
            return
        p = gfx.px(PAD, s)
        if self.rounded:
            c.configure(bg=gfx.INPUT_BORDER)
            panel.rounded_rect(c, 2, 2, w - 2, h - 2, max(2, panel.DWM_RADIUS * s - 2), fill=gfx.PANEL_BG, outline="")
        else:
            panel.rounded_rect(c, 1, 1, w - 1, h - 1, 14 * s, fill=gfx.INPUT_BORDER, outline=gfx.INPUT_BORDER)
            panel.rounded_rect(c, 3, 3, w - 3, h - 3, 12 * s, fill=gfx.PANEL_BG, outline="")

        badge = f"{mode.label} · {'local' if getattr(mode, 'local', False) else 'online'}"
        bold = self._font("bold")
        bw = bold.measure(badge) + gfx.px(22, s)
        bh = gfx.px(BAR_H - 8, s)
        by = p + gfx.px(4, s)
        colour = gfx.GREEN if getattr(mode, "local", False) else gfx.AMBER
        panel.rounded_rect(c, p, by, p + bw, by + bh, bh / 2, tag=("badge", "hit:mode:cycle"), fill=colour, outline=colour)
        c.create_text(p + bw / 2, by + bh / 2, text=badge, fill=gfx.WHITE, font=bold, tags=("badge", "hit:mode:cycle"))

        ex = p + bw + gfx.px(8, s)
        right = w - p
        if self.state == "results":
            fw = gfx.px(96, s)
            self._button(right - fw, p, fw, gfx.px(BAR_H, s), "Full page", "hit:full:x", "F11")
            right -= fw + gfx.px(8, s)
        bar_h = gfx.px(BAR_H, s)
        look = field_colours()
        key = (right - ex, bar_h, look["bg"], gfx.PANEL_BG)
        if self._pill_key != key:
            self._pill = pill_image(self.canvas, max(40, right - ex), bar_h, look["bg"], gfx.PANEL_BG)
            self._pill_key = key
        c.create_image(ex, p, image=self._pill, anchor="nw", tags="pill")
        inset = bar_h // 2
        self._entry_x = ex + inset
        self._entry_w = max(40, right - ex - 2 * inset)
        self._entry_cy = p + bar_h / 2
        self.entry.configure(font=self._font("input"), bg=look["bg"], fg=look["fg"], insertbackground=look["fg"])
        self.ghost_label.configure(font=self._font("input"), bg=look["bg"], fg=look["ghost"])
        c.create_window(self._entry_x, self._entry_cy, window=self.entry, anchor="w", width=self._entry_w,
                        height=bar_h - 8)
        if not self.var.get():
            c.create_text(self._entry_x + 1, self._entry_cy, text="Type to search…", anchor="w", fill=look["dim"],
                          font=self._font("input"))
        self._place_ghost()

        y = p + gfx.px(BAR_H, s) + gfx.px(4, s)
        if self.state == "input":
            self._draw_saved(y, w)
        else:
            self._draw_results(y, w, h)

    def suggestion_text(self) -> str:
        if self.state == "closed" or self._listing_stack or self.entry.selection_present():
            return ""
        text = self.var.get()
        if self.entry.index("insert") != len(text):
            return ""
        names = [row.name for row in self.rows[:GHOST_ROWS]]
        for item in self._saved_cache:
            names.extend((item.name, item.query))
        return suggestion(text, names)

    def _place_ghost(self) -> None:
        if self.state == "closed":
            return
        self.canvas.delete("ghost")
        self._ghost = self.suggestion_text()
        if not self._ghost:
            return
        font = self._font("input")
        try:
            x_end = self.entry.bbox(len(self.var.get()))[0]
        except (tk.TclError, TypeError):
            return
        if x_end + font.measure(self._ghost) > self._entry_w:
            self._ghost = ""
            return
        self.ghost_label.configure(text=self._ghost)
        self.canvas.create_window(self._entry_x + x_end + 1, self._entry_cy, window=self.ghost_label, anchor="w",
                                  tags="ghost")

    def _tab(self, _event) -> str:
        if not self.accept_suggestion():
            self.cycle_mode()
        return "break"

    def accept_suggestion(self) -> bool:
        """Tab: append the ghost text; False when none is shown."""
        self._place_ghost()
        if not self._ghost:
            return False
        self.var.set(self.var.get() + self._ghost)
        self.entry.icursor("end")
        return True

    def _button(self, x: float, y: float, bw: float, bh: float, label: str, tag: str, hint: str = "",
                active: bool = False, accent: str = "") -> None:
        c, s = self.canvas, self.scale
        fill = gfx.BLUE if active else gfx.BUTTON_BG
        panel.rounded_rect(c, x, y, x + bw, y + bh, bh / 2, tag=(tag, "btn"), fill=fill, outline=accent or fill)
        c.create_text(x + bw / 2, y + bh / 2, text=label, fill=gfx.PANEL_FG, font=self._font("small" if hint else "base"),
                      tags=(tag, "btn"))

    def _draw_saved(self, y: float, w: int) -> None:
        if not self._saved_visible():
            return
        c, s = self.canvas, self.scale
        p = gfx.px(PAD, s)
        c.create_text(p, y + gfx.px(8, s), text="Saved searches", anchor="w", fill=gfx.PANEL_DIM,
                      font=self._font("small"), tags="saved_title")
        y += gfx.px(20, s)
        rh = gfx.px(SAVED_ROW_H, s)
        for i, item in enumerate(self._saved_cache[:MAX_SAVED_SHOWN]):
            tag = f"hit:saved:{i}"
            if i == self.saved_sel:
                panel.rounded_rect(c, p, y, w - p, y + rh, 6 * s, tag=(tag, "saved"), fill=gfx.BUTTON_BG_HOVER, outline="")
            name = self._fit("★ " + item.name, self._font("bold"), int((w - 2 * p) * 0.4))
            c.create_text(p + 8 * s, y + rh / 2, text=name, anchor="w", fill=gfx.PANEL_FG, font=self._font("bold"),
                          tags=(tag, "saved"))
            nx = p + 8 * s + self._font("bold").measure(name) + 10 * s
            extra = item.query + (f"  [{', '.join(item.chips)}]" if item.chips else "")
            c.create_text(nx, y + rh / 2, text=self._fit(extra, self._font("small"), int(w - p - nx - 6 * s)),
                          anchor="w", fill=gfx.PANEL_DIM, font=self._font("small"), tags=(tag, "saved"))
            y += rh

    def _draw_results(self, y: float, w: int, h: int) -> None:
        c, s = self.canvas, self.scale
        p = gfx.px(PAD, s)
        small, base = self._font("small"), self._font("base")
        head = self.saved_note or self.header
        colour = gfx.PANEL_ERR if self.error else gfx.PANEL_DIM_CARD
        head_w = w - 2 * p
        if self._listing_stack:
            label = "‹ Back (Esc)"
            sw = small.measure(label) + gfx.px(14, s)
            self._button(w - p - sw, y, sw, gfx.px(HEADER_H - 2, s), label, "hit:back:x", "chip")
            head_w -= sw + gfx.px(8, s)
        elif not self._notes_view():
            label = "Sort: " + dict(SORT_CHOICES)[self.sort]
            sw = small.measure(label) + gfx.px(14, s)
            sh = gfx.px(HEADER_H - 2, s)
            self._button(w - p - sw, y, sw, sh, label, "hit:sort:x", "chip", active=bool(self.sort))
            head_w -= sw + gfx.px(8, s)
        c.create_text(p, y + gfx.px(HEADER_H, s) / 2, text=self._fit(head, small, head_w), anchor="w",
                      fill=colour, font=small, tags="header")
        y += gfx.px(HEADER_H, s)

        x, ch = p, gfx.px(CHIP_H, s)
        for name in CHIPS:
            label = CHIP_LABELS[name]
            cw = small.measure(label) + gfx.px(18, s)
            self._button(x, y, cw, ch, label, f"hit:chip:{name}", "chip", active=name in self.chips)
            x += cw + gfx.px(6, s)
        date_label = "Modified: " + dict(MODIFIED_CHOICES).get(self.modified, self.modified)
        dw = small.measure(date_label) + gfx.px(18, s)
        self._button(x + gfx.px(6, s), y, dw, ch, date_label, "hit:date:x", "chip", active=bool(self.modified))
        sw = small.measure("★ Save") + gfx.px(18, s)
        self._button(w - p - sw, y, sw, ch, "★ Save", "hit:save:x", "chip")
        y += ch + gfx.px(6, s)

        rh = gfx.px(ROW_H, s)
        list_h = rh * VISIBLE_ROWS
        if not self.rows:
            msg = "This folder is empty" if self._listing_stack else "" if self.pending else (self.result.notice if self.result and self.result.notice else
                                           ("" if self.error else "No matches"))
            c.create_text(w / 2, y + list_h / 2, text=msg, fill=gfx.PANEL_DIM, font=base, tags="empty")
        for n, row in enumerate(self.rows[self.top:self.top + VISIBLE_ROWS]):
            self._draw_row(row, self.top + n, p, y + n * rh, w - 2 * p, rh)
        if len(self.rows) > VISIBLE_ROWS:
            span = list_h
            thumb = max(gfx.px(18, s), span * VISIBLE_ROWS / len(self.rows))
            ty = y + (span - thumb) * (self.top / max(1, len(self.rows) - VISIBLE_ROWS))
            panel.rounded_rect(c, w - p + 2 * s, ty, w - p + 5 * s, ty + thumb, 2 * s, tag="scroll",
                               fill=gfx.INPUT_BORDER, outline="")
        y += list_h + gfx.px(4, s)

        dh = gfx.px(self._detail_h(), s)
        panel.rounded_rect(c, p, y, w - p, y + dh, 6 * s, tag="detailbox", fill=gfx.INPUT_BG, outline="")
        row = self.selected_row()
        detail_colour = {"err": gfx.PANEL_ERR, "ok": gfx.PANEL_OK}.get(self.details_kind, gfx.PANEL_DIM_CARD)
        if self._notes_view() and row is not None:
            path_line = self.details if self.details_kind else details_line(Row(**{**row.__dict__, "passage": ""}))
            c.create_text(p + 8 * s, y + 4 * s, text=self._fit(path_line, small, int(w - 2 * p - 16 * s), tail=True),
                          anchor="nw", fill=detail_colour, font=small, tags="details")
            self._show_passage(row, p + 6 * s, y + 24 * s, w - 2 * p - 12 * s, dh - 28 * s)
        else:
            self._hide_passage()
            c.create_text(p + 8 * s, y + 4 * s, text=self.details, anchor="nw", fill=detail_colour, font=small,
                          width=w - 2 * p - 16 * s, tags="details")
        y += dh + gfx.px(6, s)

        x = p
        bh = gfx.px(BTN_H, s)
        for verb, label, shortcut in self.BUTTONS:
            text = f"{label} ({self.chord_label(verb, shortcut)})"
            bw = small.measure(text) + gfx.px(20, s)
            self._button(x, y, bw, bh, text, f"hit:act:{verb}", "act")
            x += bw + gfx.px(6, s)
        if self._footer():
            c.create_text(w / 2, h - p - gfx.px(FOOTER_H, s) / 2 + 2 * s, text=self._footer(), fill=gfx.PANEL_DIM,
                          font=small, tags="footer")

    def _draw_row(self, row: Row, index: int, x: float, y: float, width: float, rh: float) -> None:
        c, s = self.canvas, self.scale
        tag = (f"hit:row:{index}", "row")
        if index == self.sel:
            panel.rounded_rect(c, x, y + 1, x + width, y + rh - 1, 6 * s, tag=tag, fill=gfx.BUTTON_BG_HOVER, outline="")
        tag_w = gfx.px(46, s)
        label = "?" if row.is_dir else (row.ext[:4] or "·")
        panel.rounded_rect(c, x + 4 * s, y + 5 * s, x + 4 * s + tag_w, y + rh - 5 * s, 4 * s, tag=tag,
                           fill=ext_colour(row), outline="")
        c.create_text(x + 4 * s + tag_w / 2, y + rh / 2, text=label, fill=gfx.WHITE, font=self._font("tag"), tags=tag)
        date = (format_modified(row.mtime) or "")[:10]
        dw = self._font("small").measure("0000-00-00") + 6 * s
        c.create_text(x + width - 6 * s, y + rh / 2, text=date, anchor="e", fill=gfx.PANEL_DIM, font=self._font("small"),
                      tags=tag)
        cloud = 16 * s if row.placeholder else 0
        if row.placeholder:
            c.create_text(x + width - dw - 12 * s, y + rh / 2, text="\u2601", anchor="e", fill=gfx.PANEL_DIM,
                          font=self._font("small"), tags=tag + ("cloud",))
        nx = x + 4 * s + tag_w + 10 * s
        avail = x + width - dw - 14 * s - cloud - nx
        name_w = self._draw_name(row, nx, y + rh / 2, int(avail * 0.55), tag)
        fx = nx + name_w + 12 * s
        folder = self._fit(row.folder, self._font("small"), int(x + width - dw - 14 * s - cloud - fx), tail=True)
        c.create_text(fx, y + rh / 2, text=folder, anchor="w", fill=gfx.PANEL_DIM, font=self._font("small"), tags=tag)

    def _draw_name(self, row: Row, x: float, cy: float, maxw: int, tag: tuple) -> float:
        base, bold = self._font("base"), self._font("bold")
        name = row.name
        cut = self._fit(name, base, maxw)
        limit = len(cut) - 1 if cut.endswith("…") and cut != name else len(cut)
        pieces, pos = [], 0
        for a, b in sorted((max(0, a), min(limit, b)) for a, b in row.spans if a < limit):
            if a > pos:
                pieces.append((name[pos:a], False))
            if b > a:
                pieces.append((name[a:b], True))
            pos = max(pos, b)
        if pos < limit:
            pieces.append((name[pos:limit], False))
        if cut != name:
            pieces.append(("…", False))
        total = 0.0
        for text, hot in pieces:
            font = bold if hot else base
            self.canvas.create_text(x + total, cy, text=text, anchor="w", font=font,
                                    fill=_HIGHLIGHT if hot else gfx.PANEL_FG, tags=tag + ("name",))
            total += font.measure(text)
        return total

    def _show_passage(self, row: Row, x: float, y: float, width: float, height: float) -> None:
        from . import mdrender_tk
        if self._passage is None:
            self._passage = tk.Text(self.canvas, wrap="word", relief="flat", bd=0, highlightthickness=0,
                                    bg=gfx.INPUT_BG, fg=gfx.PANEL_FG, cursor="arrow", takefocus=0)
            mdrender_tk.configure_tags(self._passage, bg=gfx.INPUT_BG, size=max(8, round(10 * self.scale)))
        self.canvas.create_window(x, y, window=self._passage, anchor="nw", width=int(width), height=int(height),
                                  tags="passage")
        if self._passage_dirty:
            _mode, query, _ = self.effective()
            mdrender_tk.render(self._passage, row.passage, terms=search_modes.query_terms(query))
            self._passage.configure(state="disabled")
            self._passage_dirty = False

    def _hide_passage(self) -> None:
        self.canvas.delete("passage")

    # ---- diagnostics --------------------------------------------------

    def debug_state(self) -> dict:
        rect = None
        if self.state != "closed":
            self.win.update_idletasks()
            x, y = self.win.winfo_x(), self.win.winfo_y()
            rect = [x, y, x + self.win.winfo_width(), y + self.win.winfo_height()]
        try:
            mode, query, switched = self.effective()
            badge = f"{mode.label} · {'local' if getattr(mode, 'local', False) else 'online'}"
            mode_keyword = mode.keyword
        except ModeUnavailable:
            badge, mode_keyword, query, switched = "", "", "", False
        sel = self.selected_row()
        press_ms = None
        if self.press_ts is not None and self.shown_ts is not None:
            press_ms = round((self.shown_ts - self.press_ts) * 1000.0, 1)
        return {
            "open": self.state != "closed",
            "state": self.state,
            "rect": rect,
            "hwnd": self.hwnd,
            "exstyle": hex(win.get_exstyle(self.hwnd)) if self.hwnd and self.state != "closed" else None,
            "placement": self._placement(),
            "anim_done": self.anim_done,
            "mode": mode_keyword,
            "badge": badge,
            "prefix_switched": switched,
            "text": self.var.get(),
            "query": query,
            "result_count": len(self.rows),
            "total": self.result.total if self.result else 0,
            "selected": self.sel,
            "selected_name": sel.name if sel else None,
            "chips": sorted(self.chips),
            "modified": self.modified,
            "sort": self.sort,
            "header": self.header,
            "interpretation": self.result.interpretation if self.result else "",
            "details": self.details,
            "details_kind": self.details_kind,
            "menu_open": self.menu_open,
            "menu_items": list(self._menu_items) if self.menu_open else [],
            "folder_listing": self._listing_stack[-1]["folder"] if self._listing_stack else None,
            "last_action": self.last_action,
            "dialog": dict(self._dialog) if self._dialog else None,
            "answer_window": self.answer_window.snapshot() if self.answer_window is not None else None,
            "footer": self._footer(),
            "row_kind": sel.kind if sel else None,
            "passage_text": (self._passage.get("1.0", "end-1c") if self._passage is not None
                             and self._notes_view() else ""),
            "pending": self.pending,
            "error": self.error,
            "saved": [s.name for s in self._saved_cache],
            "saved_visible": self._saved_visible(),
            "press_to_box_ms": press_ms,
        }


def _demo_rows(text: str) -> list:
    now = time.time()
    samples = [
        ("design-notes.md", "~/docs/projects/alpha", 4210, 0), ("design_review.docx", "~/docs/reviews", 88211, 3),
        ("system-design.pdf", "~/docs/books", 2480113, 9), ("design.py", "~/code/alpha/src", 1822, 1),
        ("designer.json", "~/code/alpha/config", 640, 2), ("design-tokens.yml", "~/code/beta", 912, 14),
        ("Design", "~/docs/projects", None, 40), ("mock-design.png", "~/images/mocks", 301122, 5),
        ("budget-design.xlsx", "~/docs/finance", 51200, 21), ("redesign-plan.md", "~/docs/projects/beta", 7733, 6),
        ("design-system.md", "~/docs/projects/beta/docs", 15320, 11), ("ui-design.txt", "~/docs/misc", 233, 80),
    ]
    rows = []
    for name, folder, size, days in samples:
        if text and not any(t in name.lower() for t in search_modes.query_terms(text)):
            continue
        ext = "" if size is None else os.path.splitext(name)[1].lstrip(".")
        rows.append(Row(name=name, folder=folder, ext=ext, size=size, mtime=now - days * 86400,
                        is_dir=size is None, spans=search_modes.highlight_spans(name, text)))
    return rows


class _DemoMode:
    keyword = "files"
    label = "Files"
    local = True
    noun = "file(s)"
    prefix = ""

    def search(self, text: str) -> list:
        return _demo_rows(text)

    def run(self, text: str, chips=(), modified: str = "", sort: str = "") -> SearchResult:
        started = time.perf_counter()
        rows = [r for r in _demo_rows(text) if not chips or any(
            (c == "folders" and r.is_dir) or r.ext in _EXT_GROUPS.get(c, ()) for c in chips)]
        if modified:
            days = {"today": 1, "7d": 7, "30d": 30}[modified]
            rows = [r for r in rows if r.mtime and time.time() - r.mtime <= days * 86400]
        interp = f"names containing {' and '.join(search_modes.query_terms(text))}" if text else ""
        if chips:
            interp += f" · types: {', '.join(chips)}"
        return SearchResult(rows=rows, total=len(rows), elapsed_ms=(time.perf_counter() - started) * 1000 + 0.4,
                            interpretation=interp, built_at=time.time() - 3 * 3600)


class _DemoNotes:
    keyword = "?"
    label = "Ask notes"
    local = True
    message = None
    footer = "search over your notes; nothing left this PC"

    def search(self, text: str) -> list:
        from types import SimpleNamespace
        passage = f"## Weekly sync\n\nWe **discussed {text}** and agreed:\n\n- ship the popup\n- review `hotkey.py`\n"
        return [SimpleNamespace(path="~/notes/meetings/meeting-2026-11-02.md", title="Weekly sync", passage=passage,
                                score=1.0, highlights=[])]


def _demo(argv: list[str]) -> int:
    import json
    from . import icon as icon_mod
    root = icon_mod.create_root()
    uibus.start(root)
    icon = icon_mod.Icon(root, icon_mod.IconActions(quit=root.destroy))
    registry = search_modes.ModeRegistry()
    registry.register(_DemoMode())
    registry.register(_DemoNotes())
    popup = SearchPopup(root, icon, registry, saved=SavedSearches())
    icon.actions.search = popup.toggle
    if "--no-open" not in argv:
        root.after(200, popup.open)
    root.after(600, lambda: print(json.dumps(popup.debug_state(), indent=2, ensure_ascii=False), flush=True))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(_demo(sys.argv[1:]))
