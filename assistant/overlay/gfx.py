import tkinter as tk

AMBER = "#F5A623"
GREEN = "#2E9E5B"
BLUE = "#2F7DD1"
PURPLE = "#8E5BD6"
RED = "#E5352B"
GREY = "#7A7A7A"
WHITE = "#FFFFFF"
CHROMA_KEY = "#010203"

PANEL_BG = "#22252B"
PANEL_FG = "#F2F2F2"
PANEL_DIM = "#9AA0A6"
PANEL_ERR = "#F28B82"
PANEL_OK = "#A8D5A2"
INPUT_BG = "#2F333A"
INPUT_BORDER = "#444A53"
PANEL_BG_BLINK = "#3A3F48"
PANEL_DIM_CARD = "#A9AFB8"
PANEL_LINK = "#7FB6FF"
BUTTON_BG = "#343942"
BUTTON_BG_HOVER = "#4A505B"

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
