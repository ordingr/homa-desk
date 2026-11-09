"""Ask Claude in the popup (AC-AK-2, AC-AK-3, AC-AK-6): the plain-text confirmation body and the answer window.
tkinter, main thread only."""
import tkinter as tk
from typing import Any, Callable, Optional

from ..actions import claude
from . import gfx, mdrender_tk

CLAUDE_MARK = "☁ "


def confirm_text(result: dict) -> str:
    """The confirmation body: exactly the summary fields the user is agreeing to, one per line."""
    summary = result.get("summary")
    if not isinstance(summary, dict):
        return str(result.get("message", ""))
    send, size = summary.get("bytes_to_send"), summary.get("file_bytes")
    lines = [f"File: {summary.get('path', '')}"]
    mode = "whole file" if summary.get("mode") == "whole_file" else "preview text"
    lines.append(f"Sends: {send} bytes ({mode}) - {summary.get('what', '')}")
    if not summary.get("whole_file"):
        lines.append(f"The file is {size} bytes; the rest is not sent.")
    if summary.get("office_text"):
        lines.append("Office file: the extracted text is sent, not the file itself.")
    if summary.get("account"):
        lines.append(f"Signed in to Claude Code with: {summary['account']}")
    lines.append("Nothing is sent until you click Send.")
    return "\n".join(lines)


class AnswerWindow:
    """A small window with Claude's answer, Copy, Continue in Claude Code, and Esc to close."""

    def __init__(self, parent: tk.Misc, name: str, path: str, answer: str, scale: float = 1.0,
                 on_continue: Optional[Callable[[str], dict]] = None) -> None:
        self.path, self.answer = path, answer
        self.on_continue = on_continue or claude.continue_session
        self.status = ""
        win = self.win = tk.Toplevel(parent)
        win.withdraw()
        win.title(f"Claude about {name}")
        win.configure(bg=gfx.PANEL_BG, padx=12, pady=10)
        win.attributes("-topmost", True)
        win.geometry(f"{gfx.px(620, scale)}x{gfx.px(440, scale)}")
        tk.Label(win, text=f"{CLAUDE_MARK}Claude's answer about {name}", bg=gfx.PANEL_BG, fg=gfx.AMBER,
                 font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", pady=(0, 6))
        bar = tk.Frame(win, bg=gfx.PANEL_BG)
        bar.pack(side="bottom", fill="x", pady=(8, 0))
        body = tk.Frame(win, bg=gfx.PANEL_BG)
        body.pack(fill="both", expand=True)
        scroll = tk.Scrollbar(body)
        scroll.pack(side="right", fill="y")
        self.text = tk.Text(body, wrap="word", bg=gfx.PANEL_BG, fg=gfx.PANEL_FG, relief="flat", bd=0,
                            highlightthickness=0, padx=4, pady=4, yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.configure(command=self.text.yview)
        mdrender_tk.configure_tags(self.text)
        mdrender_tk.render(self.text, answer)
        self.text.configure(state="disabled")
        self.note = tk.Label(bar, text="", bg=gfx.PANEL_BG, fg=gfx.PANEL_DIM, font=("Segoe UI", 9), anchor="w")
        self.note.pack(side="left", fill="x", expand=True)
        for label, command, accent in (("Close", self.close, ""), ("Continue in Claude Code", self.continue_, ""),
                                       ("Copy", self.copy, gfx.BLUE)):
            tk.Button(bar, text=label, command=command, bg=accent or gfx.BUTTON_BG, fg=gfx.PANEL_FG,
                      activebackground=gfx.BUTTON_BG_HOVER, activeforeground=gfx.WHITE, relief="flat", bd=0,
                      padx=12, pady=4, font=("Segoe UI", 10)).pack(side="right", padx=(6, 0))
        win.bind("<Escape>", lambda _e: self.close())
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.deiconify()
        win.lift()
        try:
            win.focus_force()
        except tk.TclError:
            pass

    def copy(self) -> None:
        self.win.clipboard_clear()
        self.win.clipboard_append(self.answer)
        self._say("Copied.")

    def continue_(self) -> None:
        result = self.on_continue(self.path)
        self._say(str(result.get("message", "")))

    def _say(self, message: str) -> None:
        self.status = message
        self.note.configure(text=message)

    def close(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def snapshot(self) -> dict:
        return {"answer": self.answer, "status": self.status, "path": self.path}


def show_answer(parent: tk.Misc, name: str, path: str, answer: str, scale: float = 1.0,
                on_continue: Optional[Callable[[str], dict]] = None) -> AnswerWindow:
    return AnswerWindow(parent, name, path, answer, scale, on_continue)
