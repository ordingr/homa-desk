import tkinter as tk

from . import gfx

PANEL_BG = gfx.PANEL_BG
PANEL_BG_BLINK = gfx.PANEL_BG_BLINK
TEXT = gfx.PANEL_FG
TEXT_DIM = gfx.PANEL_DIM_CARD
LINK = gfx.PANEL_LINK
BUTTON_BG = gfx.BUTTON_BG
BUTTON_BG_HOVER = gfx.BUTTON_BG_HOVER


def rounded_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list[float]:
    """Polygon points for a rounded rectangle, to be drawn with smooth=True."""
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def rounded_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, r: float,
                 tag: str = "panel", **kw) -> int:
    return canvas.create_polygon(rounded_points(x1, y1, x2, y2, r), smooth=True, tags=tag, **kw)


def draw_panel(canvas: tk.Canvas, w: int, h: int, accent: str, scale: float, blink_on: bool) -> None:
    """Card body: kind-coloured rounded frame around a dark rounded fill, plus an accent
    stripe on the left. Items are tagged 'panel' and lowered beneath the text."""
    canvas.delete("panel")
    r = 12 * scale
    border = max(2, round(3 * scale))
    rounded_rect(canvas, 1, 1, w - 1, h - 1, r, fill=accent, outline=accent)
    rounded_rect(canvas, 1 + border, 1 + border, w - 1 - border, h - 1 - border, max(2, r - border),
                 fill=PANEL_BG_BLINK if blink_on else PANEL_BG, outline="")
    stripe = round(6 * scale)
    canvas.create_rectangle(1 + border + round(8 * scale), 1 + border + round(14 * scale),
                            1 + border + round(8 * scale) + stripe, h - 1 - border - round(14 * scale),
                            fill=accent, outline=accent, tags="panel")
    canvas.tag_lower("panel")


def draw_button(canvas: tk.Canvas, x: float, y: float, w: float, h: float, label: str,
                font: tuple, tag: str, scale: float, accent: str = "") -> None:
    rounded_rect(canvas, x, y, x + w, y + h, 6 * scale, tag=(tag, "btn"),
                 fill=BUTTON_BG, outline=accent or BUTTON_BG)
    canvas.create_text(x + w / 2, y + h / 2, text=label, fill=TEXT, font=font, tags=(tag, "btn"))


def set_button_hover(canvas: tk.Canvas, tag: str, hover: bool) -> None:
    for item in canvas.find_withtag(tag):
        if canvas.type(item) == "polygon":
            canvas.itemconfigure(item, fill=BUTTON_BG_HOVER if hover else BUTTON_BG)


def kind_accent(kind: str) -> str:
    return gfx.kind_colour(kind)
