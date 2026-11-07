import hmac
import json
import logging
from typing import Callable
from urllib.parse import urlsplit

log = logging.getLogger("assistant.server.auth")

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


class AuthMiddleware:
    """Host/Origin check (403) then bearer token check (401) on every route."""

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
        if not bearer_matches(headers.get("authorization", ""), self._token_provider()):
            await self._reject(scope, send, 401, "unauthorized", "A valid bearer token is required.")
            return
        await self.app(scope, receive, send)

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
