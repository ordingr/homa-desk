import tkinter as tk

from . import gfx

_THEMED = {"PANEL_BG": "PANEL_BG", "PANEL_BG_BLINK": "PANEL_BG_BLINK", "TEXT": "PANEL_FG", "TEXT_DIM": "PANEL_DIM_CARD",
           "LINK": "PANEL_LINK", "BUTTON_BG": "BUTTON_BG", "BUTTON_BG_HOVER": "BUTTON_BG_HOVER"}


def __getattr__(name: str) -> str:
    if name in _THEMED:
        return getattr(gfx, _THEMED[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


DWM_RADIUS = 8


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


def draw_panel(canvas: tk.Canvas, w: int, h: int, accent: str, scale: float, blink_on: bool,
               rounded: bool = False) -> None:
    """Card body: kind-coloured rounded frame around a dark rounded fill, plus an accent
    stripe on the left. Items are tagged 'panel' and lowered beneath the text.
    With `rounded` the window edge is already rounded by DWM: the canvas itself is the accent frame."""
    canvas.delete("panel")
    fill = gfx.PANEL_BG_BLINK if blink_on else gfx.PANEL_BG
    if rounded:
        border = max(2, round(2 * scale))
        canvas.configure(bg=accent)
        rounded_rect(canvas, border, border, w - border, h - border, max(2, DWM_RADIUS * scale - border),
                     fill=fill, outline="")
        off = border
    else:
        r = 12 * scale
        border = max(2, round(3 * scale))
        rounded_rect(canvas, 1, 1, w - 1, h - 1, r, fill=accent, outline=accent)
        rounded_rect(canvas, 1 + border, 1 + border, w - 1 - border, h - 1 - border, max(2, r - border),
                     fill=fill, outline="")
        off = 1 + border
    stripe = round(6 * scale)
    rounded_rect(canvas, off + round(8 * scale), off + round(14 * scale),
                 off + round(8 * scale) + stripe, h - off - round(14 * scale), stripe / 2,
                 fill=accent, outline=accent)
    canvas.tag_lower("panel")


def draw_button(canvas: tk.Canvas, x: float, y: float, w: float, h: float, label: str,
                font: tuple, tag: str, scale: float, accent: str = "") -> None:
    rounded_rect(canvas, x, y, x + w, y + h, h / 2, tag=(tag, "btn"),
                 fill=gfx.BUTTON_BG, outline=accent or gfx.BUTTON_BG)
    canvas.create_text(x + w / 2, y + h / 2, text=label, fill=gfx.PANEL_FG, font=font, tags=(tag, "btn"))


def set_button_hover(canvas: tk.Canvas, tag: str, hover: bool) -> None:
    for item in canvas.find_withtag(tag):
        if canvas.type(item) == "polygon":
            canvas.itemconfigure(item, fill=gfx.BUTTON_BG_HOVER if hover else gfx.BUTTON_BG)


def kind_accent(kind: str) -> str:
    return gfx.kind_colour(kind)
