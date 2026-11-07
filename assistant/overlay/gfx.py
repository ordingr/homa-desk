import re
import tkinter as tk
from pathlib import Path
from typing import Optional

from . import theme

AMBER = "#F5A623"
GREEN = "#2E9E5B"
BLUE = "#2F7DD1"
PURPLE = "#8E5BD6"
RED = "#E5352B"
GREY = "#7A7A7A"
WHITE = "#FFFFFF"
CHROMA_KEY = "#010203"
ASSETS = Path(__file__).resolve().parent.parent / "assets"

KIND_COLOURS = {
    "waiting": AMBER,
    "done": GREEN,
    "meeting": BLUE,
    "reminder": PURPLE,
    "custom": PURPLE,
}


def kind_colour(kind: str) -> str:
    return KIND_COLOURS.get(kind, PURPLE)


def px(value: float, scale: float) -> int:
    return max(1, int(round(value * scale)))


def load_png(prefix: str, px: float) -> Optional[tuple[tk.PhotoImage, int]]:
    """Pre-rendered `<prefix>_<size>.png` from assets/ whose size is nearest to px, with that size; None if absent."""
    best: Optional[tuple[float, int, Path]] = None
    for path in ASSETS.glob(f"{prefix}_*.png"):
        m = re.fullmatch(re.escape(prefix) + r"_(\d+)\.png", path.name)
        if m and (best is None or abs(int(m.group(1)) - px) < best[0]):
            best = (abs(int(m.group(1)) - px), int(m.group(1)), path)
    if best is None:
        return None
    try:
        return tk.PhotoImage(file=str(best[2])), best[1]
    except tk.TclError:
        return None


def crop_centre(image: tk.PhotoImage, fraction: float) -> tk.PhotoImage:
    w, h = image.width(), image.height()
    mx, my = int(w * (1 - fraction) / 2), int(h * (1 - fraction) / 2)
    return image.copy(from_coords=(mx, my, w - mx, h - my))


def draw_bell(canvas: tk.Canvas, cx: float, cy: float, size: float, colour: str, tag: str = "bell") -> None:
    """Simple bell glyph built from canvas primitives; size is the glyph height."""
    w = size * 0.78
    top = cy - size / 2
    body_bottom = cy + size * 0.28
    canvas.create_oval(cx - w * 0.30, top, cx + w * 0.30, top + size * 0.30,
                       fill=colour, outline=colour, tags=tag)
    canvas.create_polygon(
        cx - w * 0.26, top + size * 0.18,
        cx + w * 0.26, top + size * 0.18,
        cx + w * 0.46, body_bottom,
        cx - w * 0.46, body_bottom,
        fill=colour, outline=colour, smooth=False, tags=tag)
    canvas.create_rectangle(cx - w * 0.52, body_bottom - size * 0.04,
                            cx + w * 0.52, body_bottom + size * 0.08,
                            fill=colour, outline=colour, tags=tag)
    canvas.create_oval(cx - w * 0.12, body_bottom + size * 0.06,
                       cx + w * 0.12, body_bottom + size * 0.26,
                       fill=colour, outline=colour, tags=tag)


def draw_cross(canvas: tk.Canvas, cx: float, cy: float, size: float, colour: str,
               width: float, tag: str = "bell") -> None:
    half = size * 0.55
    canvas.create_line(cx - half, cy + half, cx + half, cy - half,
                       fill=colour, width=width, capstyle="round", tags=tag)


def draw_badge(canvas: tk.Canvas, cx: float, cy: float, radius: float, count: int,
               font_family: str = "Segoe UI") -> None:
    text = "99+" if count > 99 else str(count)
    r = radius * (1.25 if len(text) > 1 else 1.0)
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=RED, outline=WHITE,
                       width=max(1, int(radius / 8)), tags="badge")
    canvas.create_text(cx, cy, text=text, fill=WHITE,
                       font=(font_family, -int(radius * (1.15 if len(text) == 1 else 0.95)), "bold"),
                       tags="badge")


def __getattr__(name: str) -> str:
    """PANEL_BG, PANEL_FG, INPUT_BG, BUTTON_BG and the other theme-dependent names resolve to the active palette."""
    if name in theme.NAMES:
        return theme.colour(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
