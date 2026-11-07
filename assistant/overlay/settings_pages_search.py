"""Roots & excludes and Search pages for the settings window (AC-ST-2, AC-FI-7, AC-SH-2, AC-SH-8, SEC-3).

`install(window, ...)` adds both pages to an open `SettingsWindow` using its own widget helpers, so
`settings_ui.py` does not need to know about them.
"""
import logging
import os
import time
import tkinter as tk
from tkinter import filedialog
from typing import Any, Callable, Optional

from . import gfx

log = logging.getLogger("assistant.overlay.settings_pages_search")

ROOTS_PAGE = "Roots & excludes"
SEARCH_PAGE = "Search"
REFRESH_MS = 3000
HOTKEY_SETTLE_MS = 400
MAX_COUNT_ROWS = 60

PLACEMENT_CHOICES = (("icon", "Beside the icon"), ("mouse", "On the monitor with the mouse"),
                     ("focused", "On the monitor with the focused window"),
                     ("primary", "On the primary monitor"), ("last", "Where it was last"))
PROTECTED_WARNING = ("Lists files whose names match the secrets list (.env, *.pem, *.key, id_rsa and similar) "
                     "in local search, by name only. They are still never previewed, never returned over MCP "
                     "and never sent to Claude. Leave this off unless you need to find them.")
HOTKEY_HELP = ("Examples: ctrl+alt+space, ctrl+alt+f10, win+shift+s. Leave empty to turn the hotkey off. "
               "If another app already holds the combination, choose another one; the icon always opens search.")


def format_age(built_at: Optional[float], now: Optional[float] = None) -> str:
    if not built_at:
        return "never"
    seconds = max(0, int((now if now is not None else time.time()) - built_at))
    if seconds < 90:
        return f"{seconds} s ago"
    if seconds < 5400:
        return f"{seconds // 60} min ago"
    if seconds < 172800:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} days ago"


def format_index_summary(status: dict) -> str:
    entries = int(status.get("entries") or 0)
    dirs = status.get("dirs")
    parts = [f"{entries:,} entries" + (f" in {dirs:,} folders" if isinstance(dirs, int) else ""),
             f"built {format_age(status.get('built_at'))}"]
    if status.get("walking"):
        parts.append("scanning now")
    elif status.get("last_walk_s") is not None:
        parts.append(f"last scan {status['last_walk_s']:.1f} s")
    return " \u00b7 ".join(parts)


def format_root_line(root: dict) -> str:
    line = f"{root.get('path', '')}  \u2014  {root.get('state', 'pending')}, {int(root.get('entries') or 0):,} entries"
    if root.get("error"):
        line += f" ({root['error']})"
    return line


def format_counts(counts: dict, rules: list) -> str:
    """One line per rule, hits first; rules with no hits are listed after the others."""
    seen = {r: int(counts.get(r, 0)) for r in rules}
    for rule, n in counts.items():
        seen.setdefault(rule, int(n))
    ranked = sorted(seen.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    lines = [f"{n:>9,}  {rule}" for rule, n in ranked[:MAX_COUNT_ROWS]]
    if len(ranked) > MAX_COUNT_ROWS:
        lines.append(f"{'':>9}  ... and {len(ranked) - MAX_COUNT_ROWS} more rules")
    return "\n".join(lines)


class SearchPages:
    def __init__(self, window: Any, index_status: Callable[[], dict], request_rescan: Callable[..., None],
                 hotkey_info: Callable[[], dict], notes_info: Callable[[], dict]) -> None:
        self.w = window
        self.settings = window.settings
        self._index_status = index_status
        self._request_rescan = request_rescan
        self._hotkey_info = hotkey_info
        self._notes_info = notes_info
        self._shown_excludes: Optional[list] = None
        for name in (ROOTS_PAGE, SEARCH_PAGE):
            self._add_page(name)
        self._build_roots()
        self._build_search()
        self.refresh()
        window.win.after(REFRESH_MS, self._tick)

    def _add_page(self, name: str) -> None:
        w = self.w
        item = tk.Label(w._nav["About"].master, text=name, bg=gfx.BUTTON_BG, fg=gfx.PANEL_FG, font=w._font,
                        anchor="w", padx=int(16 * w.scale), pady=int(9 * w.scale), width=14, cursor="hand2")
        item.pack(fill="x", before=w._nav["About"])
        item.bind("<Button-1>", lambda _e, n=name: w.show(n))
        item.bind("<Button-1>", lambda _e: self.refresh(), add="+")
        w._nav[name] = item
        w.pages[name] = tk.Frame(w.body, bg=gfx.PANEL_BG)

    def _index(self) -> dict:
        raw = self.settings.get("index") or {}
        return dict(raw) if isinstance(raw, dict) else {}

    def _save_index(self, page: str, **changes: Any) -> bool:
        index = self._index()
        index.update(changes)
        return self.w._save(page, "index", index)

    def _listbox(self, parent: tk.Misc, height: int) -> tk.Listbox:
        w = self.w
        return tk.Listbox(parent, height=height, font=w._small, bg=gfx.INPUT_BG, fg=gfx.PANEL_FG,
                          selectbackground=gfx.PURPLE, relief="flat", highlightthickness=1,
                          highlightbackground=gfx.INPUT_BORDER, exportselection=False)

    def _textbox(self, parent: tk.Misc, height: int, editable: bool) -> tk.Text:
        w = self.w
        return tk.Text(parent, height=height, width=40, font=w._mono, bg=gfx.INPUT_BG, fg=gfx.PANEL_FG,
                       insertbackground=gfx.PANEL_FG, relief="flat", highlightthickness=1,
                       highlightbackground=gfx.INPUT_BORDER, highlightcolor=gfx.PURPLE, wrap="none",
                       undo=editable, state="normal" if editable else "disabled")

    def _build_roots(self) -> None:
        w = self.w
        page = w._p(ROOTS_PAGE, ROOTS_PAGE)
        w._text(page, "Folders to index. Changes apply right away; no restart.", pady=(4, 2))
        row = tk.Frame(page, bg=gfx.PANEL_BG)
        row.pack(fill="x")
        self.roots_list = self._listbox(row, 4)
        self.roots_list.pack(side="left", fill="x", expand=True)
        buttons = tk.Frame(row, bg=gfx.PANEL_BG)
        buttons.pack(side="left", padx=(8, 0), anchor="n")
        w._button(buttons, "Add folder\u2026", self.add_root_dialog).pack(fill="x")
        w._button(buttons, "Remove", self.remove_selected_root).pack(fill="x", pady=(4, 0))
        self.index_label = w._text(page, "", pady=(6, 2))
        rescan = tk.Frame(page, bg=gfx.PANEL_BG)
        rescan.pack(fill="x")
        w._button(rescan, "Rescan now", self.rescan_now).pack(side="left")
        w._text(page, "Exclude rules, one per line (a trailing / matches folders only). Applied on the next scan.",
                pady=(8, 2))
        self.excludes_text = self._textbox(page, 6, True)
        self.excludes_text.pack(fill="x")
        self.excludes_text.bind("<KeyRelease>", lambda _e: w._debounce("index_excludes", self.apply_excludes))
        self.excludes_text.bind("<FocusOut>", lambda _e: w.flush())
        w._text(page, "Entries each rule excluded in the last scan", pady=(8, 2))
        box = tk.Frame(page, bg=gfx.PANEL_BG)
        box.pack(fill="both", expand=True)
        self.counts_text = self._textbox(box, 5, False)
        scroll = tk.Scrollbar(box, command=self.counts_text.yview)
        self.counts_text.configure(yscrollcommand=scroll.set)
        self.counts_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        w._error_line(ROOTS_PAGE)

    def add_root_dialog(self) -> None:
        w = self.w
        w.win.attributes("-topmost", False)
        try:
            picked = filedialog.askdirectory(parent=w.win, title="Choose a folder to index", mustexist=True)
        finally:
            if w.win is not None:
                w.win.attributes("-topmost", True)
        if picked:
            self.add_root(picked)

    def add_root(self, path: str) -> bool:
        path = os.path.normpath(path)
        roots = list(self._index().get("roots", []))
        if any(os.path.normcase(r) == os.path.normcase(path) for r in roots):
            self.w._fail(ROOTS_PAGE, "that folder is already in the list")
            return False
        if not os.path.isdir(path):
            self.w._fail(ROOTS_PAGE, "that folder does not exist")
            return False
        roots.append(path)
        ok = self._save_index(ROOTS_PAGE, roots=roots)
        self.refresh()
        return ok

    def remove_selected_root(self) -> None:
        picked = self.roots_list.curselection()
        roots = list(self._index().get("roots", []))
        if not picked or picked[0] >= len(roots):
            self.w._fail(ROOTS_PAGE, "select a folder to remove")
            return
        del roots[picked[0]]
        self._save_index(ROOTS_PAGE, roots=roots)
        self.refresh()

    def rescan_now(self) -> None:
        self._request_rescan("manual")
        self.w._ok(ROOTS_PAGE, "Scan requested")
        self.w.win.after(HOTKEY_SETTLE_MS, self.refresh)

    def apply_excludes(self) -> None:
        raw = self.excludes_text.get("1.0", "end")
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if lines == list(self._index().get("excludes", [])):
            return
        if self._save_index(ROOTS_PAGE, excludes=lines):
            self._shown_excludes = lines

    def _build_search(self) -> None:
        w = self.w
        page = w._p(SEARCH_PAGE, SEARCH_PAGE)
        row = w._row(page, "Hotkey")
        self.hotkey_var = tk.StringVar(value=str(self.settings.get("hotkey", "") or ""))
        self.hotkey_entry = w._entry(row, self.hotkey_var, 18)
        self.hotkey_entry.pack(side="left")
        self.hotkey_var.trace_add("write", lambda *_: w._debounce("hotkey", self.apply_hotkey))
        self.hotkey_entry.bind("<Return>", lambda _e: w.flush())
        self.hotkey_status = tk.Label(row, text="", bg=gfx.PANEL_BG, fg=gfx.PANEL_DIM, font=w._small, anchor="w")
        self.hotkey_status.pack(side="left", padx=10)
        w._text(page, HOTKEY_HELP)
        self.fullscreen_var = w._check(
            page, "Ignore the hotkey while a full-screen app is in front",
            self.settings.get("hotkey_skip_fullscreen", False) is True,
            lambda v: w._save(SEARCH_PAGE, "hotkey_skip_fullscreen", v))
        w._text(page, "Where the search box appears", dim=False, pady=(8, 0))
        self.placement_var = tk.StringVar(value=self._placement())
        for value, label in PLACEMENT_CHOICES:
            tk.Radiobutton(page, text=label, value=value, variable=self.placement_var, bg=gfx.PANEL_BG,
                           fg=gfx.PANEL_FG, selectcolor=gfx.PANEL_BG, activebackground=gfx.PANEL_BG,
                           activeforeground=gfx.PANEL_FG, font=w._small, anchor="w", highlightthickness=0,
                           command=self.apply_placement).pack(fill="x")
        row = w._row(page, "Notes folder")
        self.notes_var = tk.StringVar(value=self._notes_dir_setting())
        self.notes_entry = w._entry(row, self.notes_var, 30)
        self.notes_entry.pack(side="left", fill="x", expand=True)
        self.notes_var.trace_add("write", lambda *_: w._debounce("notes_dir", self.apply_notes_dir))
        w._button(row, "Browse\u2026", self.browse_notes).pack(side="left", padx=(8, 0))
        self.notes_label = w._text(page, "")
        self.protected_var = w._check(
            page, "Show protected files by name in local search",
            self._index().get("show_protected_by_name", False) is True,
            lambda v: self._save_index(SEARCH_PAGE, show_protected_by_name=v))
        w._text(page, PROTECTED_WARNING)
        w._error_line(SEARCH_PAGE)

    def _placement(self) -> str:
        value = self.settings.get("search_placement", "icon")
        return value if value in dict(PLACEMENT_CHOICES) else "icon"

    def _notes_dir_setting(self) -> str:
        notes = self.settings.get("notes") or {}
        value = notes.get("dir", "") if isinstance(notes, dict) else ""
        return value if isinstance(value, str) else ""

    def apply_hotkey(self) -> None:
        text = self.hotkey_var.get().strip()
        if text == str(self.settings.get("hotkey", "") or ""):
            return
        if self.w._save(SEARCH_PAGE, "hotkey", text):
            self.w.win.after(HOTKEY_SETTLE_MS, self.refresh)

    def apply_placement(self) -> None:
        self.w._save(SEARCH_PAGE, "search_placement", self.placement_var.get())

    def apply_notes_dir(self) -> None:
        text = self.notes_var.get().strip()
        if text and not os.path.isabs(text):
            self.w._fail(SEARCH_PAGE, "the notes folder must be a full path such as C:\\Users\\name\\Notes")
            return
        notes = dict(self.settings.get("notes") or {})
        notes["dir"] = os.path.normpath(text) if text else ""
        if self.w._save(SEARCH_PAGE, "notes", notes):
            self.w.win.after(HOTKEY_SETTLE_MS, self.refresh)

    def browse_notes(self) -> None:
        w = self.w
        w.win.attributes("-topmost", False)
        try:
            picked = filedialog.askdirectory(parent=w.win, title="Choose your notes folder", mustexist=True)
        finally:
            if w.win is not None:
                w.win.attributes("-topmost", True)
        if picked:
            self.notes_var.set(os.path.normpath(picked))

    def refresh(self) -> None:
        if self.w.win is None:
            return
        try:
            self._refresh_roots()
            self._refresh_search()
        except tk.TclError:
            pass
        except Exception:
            log.exception("refreshing the search settings pages failed")

    def _refresh_roots(self) -> None:
        status = self._index_status()
        configured = list(self._index().get("roots", []))
        by_path = {os.path.normcase(r.get("path", "")): r for r in status.get("roots", [])}
        lines = []
        for path in configured:
            info = by_path.get(os.path.normcase(os.path.abspath(path)))
            lines.append(format_root_line(info) if info else f"{path}  \u2014  not scanned yet")
        if lines != list(self.roots_list.get(0, "end")):
            self.roots_list.delete(0, "end")
            for line in lines:
                self.roots_list.insert("end", line)
        self.index_label.configure(text=format_index_summary(status) if configured
                                   else "No folders yet. Add one to build the index.")
        excludes = list(self._index().get("excludes", []))
        if excludes != self._shown_excludes and self.w.win.focus_get() is not self.excludes_text:
            self.excludes_text.delete("1.0", "end")
            self.excludes_text.insert("1.0", "\n".join(excludes))
            self._shown_excludes = excludes
        self._set_readonly(self.counts_text, format_counts(status.get("excludes") or {}, excludes))

    @staticmethod
    def _set_readonly(widget: tk.Text, text: str) -> None:
        if widget.get("1.0", "end").rstrip("\n") == text:
            return
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _refresh_search(self) -> None:
        info = self._hotkey_info()
        status = str(info.get("status", ""))
        ok = status == "registered"
        shown = status if info.get("combo") else "disabled"
        self.hotkey_status.configure(text=shown, fg=gfx.PANEL_OK if ok else gfx.PANEL_ERR if info.get("combo")
                                     else gfx.PANEL_DIM)
        notes = self._notes_info()
        lines = []
        if os.environ.get("ASSISTANT_NOTES_DIR", "").strip():
            lines.append("ASSISTANT_NOTES_DIR is set and overrides this folder.")
        if not notes.get("dir"):
            lines.append("No notes folder set. Ask notes needs one.")
        else:
            if notes.get("status"):
                lines.append(str(notes["status"]))
            lines.append(f"{int(notes.get('skipped_placeholder') or 0)} placeholder note(s) skipped")
        self.notes_label.configure(text="\n".join(lines))

    def _tick(self) -> None:
        w = self.w
        if w.win is None:
            return
        if w.current in (ROOTS_PAGE, SEARCH_PAGE):
            self.refresh()
        w.win.after(REFRESH_MS, self._tick)


def install(window: Any, index_status: Callable[[], dict], request_rescan: Callable[..., None],
            hotkey_info: Callable[[], dict], notes_info: Callable[[], dict]) -> SearchPages:
    pages = SearchPages(window, index_status, request_rescan, hotkey_info, notes_info)
    window.search_pages = pages
    window._place()
    return pages
