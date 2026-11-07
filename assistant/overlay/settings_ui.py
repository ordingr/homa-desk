import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from .. import __version__, paths, uibus
from ..cards.model import strip_control
from . import gfx, monitors, win

log = logging.getLogger("assistant.overlay.settings_ui")

PAGES = ("General", "Cards", "Calendars", "Claude Code", "MCP", "About")
RESTART_NOTE = "takes effect after restart"
COVERAGE_SENTENCE = ("Assistant sees permission prompts and task completion; it cannot yet see "
                     "AskUserQuestion or plan-approval waits (hook coverage probe pending).")
MUTE_LABELS = (("hooks", "Claude Code hooks"), ("http", "HTTP API"), ("mcp", "MCP"),
               ("file", "File drop"), ("reminders", "Reminders"), ("calendar", "Calendar meetings"))
SAVE_DELAY_MS = 400
HOTKEY_SETTLE_MS = 400
STATUS_REFRESH_MS = 5000
MCP_LOG_VIEW_LINES = 100
MAX_LABEL_CHARS = 64
CONFIRM_RESET_MS = 4000
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ROOT_DIR = Path(__file__).resolve().parents[2]


def mask_url(url: str) -> str:
    try:
        host = urlparse(url.strip()).hostname
    except ValueError:
        host = None
    return f"{host}  (path hidden)" if host else "(unrecognised address)"


def format_calendar_status(entries: list) -> str:
    """One line per calendar: label, host, last good read, last error. Never the address path."""
    if not entries:
        return "No calendars are being read."
    lines = []
    for entry in entries:
        label = str(entry.get("label") or "calendar")
        host = entry.get("host")
        ok = entry.get("last_ok")
        parts = [label]
        if host and host != label:
            parts.append(str(host))
        parts.append(f"last ok {time.strftime('%H:%M', time.localtime(ok))}" if ok else "not read yet")
        if entry.get("last_error"):
            parts.append(f"error: {entry['last_error']}")
        lines.append("  \u00b7  ".join(parts))
    return "\n".join(lines)


def outlook_classic_present() -> bool:
    """Registry check only: never starts Outlook."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Outlook.Application\CLSID"):
            return True
    except (OSError, ImportError):
        return False


def hooks_status() -> str:
    from .. import hooks_installer
    try:
        return hooks_installer.installed()
    except Exception as exc:
        return f"unknown ({type(exc).__name__})"


def run_assistant_cli(*args: str) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, "-m", "assistant", *args], capture_output=True, text=True,
                          timeout=60, creationflags=_NO_WINDOW, stdin=subprocess.DEVNULL)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def mcp_add_command(port: int, token: str) -> str:
    return (f'claude mcp add --transport http assistant http://127.0.0.1:{port}/mcp '
            f'--header "Authorization: Bearer {token}"')


def _python_command() -> str:
    exe = Path(sys.executable or "python")
    console = exe.with_name("python.exe")
    if exe.name.lower() == "pythonw.exe" and console.is_file():
        return str(console)
    return str(exe)


def desktop_snippet() -> str:
    server: dict[str, Any] = {"command": _python_command(), "args": ["-m", "assistant.mcp_stdio"]}
    if os.environ.get(paths.ENV_VAR):
        server["env"] = {paths.ENV_VAR: str(paths.home())}
    return json.dumps({"mcpServers": {"assistant": server}}, indent=2)


class SettingsWindow:
    """Settings window: six pages, every change saved on the spot (AC-ST-2, AC-ST-3)."""

    def __init__(self, root: tk.Misc, settings, token_provider: Callable[[], str], port: int,
                 scale: float = 1.0, runner: Callable[..., tuple[int, str]] = run_assistant_cli,
                 calendar_status: Optional[Callable[[], list]] = None) -> None:
        self.settings = settings
        self._token_provider = token_provider
        self._token_override: Optional[str] = None
        self.port = port
        self.scale = scale
        self._runner = runner
        self._calendar_status = calendar_status
        self._regen_thread: Optional[threading.Thread] = None
        self._pending: dict[str, tuple[str, Callable[[], None]]] = {}
        self._confirm_after: Optional[str] = None
        self.current = ""
        self.pages: dict[str, tk.Frame] = {}
        self._nav: dict[str, tk.Label] = {}
        self._errors: dict[str, tk.Label] = {}
        self._port_error = ""
        self._ics_masked: list[str] = []

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.title("Assistant settings")
        self.win.configure(bg=gfx.PANEL_BG)
        self.win.attributes("-topmost", True)
        self.win.minsize(int(720 * scale), int(460 * scale))

        self._font = ("Segoe UI", -int(13 * scale))
        self._small = ("Segoe UI", -int(12 * scale))
        self._mono = ("Consolas", -int(12 * scale))
        self._head = ("Segoe UI", -int(18 * scale), "bold")

        side = tk.Frame(self.win, bg=gfx.BUTTON_BG)
        side.pack(side="left", fill="y")
        self.body = tk.Frame(self.win, bg=gfx.PANEL_BG, padx=int(18 * scale), pady=int(14 * scale))
        self.body.pack(side="left", fill="both", expand=True)
        for name in PAGES:
            item = tk.Label(side, text=name, bg=gfx.BUTTON_BG, fg=gfx.PANEL_FG, font=self._font, anchor="w",
                            padx=int(16 * scale), pady=int(9 * scale), width=14, cursor="hand2")
            item.pack(fill="x")
            item.bind("<Button-1>", lambda _e, n=name: self.show(n))
            self._nav[name] = item
            self.pages[name] = tk.Frame(self.body, bg=gfx.PANEL_BG)

        self._build_general()
        self._build_cards()
        self._build_calendars()
        self._build_claude_code()
        self._build_mcp()
        self._build_about()

        self.win.bind("<Escape>", lambda _e: self.close())
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.show("General")
        self._place()
        self.win.deiconify()
        win.apply_dialog_styles(self.win)
        self.win.lift()
        self.win.focus_force()
        self.win.after(STATUS_REFRESH_MS, self._status_tick)

    def _place(self) -> None:
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        self._refresh_mcp()
        tallest = 0
        for frame in self.pages.values():
            frame.pack(fill="both", expand=True)
            self.win.update_idletasks()
            tallest = max(tallest, frame.winfo_reqheight())
            frame.pack_forget()
        self.pages[self.current].pack(fill="both", expand=True)
        h = max(h, tallest + int(28 * self.scale))
        mon = monitors.primary_monitor()
        x = mon.work[0] + (mon.work[2] - mon.work[0] - w) // 2
        y = mon.work[1] + (mon.work[3] - mon.work[1] - h) // 3
        self.win.geometry(f"{w}x{h}+{max(mon.work[0], x)}+{max(mon.work[1], y)}")

    def show(self, name: str) -> None:
        if self._alive():
            self.flush()
        for page, frame in self.pages.items():
            frame.pack_forget()
            self._nav[page].configure(bg=gfx.BUTTON_BG_HOVER if page == name else gfx.BUTTON_BG)
        self.pages[name].pack(fill="both", expand=True)
        self.current = name
        if name == "General":
            self.sound_var.set(bool(self.settings.get("sound_on", True)))
            self._refresh_hotkey()
        elif name == "Calendars":
            self._refresh_status()
        elif name == "Claude Code":
            self._refresh_hooks()
        elif name == "MCP":
            self._refresh_mcp()
            self._refresh_mcp_log()

    def _alive(self) -> bool:
        return self.win is not None

    def _p(self, name: str, title: str) -> tk.Frame:
        frame = self.pages[name]
        tk.Label(frame, text=title, bg=gfx.PANEL_BG, fg=gfx.PANEL_FG, font=self._head, anchor="w").pack(fill="x")
        return frame

    def _text(self, parent, text: str, dim: bool = True, mono: bool = False, pady=(2, 2)) -> tk.Label:
        label = tk.Label(parent, text=text, bg=gfx.PANEL_BG, fg=gfx.PANEL_DIM if dim else gfx.PANEL_FG,
                         font=self._mono if mono else self._small, anchor="w", justify="left",
                         wraplength=int(480 * self.scale))
        label.pack(fill="x", pady=pady)
        return label

    def _check(self, parent, text: str, value: bool, command: Callable[[bool], None],
               disabled: bool = False) -> tk.BooleanVar:
        var = tk.BooleanVar(value=value)
        box = tk.Checkbutton(parent, text=text, variable=var, bg=gfx.PANEL_BG, fg=gfx.PANEL_FG,
                             selectcolor=gfx.PANEL_BG, activebackground=gfx.PANEL_BG,
                             activeforeground=gfx.PANEL_FG, font=self._font, anchor="w",
                             highlightthickness=0, command=lambda: command(bool(var.get())),
                             state="disabled" if disabled else "normal")
        box.pack(fill="x", pady=2)
        return var

    def _entry(self, parent, var: tk.StringVar, width: int = 12, disabled: bool = False) -> tk.Entry:
        return tk.Entry(parent, textvariable=var, width=width, font=self._font, bg=gfx.INPUT_BG,
                        fg=gfx.PANEL_FG, insertbackground=gfx.PANEL_FG, relief="flat",
                        disabledbackground=gfx.BUTTON_BG, disabledforeground=gfx.PANEL_DIM,
                        highlightthickness=1, highlightbackground=gfx.INPUT_BORDER,
                        highlightcolor=gfx.PURPLE, state="disabled" if disabled else "normal")

    def _button(self, parent, text: str, command: Callable[[], None]) -> tk.Button:
        return tk.Button(parent, text=text, command=command, bg=gfx.BUTTON_BG, fg=gfx.PANEL_FG,
                         activebackground=gfx.BUTTON_BG_HOVER, activeforeground=gfx.PANEL_FG,
                         relief="flat", font=self._small, padx=int(10 * self.scale), pady=int(3 * self.scale))

    def _row(self, parent, label: str) -> tk.Frame:
        row = tk.Frame(parent, bg=gfx.PANEL_BG)
        row.pack(fill="x", pady=(6, 2))
        tk.Label(row, text=label, bg=gfx.PANEL_BG, fg=gfx.PANEL_FG, font=self._font, width=16,
                 anchor="w").pack(side="left")
        return row

    def _error_line(self, page: str) -> tk.Label:
        label = tk.Label(self.pages[page], text="", bg=gfx.PANEL_BG, fg=gfx.PANEL_ERR, font=self._small,
                         anchor="w", justify="left", wraplength=int(480 * self.scale))
        label.pack(fill="x", pady=(6, 0), side="bottom")
        self._errors[page] = label
        return label

    def _fail(self, page: str, message: str) -> None:
        self._errors[page].configure(text=message, fg=gfx.PANEL_ERR)

    def _ok(self, page: str, message: str = "") -> None:
        self._errors[page].configure(text=message, fg=gfx.PANEL_OK)

    def _save(self, page: str, key: str, value: Any) -> bool:
        try:
            self.settings.set(key, value)
        except (ValueError, OSError) as exc:
            self._fail(page, str(exc))
            return False
        self._ok(page, "Saved")
        return True

    def _debounce(self, token: str, fn: Callable[[], None]) -> None:
        old = self._pending.get(token)
        if old:
            self.win.after_cancel(old[0])
        self._pending[token] = (self.win.after(SAVE_DELAY_MS, lambda: self._run_pending(token)), fn)

    def _run_pending(self, token: str) -> None:
        entry = self._pending.pop(token, None)
        if entry:
            entry[1]()

    def flush(self) -> None:
        for token in list(self._pending):
            after_id, fn = self._pending.pop(token)
            try:
                self.win.after_cancel(after_id)
            except tk.TclError:
                pass
            fn()

    def _general_apply_port(self) -> None:
        text = self.port_var.get().strip()
        try:
            value = int(text)
        except ValueError:
            self._port_error = "port must be a whole number from 1024 to 65535"
            self._fail("General", self._port_error)
            return
        if self._save("General", "port", value):
            self._port_error = ""
            self._ok("General", f"Saved. Port {value} {RESTART_NOTE}.")
            self._offer_hooks_reinstall(value)
        else:
            self._port_error = self._errors["General"].cget("text")

    def _general_apply_hotkey(self) -> None:
        text = self.hotkey_var.get().strip()
        if text == str(self.settings.get("hotkey", "") or ""):
            return
        if not self._save("General", "hotkey", text):
            return
        pages = getattr(self, "search_pages", None)
        if pages is not None and pages.hotkey_var.get() != text:
            pages.hotkey_var.set(text)
        self.win.after(HOTKEY_SETTLE_MS, self._refresh_hotkey)

    def _refresh_hotkey(self) -> None:
        if self.win is None:
            return
        current = str(self.settings.get("hotkey", "") or "")
        if self.hotkey_var.get().strip() != current and self.win.focus_get() is not self.hotkey_entry                 and "hotkey" not in self._pending:
            self.hotkey_var.set(current)
        pages = getattr(self, "search_pages", None)
        if pages is None:
            return
        try:
            info = pages._hotkey_info()
        except Exception:
            return
        status = str(info.get("status", ""))
        ok = status == "registered"
        self.hotkey_note.configure(
            text=status if info.get("combo") else "disabled",
            fg=gfx.PANEL_OK if ok else gfx.PANEL_ERR if info.get("combo") else gfx.PANEL_DIM)

    def _offer_hooks_reinstall(self, value: int) -> None:
        if value == self.port:
            self.hooks_offer.pack_forget()
            return
        self.hooks_offer_text.configure(
            text=f"Claude Code hooks send to port {self.port}. Re-install them for port {value}, then restart "
                 "Assistant; until both are done, hook cards do not arrive.")
        self.hooks_offer_button.configure(text=f"Re-install Claude Code hooks for port {value}")
        self.hooks_offer.pack(fill="x", pady=(4, 0), after=self.port_row)

    def _reinstall_hooks(self) -> None:
        self.flush()
        self.hooks_offer_button.configure(state="disabled")
        self._hooks_cli("install", then=self._reinstall_done)

    def _reinstall_done(self) -> None:
        self.hooks_offer_button.configure(state="normal")
        if getattr(self, "hooks_exit", 1) == 0:
            self._ok("General", "Claude Code hooks re-installed for the new port. Restart Assistant to use it.")
        else:
            self._fail("General", "Re-installing the hooks failed; see the Claude Code page.")

    def _build_general(self) -> None:
        page = self._p("General", "General")
        row = self._row(page, "Port")
        self.port_row = row
        self.port_var = tk.StringVar(value=str(self.settings.get("port", self.port)))
        self.port_entry = self._entry(row, self.port_var, 8)
        self.port_entry.pack(side="left")
        self.port_entry.bind("<Return>", lambda _e: self._general_apply_port())
        self.port_entry.bind("<FocusOut>", lambda _e: self._general_apply_port())
        self.port_var.trace_add("write", lambda *_: self._debounce("port", self._general_apply_port))
        self.port_note = tk.Label(row, text=f"{RESTART_NOTE} (running on {self.port})", bg=gfx.PANEL_BG,
                                  fg=gfx.PANEL_DIM, font=self._small)
        self.port_note.pack(side="left", padx=10)
        self.hooks_offer = tk.Frame(page, bg=gfx.PANEL_BG)
        self.hooks_offer_text = self._text(self.hooks_offer, "")
        self.hooks_offer_button = self._button(self.hooks_offer, "", self._reinstall_hooks)
        self.hooks_offer_button.pack(anchor="w", pady=(2, 4))
        row = self._row(page, "Hotkey")
        self.hotkey_var = tk.StringVar(value=str(self.settings.get("hotkey", "") or ""))
        self.hotkey_entry = self._entry(row, self.hotkey_var, 16)
        self.hotkey_entry.pack(side="left")
        self.hotkey_var.trace_add("write", lambda *_: self._debounce("hotkey", self._general_apply_hotkey))
        self.hotkey_entry.bind("<Return>", lambda _e: self.flush())
        self.hotkey_note = tk.Label(row, text="", bg=gfx.PANEL_BG, fg=gfx.PANEL_DIM, font=self._small)
        self.hotkey_note.pack(side="left", padx=10)
        self.sound_var = self._check(page, "Play sounds for new cards", bool(self.settings.get("sound_on", True)),
                                     lambda v: self._save("General", "sound_on", v))
        row = self._row(page, "Theme")
        tk.Label(row, text="Dark (more themes later)", bg=gfx.PANEL_BG, fg=gfx.PANEL_DIM,
                 font=self._small).pack(side="left")
        self._error_line("General")

    def _build_cards(self) -> None:
        page = self._p("Cards", "Cards")
        self.every_monitor_var = self._check(
            page, "Show cards on every monitor", bool(self.settings.get("cards_every_monitor", True)),
            lambda v: self._save("Cards", "cards_every_monitor", v))
        tk.Label(page, text="Mute a source (its cards are dropped, nothing is shown)", bg=gfx.PANEL_BG,
                 fg=gfx.PANEL_DIM, font=self._small, anchor="w").pack(fill="x", pady=(14, 2))
        muted = self.settings.get("mute_sources", {})
        self.mute_vars: dict[str, tk.BooleanVar] = {}
        for source, label in MUTE_LABELS:
            self.mute_vars[source] = self._check(
                page, f"Mute {label}  ({source})", bool(muted.get(source, False)),
                lambda v, s=source: self._set_mute(s, v))
        self._error_line("Cards")

    def _set_mute(self, source: str, value: bool) -> None:
        current = self.settings.get("mute_sources", {})
        current[source] = value
        self._save("Cards", "mute_sources", current)

    def _calendars(self) -> dict:
        return self.settings.get("calendars", {})

    def _set_calendar(self, **changes: Any) -> bool:
        cal = self._calendars()
        cal.update(changes)
        return self._save("Calendars", "calendars", cal)

    def _build_calendars(self) -> None:
        page = self._p("Calendars", "Calendars")
        self._text(page, "ICS feeds (addresses are hidden after you add them)", pady=(8, 2))
        listbox_row = tk.Frame(page, bg=gfx.PANEL_BG)
        listbox_row.pack(fill="x")
        self.ics_list = tk.Listbox(listbox_row, height=4, font=self._small, bg=gfx.INPUT_BG, fg=gfx.PANEL_FG,
                                   selectbackground=gfx.PURPLE, relief="flat", highlightthickness=1,
                                   highlightbackground=gfx.INPUT_BORDER, exportselection=False)
        self.ics_list.pack(side="left", fill="x", expand=True)
        self._button(listbox_row, "Remove", self._remove_ics).pack(side="left", padx=(8, 0), anchor="n")
        add_row = tk.Frame(page, bg=gfx.PANEL_BG)
        add_row.pack(fill="x", pady=(6, 0))
        self.ics_var = tk.StringVar()
        self.ics_entry = self._entry(add_row, self.ics_var, 40)
        self.ics_entry.pack(side="left", fill="x", expand=True, ipady=2)
        self.ics_entry.bind("<Return>", lambda _e: self._add_ics())
        self._button(add_row, "Add", self._add_ics).pack(side="left", padx=(8, 0))
        label_row = tk.Frame(page, bg=gfx.PANEL_BG)
        label_row.pack(fill="x", pady=(6, 0))
        self.label_var = tk.StringVar()
        self.label_entry = self._entry(label_row, self.label_var, 24)
        self.label_entry.pack(side="left", ipady=2)
        self._button(label_row, "Set label for selected feed", self._set_label).pack(side="left", padx=(8, 0))
        self.ics_list.bind("<<ListboxSelect>>", lambda _e: self._load_label())
        self.status_label = self._text(page, "", mono=True, pady=(8, 2))
        self._refresh_ics()

        cal = self._calendars()
        self.outlook_present = outlook_classic_present()
        self.outlook_var = self._check(page, "Read meetings from classic Outlook",
                                       bool(cal.get("outlook", False)),
                                       lambda v: self._set_calendar(outlook=v))
        self.outlook_note = self._text(
            page, f"classic Outlook detected: {'yes' if self.outlook_present else 'no'}")
        self.lead_var = tk.StringVar(value=str(cal.get("lead_minutes", 5)))
        self.poll_var = tk.StringVar(value=str(cal.get("poll_minutes", 5)))
        for label, var, token, low, high in (("Lead time (min)", self.lead_var, "lead_minutes", 0, 120),
                                             ("Check every (min)", self.poll_var, "poll_minutes", 1, 60)):
            row = self._row(page, label)
            self._entry(row, var, 5).pack(side="left")
            tk.Label(row, text=f"{low}–{high}", bg=gfx.PANEL_BG, fg=gfx.PANEL_DIM,
                     font=self._small).pack(side="left", padx=10)
            var.trace_add("write", lambda *_a, v=var, t=token, lo=low, hi=high:
                          self._debounce(t, lambda: self._apply_minutes(v, t, lo, hi)))
        self._error_line("Calendars")

    def _apply_minutes(self, var: tk.StringVar, key: str, low: int, high: int) -> None:
        try:
            value = int(var.get().strip())
            if not low <= value <= high:
                raise ValueError
        except ValueError:
            self._fail("Calendars", f"{key.replace('_', ' ')} must be a whole number from {low} to {high}")
            return
        self._set_calendar(**{key: value})

    def _refresh_ics(self) -> None:
        cal = self._calendars()
        urls, labels = cal.get("ics_urls", []), cal.get("labels", {})
        self._ics_masked = [f"{labels[u]}  \u00b7  {mask_url(u)}" if labels.get(u) else mask_url(u) for u in urls]
        self.ics_list.delete(0, "end")
        for line in self._ics_masked:
            self.ics_list.insert("end", line)

    def _add_ics(self) -> None:
        url = self.ics_var.get().strip()
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https", "webcal") or not parsed.hostname:
            self._fail("Calendars", "enter a full address starting with https:// (or webcal://)")
            return
        urls = list(self._calendars().get("ics_urls", []))
        if url in urls:
            self._fail("Calendars", "that feed is already in the list")
            return
        urls.append(url)
        if self._set_calendar(ics_urls=urls):
            self.ics_var.set("")
            self._refresh_ics()

    def _remove_ics(self) -> None:
        picked = self.ics_list.curselection()
        if not picked:
            self._fail("Calendars", "select a feed to remove")
            return
        urls = list(self._calendars().get("ics_urls", []))
        labels = dict(self._calendars().get("labels", {}))
        labels.pop(urls[picked[0]], None)
        del urls[picked[0]]
        if self._set_calendar(ics_urls=urls, labels=labels):
            self._refresh_ics()

    def _load_label(self) -> None:
        picked = self.ics_list.curselection()
        urls = self._calendars().get("ics_urls", [])
        if picked and picked[0] < len(urls):
            self.label_var.set(self._calendars().get("labels", {}).get(urls[picked[0]], ""))

    def _set_label(self) -> None:
        picked = self.ics_list.curselection()
        if not picked:
            self._fail("Calendars", "select a feed to label")
            return
        urls = self._calendars().get("ics_urls", [])
        labels = dict(self._calendars().get("labels", {}))
        text = strip_control(self.label_var.get()).strip()[:MAX_LABEL_CHARS]
        if text:
            labels[urls[picked[0]]] = text
        else:
            labels.pop(urls[picked[0]], None)
        if self._set_calendar(labels=labels):
            self._refresh_ics()
            self.ics_list.selection_set(picked[0])

    def _refresh_status(self) -> None:
        try:
            entries = self._calendar_status() if self._calendar_status else []
        except Exception:
            entries = []
        self.status_label.configure(text=format_calendar_status(entries))

    def _status_tick(self) -> None:
        if self.win is None:
            return
        if self.current == "Calendars":
            self._refresh_status()
        elif self.current == "General":
            self._refresh_hotkey()
        self.win.after(STATUS_REFRESH_MS, self._status_tick)

    def _build_claude_code(self) -> None:
        page = self._p("Claude Code", "Claude Code")
        self.hooks_label = self._text(page, "", dim=False, pady=(8, 2))
        self._text(page, COVERAGE_SENTENCE, pady=(6, 10))
        row = tk.Frame(page, bg=gfx.PANEL_BG)
        row.pack(fill="x")
        self.install_button = self._button(row, "Install hooks", lambda: self._hooks_cli("install"))
        self.install_button.pack(side="left")
        self.remove_button = self._button(row, "Remove hooks", lambda: self._hooks_cli("remove"))
        self.remove_button.pack(side="left", padx=8)
        self.hooks_output = self._text(page, "", mono=True, pady=(10, 0))
        self._error_line("Claude Code")

    def _refresh_hooks(self) -> None:
        self.hooks_label.configure(text=f"Hooks installed: {hooks_status()}")

    def _hooks_cli(self, action: str, then: Optional[Callable[[], None]] = None) -> None:
        self.hooks_output.configure(text=f"running: python -m assistant hooks {action} …")

        def work() -> None:
            try:
                code, output = self._runner("hooks", action)
            except Exception as exc:
                code, output = 1, f"could not run: {type(exc).__name__}"
            uibus.post(self._hooks_done, action, code, output, then)

        threading.Thread(target=work, name="settings-hooks", daemon=True).start()

    def _hooks_done(self, action: str, code: int, output: str, then) -> None:
        if not self._alive():
            return
        self.hooks_output.configure(text=output[-600:] or f"hooks {action}: exit {code}")
        self.hooks_exit = code
        self._refresh_hooks()
        if then:
            then()

    def _token(self) -> str:
        if self._token_override:
            return self._token_override
        try:
            return self._token_provider() or ""
        except Exception:
            return ""

    def _build_mcp(self) -> None:
        page = self._p("MCP", "MCP")
        self._text(page, "Claude Code (the token is hidden on screen; Copy puts the real one on the clipboard)",
                   pady=(8, 2))
        self.mcp_command_label = self._text(page, "", dim=False, mono=True)
        self._button(page, "Copy command", self._copy_command).pack(anchor="w", pady=(2, 8))
        self._text(page, "Claude Desktop (claude_desktop_config.json)", pady=(6, 2))
        self.mcp_json_label = self._text(page, "", dim=False, mono=True)
        self._button(page, "Copy JSON", self._copy_json).pack(anchor="w", pady=(2, 8))
        self.regen_button = self._button(page, "Regenerate token", self._regenerate_clicked)
        self.regen_button.pack(anchor="w", pady=(8, 0))
        self._text(page, "Clients using the old token (hooks, MCP) stop working until updated; "
                         "hooks are reinstalled for you.")
        head = tk.Frame(page, bg=gfx.PANEL_BG)
        head.pack(fill="x", pady=(10, 2))
        tk.Label(head, text="MCP log (latest calls, no content)", bg=gfx.PANEL_BG, fg=gfx.PANEL_DIM,
                 font=self._small, anchor="w").pack(side="left")
        self._button(head, "Refresh", self._refresh_mcp_log).pack(side="left", padx=8)
        self.mcp_log_text = tk.Text(page, height=6, wrap="char", font=self._mono, bg=gfx.INPUT_BG,
                                    fg=gfx.PANEL_FG, relief="flat", highlightthickness=1,
                                    highlightbackground=gfx.INPUT_BORDER, state="disabled")
        self.mcp_log_text.pack(fill="x")
        self._error_line("MCP")

    def mcp_command_text(self, masked: bool = True) -> str:
        return mcp_add_command(self.port, "<token hidden>" if masked else self._token())

    def _refresh_mcp(self) -> None:
        self.mcp_command_label.configure(text=self.mcp_command_text(masked=True))
        self.mcp_json_label.configure(text=desktop_snippet())

    def _refresh_mcp_log(self) -> None:
        from ..server.routes_health import mcp_log_tail
        lines = mcp_log_tail(MCP_LOG_VIEW_LINES)
        self.mcp_log_text.configure(state="normal")
        self.mcp_log_text.delete("1.0", "end")
        self.mcp_log_text.insert("1.0", "\n".join(lines) if lines else "(no MCP calls logged yet)")
        self.mcp_log_text.configure(state="disabled")
        self.mcp_log_text.see("end")

    def _clip(self, text: str) -> None:
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        self.win.update()

    def _copy_command(self) -> None:
        if not self._token():
            self._fail("MCP", "no token available yet; start Assistant first")
            return
        self._clip(self.mcp_command_text(masked=False))
        self._ok("MCP", "Copied (contains the real token)")

    def _copy_json(self) -> None:
        self._clip(desktop_snippet())
        self._ok("MCP", "Copied")

    def _regenerate_clicked(self) -> None:
        if self._confirm_after is None:
            self.regen_button.configure(text="Click again to confirm")
            self._confirm_after = self.win.after(CONFIRM_RESET_MS, self._reset_regen_button)
            return
        self._reset_regen_button()
        self.regenerate_token()

    def _reset_regen_button(self) -> None:
        if self._confirm_after is not None:
            try:
                self.win.after_cancel(self._confirm_after)
            except tk.TclError:
                pass
        self._confirm_after = None
        if self._alive():
            self.regen_button.configure(text="Regenerate token", state="normal")

    def regenerate_token(self) -> None:
        if self._regen_thread is not None and self._regen_thread.is_alive():
            return
        from ..security import token as token_mod
        self.regen_button.configure(text="Regenerating…", state="disabled")
        self._ok("MCP", "Creating a new token…")

        def work() -> None:
            try:
                result, exc = token_mod.regenerate(), None
            except Exception as err:
                result, exc = None, err
            uibus.post(self._regenerate_done, result, exc)

        self._regen_thread = threading.Thread(target=work, name="settings-regenerate", daemon=True)
        self._regen_thread.start()

    def _regenerate_done(self, result, exc) -> None:
        if not self._alive():
            return
        self._reset_regen_button()
        if exc is not None:
            self._fail("MCP", f"could not regenerate the token ({type(exc).__name__})")
            return
        self._token_override = getattr(result, "token", result)
        hooks = getattr(result, "hooks", "not installed")
        self._refresh_mcp()
        self._refresh_hooks()
        if hooks.startswith("failed"):
            self._fail("MCP", f"New token created, but the Claude Code hooks were not updated ({hooks}). "
                              "Use Install hooks on the Claude Code page.")
        else:
            self._ok("MCP", "New token created; Claude Code hooks "
                            + ("updated." if hooks == "updated" else "are not installed, nothing to update."))

    def _build_about(self) -> None:
        page = self._p("About", "About")
        self._text(page, f"Version {__version__}", dim=False, pady=(8, 2))
        self.home_label = self._text(page, f"Home folder: {paths.home()}", dim=False)
        self.log_label = self._text(page, f"Log folder: {paths.logs_dir()}", dim=False)
        row = tk.Frame(page, bg=gfx.PANEL_BG)
        row.pack(fill="x", pady=(10, 0))
        self._button(row, "Open home folder", lambda: self._open(paths.home())).pack(side="left")
        self._button(row, "Open logs", lambda: self._open(paths.logs_dir())).pack(side="left", padx=8)
        row = tk.Frame(page, bg=gfx.PANEL_BG)
        row.pack(fill="x", pady=(10, 0))
        self._button(row, "README", lambda: self._open(_ROOT_DIR / "README.md")).pack(side="left")
        self._button(row, "API reference (api.md)", lambda: self._open(_ROOT_DIR / "docs" / "api.md")
                     ).pack(side="left", padx=8)
        self._error_line("About")

    def _open(self, target: Path) -> None:
        if not target.exists():
            self._fail("About", f"{target.name} was not found")
            return
        try:
            os.startfile(str(target))
        except OSError as exc:
            self._fail("About", f"could not open {target.name} ({type(exc).__name__})")

    def debug_state(self) -> dict:
        def texts(frame: tk.Misc) -> list[str]:
            out = []
            for child in frame.winfo_children():
                if isinstance(child, (tk.Label, tk.Button, tk.Checkbutton)):
                    out.append(str(child.cget("text")))
                out.extend(texts(child))
            return out
        return {
            "pages": list(PAGES), "current": self.current,
            "texts": {name: [t for t in texts(frame) if t] for name, frame in self.pages.items()},
            "port_field": self.port_var.get(), "hotkey_state": str(self.hotkey_entry.cget("state")),
            "ics_list": list(self.ics_list.get(0, "end")),
            "mcp_command": self.mcp_command_label.cget("text"),
            "mcp_json": self.mcp_json_label.cget("text"),
            "mcp_log": self.mcp_log_text.get("1.0", "end").rstrip("\n"),
            "calendar_status": self.status_label.cget("text"),
            "hooks_status": self.hooks_label.cget("text"),
            "exstyle": win.describe_exstyle(win.get_exstyle(win.hwnd_of(self.win))),
        }

    def close(self) -> None:
        if self.win is not None:
            try:
                self.flush()
                self._reset_regen_button()
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None

    @property
    def open(self) -> bool:
        return self.win is not None


def _demo(argv: list[str]) -> int:
    from .. import settings as settings_mod
    from ..security import token as token_mod
    from . import icon
    if "--home" in argv:
        os.environ[paths.ENV_VAR] = argv[argv.index("--home") + 1]
    elif not os.environ.get(paths.ENV_VAR):
        os.environ[paths.ENV_VAR] = tempfile.mkdtemp(prefix="assistant-settings-")
    paths.ensure_layout()
    store = settings_mod.Settings()
    store.load()
    token_mod.ensure()
    root = icon.create_root()
    uibus.start(root)
    store.on_change("cards_every_monitor", lambda k, v: print(f"on_change {k}={v}", flush=True))
    store.on_change("calendars", lambda k, v: print("on_change calendars", flush=True))
    window = SettingsWindow(root, store, token_mod.read, store.port)
    if "--page" in argv:
        window.show(argv[argv.index("--page") + 1])
    print(f"home: {paths.home()}", flush=True)
    print(json.dumps(window.debug_state(), indent=2, ensure_ascii=False), flush=True)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    window.win.protocol("WM_DELETE_WINDOW", lambda: (window.close(), root.destroy()))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(_demo(sys.argv[1:]))
