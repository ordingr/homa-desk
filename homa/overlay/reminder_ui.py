import tkinter as tk
from datetime import datetime
from typing import Callable, Optional

from . import gfx, monitors, win

DIDNT_UNDERSTAND = "didn't understand — try 'text in 10m' or 'text at 3pm'"
PARSER_MISSING = "reminder parser not available"


def _default_parse():
    try:
        from ..reminders import grammar
    except ImportError:
        return None, Exception
    return grammar.parse, getattr(grammar, "DidntUnderstand", Exception)


class ReminderInput:
    """One-line 'Remind me…' dialog with a live interpretation line (AC-RM-1, AC-RM-3, AC-RM-4)."""

    def __init__(self, root: tk.Misc, on_submit: Callable[[object, bool], None],
                 anchor: Optional[tuple[int, int]] = None, scale: float = 1.0,
                 parse: Optional[Callable] = None, error_type: type = Exception,
                 clock: Callable[[], datetime] = datetime.now) -> None:
        if parse is None:
            parse, error_type = _default_parse()
        self._parse = parse
        self._error_type = error_type
        self._clock = clock
        self._on_submit = on_submit
        self._parsed = None
        self.interpretation = ""
        self.error = ""

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.title("Remind me")
        self.win.configure(bg=gfx.PANEL_BG, padx=int(14 * scale), pady=int(12 * scale))
        self.win.attributes("-topmost", True)
        self.win.resizable(False, False)

        self.var = tk.StringVar()
        self.sticky = tk.BooleanVar(value=False)
        font = ("Segoe UI", -int(15 * scale))
        small = ("Segoe UI", -int(12 * scale))

        tk.Label(self.win, text="Remind me…", bg=gfx.PANEL_BG, fg=gfx.PANEL_DIM, font=small, anchor="w").pack(fill="x")
        self.entry = tk.Entry(self.win, textvariable=self.var, width=40, font=font, bg=gfx.INPUT_BG,
                              fg=gfx.PANEL_FG, insertbackground=gfx.PANEL_FG, relief="flat",
                              highlightthickness=1, highlightbackground=gfx.INPUT_BORDER,
                              highlightcolor=gfx.PURPLE)
        self.entry.pack(fill="x", pady=(int(4 * scale), int(4 * scale)), ipady=int(4 * scale))
        self.status = tk.Label(self.win, text="", bg=gfx.PANEL_BG, fg=gfx.PANEL_DIM, font=small, anchor="w",
                               justify="left", wraplength=int(420 * scale))
        self.status.pack(fill="x")
        self.check = tk.Checkbutton(self.win, text="sticky (stay until dismissed)",
                                    variable=self.sticky, bg=gfx.PANEL_BG, fg=gfx.PANEL_FG, selectcolor=gfx.PANEL_BG,
                                    activebackground=gfx.PANEL_BG, activeforeground=gfx.PANEL_FG, font=small,
                                    anchor="w", highlightthickness=0)
        self.check.pack(fill="x", pady=(int(4 * scale), 0))

        self.var.trace_add("write", lambda *_: self._refresh())
        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<KP_Enter>", self._submit)
        self.win.bind("<Escape>", lambda _e: self.close())
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        self._place(anchor)
        self.win.deiconify()
        win.apply_dialog_styles(self.win)
        self.win.lift()
        self.win.focus_force()
        self.entry.focus_set()
        self._refresh()

    def _place(self, anchor: Optional[tuple[int, int]]) -> None:
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        if anchor is None:
            mon = monitors.primary_monitor()
            x = mon.work[0] + (mon.work[2] - mon.work[0] - w) // 2
            y = mon.work[1] + (mon.work[3] - mon.work[1] - h) // 3
        else:
            x, y = anchor[0] - w - 12, anchor[1] - h + 60
        mon = monitors.monitor_at(x + w // 2, y + h // 2)
        l, t, r, b = mon.work
        x = max(l, min(x, r - w))
        y = max(t, min(y, b - h))
        self.win.geometry(f"+{x}+{y}")

    def _refresh(self) -> None:
        text = self.var.get().strip()
        self._parsed = None
        self.interpretation = ""
        self.error = ""
        if not text:
            self.status.configure(text="", fg=gfx.PANEL_DIM)
            return
        if self._parse is None:
            self.error = PARSER_MISSING
            self.status.configure(text=self.error, fg=gfx.PANEL_ERR)
            return
        try:
            self._parsed = self._parse(text, self._clock())
        except self._error_type as exc:
            self.error = str(getattr(exc, "help_sentence", "") or exc or DIDNT_UNDERSTAND)
            self.status.configure(text=self.error, fg=gfx.PANEL_ERR)
            return
        self.interpretation = self._parsed.interpretation
        self.status.configure(text=self.interpretation, fg=gfx.PANEL_OK)

    def _submit(self, _event=None) -> str:
        if self._parsed is None:
            return "break"
        parsed, sticky = self._parsed, bool(self.sticky.get())
        self.close()
        self._on_submit(parsed, sticky)
        return "break"

    def set_text(self, text: str) -> None:
        self.var.set(text)

    def submit(self) -> None:
        self._submit()

    def close(self) -> None:
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None

    @property
    def open(self) -> bool:
        return self.win is not None
