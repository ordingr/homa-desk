from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Callable, Optional

from .model import MAX_DETAIL, MAX_TITLE, Card
from .store import CardNotFound, CardStore

log = logging.getLogger("assistant.cards.claude_sessions")

SOURCE = "hooks"
SNIPPET_CHARS = 160
MAX_SESSIONS = 256
FALLBACK_PROJECT = "Claude Code"

_CLEARING = frozenset({"PostToolUse", "PreToolUse", "UserPromptSubmit", "SessionStart", "SessionEnd"})
_WAIT_NOTIFICATIONS = {
    "permission_prompt": "Claude needs your permission",
    "idle_prompt": "Claude is waiting for your input",
    "elicitation_dialog": "Claude is asking you a question",
    "agent_needs_input": "Claude needs your input",
}
_CLEARING_NOTIFICATIONS = frozenset({"elicitation_complete", "elicitation_response", "quota_auto_resume_fired"})
_SUMMARY_KEYS = ("command", "file_path", "path", "url", "pattern", "query", "description", "prompt")
_ERROR_KEYS = ("error", "error_type", "error_class")


def _text(value, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _project_label(payload: dict) -> str:
    title = _text(payload.get("session_title"), MAX_TITLE)
    if title:
        return title
    cwd = payload.get("cwd")
    if isinstance(cwd, str):
        parts = [p for p in cwd.replace("\\", "/").split("/") if p.strip()]
        if parts:
            return _text(parts[-1], MAX_TITLE) or FALLBACK_PROJECT
    return FALLBACK_PROJECT


def _tool_summary(tool_input) -> str:
    if isinstance(tool_input, dict):
        for key in _SUMMARY_KEYS:
            text = _text(tool_input.get(key), 300)
            if text:
                return text
        try:
            return _text(json.dumps(tool_input, ensure_ascii=False, default=str), 300)
        except (TypeError, ValueError, RecursionError):
            return ""
    return _text(tool_input, 300)


def _elapsed(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


class ClaudeSessions:
    """Per-session card state machine for Claude Code hook events.

    One waiting card and one done card per session_id, addressed by deterministic card ids so a
    repeat event updates in place. Passive: handle() only reads the payload and changes cards.
    """

    def __init__(self, store: CardStore, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock
        self._lock = threading.Lock()
        self._prompt_at: "OrderedDict[str, float]" = OrderedDict()

    def handle(self, payload: dict) -> None:
        event = _text(payload.get("hook_event_name"), 80)
        if not event:
            return
        sid = _text(payload.get("session_id"), 100) or "unknown-session"
        with self._lock:
            now = self._clock()
            project = _project_label(payload)
            if event == "PermissionRequest":
                self._permission(sid, project, payload, now)
            elif event == "Notification":
                self._notification(sid, project, payload, now)
            elif event == "Stop":
                self._stop(sid, project, payload, now)
            elif event == "StopFailure":
                self._stop_failure(sid, project, payload, now)
            elif event in _CLEARING:
                if event == "UserPromptSubmit":
                    self._prompt_at[sid] = now
                    self._prompt_at.move_to_end(sid)
                    while len(self._prompt_at) > MAX_SESSIONS:
                        self._prompt_at.popitem(last=False)
                elif event == "SessionEnd":
                    self._prompt_at.pop(sid, None)
                self._clear(self._wait_id(sid))
            else:
                if self._open(self._wait_id(sid)) is None:
                    self._show_wait(sid, project, f"Waiting on {event} (unrecognised event)", now)

    @staticmethod
    def _wait_id(sid: str) -> str:
        return "cc-wait-" + sid

    @staticmethod
    def _done_id(sid: str) -> str:
        return "cc-done-" + sid

    def _open(self, card_id: str) -> Optional[Card]:
        try:
            card = self._store.get(card_id)
        except CardNotFound:
            return None
        return card if card.dismissed_at is None else None

    def _clear(self, card_id: str) -> None:
        if self._open(card_id) is not None:
            self._store.dismiss(card_id)

    def _show_wait(self, sid: str, project: str, detail: str, now: float) -> None:
        card = Card(id=self._wait_id(sid), kind="waiting", title=project,
                    detail=detail[:MAX_DETAIL], timer_started_at=now)
        self._store.upsert(card, SOURCE, renotify=True)

    def _permission(self, sid: str, project: str, payload: dict, now: float) -> None:
        tool = _text(payload.get("tool_name"), 60) or "a tool"
        summary = _tool_summary(payload.get("tool_input"))
        detail = f"Permission needed: {tool}" + (f" — {summary}" if summary else "")
        self._show_wait(sid, project, detail, now)

    def _notification(self, sid: str, project: str, payload: dict, now: float) -> None:
        ntype = _text(payload.get("notification_type"), 60)
        wid = self._wait_id(sid)
        if ntype in _CLEARING_NOTIFICATIONS:
            self._clear(wid)
            return
        if ntype not in _WAIT_NOTIFICATIONS:
            return
        message = _text(payload.get("message"), 300) or _WAIT_NOTIFICATIONS[ntype]
        existing = self._open(wid)
        if existing is not None:
            if ntype != "permission_prompt":
                self._store.patch(wid, detail=message)
            return
        if ntype == "idle_prompt" and self._open(self._done_id(sid)) is not None:
            return
        self._show_wait(sid, project, message, now)

    def _stop(self, sid: str, project: str, payload: dict, now: float) -> None:
        self._clear(self._wait_id(sid))
        started = self._prompt_at.pop(sid, None)
        paused = bool(payload.get("background_tasks")) or bool(payload.get("session_crons"))
        message = payload.get("last_assistant_message")
        snippet = _text(message[:SNIPPET_CHARS], SNIPPET_CHARS) if isinstance(message, str) else ""
        lines = []
        if paused:
            lines.append("Paused — background work running"
                         + (f" (after {_elapsed(now - started)})" if started is not None else ""))
        elif started is not None:
            lines.append(f"Task completed in {_elapsed(now - started)}")
        elif not snippet:
            lines.append("Task finished")
        if snippet:
            lines.append(snippet)
        card = Card(id=self._done_id(sid), kind="done", title=project,
                    detail="\n".join(lines)[:MAX_DETAIL], timer_started_at=now)
        self._store.upsert(card, SOURCE, renotify=True)

    def _stop_failure(self, sid: str, project: str, payload: dict, now: float) -> None:
        label = ""
        for key in _ERROR_KEYS:
            label = _text(payload.get(key), 80).replace("_", " ")
            if label:
                break
        self._show_wait(sid, project, f"Claude stopped: {label or 'API error'}", now)
