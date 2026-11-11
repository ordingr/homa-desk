from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from .. import paths
from ..settings import CARD_INFO_SECONDS, CARD_WAITING_MINUTES
from .model import Card, CardValidationError, validate_fields

log = logging.getLogger("homa.cards.store")

FADE_SECONDS = 12.0
WAITING_MINUTES = 15
HISTORY_LIMIT = 100
STATE_VERSION = 1
MAX_SNOOZE_MINUTES = 7 * 24 * 60
MAX_OPEN_CARDS = 200

_CONTENT_FIELDS = ("kind", "priority", "sticky", "title", "detail", "url", "icon",
                   "actions", "timer_started_at", "expires_at")

Subscriber = Callable[[str, Optional[Card]], None]
Timing = Callable[[], dict]


class CardNotFound(KeyError):
    def __init__(self, card_id: str):
        super().__init__(card_id)
        self.card_id = card_id


class CardStore:
    """Open cards, snooze/dismiss/seen/mute semantics, persistence and per-card events.

    Time-based transitions (snooze return, fade after being seen, expires_at, timed-mute end)
    are applied lazily on every call using the injected clock; tick() applies them on demand
    and next_deadline() tells a scheduler when the next one is due.

    A seen card fades after info_seconds, or after waiting_minutes when it is sticky (0 keeps
    it until dismissed). `timing` returns {"info_seconds", "waiting_minutes"} and is read on
    every call, so changed values apply at once; without it the defaults apply.

    Subscribers are called as callback(event, card_copy_or_None) on the thread that
    triggered the change, after the store lock is released. Events: added, updated,
    seen, snoozed, snooze_ended, dismissed, expired, pressed, muted, unmuted.
    """

    def __init__(self, path: Optional[Path] = None, clock: Callable[[], float] = time.time,
                 timing: Optional[Timing] = None):
        self._path = Path(path) if path is not None else None
        self._clock = clock
        self._timing = timing
        self._lock = threading.RLock()
        self._cards: dict[str, Card] = {}
        self._events: dict[str, list] = {}
        self._mute_until: Optional[float] = None
        self._subscribers: list[Subscriber] = []
        self._pending: list = []
        self._load()

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else paths.home() / "state.json"

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def upsert(self, card: Card, source: str, renotify: bool = False) -> Card:
        with self._locked() as now:
            existing = self._cards.get(card.id) if card.id else None
            if existing is not None and existing.dismissed_at is None:
                for name in _CONTENT_FIELDS:
                    setattr(existing, name, _copy_value(getattr(card, name)))
                existing.source = source
                existing.renotify = bool(renotify or card.renotify)
                self._reshow(existing, existing.renotify)
                self._emit("updated", existing)
                return existing.copy()
            self._make_room(now)
            new = card.copy()
            if not new.id:
                new.id = uuid.uuid4().hex
            else:
                self._events.pop(new.id, None)
            new.source = source
            new.renotify = bool(renotify or card.renotify)
            new.created_at = now
            new.seen_at = None
            new.snoozed_until = None
            new.dismissed_at = None
            self._cards[new.id] = new
            self._events.setdefault(new.id, [])
            self._emit("added", new)
            return new.copy()

    def get(self, card_id: str) -> Card:
        with self._locked():
            return self._require(card_id, allow_dismissed=True).copy()

    def list(self, open_only: bool = True) -> list:
        """Open cards (visible, snoozed or queued by mute) oldest first; with
        open_only=False also recently dismissed history."""
        with self._locked():
            cards = [c for c in self._cards.values() if not open_only or c.dismissed_at is None]
            cards.sort(key=lambda c: (c.created_at or 0.0, c.id))
            return [c.copy() for c in cards]

    def visible(self) -> list:
        """Cards the overlay should currently show: open, not snoozed, not muted."""
        with self._locked() as now:
            if self._muted(now):
                return []
            cards = [c for c in self._cards.values() if c.dismissed_at is None and c.snoozed_until is None]
            cards.sort(key=lambda c: (c.created_at or 0.0, c.id))
            return [c.copy() for c in cards]

    def open_count(self) -> int:
        """Icon badge: open cards that are not snoozed and not dismissed (queued ones count)."""
        with self._locked():
            return sum(1 for c in self._cards.values()
                       if c.dismissed_at is None and c.snoozed_until is None)

    def patch(self, card_id: str, **fields) -> Card:
        with self._locked():
            card = self._require(card_id)
            clean = validate_fields(fields, require_title=False)
            if "id" in clean and clean["id"] != card.id:
                raise CardValidationError("id", "id cannot be changed.")
            renotify = clean.pop("renotify", False)
            clean.pop("id", None)
            for name, value in clean.items():
                setattr(card, name, value)
            card.renotify = renotify
            self._reshow(card, renotify)
            self._emit("updated", card)
            return card.copy()

    def snooze(self, card_id: str, minutes: float) -> Card:
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 1 <= minutes <= MAX_SNOOZE_MINUTES:
            raise CardValidationError("minutes", f"minutes must be a number from 1 to {MAX_SNOOZE_MINUTES}.")
        with self._locked() as now:
            card = self._require(card_id)
            card.snoozed_until = now + minutes * 60.0
            self._record(card_id, "snoozed")
            self._emit("snoozed", card)
            return card.copy()

    def dismiss(self, card_id: str) -> Card:
        with self._locked() as now:
            card = self._require(card_id, allow_dismissed=True)
            if card.dismissed_at is None:
                self._dismiss(card, now, "dismissed")
            return card.copy()

    def dismiss_all(self) -> int:
        """Dismiss every open card that is not snoozed; returns how many were dismissed."""
        with self._locked() as now:
            cards = [c for c in self._cards.values() if c.dismissed_at is None and c.snoozed_until is None]
            cards.sort(key=lambda c: (c.created_at or 0.0, c.id))
            for card in cards:
                self._dismiss(card, now, "dismissed")
            return len(cards)

    def mark_seen(self, card_id: str) -> Card:
        with self._locked() as now:
            card = self._require(card_id)
            if card.seen_at is None:
                card.seen_at = now
                self._record(card_id, "seen")
                self._emit("seen", card)
            return card.copy()

    def press(self, card_id: str, action_id: str) -> Card:
        with self._locked():
            card = self._require(card_id)
            if not any(a["id"] == action_id for a in card.actions):
                raise CardValidationError("action_id", "This card has no action with that id.",
                                          "unknown_action")
            self._record(card_id, "pressed", action_id=action_id)
            self._emit("pressed", card)
            return card.copy()

    def events_since(self, card_id: str, cursor: int = 0) -> list:
        """Events with seq > cursor, oldest first. Pass the last seq received as the next cursor."""
        with self._locked():
            self._require(card_id, allow_dismissed=True)
            return [dict(e) for e in self._events.get(card_id, []) if e["seq"] > cursor]

    def set_mute(self, until: Optional[float]) -> None:
        """None unmutes; a timestamp mutes until then; math.inf mutes until unmuted manually."""
        with self._locked() as now:
            was = self._muted(now)
            self._mute_until = until
            if until is not None and until != math.inf and until <= now:
                self._mute_until = None
            muted = self._muted(now)
            if muted != was or muted:
                self._emit("muted" if muted else "unmuted", None)

    def mute_until(self) -> Optional[float]:
        with self._locked() as now:
            return self._mute_until if self._muted(now) else None

    def is_muted(self) -> bool:
        with self._locked() as now:
            return self._muted(now)

    def queued_count(self) -> int:
        """Cards stored while muted that are not yet shown."""
        with self._locked() as now:
            if not self._muted(now):
                return 0
            return sum(1 for c in self._cards.values()
                       if c.dismissed_at is None and c.snoozed_until is None)

    def tick(self) -> None:
        with self._locked():
            pass

    def next_deadline(self) -> Optional[float]:
        """Earliest time at which a time-based transition will change store state."""
        with self._locked():
            fades = self._fades()
            times = []
            if self._mute_until is not None and self._mute_until != math.inf:
                times.append(self._mute_until)
            for c in self._cards.values():
                if c.dismissed_at is not None:
                    continue
                if c.snoozed_until is not None:
                    times.append(c.snoozed_until)
                if c.expires_at is not None:
                    times.append(c.expires_at)
                fade_at = self._fade_at(c, fades) if c.snoozed_until is None else None
                if fade_at is not None:
                    times.append(fade_at)
            return min(times) if times else None

    def _locked(self):
        return _Guard(self)

    def _fades(self) -> tuple:
        """(seconds a seen informational card stays, seconds a seen sticky card stays or None for ever)."""
        raw = {}
        if self._timing is not None:
            try:
                raw = self._timing() or {}
            except Exception:
                log.exception("could not read the card timing settings; using the defaults")
        if not isinstance(raw, dict):
            raw = {}
        info = _clamped(raw.get("info_seconds"), FADE_SECONDS, *CARD_INFO_SECONDS)
        minutes = _clamped(raw.get("waiting_minutes"), WAITING_MINUTES, *CARD_WAITING_MINUTES)
        return info, (minutes * 60.0 if minutes else None)

    @staticmethod
    def _fade_at(card: Card, fades: tuple) -> Optional[float]:
        if card.seen_at is None:
            return None
        span = fades[1] if card.effective_sticky else fades[0]
        return None if span is None else card.seen_at + span

    def _muted(self, now: float) -> bool:
        return self._mute_until is not None and (self._mute_until == math.inf or self._mute_until > now)

    def _require(self, card_id: str, allow_dismissed: bool = False) -> Card:
        card = self._cards.get(card_id)
        if card is None or (card.dismissed_at is not None and not allow_dismissed):
            raise CardNotFound(card_id)
        return card

    def _reshow(self, card: Card, renotify: bool) -> None:
        card.snoozed_until = None
        if renotify:
            card.seen_at = None

    def _record(self, card_id: str, type_: str, **extra) -> None:
        events = self._events.setdefault(card_id, [])
        seq = events[-1]["seq"] + 1 if events else 1
        entry = {"seq": seq, "at": self._clock(), "type": type_}
        entry.update(extra)
        events.append(entry)

    def _dismiss(self, card: Card, now: float, event: str) -> None:
        card.dismissed_at = now
        self._record(card.id, event)
        self._emit(event, card)
        history = sorted((c for c in self._cards.values() if c.dismissed_at is not None),
                         key=lambda c: c.dismissed_at)
        for old in history[:-HISTORY_LIMIT]:
            self._cards.pop(old.id, None)
            self._events.pop(old.id, None)

    def _make_room(self, now: float) -> None:
        """At MAX_OPEN_CARDS, dismiss the oldest non-sticky open card; if every open card is sticky, refuse."""
        open_cards = [c for c in self._cards.values() if c.dismissed_at is None]
        if len(open_cards) < MAX_OPEN_CARDS:
            return
        evictable = [c for c in open_cards if not c.effective_sticky]
        if not evictable:
            raise CardValidationError("", f"At most {MAX_OPEN_CARDS} cards can be open at once.", "too_many_cards")
        oldest = min(evictable, key=lambda c: (c.created_at or 0.0, c.id))
        log.warning("open-card limit reached; dismissing the oldest non-sticky card")
        self._dismiss(oldest, now, "dismissed")

    def _emit(self, event: str, card: Optional[Card]) -> None:
        self._pending.append((event, card.copy() if card is not None else None))

    def _sweep(self, now: float) -> None:
        fades = self._fades()
        if self._mute_until is not None and self._mute_until != math.inf and self._mute_until <= now:
            self._mute_until = None
            self._emit("unmuted", None)
        for card in list(self._cards.values()):
            if card.dismissed_at is not None:
                continue
            if card.snoozed_until is not None and card.snoozed_until <= now:
                card.snoozed_until = None
                card.seen_at = None
                self._emit("snooze_ended", card)
            if card.expires_at is not None and card.expires_at <= now:
                self._dismiss(card, now, "expired")
            elif card.snoozed_until is None:
                fade_at = self._fade_at(card, fades)
                if fade_at is not None and now >= fade_at:
                    self._dismiss(card, now, "expired")

    def _load(self) -> None:
        path = self.path
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError:
            log.exception("cannot read %s; starting empty", path)
            return
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("state.json root must be an object")
            cards = {}
            for item in data.get("cards", []):
                card = Card.from_dict(item, trusted=True)
                if card.id:
                    cards[card.id] = card
            events = {}
            for cid, evs in (data.get("events") or {}).items():
                if cid in cards and isinstance(evs, list):
                    events[cid] = [e for e in evs if isinstance(e, dict) and "seq" in e and "type" in e]
            mute = data.get("mute") or {}
            until = math.inf if mute.get("forever") else mute.get("until")
            if until is not None and not isinstance(until, (int, float)):
                until = None
        except (ValueError, TypeError, AttributeError, CardValidationError):
            log.exception("state.json is unreadable; keeping a copy and starting empty")
            try:
                os.replace(path, path.with_name(path.name + ".corrupt"))
            except OSError:
                log.exception("could not set aside the unreadable state file")
            return
        self._cards = cards
        self._events = events
        self._mute_until = until
        for cid in cards:
            self._events.setdefault(cid, [])

    def _save(self) -> None:
        path = self.path
        mute = {"until": None if self._mute_until == math.inf else self._mute_until,
                "forever": self._mute_until == math.inf}
        data = {
            "version": STATE_VERSION,
            "cards": [c.to_dict(resolved=False) for c in self._cards.values()],
            "events": {cid: evs for cid, evs in self._events.items() if cid in self._cards},
            "mute": mute,
        }
        tmp = path.with_name(path.name + ".tmp")
        try:
            text = json.dumps(data, indent=1, allow_nan=False)
        except (ValueError, TypeError):
            log.exception("state is not JSON-serialisable; %s was not written", path)
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            log.exception("could not write %s", path)


class _Guard:
    """Lock + lazy sweep on entry; persist and fan out notifications on exit."""

    def __init__(self, store: CardStore):
        self._s = store

    def __enter__(self) -> float:
        self._s._lock.acquire()
        now = self._s._clock()
        self._s._sweep(now)
        return now

    def __exit__(self, exc_type, exc, tb) -> bool:
        s = self._s
        pending, s._pending = s._pending, []
        subs = list(s._subscribers)
        if pending:
            s._save()
        s._lock.release()
        for event, card in pending:
            for cb in subs:
                try:
                    cb(event, card)
                except Exception:
                    log.exception("card subscriber failed on %s", event)
        return False


def _clamped(value, default: float, low: float, high: float) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or isinstance(value, float) and not math.isfinite(value)):
        return default
    return min(max(value, low), high)


def _copy_value(v):
    return [dict(a) for a in v] if isinstance(v, list) else v
