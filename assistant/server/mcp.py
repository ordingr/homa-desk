import asyncio
import json
import logging
import threading
from datetime import datetime
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .. import paths
from ..cards.model import Card, CardValidationError
from ..cards.store import CardNotFound, CardStore
from ..reminders.store import ReminderStore
from .routes_cards import ApiError
from .routes_reminders import _parse_text

log = logging.getLogger("assistant.server.mcp")

SOURCE = "mcp"
SERVER_NAME = "assistant"
LOG_NAME = "mcp.log"
LOG_MAX_BYTES = 1_048_576
_LOGGED_METHODS = ("tools/call", "tools/list")
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

_log_lock = threading.Lock()


def _describe(value: Any) -> str:
    """Shape only, never content: a string is its length, a list its item count."""
    if isinstance(value, str):
        return f"str[{len(value)}]"
    if isinstance(value, (list, tuple, dict)):
        return f"{type(value).__name__}[{len(value)}]"
    return type(value).__name__


def _append_log(line: str) -> None:
    try:
        target = paths.logs_dir() / LOG_NAME
        with _log_lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if target.stat().st_size > LOG_MAX_BYTES:
                    target.replace(target.with_name(LOG_NAME + ".1"))
            except OSError:
                pass
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except OSError as exc:
        log.warning("could not write %s (%s)", LOG_NAME, type(exc).__name__)


async def _call_logger(ctx, call_next):
    """Server middleware: one mcp.log line per tools/call and tools/list, including calls that fail validation."""
    if ctx.method not in _LOGGED_METHODS:
        return await call_next(ctx)
    params = ctx.params or {}
    tool = str(params.get("name", "-"))[:64] if ctx.method == "tools/call" else "-"
    arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    shape = ",".join(f"{str(k)[:32]}={_describe(v)}" for k, v in arguments.items())
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        result = await call_next(ctx)
    except BaseException as exc:
        _append_log(f"{stamp} {ctx.method} tool={tool} args=[{shape}] error={type(exc).__name__}")
        raise
    payload = result.model_dump(by_alias=True) if hasattr(result, "model_dump") else (result or {})
    size = len(json.dumps(payload, default=str))
    outcome = "error" if payload.get("isError") else "ok"
    _append_log(f"{stamp} {ctx.method} tool={tool} args=[{shape}] result_bytes={size} {outcome}")
    return result


class _McpApp:
    """ASGI wrapper that owns the session manager's lifetime.

    The inner Starlette app only starts its session manager from its own lifespan, which never runs when
    it is mounted inside another app, so the first request starts it on the serving event loop.
    """

    def __init__(self, inner, manager) -> None:
        self._inner = inner
        self._manager = manager
        self._start_lock: Optional[asyncio.Lock] = None
        self._ready: Optional[asyncio.Event] = None
        self._stop: Optional[asyncio.Event] = None
        self._runner: Optional[asyncio.Task] = None

    async def _run_manager(self) -> None:
        try:
            async with self._manager.run():
                self._ready.set()
                await self._stop.wait()
        finally:
            self._ready.set()

    async def _ensure_started(self) -> None:
        if self._runner is None:
            self._start_lock = self._start_lock or asyncio.Lock()
        async with self._start_lock:
            if self._runner is None:
                self._ready, self._stop = asyncio.Event(), asyncio.Event()
                self._runner = asyncio.get_running_loop().create_task(self._run_manager())
        await self._ready.wait()

    async def aclose(self) -> None:
        if self._runner is not None:
            self._stop.set()
            await self._runner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return
        await self._ensure_started()
        await self._inner(scope, receive, send)


def build_mcp(store: CardStore, reminders: ReminderStore):
    """Returns (MCPServer, asgi_app). Mount the ASGI app at /mcp behind the auth middleware."""
    server = MCPServer(SERVER_NAME, middleware=[_call_logger])

    @server.tool(annotations=_WRITE)
    def send_card(title: str, detail: Optional[str] = None, kind: Optional[str] = None,
                  priority: Optional[int] = None, sticky: Optional[bool] = None,
                  url: Optional[str] = None, actions: Optional[list[dict[str, Any]]] = None,
                  id: Optional[str] = None) -> dict[str, str]:
        """Show a card in the Assistant overlay. kind is one of waiting, done, meeting, reminder, custom.
        priority is 1 to 5. actions is up to three objects with id and label. Passing the id of an open
        card replaces it. Returns the card id."""
        body = {"title": title, "detail": detail, "kind": kind, "priority": priority,
                "sticky": sticky, "url": url, "actions": actions, "id": id}
        try:
            card = Card.from_dict({k: v for k, v in body.items() if v is not None})
            saved = store.upsert(card, SOURCE)
        except CardValidationError as exc:
            raise ToolError(f"{exc.field}: {exc.message}" if exc.field else exc.message) from None
        return {"id": saved.id}

    @server.tool(annotations=_WRITE)
    def update_card(id: str, title: Optional[str] = None, detail: Optional[str] = None,
                    kind: Optional[str] = None, priority: Optional[int] = None,
                    sticky: Optional[bool] = None, url: Optional[str] = None,
                    actions: Optional[list[dict[str, Any]]] = None) -> dict[str, str]:
        """Change fields of an open card by id. Only the fields you pass change. Returns the card id."""
        fields = {k: v for k, v in (("title", title), ("detail", detail), ("kind", kind),
                                    ("priority", priority), ("sticky", sticky), ("url", url),
                                    ("actions", actions)) if v is not None}
        try:
            card = store.patch(id, **fields)
        except CardNotFound:
            raise ToolError("id: No card with that id.") from None
        except CardValidationError as exc:
            raise ToolError(f"{exc.field}: {exc.message}" if exc.field else exc.message) from None
        return {"id": card.id}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    def dismiss_card(id: str) -> dict[str, bool]:
        """Dismiss a card by id. Dismissing an already dismissed card is not an error."""
        try:
            store.dismiss(id)
        except CardNotFound:
            raise ToolError("id: No card with that id.") from None
        return {"ok": True}

    @server.tool(annotations=_WRITE)
    def add_reminder(text: str, sticky: bool = False) -> dict[str, str]:
        """Add a reminder from plain text such as 'call Sam in 10m', 'standup at 3pm' or
        'review tomorrow 9am'. Returns the reminder id and how the text was interpreted."""
        try:
            parsed = _parse_text(text, datetime.now())
        except ApiError as exc:
            raise ToolError(exc.message) from None
        reminder = reminders.add(parsed.title, parsed.at, parsed.detail, sticky)
        return {"id": reminder.id, "interpretation": parsed.interpretation}

    inner = server.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True)
    return server, _McpApp(inner, server._lowlevel_server._session_manager)
