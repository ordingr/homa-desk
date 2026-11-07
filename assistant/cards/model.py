from __future__ import annotations

import copy
import logging
import math
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("assistant.cards.model")

KINDS = ("waiting", "done", "meeting", "reminder", "custom")

DEFAULT_PRIORITY = {"waiting": 4, "meeting": 4, "done": 2, "reminder": 3, "custom": 3}
DEFAULT_STICKY = {"waiting": True, "meeting": True, "done": False, "reminder": False, "custom": False}

MAX_TITLE = 120
MAX_DETAIL = 500
MAX_ACTIONS = 3
MAX_URL = 2048
MAX_ICON = 256
MAX_ACTION_ID = 64
MAX_ACTION_LABEL = 40
MAX_ACTION_SHORTCUT = 16
MAX_AHEAD_SECONDS = 366 * 24 * 3600
MAX_UNKNOWN_LOGGED = 100

_INPUT_FIELDS = ("id", "kind", "priority", "sticky", "title", "detail", "url", "icon",
                 "actions", "timer_started_at", "expires_at", "renotify")
_HOST_FIELDS = ("source", "created_at", "seen_at", "snoozed_until", "dismissed_at")
_KNOWN = frozenset(_INPUT_FIELDS) | frozenset(_HOST_FIELDS)

_logged_unknown: set = set()


class CardValidationError(ValueError):
    def __init__(self, field_name: str, message: str, code: str = "invalid_field"):
        super().__init__(message)
        self.field = field_name
        self.message = message
        self.code = code

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "field": self.field}}


@dataclass
class Card:
    title: str
    id: Optional[str] = None
    kind: str = "custom"
    priority: Optional[int] = None
    sticky: Optional[bool] = None
    detail: Optional[str] = None
    url: Optional[str] = None
    icon: Optional[str] = None
    actions: list = field(default_factory=list)
    source: Optional[str] = None
    timer_started_at: Optional[float] = None
    expires_at: Optional[float] = None
    renotify: bool = False
    created_at: Optional[float] = None
    seen_at: Optional[float] = None
    snoozed_until: Optional[float] = None
    dismissed_at: Optional[float] = None

    @property
    def effective_priority(self) -> int:
        return self.priority if self.priority is not None else DEFAULT_PRIORITY[self.kind]

    @property
    def effective_sticky(self) -> bool:
        return self.sticky if self.sticky is not None else DEFAULT_STICKY[self.kind]

    def copy(self) -> "Card":
        return copy.deepcopy(self)

    def to_dict(self, resolved: bool = True) -> dict:
        d = {
            "id": self.id,
            "kind": self.kind,
            "priority": self.effective_priority if resolved else self.priority,
            "sticky": self.effective_sticky if resolved else self.sticky,
            "title": self.title,
            "detail": self.detail,
            "url": self.url,
            "icon": self.icon,
            "actions": copy.deepcopy(self.actions),
            "source": self.source,
            "timer_started_at": self.timer_started_at,
            "expires_at": self.expires_at,
            "renotify": self.renotify,
            "created_at": self.created_at,
            "seen_at": self.seen_at,
            "snoozed_until": self.snoozed_until,
            "dismissed_at": self.dismissed_at,
        }
        return d

    @classmethod
    def from_dict(cls, data: Any, *, trusted: bool = False) -> "Card":
        """Validate an incoming card body. Host-set fields are ignored unless
        trusted (state.json reload). Unknown fields are ignored and logged once."""
        if not isinstance(data, dict):
            raise CardValidationError("", "A card must be a JSON object.", "invalid_body")
        for key in data:
            if key not in _KNOWN and key not in _logged_unknown:
                if len(_logged_unknown) == MAX_UNKNOWN_LOGGED:
                    _logged_unknown.add(None)
                    log.warning("further unknown card fields are ignored without logging")
                if len(_logged_unknown) > MAX_UNKNOWN_LOGGED:
                    break
                _logged_unknown.add(key)
                log.warning("ignoring unknown card field %r", key[:100] if isinstance(key, str) else key)
        kw = validate_fields({k: data[k] for k in _INPUT_FIELDS if k in data}, require_title=True)
        card = cls(**kw)
        if trusted:
            card.source = _opt(data, "source", str)
            for name in ("created_at", "seen_at", "snoozed_until", "dismissed_at"):
                setattr(card, name, _opt_number(data, name))
            if card.snoozed_until is not None:
                card.snoozed_until = min(card.snoozed_until, time.time() + MAX_AHEAD_SECONDS)
        return card


def _opt(data: dict, name: str, typ: type):
    v = data.get(name)
    return v if isinstance(v, typ) else None


def _opt_number(data: dict, name: str):
    v = data.get(name)
    return float(v) if _is_number(v) and math.isfinite(v) else None


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def strip_control(text: str) -> str:
    """Drop Unicode Cc/Cf characters (BEL, ESC, zero-width, bidi overrides); newline and tab are kept."""
    return "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf"))


def validate_fields(data: dict, *, require_title: bool) -> dict:
    """Validate a (possibly partial) set of input fields; returns normalised kwargs
    containing only the keys present. Raises CardValidationError(field=...)."""
    out: dict = {}

    def bad(name: str, msg: str):
        raise CardValidationError(name, msg)

    if "title" in data or require_title:
        t = data.get("title")
        if isinstance(t, str):
            t = strip_control(t)
        if not isinstance(t, str) or not t.strip():
            raise CardValidationError("title", "title is required and must be a non-empty string.",
                                      "missing_field" if t is None else "invalid_field")
        if len(t) > MAX_TITLE:
            bad("title", f"title must be at most {MAX_TITLE} characters.")
        out["title"] = t

    if "id" in data and data["id"] is not None:
        if not isinstance(data["id"], str) or not data["id"].strip() or len(data["id"]) > 200:
            bad("id", "id must be a non-empty string of at most 200 characters.")
        out["id"] = data["id"]

    if "kind" in data and data["kind"] is not None:
        if data["kind"] not in KINDS:
            bad("kind", "kind must be one of: " + ", ".join(KINDS) + ".")
        out["kind"] = data["kind"]

    if "priority" in data and data["priority"] is not None:
        p = data["priority"]
        if isinstance(p, bool) or not isinstance(p, int) or not 1 <= p <= 5:
            bad("priority", "priority must be an integer from 1 to 5.")
        out["priority"] = p

    for name in ("sticky", "renotify"):
        if name in data and data[name] is not None:
            if not isinstance(data[name], bool):
                bad(name, f"{name} must be true or false.")
            out[name] = data[name]

    if "detail" in data and data["detail"] is not None:
        d = data["detail"]
        if not isinstance(d, str):
            bad("detail", "detail must be a string.")
        d = strip_control(d)
        if len(d) > MAX_DETAIL:
            bad("detail", f"detail must be at most {MAX_DETAIL} characters.")
        out["detail"] = d

    for name, limit in (("url", MAX_URL), ("icon", MAX_ICON)):
        if name in data and data[name] is not None:
            if not isinstance(data[name], str):
                bad(name, f"{name} must be a string.")
            if len(data[name]) > limit:
                bad(name, f"{name} must be at most {limit} characters.")
            out[name] = data[name]

    for name in ("timer_started_at", "expires_at"):
        if name in data and data[name] is not None:
            if not _is_number(data[name]):
                bad(name, f"{name} must be a timestamp (seconds since the epoch).")
            if name == "expires_at" and not data[name] <= time.time() + MAX_AHEAD_SECONDS:
                bad(name, "expires_at is too far in the future.")
            out[name] = float(data[name])

    if "actions" in data and data["actions"] is not None:
        out["actions"] = _validate_actions(data["actions"])

    return out


def _validate_actions(actions: Any) -> list:
    if not isinstance(actions, list):
        raise CardValidationError("actions", "actions must be a list.")
    if len(actions) > MAX_ACTIONS:
        raise CardValidationError("actions", f"at most {MAX_ACTIONS} actions are allowed.")
    clean = []
    seen = set()
    for i, a in enumerate(actions):
        if not isinstance(a, dict):
            raise CardValidationError("actions", f"actions[{i}] must be an object with id and label.")
        aid, label, shortcut = a.get("id"), a.get("label"), a.get("shortcut")
        if not isinstance(aid, str) or not aid:
            raise CardValidationError("actions", f"actions[{i}].id must be a non-empty string.")
        if not isinstance(label, str) or not label:
            raise CardValidationError("actions", f"actions[{i}].label must be a non-empty string.")
        if shortcut is not None and not isinstance(shortcut, str):
            raise CardValidationError("actions", f"actions[{i}].shortcut must be a string.")
        for part, value, limit in (("id", aid, MAX_ACTION_ID), ("label", label, MAX_ACTION_LABEL),
                                   ("shortcut", shortcut, MAX_ACTION_SHORTCUT)):
            if value is not None and len(value) > limit:
                raise CardValidationError("actions", f"actions[{i}].{part} must be at most {limit} characters.")
        label = strip_control(label)
        if not label.strip():
            raise CardValidationError("actions", f"actions[{i}].label must be a non-empty string.")
        if aid in seen:
            raise CardValidationError("actions", f"actions[{i}].id duplicates an earlier action.")
        seen.add(aid)
        item = {"id": aid, "label": label}
        if shortcut is not None:
            item["shortcut"] = shortcut
        clean.append(item)
    return clean
