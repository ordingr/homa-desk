"""GET /v1/settings: a read-only, safe subset of the settings for the full page. Calendar addresses, tokens,
index roots and excludes, notes folder and update source are never part of it."""
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..settings import DEFAULT_SHORTCUTS, DEFAULTS, THEMES
from .routes_cards import ApiError, read_json
from .routes_search import known_exts


THEME_BODY_MAX = 256


def _subset(settings) -> dict:
    shortcuts = dict(DEFAULT_SHORTCUTS)
    shortcuts.update(settings.get("shortcuts") or {})
    md_vault = settings.get("md_vault") or {}
    return {
        "theme": settings.get("theme", DEFAULTS["theme"]),
        "shortcuts": shortcuts,
        "search_placement": settings.get("search_placement", DEFAULTS["search_placement"]),
        "hotkey": settings.get("hotkey", DEFAULTS["hotkey"]),
        "md_vault": {"url": md_vault.get("url", "")},
        "index": {"known_exts": sorted(known_exts(settings))},
    }


async def get_settings(request: Request) -> JSONResponse:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise ApiError(503, "settings_unavailable", "Settings are not ready yet.")
    return JSONResponse(_subset(settings))


async def post_theme(request: Request) -> JSONResponse:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise ApiError(503, "settings_unavailable", "Settings are not ready yet.")
    body = await read_json(request, THEME_BODY_MAX)
    theme = body.get("theme") if isinstance(body, dict) else None
    if not isinstance(theme, str) or theme not in THEMES:
        raise ApiError(422, "invalid_parameter", "theme must be dark, light or system.", "theme")
    settings.set("theme", theme)
    return JSONResponse(_subset(settings))


routes = [Route("/v1/settings", get_settings, methods=["GET"]),
          Route("/v1/settings/theme", post_theme, methods=["POST"])]
