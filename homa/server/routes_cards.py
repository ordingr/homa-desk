import json
import logging
import math
from typing import Any, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..cards.model import _INPUT_FIELDS, Card, CardValidationError
from ..cards.store import CardNotFound

log = logging.getLogger("homa.server.routes_cards")

SOURCE = "http"
CARD_BODY_MAX = 65_536
SMALL_BODY_MAX = 4_096


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, field: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "field": self.field or None}}


class PayloadTooLarge(Exception):
    pass


def payload_too_large_response() -> JSONResponse:
    return JSONResponse(ApiError(413, "payload_too_large", "payload too large").to_dict(), status_code=413, headers={"connection": "close"})


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("number out of range")
    return value


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def parse_json(raw: bytes) -> Any:
    """Strict JSON: NaN, Infinity and overflowing floats are rejected so nothing non-finite is ever stored."""
    return json.loads(raw, parse_float=_finite_float, parse_constant=_reject_constant)


async def read_json(request: Request, limit: Optional[int]) -> Any:
    """Read and parse the body; an empty body is {}. `limit` may only be lower than the server-wide cap."""
    if limit is not None:
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            log.warning("rejected %r with 413 (route body cap)", request.url.path[:200])
            raise PayloadTooLarge
    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if limit is not None and total > limit:
            log.warning("rejected %r with 413 (route body cap)", request.url.path[:200])
            raise PayloadTooLarge
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw.strip():
        return {}
    try:
        return parse_json(raw)
    except (ValueError, RecursionError):
        raise ApiError(400, "invalid_json", "The request body is not valid JSON.")


def _object(body: Any) -> dict:
    if not isinstance(body, dict):
        raise CardValidationError("", "The request body must be a JSON object.", "invalid_body")
    return body


def _is_open(store, card_id: str) -> bool:
    try:
        return store.get(card_id).dismissed_at is None
    except CardNotFound:
        return False


async def create_card(request: Request) -> JSONResponse:
    body = await read_json(request, CARD_BODY_MAX)
    card = Card.from_dict(body)
    store = request.app.state.store
    existed = card.id is not None and _is_open(store, card.id)
    saved = store.upsert(card, SOURCE)
    return JSONResponse(saved.to_dict(), status_code=200 if existed else 201)


async def list_cards(request: Request) -> JSONResponse:
    kind = request.query_params.get("kind")
    source = request.query_params.get("source")
    cards = request.app.state.store.list()
    if kind is not None:
        cards = [c for c in cards if c.kind == kind]
    if source is not None:
        cards = [c for c in cards if c.source == source]
    return JSONResponse([c.to_dict() for c in cards])


async def get_card(request: Request) -> JSONResponse:
    return JSONResponse(request.app.state.store.get(request.path_params["card_id"]).to_dict())


async def patch_card(request: Request) -> JSONResponse:
    body = _object(await read_json(request, CARD_BODY_MAX))
    fields = {k: body[k] for k in _INPUT_FIELDS if k in body}
    card = request.app.state.store.patch(request.path_params["card_id"], **fields)
    return JSONResponse(card.to_dict())


async def snooze_card(request: Request) -> JSONResponse:
    body = _object(await read_json(request, SMALL_BODY_MAX))
    card = request.app.state.store.snooze(request.path_params["card_id"], body.get("minutes"))
    return JSONResponse(card.to_dict())


async def dismiss_card(request: Request) -> Response:
    request.app.state.store.dismiss(request.path_params["card_id"])
    return Response(status_code=204)


async def dismiss_all_cards(request: Request) -> JSONResponse:
    return JSONResponse({"dismissed": request.app.state.store.dismiss_all()})


async def card_events(request: Request) -> JSONResponse:
    raw = request.query_params.get("since", "0")
    try:
        since = int(raw)
    except ValueError:
        raise CardValidationError("since", "since must be an integer cursor.")
    return JSONResponse(request.app.state.store.events_since(request.path_params["card_id"], since))


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(exc.to_dict(), status_code=exc.status)


async def validation_error_handler(request: Request, exc: CardValidationError) -> JSONResponse:
    return JSONResponse(exc.to_dict(), status_code=422)


async def not_found_handler(request: Request, exc: CardNotFound) -> JSONResponse:
    return JSONResponse(ApiError(404, "not_found", "No card with that id.", "id").to_dict(), status_code=404)


async def too_large_handler(request: Request, exc: PayloadTooLarge) -> JSONResponse:
    return payload_too_large_response()


async def http_404_handler(request: Request, exc) -> JSONResponse:
    return JSONResponse(ApiError(404, "not_found", "No such route.").to_dict(), status_code=404)


async def http_405_handler(request: Request, exc) -> JSONResponse:
    return JSONResponse(ApiError(405, "method_not_allowed", "That method is not allowed on this route.").to_dict(),
                        status_code=405, headers=getattr(exc, "headers", None))


exception_handlers = {
    404: http_404_handler,
    405: http_405_handler,
    ApiError: api_error_handler,
    CardValidationError: validation_error_handler,
    CardNotFound: not_found_handler,
    PayloadTooLarge: too_large_handler,
}

routes = [
    Route("/v1/cards", create_card, methods=["POST"]),
    Route("/v1/cards", list_cards, methods=["GET"]),
    Route("/v1/cards/dismiss_all", dismiss_all_cards, methods=["POST"]),
    Route("/v1/cards/{card_id}", get_card, methods=["GET"]),
    Route("/v1/cards/{card_id}", patch_card, methods=["PATCH"]),
    Route("/v1/cards/{card_id}", dismiss_card, methods=["DELETE"]),
    Route("/v1/cards/{card_id}/snooze", snooze_card, methods=["POST"]),
    Route("/v1/cards/{card_id}/events", card_events, methods=["GET"]),
]
