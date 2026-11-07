import json
import logging
import sys
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from typing import Any, Callable, Optional

from .. import uibus
from ..cards.model import Card
from . import gfx, icon as icon_mod, monitors, panel, sounds, win

log = logging.getLogger("assistant.overlay.cards_ui")

SETTING_EVERY_MONITOR = "cards_every_monitor"

CARD_WIDTH = 340
GAP = 8
SLIDE = 56
TICK_MS = 250
FRAME_MS = 16
SOUND_REPEAT_S = 5.0
ALPHA_STEP = 0.12
EASE = 0.35
STRIP_WIDTH = 120
STRIP_HEIGHT = 26
OFFSCREEN = -32000

KIND_LABELS = {
    "waiting": "!! CLAUDE NEEDS YOU",
    "done": "CLAUDE FINISHED",
    "meeting": "!! MEETING",
    "reminder": "REMINDER",
    "custom": "NOTIFICATION",
}


def _noop(*_args, **_kwargs) -> None:
    return None


@dataclass
class CardCallbacks:
    """Callbacks out of the card windows; all run on the main thread."""
    on_seen: Callable[[str], None] = _noop
    on_snooze: Callable[[str, int], None] = _noop
    on_dismiss: Callable[[str], None] = _noop
    on_press: Callable[[str, str], None] = _noop
    on_open_url: Callable[[str], None] = _noop


def callbacks_for_store(store: Any, open_url: Callable[[str], None] = _noop) -> CardCallbacks:
    return CardCallbacks(
        on_seen=store.mark_seen,
        on_snooze=store.snooze,
        on_dismiss=store.dismiss,
        on_press=store.press,
        on_open_url=open_url,
    )


def timer_text(card: Card, now: float, fallback: float) -> str:
    start = card.timer_started_at or card.created_at or fallback
    secs = max(0, int(now - start))
    return f"{secs // 60}:{secs % 60:02d}"


def created_text(card: Card, fallback: float) -> str:
    return time.strftime("%H:%M", time.localtime(card.created_at or fallback))


class _State:
    def __init__(self, card: Card, seq: int, shown_at: float) -> None:
        self.card = card
        self.seq = seq
        self.shown_at = shown_at
        self.seen = card.seen_at is not None
        self.last_sound = 0.0
        self.wins: list["CardWindow"] = []

    @property
    def sort_key(self) -> tuple:
        return (1 if self.card.effective_priority >= 5 else 0, self.seq)


class CardWindow:
    def __init__(self, ui: "CardsUI", state: _State, monitor: monitors.Monitor) -> None:
        self.ui = ui
        self.state = state
        self.monitor = monitor
        self.scale = monitor.scale
        self.w = 0
        self.h = 0
        self.x = self.y = 0.0
        self.tx = self.ty = 0.0
        self.alpha = 0.0
        self.talpha = 1.0
        self.fresh = True
        self.removing = False
        self.hidden = False
        self.shown_xy: Optional[tuple[int, int]] = None
        self.blink_on = False
        self.timer_str = ""
        self.hover_tag = ""
        self.hits: dict[str, Callable[[], None]] = {}

        self.win = tk.Toplevel(ui.root)
        self.win.withdraw()
        ui.adopt_close(self.win)
        self.win.overrideredirect(True)
        self.rounded = win.round_corners(win.hwnd_of(self.win))
        key = gfx.PANEL_BG if self.rounded else gfx.CHROMA_KEY
        self.win.configure(bg=key)
        self.win.attributes("-topmost", True)
        if not self.rounded:
            self.win.attributes("-transparentcolor", gfx.CHROMA_KEY)
        self.canvas = tk.Canvas(self.win, bg=key, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Enter>", lambda _e: ui.hover(self.state.card.id))
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda _e: self._set_hover(""))
        self.canvas.bind("<ButtonPress-1>", self._click)
        self.render()
        self.win.geometry(f"{self.w}x{self.h}+-32000+-32000")
        self.win.attributes("-alpha", 0.0)
        self.win.deiconify()
        self.exstyle = win.apply_overlay_styles(self.win)
        self.hwnd = win.hwnd_of(self.win)

    def _font(self, size: float, weight: str = "normal", underline: bool = False) -> tkfont.Font:
        return self.ui.font(gfx.px(size, self.scale), weight, underline)

    def render(self) -> None:
        card = self.state.card
        s = self.scale
        c = self.canvas
        c.delete("all")
        self.hits = {}
        accent = panel.kind_accent(card.kind)
        W = gfx.px(CARD_WIDTH, s)
        left = gfx.px(30, s)
        right = W - gfx.px(16, s)
        text_w = right - left
        y = gfx.px(14, s)

        label_font = self._font(11, "bold")
        c.create_text(left, y, anchor="nw", text=KIND_LABELS.get(card.kind, "NOTIFICATION"),
                      fill=accent, font=label_font, tags="label")
        c.create_text(right, y, anchor="ne", text=created_text(card, self.state.shown_at),
                      fill=panel.TEXT_DIM, font=self._font(11), tags="created")
        y += label_font.metrics("linespace") + gfx.px(6, s)

        title = c.create_text(left, y, anchor="nw", text=card.title, fill=panel.TEXT,
                              font=self._font(15, "bold"), width=text_w, tags="title")
        y = c.bbox(title)[3] + gfx.px(4, s)

        if card.detail:
            det = c.create_text(left, y, anchor="nw", text=card.detail, fill=panel.TEXT_DIM,
                                font=self._font(12), width=text_w, tags="detail")
            y = c.bbox(det)[3] + gfx.px(4, s)

        if card.kind == "waiting":
            self.timer_str = self.ui.timer_for(self.state)
            tid = c.create_text(left, y, anchor="nw", text=f"waiting {self.timer_str}", fill=accent,
                                font=self._font(12, "bold"), tags="timer")
            y = c.bbox(tid)[3] + gfx.px(4, s)

        if card.url:
            link = c.create_text(left, y, anchor="nw", text=card.url, fill=panel.LINK,
                                 font=self._font(11, underline=True), width=text_w, tags=("url", "link"))
            y = c.bbox(link)[3] + gfx.px(4, s)
            self.hits["url"] = lambda: self.ui.cb.on_open_url(card.url)

        y += gfx.px(6, s)
        buttons = [("Snooze 5m", "btn_snooze5", lambda: self.ui.cb.on_snooze(card.id, 5)),
                   ("Snooze 15m", "btn_snooze15", lambda: self.ui.cb.on_snooze(card.id, 15)),
                   ("Dismiss", "btn_dismiss", lambda: self.ui.cb.on_dismiss(card.id))]
        for i, a in enumerate(card.actions or []):
            buttons.append((str(a.get("label", ""))[:20], f"btn_act{i}",
                            lambda aid=a.get("id"): self.ui.cb.on_press(card.id, aid)))
        bfont = self._font(11, "bold")
        bh, padx, bgap = gfx.px(26, s), gfx.px(10, s), gfx.px(6, s)
        x = left
        for label, tag, fn in buttons:
            bw = bfont.measure(label) + 2 * padx
            if x > left and x + bw > right:
                x = left
                y += bh + bgap
            panel.draw_button(c, x, y, bw, bh, label, bfont, tag, s,
                              accent=accent if tag.startswith("btn_act") else "")
            self.hits[tag] = fn
            x += bw + bgap
        y += bh + gfx.px(14, s)

        self.w, self.h = W, int(y)
        c.configure(width=self.w, height=self.h)
        self.draw_frame()

    def draw_frame(self) -> None:
        panel.draw_panel(self.canvas, self.w, self.h, panel.kind_accent(self.state.card.kind),
                         self.scale, self.blink_on, self.rounded)

    def set_blink(self, on: bool) -> None:
        if on != self.blink_on:
            self.blink_on = on
            self.draw_frame()

    def refresh_timer(self) -> None:
        if self.state.card.kind != "waiting":
            return
        text = self.ui.timer_for(self.state)
        if text != self.timer_str:
            self.timer_str = text
            self.canvas.itemconfigure("timer", text=f"waiting {text}")

    def _tag_at(self) -> str:
        for item in self.canvas.find_withtag("current"):
            for t in self.canvas.gettags(item):
                if t in self.hits:
                    return t
        return ""

    def _set_hover(self, tag: str) -> None:
        if tag == self.hover_tag:
            return
        if self.hover_tag.startswith("btn_"):
            panel.set_button_hover(self.canvas, self.hover_tag, False)
        self.hover_tag = tag
        if tag.startswith("btn_"):
            panel.set_button_hover(self.canvas, tag, True)
        self.canvas.configure(cursor="hand2" if tag else "")

    def _motion(self, _event) -> None:
        self._set_hover(self._tag_at())

    def _click(self, _event) -> None:
        tag = self._tag_at()
        fn = self.hits.get(tag)
        if fn is not None:
            self.ui.safe(fn)

    def step(self) -> bool:
        """Advance one animation frame; returns True while still moving."""
        moving = False
        for attr in ("x", "y"):
            cur, tgt = getattr(self, attr), getattr(self, "t" + attr)
            if abs(tgt - cur) > 0.6:
                cur += (tgt - cur) * EASE
                moving = True
            else:
                cur = tgt
            setattr(self, attr, cur)
        if self.alpha < self.talpha:
            self.alpha = min(self.talpha, self.alpha + ALPHA_STEP)
            moving = True
        elif self.alpha > self.talpha:
            self.alpha = max(self.talpha, self.alpha - ALPHA_STEP)
            moving = True
        self.apply()
        return moving

    def apply(self) -> None:
        xy = (int(round(self.x)), int(round(self.y)))
        if xy != self.shown_xy or self.win.winfo_width() != self.w:
            self.shown_xy = xy
            self.win.geometry(f"{self.w}x{self.h}+{xy[0]}+{xy[1]}")
        self.win.attributes("-alpha", max(0.0, min(1.0, self.alpha)))

    def hide(self) -> None:
        if self.hidden:
            return
        self.hidden = True
        self.fresh = True
        self.x = self.y = self.tx = self.ty = OFFSCREEN
        self.alpha = self.talpha = 0.0
        self.apply()

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def destroy(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass


class MoreStrip:
    """Small '+N more' window that closes a stack that does not fit the work area."""

    def __init__(self, ui: "CardsUI", monitor: monitors.Monitor) -> None:
        self.monitor = monitor
        self.scale = monitor.scale
        self.count = 0
        self.w = gfx.px(STRIP_WIDTH, self.scale)
        self.h = gfx.px(STRIP_HEIGHT, self.scale)
        self.win = tk.Toplevel(ui.root)
        self.win.withdraw()
        ui.adopt_close(self.win)
        self.win.overrideredirect(True)
        self.rounded = win.round_corners(win.hwnd_of(self.win), small=True)
        key = panel.TEXT_DIM if self.rounded else gfx.CHROMA_KEY
        self.win.configure(bg=key)
        self.win.attributes("-topmost", True)
        if not self.rounded:
            self.win.attributes("-transparentcolor", gfx.CHROMA_KEY)
        self.canvas = tk.Canvas(self.win, bg=key, highlightthickness=0, bd=0,
                                width=self.w, height=self.h)
        self.canvas.pack(fill="both", expand=True)
        self.font = ui.font(gfx.px(11, self.scale), "bold")
        self.win.geometry(f"{self.w}x{self.h}+{OFFSCREEN}+{OFFSCREEN}")
        self.win.deiconify()
        win.apply_overlay_styles(self.win)
        self.hwnd = win.hwnd_of(self.win)
        self.xy = (OFFSCREEN, OFFSCREEN)

    def show(self, count: int, x: int, y: int) -> None:
        if count != self.count:
            self.count = count
            self.canvas.delete("all")
            if self.rounded:
                edge = max(1, round(self.scale))
                panel.rounded_rect(self.canvas, edge, edge, self.w - edge, self.h - edge, 3 * self.scale,
                                   fill=panel.BUTTON_BG, outline="")
            else:
                panel.rounded_rect(self.canvas, 1, 1, self.w - 1, self.h - 1, 6 * self.scale,
                                   fill=panel.BUTTON_BG, outline=panel.TEXT_DIM)
            self.canvas.create_text(self.w / 2, self.h / 2, text=f"+{count} more",
                                    fill=panel.TEXT, font=self.font)
        if (x, y) != self.xy:
            self.xy = (x, y)
            self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")

    def hide(self) -> None:
        if self.count:
            self.count = 0
            self.xy = (OFFSCREEN, OFFSCREEN)
            self.win.geometry(f"{self.w}x{self.h}+{OFFSCREEN}+{OFFSCREEN}")

    def destroy(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass


class CardsUI:
    def __init__(self, root: tk.Misc, icon: Any, store_callbacks: Optional[CardCallbacks] = None,
                 settings: Any = None, clock: Callable[[], float] = time.time,
                 on_close_request: Optional[Callable[[], None]] = None) -> None:
        self.root = root
        self.on_close_request = on_close_request
        self.icon = icon
        self.cb = store_callbacks or CardCallbacks()
        self.settings = settings
        self.clock = clock
        self._states: dict[str, _State] = {}
        self._dying: list[CardWindow] = []
        self._strips: dict[int, MoreStrip] = {}
        self._fonts: dict[tuple, tkfont.Font] = {}
        self._seq = 0
        self._tick_n = 0
        self._animating = False
        self._icon_sig: Optional[tuple] = None
        self._targets_sig: Optional[tuple] = None
        self._closed = False
        self._ticking = False
        if settings is not None and hasattr(settings, "on_change"):
            settings.on_change(self._on_setting)

    def adopt_close(self, window: tk.Toplevel) -> None:
        """A close request on any overlay window (taskkill without /F posts WM_CLOSE) means quit."""
        if self.on_close_request is not None:
            window.protocol("WM_DELETE_WINDOW", self.on_close_request)

    def _ensure_tick(self) -> None:
        if not self._ticking and not self._closed:
            self._ticking = True
            self.root.after(TICK_MS, self._tick)

    def _prune_dead(self) -> None:
        for state in self._states.values():
            if not all(w.alive() for w in state.wins):
                state.wins = [w for w in state.wins if w.alive()]
        self._dying = [w for w in self._dying if w.alive()]

    def font(self, size: int, weight: str = "normal", underline: bool = False) -> tkfont.Font:
        key = (size, weight, underline)
        f = self._fonts.get(key)
        if f is None:
            f = tkfont.Font(root=self.root, family="Segoe UI", size=-size, weight=weight,
                            underline=underline)
            self._fonts[key] = f
        return f

    def safe(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            log.exception("card callback failed")

    def timer_for(self, state: _State) -> str:
        return timer_text(state.card, self.clock(), state.shown_at)

    def every_monitor(self) -> bool:
        if self.settings is None:
            return True
        return bool(self.settings.get(SETTING_EVERY_MONITOR, True))

    def _on_setting(self, key: str, _value: Any) -> None:
        if key == SETTING_EVERY_MONITOR:
            uibus.post(self.relayout)

    def _icon_rect(self) -> tuple[int, int, int]:
        x, y = self.icon.position()
        return x, y, self.icon.size

    def target_monitors(self) -> list[monitors.Monitor]:
        mons = monitors.list_monitors()
        if self.every_monitor():
            return mons
        ix, iy, isz = self._icon_rect()
        return [monitors.monitor_at(ix + 1, iy + 1)]

    def attach(self, store: Any) -> Callable[[], None]:
        """Mirror a CardStore: its events arrive on arbitrary threads, so hop to the UI thread."""
        def on_event(event: str, card: Optional[Card]) -> None:
            uibus.post(self.on_store_event, event, card)
        unsub = store.subscribe(on_event)
        for card in store.visible():
            self.show(card)
        return unsub

    def on_store_event(self, event: str, card: Optional[Card]) -> None:
        if card is None:
            return
        if event in ("added", "updated", "snooze_ended"):
            if card.dismissed_at is None and card.snoozed_until is None:
                self.show(card)
        elif event == "seen":
            self.update(card)
        elif event in ("snoozed", "dismissed", "expired"):
            self.remove(card.id, animate=True)

    def show(self, card: Card) -> None:
        if card.id in self._states:
            self.update(card)
            return
        self._seq += 1
        state = _State(card.copy(), self._seq, self.clock())
        self._states[card.id] = state
        self._reconcile_state(state)
        if not state.seen:
            self._notify(state)
        self.relayout()
        self._kick()
        self._ensure_tick()

    def update(self, card: Card) -> None:
        state = self._states.get(card.id)
        if state is None:
            self.show(card)
            return
        was_seen = state.seen
        state.card = card.copy()
        state.seen = card.seen_at is not None
        for w in state.wins:
            w.render()
            w.set_blink(not state.seen and self._blink_phase())
        if was_seen and not state.seen:
            self._notify(state)
        self.relayout()
        self._kick()

    def remove(self, card_id: str, animate: bool = True) -> None:
        state = self._states.pop(card_id, None)
        if state is None:
            return
        for w in state.wins:
            if animate:
                w.removing = True
                w.talpha = 0.0
                w.tx += _dir(w) * gfx.px(SLIDE, w.scale) * 0.5
                self._dying.append(w)
            else:
                w.destroy()
        state.wins = []
        self.relayout()
        self._kick()

    def hover(self, card_id: str) -> None:
        state = self._states.get(card_id)
        if state is None or state.seen:
            return
        state.seen = True
        state.card.seen_at = self.clock()
        for w in state.wins:
            w.set_blink(False)
        self.safe(lambda: self.cb.on_seen(card_id))

    def _blink_phase(self) -> bool:
        return (self._tick_n // 2) % 2 == 0

    def _notify(self, state: _State) -> None:
        state.last_sound = time.monotonic()
        sounds.play(state.card.kind)

    def _reconcile_state(self, state: _State) -> None:
        wanted = {m.handle: m for m in self.target_monitors()}
        for w in list(state.wins):
            if w.monitor.handle not in wanted:
                w.destroy()
                state.wins.remove(w)
        have = {w.monitor.handle for w in state.wins}
        for handle, m in wanted.items():
            if handle not in have:
                w = CardWindow(self, state, m)
                w.set_blink(not state.seen)
                state.wins.append(w)

    def reconcile(self) -> None:
        for state in self._states.values():
            self._reconcile_state(state)
        self.relayout()
        self._kick()

    def _anchor_for(self, m: monitors.Monitor, ix: int, iy: int, isz: int,
                    im: monitors.Monitor) -> tuple[int, int, int]:
        if m.handle == im.handle:
            return ix, iy, isz
        size = gfx.px(icon_mod.BASE_SIZE, m.scale)
        il, it, ir, ib = im.work
        l, t, r, b = m.work
        fx = min(1.0, max(0.0, (ix - il) / max(1, (ir - il) - isz)))
        fy = min(1.0, max(0.0, (iy - it) / max(1, (ib - it) - isz)))
        return int(l + fx * ((r - l) - size)), int(t + fy * ((b - t) - size)), size

    def relayout(self) -> None:
        if self._closed:
            return
        ix, iy, isz = self._icon_rect()
        im = monitors.monitor_at(ix + 1, iy + 1)
        order = sorted(self._states.values(), key=lambda s: s.sort_key)
        for m in self.target_monitors():
            ax, ay, asz = self._anchor_for(m, ix, iy, isz, im)
            wl, wt, wr, wb = m.work
            gap = gfx.px(GAP, m.scale)
            right_side = ax + asz / 2 >= (wl + wr) / 2
            above = ay + asz / 2 >= (wt + wb) / 2
            wins = [w for s in order for w in s.wins if w.monitor.handle == m.handle]
            seq = list(reversed(wins))
            strip_h = gfx.px(STRIP_HEIGHT, m.scale)
            room = (ay - gap - wt) if above else (wb - (ay + asz + gap))
            shown = len(seq)
            if sum(w.h + gap for w in seq) - gap > room:
                shown, used = 0, 0
                for w in seq:
                    used += w.h + gap
                    if used + strip_h > room:
                        break
                    shown += 1
                shown = max(1, shown)
            cursor = ay - gap if above else ay + asz + gap
            for i, w in enumerate(seq):
                if i >= shown:
                    w.hide()
                    continue
                w.hidden = False
                tx = ax + asz - w.w if right_side else ax
                tx = max(wl, min(tx, wr - w.w))
                if above:
                    ty = cursor - w.h
                    cursor = ty - gap
                else:
                    ty = cursor
                    cursor = ty + w.h + gap
                ty = max(wt, min(ty, wb - w.h))
                w.tx, w.ty = float(tx), float(ty)
                if w.fresh:
                    w.fresh = False
                    w.x = w.tx + _dir(w, right_side) * gfx.px(SLIDE, w.scale)
                    w.y = w.ty
                    w.talpha = 1.0
                    w.apply()
            self._place_strip(m, len(seq) - shown, cursor, above, right_side, ax, asz)
        self._kick()

    def _place_strip(self, m: monitors.Monitor, hidden: int, cursor: int, above: bool,
                     right_side: bool, ax: int, asz: int) -> None:
        strip = self._strips.get(m.handle)
        if hidden <= 0:
            if strip is not None:
                strip.hide()
            return
        if strip is None:
            strip = self._strips[m.handle] = MoreStrip(self, m)
        wl, wt, wr, wb = m.work
        x = ax + asz - strip.w if right_side else ax
        x = max(wl, min(x, wr - strip.w))
        y = cursor - strip.h if above else cursor
        y = max(wt, min(y, wb - strip.h))
        strip.show(hidden, x, y)

    def strip_state(self) -> list[dict]:
        out = []
        for handle, strip in self._strips.items():
            if strip.count:
                out.append({"monitor_handle": handle, "count": strip.count,
                            "rect": [strip.xy[0], strip.xy[1], strip.xy[0] + strip.w,
                                     strip.xy[1] + strip.h]})
        return out

    def _kick(self) -> None:
        if not self._animating and not self._closed:
            self._animating = True
            self.root.after(FRAME_MS, self._frame)

    def _frame(self) -> None:
        if self._closed:
            return
        moving = False
        self._prune_dead()
        for state in self._states.values():
            for w in state.wins:
                moving |= w.step()
        for w in list(self._dying):
            if not w.step() and w.alpha <= 0.0:
                w.destroy()
                self._dying.remove(w)
            else:
                moving = True
        if moving:
            self.root.after(FRAME_MS, self._frame)
        else:
            self._animating = False

    def _tick(self) -> None:
        if self._closed:
            return
        if not self._states:
            self._ticking = False
            return
        try:
            self._prune_dead()
            self._tick_n += 1
            sig = tuple(m.handle for m in self.target_monitors())
            icon_sig = self._icon_rect()
            if sig != self._targets_sig:
                self._targets_sig = sig
                self.reconcile()
            elif icon_sig != self._icon_sig:
                self.relayout()
            self._icon_sig = icon_sig
            phase = self._blink_phase()
            mono = time.monotonic()
            cx, cy = _cursor()
            for state in list(self._states.values()):
                if not state.seen:
                    for w in state.wins:
                        w.set_blink(phase)
                        if w.shown_xy and not w.removing and _inside(cx, cy, w):
                            self.hover(state.card.id)
                            break
                    if not state.seen and sounds.is_enabled() and mono - state.last_sound >= SOUND_REPEAT_S:
                        self._notify(state)
                for w in state.wins:
                    w.refresh_timer()
        except Exception:
            log.exception("cards tick failed")
        self.root.after(TICK_MS, self._tick)

    def debug_state(self) -> list[dict]:
        self.root.update_idletasks()
        mons = monitors.list_monitors()
        index = {m.handle: i for i, m in enumerate(mons)}
        out = []
        for state in sorted(self._states.values(), key=lambda s: s.sort_key):
            for w in state.wins:
                try:
                    rect = list(win.window_rect(w.hwnd))
                except Exception:
                    rect = list(w.shown_xy or (0, 0)) + [0, 0]
                style = win.get_exstyle(w.hwnd)
                out.append({
                    "id": state.card.id,
                    "kind": state.card.kind,
                    "colour": gfx.kind_colour(state.card.kind),
                    "title": state.card.title,
                    "priority": state.card.effective_priority,
                    "rect": rect,
                    "hidden": w.hidden,
                    "target_rect": [int(w.tx), int(w.ty), int(w.tx) + w.w, int(w.ty) + w.h],
                    "monitor": index.get(w.monitor.handle, -1),
                    "hwnd": w.hwnd,
                    "rounded": w.rounded,
                    "corner_preference": win.corner_preference(w.hwnd),
                    "seen": state.seen,
                    "blinking": not state.seen,
                    "timer": w.timer_str if state.card.kind == "waiting" else None,
                    "buttons": [t for t in w.hits],
                    "exstyle_flags": win.describe_exstyle(style),
                })
        return out

    def close(self) -> None:
        self._closed = True
        for state in self._states.values():
            for w in state.wins:
                w.destroy()
        for w in self._dying:
            w.destroy()
        for strip in self._strips.values():
            strip.destroy()
        self._strips.clear()
        self._states.clear()
        self._dying.clear()


def _dir(w: CardWindow, right_side: Optional[bool] = None) -> int:
    """+1 when the card sits at the right of its monitor (slides in from the right)."""
    if right_side is None:
        l, _t, r, _b = w.monitor.work
        right_side = w.tx + w.w / 2 >= (l + r) / 2
    return 1 if right_side else -1


def _cursor() -> tuple[int, int]:
    import win32api
    try:
        return win32api.GetCursorPos()
    except Exception:
        return -1, -1


def _inside(px_: int, py_: int, w: CardWindow) -> bool:
    x, y = w.shown_xy
    return x <= px_ < x + w.w and y <= py_ < y + w.h


class _FakeStore:
    """Stand-in for the card store in the demo: applies the callbacks locally."""

    def __init__(self, ui_getter: Callable[[], "CardsUI"], root: tk.Misc) -> None:
        self._ui = ui_getter
        self._root = root
        self.cards: dict[str, Card] = {}

    def callbacks(self) -> CardCallbacks:
        def seen(cid: str) -> None:
            print(f"on_seen {cid}", flush=True)
            card = self.cards[cid]
            card.seen_at = time.time()
            if not card.effective_sticky:
                self._root.after(20000, lambda: self._ui().remove(cid))

        def snooze(cid: str, minutes: int) -> None:
            print(f"on_snooze {cid} {minutes}", flush=True)
            self._ui().remove(cid)

        def dismiss(cid: str) -> None:
            print(f"on_dismiss {cid}", flush=True)
            self._ui().remove(cid)

        def press(cid: str, aid: str) -> None:
            print(f"on_press {cid} {aid}", flush=True)

        def open_url(url: str) -> None:
            print(f"on_open_url {url}", flush=True)

        return CardCallbacks(seen, snooze, dismiss, press, open_url)


class _DemoSettings:
    def __init__(self, every_monitor: bool) -> None:
        self.value = every_monitor

    def get(self, key: str, default: Any = None) -> Any:
        return self.value if key == SETTING_EVERY_MONITOR else default


def _demo(argv: list[str]) -> int:
    root = icon_mod.create_root()
    uibus.start(root)
    if "--quiet" in argv:
        sounds.set_enabled(False)
    holder: dict[str, CardsUI] = {}
    fake = _FakeStore(lambda: holder["ui"], root)
    ic = icon_mod.Icon(root, icon_mod.IconActions(quit=root.destroy))
    settings = _DemoSettings("--single-monitor" not in argv)
    ui = CardsUI(root, ic, fake.callbacks(), settings)
    holder["ui"] = ui
    now = time.time()
    first_p5 = "--p5" in argv
    samples = [
        Card(id="demo-waiting", kind="waiting", title="my-project is waiting for you",
             detail="Permission needed: run python upload.py?", timer_started_at=now - 5,
             actions=[{"id": "allow", "label": "Allow"}, {"id": "deny", "label": "Deny"}],
             priority=5 if first_p5 else None),
        Card(id="demo-done", kind="done", title="Nightly build finished",
             detail="Task completed in 6m 12s, 125 tests passing", url="https://example.com/build/42"),
        Card(id="demo-meeting", kind="meeting", title="Standup starts in 5 minutes",
             detail="10:00-10:15, Room 4B"),
        Card(id="demo-reminder", kind="reminder", title="Call the dentist"),
        Card(id="demo-custom", kind="custom", title="Backup completed", detail="3 files changed"),
    ]
    delay = 0
    for c in samples:
        c.created_at = now
        fake.cards[c.id] = c
        root.after(delay, lambda c=c: ui.show(c))
        delay += 400

    if "--dump" in argv:
        def dump() -> None:
            print(json.dumps(ui.debug_state(), ensure_ascii=False), flush=True)
            root.after(1000, dump)
        root.after(2500, dump)
    if "--exit-after" in argv:
        root.after(int(float(argv[argv.index("--exit-after") + 1]) * 1000), root.destroy)
    print(json.dumps({"icon": ic.debug_state()["icon"]}), flush=True)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(_demo(sys.argv[1:]))
