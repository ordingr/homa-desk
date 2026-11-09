import hmac
import json
import logging
import secrets
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

log = logging.getLogger("homa.server.auth")

_ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


def _host_only(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):
        return value.split("]", 1)[0] + "]"
    return value.rsplit(":", 1)[0] if ":" in value else value


def host_allowed(host_header: str) -> bool:
    return bool(host_header) and _host_only(host_header) in _ALLOWED_HOSTS


def origin_allowed(origin_header: str) -> bool:
    try:
        host = urlsplit(origin_header).hostname
    except ValueError:
        return False
    return host is not None and host.lower() in _ALLOWED_HOSTS


def bearer_matches(authorization: str, expected: str) -> bool:
    scheme, _, presented = authorization.partition(" ")
    if scheme.lower() != "bearer" or not presented.strip():
        return False
    return hmac.compare_digest(presented.strip().encode("utf-8"), expected.encode("utf-8"))


NONCE_TTL_S = 30.0
NONCE_MAX_OUTSTANDING = 16
EXCHANGE_PATH = "/v1/session/exchange"
EXCHANGE_BODY_MAX = 1024

_clock = time.monotonic
_nonces: dict = {}
_nonce_lock = threading.Lock()


def mint_nonce() -> str:
    """A one-shot launch nonce (SEC-2, A12): valid for NONCE_TTL_S seconds, redeemable once."""
    nonce = secrets.token_urlsafe(32)
    now = _clock()
    with _nonce_lock:
        for stale in [n for n, exp in _nonces.items() if exp <= now]:
            del _nonces[stale]
        while len(_nonces) >= NONCE_MAX_OUTSTANDING:
            del _nonces[next(iter(_nonces))]
        _nonces[nonce] = now + NONCE_TTL_S
    return nonce


def consume_nonce(candidate: object) -> bool:
    if not isinstance(candidate, str) or not candidate or len(candidate) > 256:
        return False
    now = _clock()
    found = None
    with _nonce_lock:
        for stale in [n for n, exp in _nonces.items() if exp <= now]:
            del _nonces[stale]
        for nonce in _nonces:
            if hmac.compare_digest(nonce.encode("utf-8"), candidate.encode("utf-8", "replace")):
                found = nonce
        if found is not None:
            del _nonces[found]
    return found is not None


def static_path_exempt(path: str, method: str) -> bool:
    """The page and its assets load without a bearer (a script tag cannot send one); /v1 never does."""
    if method not in ("GET", "HEAD") or "\\" in path or ".." in path.split("/"):
        return False
    if path == "/favicon.ico":
        return True
    return path == "/app" or path.startswith("/app/")


class AuthMiddleware:
    """Host/Origin check (403) then bearer token check (401) on every route.

    Exempt from the bearer check: GET/HEAD of /app/ (the page), and POST /v1/session/exchange, which trades a
    launch nonce for the token. Both still pass the Host and Origin checks."""

    def __init__(self, app, token_provider: Callable[[], str]) -> None:
        self.app = app
        self._token_provider = token_provider

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if not host_allowed(headers.get("host", "")):
            await self._reject(scope, send, 403, "forbidden_host", "The Host header must be 127.0.0.1 or localhost.")
            return
        if "origin" in headers and not origin_allowed(headers["origin"]):
            await self._reject(scope, send, 403, "forbidden_origin", "The Origin header is not allowed.")
            return
        if scope["type"] == "http":
            path, method = scope.get("path", ""), scope.get("method", "")
            if static_path_exempt(path, method):
                await self.app(scope, receive, send)
                return
            if path == EXCHANGE_PATH and method == "POST":
                await self._exchange(scope, receive, send)
                return
        if not bearer_matches(headers.get("authorization", ""), self._token_provider()):
            await self._reject(scope, send, 401, "unauthorized", "A valid bearer token is required.")
            return
        await self.app(scope, receive, send)

    async def _exchange(self, scope, receive, send) -> None:
        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            body += message.get("body", b"")
            if len(body) > EXCHANGE_BODY_MAX:
                await self._reject(scope, send, 413, "payload_too_large", "payload too large")
                return
            if not message.get("more_body"):
                break
        try:
            nonce = json.loads(body.decode("utf-8")).get("nonce")
        except (ValueError, AttributeError):
            nonce = None
        if not consume_nonce(nonce):
            await self._reject(scope, send, 403, "invalid_nonce", "The launch code is missing, used or expired.")
            return
        out = json.dumps({"token": self._token_provider()}).encode("utf-8")
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(out)).encode("ascii")),
                                (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": out})

    async def _reject(self, scope, send, status: int, code: str, message: str) -> None:
        log.warning("rejected %s %r with %d (%s)", scope["type"], scope.get("path", "")[:200], status, code)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"error": {"code": code, "message": message, "field": None}}).encode("utf-8")
        response_headers = [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii"))]
        if status == 401:
            response_headers.append((b"www-authenticate", b"Bearer"))
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": body})
