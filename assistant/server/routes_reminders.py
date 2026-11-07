import math
import time
from dataclasses import asdict
from datetime import datetime

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..cards.model import MAX_DETAIL, MAX_TITLE
from ..reminders import grammar
from .routes_cards import ApiError, read_json

REMINDER_BODY_MAX = 16_384
MAX_AHEAD_SECONDS = 366 * 24 * 3600


def _bad(message: str, field: str, code: str = "invalid_field") -> ApiError:
    return ApiError(422, code, message, field)


def _parse_text(text, now: datetime):
    try:
        parsed = grammar.parse(text, now)
    except grammar.DidntUnderstand as exc:
        raise _bad(exc.help_sentence, "text", "didnt_understand")
    if len(parsed.title) > MAX_TITLE:
        raise _bad(f"title must be at most {MAX_TITLE} characters.", "text")
    return parsed


def _parse_structured(body: dict, now: datetime):
    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        raise _bad("Provide text, or title and at.", "text", "missing_field")
    title = title.strip()
    if len(title) > MAX_TITLE:
        raise _bad(f"title must be at most {MAX_TITLE} characters.", "title")
    detail = body.get("detail", "")
    if detail is None:
        detail = ""
    if not isinstance(detail, str) or len(detail) > MAX_DETAIL:
        raise _bad(f"detail must be a string of at most {MAX_DETAIL} characters.", "detail")
    at = body.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at):
        raise _bad("at must be a timestamp (seconds since the epoch).", "at")
    if at > now.timestamp() + MAX_AHEAD_SECONDS:
        raise _bad("at is too far in the future.", "at")
    try:
        when = datetime.fromtimestamp(at)
    except (OverflowError, OSError, ValueError):
        raise _bad("at must be a timestamp (seconds since the epoch).", "at")
    return grammar.Parsed(title=title, detail=detail, at=float(at),
                          interpretation=grammar._result(title, when, now).interpretation)


async def add_reminder(request: Request) -> JSONResponse:
    body = await read_json(request, REMINDER_BODY_MAX)
    if not isinstance(body, dict):
        raise _bad("The request body must be a JSON object.", "", "invalid_body")
    sticky = body.get("sticky", False)
    if sticky is None:
        sticky = False
    if not isinstance(sticky, bool):
        raise _bad("sticky must be true or false.", "sticky")
    now = datetime.fromtimestamp(time.time())
    if "text" in body:
        text = body["text"]
        if not isinstance(text, str):
            raise _bad("text must be a string.", "text")
        parsed = _parse_text(text, now)
    else:
        parsed = _parse_structured(body, now)
    reminder = request.app.state.reminders.add(parsed.title, parsed.at, parsed.detail, sticky)
    return JSONResponse({"id": reminder.id, "title": reminder.title, "at": reminder.at,
                         "interpretation": parsed.interpretation}, status_code=201)


async def list_reminders(request: Request) -> JSONResponse:
    return JSONResponse([asdict(r) for r in request.app.state.reminders.list()])


async def cancel_reminder(request: Request) -> Response:
    if not request.app.state.reminders.cancel(request.path_params["reminder_id"]):
        raise ApiError(404, "not_found", "No pending reminder with that id.", "id")
    return Response(status_code=204)


routes = [
    Route("/v1/reminders", add_reminder, methods=["POST"]),
    Route("/v1/reminders", list_reminders, methods=["GET"]),
    Route("/v1/reminders/{reminder_id}", cancel_reminder, methods=["DELETE"]),
]
