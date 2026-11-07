from __future__ import annotations

import hashlib
import logging
import math
import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional, Protocol
from urllib.parse import urlsplit

from ..cards.model import MAX_DETAIL, MAX_TITLE, MAX_URL, Card, CardValidationError, strip_control
from ..cards.store import CardNotFound

log = logging.getLogger("assistant.calendar.meetings")

SETTING_KEY = "calendars"
DEFAULT_LEAD_MINUTES = 5
DEFAULT_POLL_MINUTES = 5
MAX_LEAD_MINUTES = 120
MAX_POLL_MINUTES = 60
HORIZON_S = 24 * 3600
STALE_AFTER_S = 3600.0
DEDUPE_TOLERANCE_S = 60.0
SOURCE_DEADLINE_S = 15.0
MAX_LOCATION_SHOWN = 80

JOIN_DOMAINS = ("zoom.us", "zoom.com", "zoomgov.com", "teams.microsoft.com", "teams.live.com",
                "meet.google.com", "webex.com")
_JOIN_RE = re.compile(
    r"https?://(?:[A-Za-z0-9-]+\.)*(?:" + "|".join(re.escape(d) for d in JOIN_DOMAINS) + r")"
    r"(?![A-Za-z0-9.-])[^\s<>\"'\\^`{}|\[\]]*",
    re.IGNORECASE)
_JOIN_LABELS = (("zoom", "Zoom"), ("teams", "Teams"), ("meet.google", "Meet"), ("webex", "Webex"))


class SourceError(Exception):
    """A calendar source could not be read. The message never contains the source URL."""


@dataclass(frozen=True)
class Event:
    uid: str
    subject: str
    start: datetime
    end: datetime
    location: Optional[str] = None
    join_url: Optional[str] = None
    all_day: bool = False
    declined: bool = False
    source_label: str = ""


class MeetingSource(Protocol):
    label: str

    def fetch(self, window_start: datetime, window_end: datetime) -> list: ...


def find_join_url(*texts: Optional[str]) -> Optional[str]:
    """First http(s) URL on a Zoom, Teams, Meet or Webex host across the given texts, in order."""
    for text in texts:
        if not text:
            continue
        for m in _JOIN_RE.finditer(text):
            url = m.group(0).rstrip(".,;:!?)")
            if len(url) <= MAX_URL and _is_join_url(url):
                return url
    return None


def _is_join_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        return (parts.scheme.lower() == "https" and parts.username is None and parts.password is None
                and parts.port in (None, 443)
                and any(host == d or host.endswith("." + d) for d in JOIN_DOMAINS))
    except ValueError:
        return False


def join_label(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    low = url.lower()
    for needle, label in _JOIN_LABELS:
        if needle in low:
            return label
    return "Join"


def redact_url(url: str) -> str:
    """Scheme and host only; the path and query of a calendar URL are credentials."""
    m = re.match(r"([A-Za-z][A-Za-z0-9+.-]*)://([^/?#@]*@)?([^/?#:]*)", url or "")
    return f"{m.group(1)}://{m.group(3)}/..." if m else "(invalid url)"


class _SourceState:
    def __init__(self, source: Any):
        self.source = source
        self.label: str = str(getattr(source, "label", "") or "calendar")
        self.kind: str = str(getattr(source, "kind", "") or "calendar")
        self.host: Optional[str] = getattr(source, "host", None)
        self.last_ok: Optional[float] = None
        self.last_error: Optional[str] = None
        self.error_since: Optional[float] = None
        self.events: list = []
        self.worker: Optional[threading.Thread] = None


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    return min(max(float(value), lo), hi)


def _norm_subject(subject: str) -> str:
    return " ".join(subject.lower().split())


def _norm_uid(uid: str) -> str:
    return "" if uid.startswith("nouid-") else uid.strip().lower()


class MeetingScheduler:
    """Polls calendar sources, de-duplicates events across them and raises one blue
    ``meeting`` card per event at start minus the lead time.

    Settings read: ``calendars.lead_minutes`` (0-120, default 5) and ``calendars.poll_minutes``
    (1-60, default 5); both apply live through ``settings.on_change("calendars", ...)``.
    """

    def __init__(self, sources, store, scheduler, settings, clock: Callable[[], float] = time.time):
        self._store = store
        self._scheduler = scheduler
        self._settings = settings
        self._clock = clock
        self._lock = threading.RLock()
        self._poll_gate = threading.Lock()
        self._states: list = [_SourceState(s) for s in sources]
        self._groups: dict = {}
        self._fires: dict = {}
        self._poll_handle = None
        self._started = False
        self._applied = (self.lead_minutes, self.poll_minutes)
        on_change = getattr(settings, "on_change", None)
        if on_change is not None:
            on_change(SETTING_KEY, self._on_setting)

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._schedule_polling()

    def stop(self) -> None:
        with self._lock:
            self._started = False
            if self._poll_handle is not None:
                self._poll_handle.cancel()
                self._poll_handle = None
            for _, handle in self._fires.values():
                handle.cancel()
            self._fires.clear()

    def set_sources(self, sources) -> None:
        """Replace the source list (settings changed); state of sources kept by identity is dropped."""
        with self._lock:
            self._states = [_SourceState(s) for s in sources]
            self._groups = {}
        self.poll_async()

    def _cal_setting(self, name: str, default: float, lo: float, hi: float) -> float:
        section = self._settings.get(SETTING_KEY) or {}
        return _num(section.get(name, default) if isinstance(section, dict) else default, default, lo, hi)

    @property
    def lead_minutes(self) -> float:
        return self._cal_setting("lead_minutes", DEFAULT_LEAD_MINUTES, 0, MAX_LEAD_MINUTES)

    @property
    def poll_minutes(self) -> float:
        return self._cal_setting("poll_minutes", DEFAULT_POLL_MINUTES, 1, MAX_POLL_MINUTES)

    def status(self) -> dict:
        """Per-source state for /v1/health: ``{"calendars": [{label, kind, host, last_ok, last_error, events}]}``."""
        with self._lock:
            return {"calendars": [
                {"label": st.label, "kind": st.kind, "host": st.host, "last_ok": st.last_ok,
                 "last_error": st.last_error, "events": len(st.events)}
                for st in self._states]}

    def poll_async(self) -> None:
        threading.Thread(target=self.poll, name="meeting-poll", daemon=True).start()

    def poll(self) -> None:
        """Fetch every source on its own thread; one that has not answered within SOURCE_DEADLINE_S is
        recorded as timed out and abandoned, so it cannot hold back the others."""
        if not self._poll_gate.acquire(blocking=False):
            return
        try:
            results: queue.Queue = queue.Queue()
            waiting = []
            for st in list(self._states):
                if st.worker is not None and st.worker.is_alive():
                    self._record(st, None, "timed out", self._clock())
                    continue
                st.worker = threading.Thread(target=self._fetch_into, args=(st, results),
                                             name="meeting-fetch", daemon=True)
                st.worker.start()
                waiting.append(st)
            deadline = time.monotonic() + SOURCE_DEADLINE_S
            while waiting:
                try:
                    st, events, err = results.get(timeout=max(0.0, deadline - time.monotonic()))
                except queue.Empty:
                    break
                waiting.remove(st)
                self._record(st, events, err, self._clock())
                with self._lock:
                    self._sync()
            for st in waiting:
                self._record(st, None, "timed out", self._clock())
            with self._lock:
                self._sync()
        except Exception:
            log.exception("meeting poll failed")
        finally:
            self._poll_gate.release()

    def _fetch_into(self, st: _SourceState, results: queue.Queue) -> None:
        from datetime import timedelta, timezone
        start = datetime.fromtimestamp(self._clock(), timezone.utc)
        try:
            events, err = st.source.fetch(start, start + timedelta(seconds=HORIZON_S)), None
        except Exception as exc:
            events, err = None, _safe_error(exc)
        results.put((st, events, err))

    def _record(self, st: _SourceState, events, err: Optional[str], now: float) -> None:
        with self._lock:
            if err is None:
                st.events = list(events or [])
                st.last_ok = now
                st.last_error = None
                st.error_since = None
            else:
                st.last_error = err
                if st.error_since is None:
                    st.error_since = now
                log.warning("calendar source %r (%s) failed: %s", st.label, st.host or st.kind, err)
        self._check_persistent_failure(st, now)

    def _check_persistent_failure(self, st: _SourceState, now: float) -> None:
        card_id = "calendar-error-" + hashlib.sha1(st.label.encode("utf-8")).hexdigest()[:12]
        try:
            if st.last_error is None:
                try:
                    if self._store.get(card_id).dismissed_at is None:
                        self._store.dismiss(card_id)
                except CardNotFound:
                    pass
                return
            since = st.last_ok if st.last_ok is not None else st.error_since
            if since is None or now - since <= STALE_AFTER_S:
                return
            try:
                self._store.get(card_id)
                return
            except CardNotFound:
                pass
            self._store.upsert(Card(
                id=card_id, kind="custom",
                title=_clip(strip_control(f"Calendar not updating: {st.label}"), MAX_TITLE),
                detail=_clip(strip_control(f"Last error: {st.last_error}. No meeting cards come from this calendar until it recovers."), MAX_DETAIL)),
                "calendar")
        except CardValidationError:
            log.exception("could not raise the calendar failure card")

    def _usable_events(self, now: float) -> list:
        out = []
        for st in self._states:
            if st.last_ok is None or now - st.last_ok > STALE_AFTER_S:
                continue
            for ev in st.events:
                out.append((st, ev))
        return out

    def _sync(self) -> None:
        now = self._clock()
        lead = self.lead_minutes * 60.0
        groups = self._group(self._usable_events(now), now)
        self._groups = groups
        wanted = {}
        for key, g in groups.items():
            fire_at = g["start"] - lead
            if g["start"] <= now:
                continue
            if fire_at <= now:
                self._emit(key, g)
            else:
                wanted[key] = fire_at
        for key in list(self._fires):
            fire_at, handle = self._fires[key]
            if key not in wanted or abs(wanted[key] - fire_at) > 0.5:
                handle.cancel()
                del self._fires[key]
        for key, fire_at in wanted.items():
            if key not in self._fires:
                self._fires[key] = (fire_at, self._scheduler.at(fire_at, lambda k=key: self._fire(k)))

    def _fire(self, key: str) -> None:
        with self._lock:
            self._fires.pop(key, None)
            now = self._clock()
            g = self._groups.get(key)
            if g is None or g["start"] <= now:
                return
            if not any(st.last_ok is not None and now - st.last_ok <= STALE_AFTER_S for st in g["states"]):
                return
            self._emit(key, g)

    def _group(self, pairs: list, now: float) -> dict:
        items = []
        for st, ev in pairs:
            if ev.all_day or ev.declined or ev.end.timestamp() <= now:
                continue
            items.append((st, ev, ev.start.timestamp()))
        items.sort(key=lambda t: t[2])
        groups: list = []
        for st, ev, ts in items:
            norm = _norm_subject(ev.subject)
            uid = _norm_uid(ev.uid)
            for g in groups:
                if abs(g["start"] - ts) <= DEDUPE_TOLERANCE_S and (g["norm"] == norm or (uid and uid in g["uids"])):
                    g["events"].append(ev)
                    g["states"].append(st)
                    g["uids"].add(uid)
                    break
            else:
                groups.append({"norm": norm, "start": ts, "events": [ev], "states": [st], "uids": {uid}})
        out = {}
        for g in groups:
            first = g["events"][0]
            g["event"] = first
            g["location"] = next((e.location for e in g["events"] if e.location), None)
            g["join_url"] = next((e.join_url for e in g["events"] if e.join_url), None)
            g["end"] = max(e.end.timestamp() for e in g["events"])
            digest = hashlib.sha1(f"{g['norm']}|{int(g['start'] // 60)}".encode("utf-8")).hexdigest()[:16]
            out["meeting-" + digest] = g
        return out

    def _build_card(self, key: str, g: dict) -> Card:
        ev = g["event"]
        n = int(self.lead_minutes)
        subject = strip_control(ev.subject).strip() or "(no title)"
        if n <= 0:
            suffix = " starts now"
        else:
            suffix = f" starts in {n} minute{'' if n == 1 else 's'}"
        title = _clip(subject, MAX_TITLE - len(suffix)) + suffix
        start = datetime.fromtimestamp(g["start"])
        end = datetime.fromtimestamp(g["end"])
        parts = [f"{start:%H:%M}\u2013{end:%H:%M}"]
        loc = strip_control(g["location"] or "").strip()
        if loc and not (g["join_url"] and loc.startswith(("http://", "https://"))):
            parts.append(_clip(" ".join(loc.split()), MAX_LOCATION_SHOWN))
        label = join_label(g["join_url"])
        if label:
            parts.append(label)
        expires = g["end"] if g["end"] > g["start"] else g["start"] + 15 * 60
        return Card(id=key, kind="meeting", title=title, detail=_clip(" \u00b7 ".join(parts), MAX_DETAIL),
                    url=g["join_url"], expires_at=expires)

    def _emit(self, key: str, g: dict) -> None:
        card = self._build_card(key, g)
        try:
            existing = self._store.get(key)
        except CardNotFound:
            existing = None
        if existing is not None:
            if existing.dismissed_at is not None and existing.expires_at == card.expires_at:
                return
            if (existing.dismissed_at is None and existing.title == card.title and existing.detail == card.detail
                    and existing.url == card.url and existing.expires_at == card.expires_at):
                return
        try:
            self._store.upsert(card, "calendar", renotify=existing is not None and existing.expires_at != card.expires_at)
        except CardValidationError:
            log.exception("meeting card rejected")

    def _schedule_polling(self) -> None:
        if self._poll_handle is not None:
            self._poll_handle.cancel()
        self._poll_handle = self._scheduler.every(self.poll_minutes * 60.0, self.poll_async)
        self._scheduler.at(self._clock(), self.poll_async)

    def _on_setting(self, key: str, value: Any) -> None:
        with self._lock:
            if not self._started:
                return
            lead, poll = self.lead_minutes, self.poll_minutes
            old_lead, old_poll = self._applied
            self._applied = (lead, poll)
            if poll != old_poll:
                self._schedule_polling()
            if lead != old_lead:
                self._sync()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "\u2026"


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, SourceError):
        return str(exc)[:200]
    return type(exc).__name__
