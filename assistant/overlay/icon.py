import json
import logging
import os
import sys
import time
import tkinter as tk
from dataclasses import dataclass
from typing import Callable, Optional

from .. import paths
from . import gfx, monitors, sounds, win

log = logging.getLogger("assistant.overlay.icon")

BASE_SIZE = 60
MARGIN = 16
DRAG_THRESHOLD = 4
STATE_FILE = "state_ui.json"

MUTE_CHOICES = (("Until unmuted", None), ("30 minutes", 30 * 60), ("1 hour", 60 * 60))


def _noop(*_args, **_kwargs) -> None:
    return None


@dataclass
class IconActions:
    """Callbacks the app (M1-6) wires in; all run on the main thread."""
    search: Callable[[], None] = _noop
    remind_submit: Callable[[object, bool], None] = _noop
    mute: Callable[[Optional[float]], None] = _noop
    unmute: Callable[[], None] = _noop
    settings: Callable[[], None] = _noop
    quit: Callable[[], None] = _noop
    position_changed: Callable[[int, int], None] = _noop


def state_path():
    return paths.home() / STATE_FILE


def load_state() -> dict:
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(update: dict) -> None:
    data = load_state()
    data.update(update)
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def menu_labels(muted: bool, sound_on: bool) -> list[str]:
    """AC-OV-11 order. 'Mute' carries a submenu; the sound entry names the action it performs."""
    return [
        "Search",
        "Remind me…",
        "Unmute" if muted else "Mute",
        "Sound off" if sound_on else "Sound on",
        "Test sound",
        "Reset position",
        "Settings",
        "Quit",
    ]


class Icon:
    def __init__(self, root: tk.Tk, actions: Optional[IconActions] = None,
                 parse: Optional[Callable] = None, error_type: type = Exception) -> None:
        self.root = root
        self.actions = actions or IconActions()
        self._parse = parse
        self._error_type = error_type
        self.count = 0
        self.muted = False
        self.mute_until: Optional[float] = None
        self.reminder_input = None
        self.last_menu: Optional[tk.Menu] = None
        self.scale = 1.0
        self.size = BASE_SIZE
        self._drag: Optional[dict] = None

        sounds.set_enabled(bool(load_state().get("sound", True)))

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.protocol("WM_DELETE_WINDOW", lambda: self.actions.quit())
        self.win.overrideredirect(True)
        self.win.configure(bg=gfx.CHROMA_KEY)
        self.win.attributes("-topmost", True)
        self.win.attributes("-transparentcolor", gfx.CHROMA_KEY)
        self.canvas = tk.Canvas(self.win, bg=gfx.CHROMA_KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        x, y = self._initial_position()
        self._apply_scale_at(x, y)
        self.win.geometry(f"{self.size}x{self.size}+{x}+{y}")
        self.win.deiconify()
        self.exstyle = win.apply_overlay_styles(self.win)
        self.hwnd = win.hwnd_of(self.win)
        self._draw()

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<ButtonPress-3>", self._popup_menu)

    def _apply_scale_at(self, x: int, y: int) -> None:
        self.scale = monitors.monitor_at(x + 1, y + 1).scale
        self.size = gfx.px(BASE_SIZE, self.scale)
        self.canvas.configure(width=self.size, height=self.size)

    def _initial_position(self) -> tuple[int, int]:
        saved = load_state().get("icon")
        if isinstance(saved, dict) and isinstance(saved.get("x"), int) and isinstance(saved.get("y"), int):
            sx, sy = saved["x"], saved["y"]
            size = gfx.px(BASE_SIZE, monitors.monitor_at(sx + 1, sy + 1).scale)
            if monitors.is_visible(sx, sy, size, size):
                return sx, sy
        scale = monitors.primary_monitor().scale
        return monitors.default_icon_position(gfx.px(BASE_SIZE, scale), gfx.px(MARGIN, scale))

    def position(self) -> tuple[int, int]:
        return self.win.winfo_x(), self.win.winfo_y()

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        s = self.size
        pad = max(2, s * 0.06)
        body = gfx.GREY if self.muted else gfx.PURPLE
        c.create_oval(pad, pad, s - pad, s - pad, fill=body, outline=gfx.WHITE,
                      width=max(1, s // 30), tags="body")
        gfx.draw_bell(c, s / 2, s / 2, s * 0.42, gfx.WHITE)
        if self.muted:
            gfx.draw_cross(c, s / 2, s / 2, s * 0.42, gfx.RED, max(2, s / 14))
        if self.count > 0:
            gfx.draw_badge(c, s * 0.76, s * 0.24, s * 0.19, self.count)

    def set_count(self, count: int) -> None:
        count = max(0, int(count))
        if count != self.count:
            self.count = count
            self._draw()

    def set_muted(self, muted: bool, until: Optional[float] = None) -> None:
        self.muted = bool(muted)
        self.mute_until = until if muted else None
        self._draw()

    def _press(self, event) -> None:
        self._drag = {"rx": event.x_root, "ry": event.y_root, "wx": self.win.winfo_x(),
                      "wy": self.win.winfo_y(), "moved": False}

    def _motion(self, event) -> None:
        d = self._drag
        if not d:
            return
        dx, dy = event.x_root - d["rx"], event.y_root - d["ry"]
        if not d["moved"] and abs(dx) < DRAG_THRESHOLD and abs(dy) < DRAG_THRESHOLD:
            return
        d["moved"] = True
        d["tx"], d["ty"] = d["wx"] + dx, d["wy"] + dy
        self.win.geometry(f"+{d['tx']}+{d['ty']}")

    def _release(self, _event) -> None:
        d, self._drag = self._drag, None
        if not d:
            return
        if not d["moved"]:
            self.actions.search()
            return
        x, y = d["tx"], d["ty"]
        old = self.size
        self._apply_scale_at(x, y)
        if self.size != old:
            self.win.geometry(f"{self.size}x{self.size}+{x}+{y}")
            self._draw()
        self._persist_position(x, y)

    def move_to(self, x: int, y: int) -> None:
        self._apply_scale_at(x, y)
        self.win.geometry(f"{self.size}x{self.size}+{x}+{y}")
        self._draw()
        self.win.update_idletasks()
        self._persist_position()

    def _persist_position(self, x: Optional[int] = None, y: Optional[int] = None) -> None:
        if x is None or y is None:
            x, y = self.position()
        try:
            save_state({"icon": {"x": x, "y": y}})
        except OSError:
            log.exception("could not save icon position")
        self.actions.position_changed(x, y)

    def reset_position(self) -> None:
        scale = monitors.primary_monitor().scale
        x, y = monitors.default_icon_position(gfx.px(BASE_SIZE, scale), gfx.px(MARGIN, scale))
        self.move_to(x, y)

    def _popup_menu(self, event) -> None:
        menu = self.build_menu()
        self.last_menu = menu
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def build_menu(self) -> tk.Menu:
        labels = menu_labels(self.muted, sounds.is_enabled())
        menu = tk.Menu(self.win, tearoff=False)
        handlers = {
            "Search": self.actions.search,
            "Remind me…": self.open_reminder,
            "Unmute": self.actions.unmute,
            "Sound off": lambda: self.set_sound(False),
            "Sound on": lambda: self.set_sound(True),
            "Test sound": sounds.test_sound,
            "Reset position": self.reset_position,
            "Settings": self.actions.settings,
            "Quit": self.actions.quit,
        }
        for label in labels:
            if label == "Mute":
                sub = tk.Menu(menu, tearoff=False)
                for text, seconds in MUTE_CHOICES:
                    sub.add_command(label=text, command=lambda s=seconds: self.mute(s))
                menu.add_cascade(label="Mute", menu=sub)
            else:
                menu.add_command(label=label, command=handlers[label])
        return menu

    def mute(self, seconds: Optional[float]) -> None:
        until = None if seconds is None else time.time() + seconds
        self.actions.mute(until)

    def set_sound(self, enabled: bool) -> None:
        sounds.set_enabled(enabled)
        try:
            save_state({"sound": enabled})
        except OSError:
            log.exception("could not save sound setting")

    def open_reminder(self) -> None:
        if self.reminder_input is not None and self.reminder_input.open:
            self.reminder_input.win.lift()
            self.reminder_input.entry.focus_force()
            return
        from .reminder_ui import ReminderInput
        self.reminder_input = ReminderInput(
            self.root, self.actions.remind_submit, anchor=self.position(), scale=self.scale,
            parse=self._parse, error_type=self._error_type)

    def debug_state(self) -> dict:
        """Snapshot for /v1/_debug/ui and the standalone demo."""
        x, y = self.position()
        style = win.get_exstyle(self.hwnd)
        return {
            "icon": {
                "hwnd": self.hwnd,
                "rect": [x, y, x + self.size, y + self.size],
                "exstyle": hex(style),
                "exstyle_flags": win.describe_exstyle(style),
                "count": self.count,
                "muted": self.muted,
                "body_colour": gfx.GREY if self.muted else gfx.PURPLE,
                "scale": self.scale,
            },
            "menu": menu_labels(self.muted, sounds.is_enabled()),
            "mute_choices": [t for t, _ in MUTE_CHOICES],
            "sound_on": sounds.is_enabled(),
            "reminder_input_open": bool(self.reminder_input and self.reminder_input.open),
        }

    def destroy(self) -> None:
        if self.reminder_input is not None:
            self.reminder_input.close()
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def create_root() -> tk.Tk:
    win.set_dpi_aware()
    root = tk.Tk()
    root.withdraw()
    return root


def _demo(argv: list[str]) -> int:
    from .. import uibus
    root = create_root()
    uibus.start(root)
    icon = Icon(root, IconActions(quit=root.destroy))
    if "--count" in argv:
        icon.set_count(int(argv[argv.index("--count") + 1]))
    if "--muted" in argv:
        icon.set_muted(True)
    print(json.dumps(icon.debug_state(), indent=2, ensure_ascii=False), flush=True)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(_demo(sys.argv[1:]))
