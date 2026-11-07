from __future__ import annotations

import calendar as _pycal
import contextvars
import logging
import re
import urllib.error
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone, tzinfo
from typing import Iterable, Optional
from urllib.parse import urlsplit

from .meetings import Event, SourceError, find_join_url, redact_url

log = logging.getLogger("assistant.calendar.ics")

FETCH_TIMEOUT_S = 15.0
MAX_FEED_BYTES = 8 * 1024 * 1024
MAX_DAY_STEPS = 20000

_WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_WINDOWS_ZONES = {
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
    "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "Atlantic Standard Time": "America/Halifax",
    "GMT Standard Time": "Europe/London",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "Romance Standard Time": "Europe/Paris",
    "E. Europe Standard Time": "Europe/Chisinau",
    "India Standard Time": "Asia/Kolkata",
    "China Standard Time": "Asia/Shanghai",
    "Tokyo Standard Time": "Asia/Tokyo",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "UTC": "UTC",
}


class IcsSource:
    """A calendar published as an iCalendar URL. The URL is a credential: it is kept in a
    private attribute and never logged, repr'd or put in an error message (host only)."""

    kind = "ics"

    def __init__(self, url: str, label: str, user_emails: Iterable[str] = ()):
        url = (url or "").strip()
        if url.lower().startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            raise ValueError("calendar URL must be an http(s) address")
        self._url = url
        self.label = label
        self.host = parts.hostname
        self._user_emails = {e.strip().lower() for e in user_emails if e and e.strip()}

    @classmethod
    def from_url(cls, url: str, label: Optional[str] = None, user_emails: Iterable[str] = ()) -> "IcsSource":
        """Label defaults to the URL's host (never its path)."""
        host = urlsplit((url or "").strip().replace("webcal://", "https://", 1)).hostname or "calendar"
        return cls(url, label or host, user_emails)

    def __repr__(self) -> str:
        return f"IcsSource(label={self.label!r}, url={redact_url(self._url)!r})"

    def fetch(self, window_start: datetime, window_end: datetime) -> list:
        text = self._download()
        return parse_ics(text, window_start, window_end, self.label, self._user_emails)

    def _download(self) -> str:
        request = urllib.request.Request(self._url, headers={"User-Agent": "Assistant/1", "Accept": "text/calendar, */*"})
        try:
            with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as resp:
                raw = resp.read(MAX_FEED_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise SourceError(f"HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            raise SourceError(f"{type(reason).__name__}: {reason}"[:120] if isinstance(reason, OSError)
                              else "connection failed") from None
        except (OSError, ValueError) as exc:
            raise SourceError(type(exc).__name__) from None
        if len(raw) > MAX_FEED_BYTES:
            raise SourceError("feed too large")
        text = raw.decode("utf-8-sig", errors="replace")
        if "BEGIN:VCALENDAR" not in text.upper():
            raise SourceError("response is not an iCalendar feed")
        return text


def parse_ics(text: str, window_start: datetime, window_end: datetime, label: str = "",
              user_emails: Iterable[str] = ()) -> list:
    """Events overlapping [window_start, window_end), recurrences expanded, sorted by start."""
    token = _FEED_ZONES.set(_parse_vtimezones(text))
    try:
        return _parse_feed(text, window_start, window_end, label, user_emails)
    finally:
        _FEED_ZONES.reset(token)


def _parse_feed(text: str, window_start: datetime, window_end: datetime, label: str,
                user_emails: Iterable[str]) -> list:
    props_by_event, cal_props = _parse_components(text)
    me = {e.lower() for e in user_emails}
    for name, _params, value in cal_props:
        if name == "X-WR-CALNAME" and "@" in value:
            me.add(value.strip().lower())
    masters, overrides = [], {}
    for props in props_by_event:
        info = _event_info(props)
        if info is None:
            continue
        if info["recurrence_id"] is not None:
            overrides[(info["uid"], info["recurrence_id"].timestamp())] = info
        masters.append(info)
    out = []
    for info in masters:
        if info["recurrence_id"] is None and info["rrule"]:
            for occ_start, occ_end in _expand(info, window_start, window_end):
                if (info["uid"], occ_start.timestamp()) in overrides:
                    continue
                out.append(_make_event(info, occ_start, occ_end, label, me, recurring=True))
        else:
            start, end = info["start"], info["end"]
            if _overlaps(start, end, window_start, window_end):
                out.append(_make_event(info, start, end, label, me, recurring=False))
    out.sort(key=lambda e: (e.start, e.subject))
    return out


def _overlaps(start: datetime, end: datetime, ws: datetime, we: datetime) -> bool:
    return start < we and (end > ws or start >= ws)


def _make_event(info: dict, start: datetime, end: datetime, label: str, me: set, recurring: bool) -> Event:
    uid = info["uid"]
    if recurring:
        uid = f"{uid}#{start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"
    return Event(uid=uid, subject=info["subject"], start=start, end=end, location=info["location"],
                 join_url=info["join_url"], all_day=info["all_day"], declined=_declined(info, me),
                 source_label=label)


def _declined(info: dict, me: set) -> bool:
    if info["status"] == "CANCELLED":
        return True
    attendees = info["attendees"]
    if me:
        return any(addr in me and partstat == "DECLINED" for addr, partstat in attendees)
    return bool(attendees) and all(partstat == "DECLINED" for _addr, partstat in attendees)


def _unfold(text: str) -> list:
    lines: list = []
    for raw in re.split(r"\r\n|\n|\r", text):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _split_line(line: str):
    in_quote = False
    split_at = -1
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == ":" and not in_quote:
            split_at = i
            break
    if split_at < 0:
        return None
    head, value = line[:split_at], line[split_at + 1:]
    pieces, cur, in_quote = [], "", False
    for ch in head:
        if ch == '"':
            in_quote = not in_quote
            cur += ch
        elif ch == ";" and not in_quote:
            pieces.append(cur)
            cur = ""
        else:
            cur += ch
    pieces.append(cur)
    name = pieces[0].upper()
    params = {}
    for piece in pieces[1:]:
        k, _, v = piece.partition("=")
        params[k.upper()] = v.strip('"')
    return name, params, value


def _parse_components(text: str):
    events, current, cal_props, depth_other = [], None, [], 0
    for line in _unfold(text):
        parsed = _split_line(line)
        if parsed is None:
            continue
        name, params, value = parsed
        if name == "BEGIN":
            if value.upper() == "VEVENT" and current is None:
                current = []
            elif current is not None:
                depth_other += 1
            continue
        if name == "END":
            if value.upper() == "VEVENT" and current is not None and depth_other == 0:
                events.append(current)
                current = None
            elif current is not None and depth_other > 0:
                depth_other -= 1
            continue
        if current is not None:
            if depth_other == 0:
                current.append((name, params, value))
        else:
            cal_props.append((name, params, value))
    return events, cal_props


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


_FEED_ZONES: contextvars.ContextVar = contextvars.ContextVar("ics_feed_zones", default={})
_WARNED_TZIDS: set = set()


def _tz_for(tzid: Optional[str]):
    if not tzid:
        return None
    tzid = tzid.strip().strip('"')
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        ZoneInfo = None
    if ZoneInfo is not None:
        for candidate in (tzid, _WINDOWS_ZONES.get(tzid)):
            if candidate:
                try:
                    return ZoneInfo(candidate)
                except Exception:
                    continue
    zone = _FEED_ZONES.get().get(tzid)
    if zone is not None:
        return zone
    if tzid.upper() in ("UTC", "GMT"):
        return timezone.utc
    if tzid not in _WARNED_TZIDS:
        _WARNED_TZIDS.add(tzid)
        log.warning("calendar feed uses time zone %r which cannot be resolved; treating its times as UTC", tzid)
    return timezone.utc


def _parse_offset(value: str) -> Optional[timedelta]:
    m = re.fullmatch(r"([+-])(\d{2})(\d{2})(\d{2})?", value.strip())
    if not m:
        return None
    delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)), seconds=int(m.group(4) or 0))
    return -delta if m.group(1) == "-" else delta


class _FeedZone(tzinfo):
    """A time zone built from a feed's own VTIMEZONE block (STANDARD/DAYLIGHT observances)."""

    def __init__(self, tzid: str, observances: list):
        self._tzid = tzid
        self._observances = observances
        self._first_offset = min(observances, key=lambda o: o["start"])["offset_from"]

    def _transitions(self, year: int) -> list:
        out = []
        for o in self._observances:
            if o["rule"] is None:
                if o["start"].year == year:
                    out.append((o["start"], o["offset_to"]))
                continue
            month, nth, weekday, monthday, until_year = o["rule"]
            if year < o["start"].year or (until_year is not None and year > until_year):
                continue
            last = _pycal.monthrange(year, month)[1]
            if weekday is not None:
                first_wd = _pycal.weekday(year, month, 1)
                days = [d for d in range(1, last + 1) if (first_wd + d - 1) % 7 == weekday]
                try:
                    day = days[(nth - 1) if nth > 0 else nth]
                except IndexError:
                    continue
            else:
                day = min(monthday if monthday is not None else o["start"].day, last)
            out.append((datetime.combine(date(year, month, day), o["start"].time()), o["offset_to"]))
        return out

    def _offset_at_wall(self, wall: datetime) -> timedelta:
        best = None
        for year in (wall.year - 1, wall.year):
            for when, offset in self._transitions(year):
                if when <= wall and (best is None or when > best[0]):
                    best = (when, offset)
        return self._first_offset if best is None else best[1]

    def utcoffset(self, dt):
        return self._offset_at_wall(dt.replace(tzinfo=None))

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return self._tzid

    def fromutc(self, dt):
        naive = dt.replace(tzinfo=None)
        offsets = {o["offset_from"] for o in self._observances} | {o["offset_to"] for o in self._observances}
        for off in sorted(offsets, reverse=True):
            local = naive + off
            if self._offset_at_wall(local) == off:
                return local.replace(tzinfo=self)
        return (naive + self._first_offset).replace(tzinfo=self)


def _parse_vtimezones(text: str) -> dict:
    zones: dict = {}
    tzid, observances, current, in_zone = None, [], None, False
    for line in _unfold(text):
        parsed = _split_line(line)
        if parsed is None:
            continue
        name, _params, value = parsed
        value_u = value.strip().upper()
        if name == "BEGIN":
            if value_u == "VTIMEZONE":
                tzid, observances, in_zone = None, [], True
            elif in_zone and value_u in ("STANDARD", "DAYLIGHT"):
                current = {}
        elif name == "END":
            if value_u == "VTIMEZONE" and in_zone:
                in_zone = False
                if tzid and observances:
                    zones[tzid] = _FeedZone(tzid, observances)
            elif in_zone and value_u in ("STANDARD", "DAYLIGHT") and current is not None:
                obs = _observance(current)
                if obs is not None:
                    observances.append(obs)
                current = None
        elif in_zone and current is not None:
            current.setdefault(name, value)
        elif in_zone and name == "TZID":
            tzid = value.strip()
    return zones


def _observance(props: dict) -> Optional[dict]:
    offset_from = _parse_offset(props.get("TZOFFSETFROM", ""))
    offset_to = _parse_offset(props.get("TZOFFSETTO", ""))
    parsed = _parse_time(props["DTSTART"], {}) if "DTSTART" in props else None
    if offset_from is None or offset_to is None or parsed is None or parsed[2]:
        return None
    start = parsed[0]
    rule = None
    if "RRULE" in props:
        r = _parse_rrule(props["RRULE"])
        if r is None or r["FREQ"].upper() != "YEARLY":
            return None
        try:
            month = int(r.get("BYMONTH", start.month))
            nth, weekday, monthday = 1, None, None
            if r.get("BYDAY"):
                m = re.fullmatch(r"([+-]?\d+)?([A-Z]{2})", r["BYDAY"].split(",")[0].strip().upper())
                nth, weekday = int(m.group(1) or 1), _WEEKDAYS.index(m.group(2))
            elif r.get("BYMONTHDAY"):
                monthday = int(r["BYMONTHDAY"])
            until_year = None
            if r.get("UNTIL"):
                until = _parse_time(r["UNTIL"], {})
                until_year = until[0].year if until else None
        except (ValueError, AttributeError):
            return None
        rule = (month, nth, weekday, monthday, until_year)
    return {"start": start, "offset_from": offset_from, "offset_to": offset_to, "rule": rule}


def _localize(wall: datetime, tz) -> datetime:
    return wall.replace(tzinfo=tz) if tz is not None else wall.astimezone()


def _parse_time(value: str, params: dict):
    """-> (wall naive datetime, tz or None for system-local, all_day) or None."""
    value = value.strip()
    all_day = params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value) is not None
    try:
        if all_day:
            d = datetime.strptime(value[:8], "%Y%m%d")
            return d, None, True
        utc = value.endswith(("Z", "z"))
        wall = datetime.strptime(value.rstrip("Zz"), "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    if utc:
        return wall, timezone.utc, False
    return wall, _tz_for(params.get("TZID")), False


def _parse_duration(value: str) -> Optional[timedelta]:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    w, d, h, mi, s = (int(g or 0) for g in m.groups()[1:])
    return sign * timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def _event_info(props: list) -> Optional[dict]:
    first: dict = {}
    attendees, exdates, join_sources = [], [], {}
    for name, params, value in props:
        if name == "ATTENDEE":
            attendees.append((re.sub(r"^mailto:", "", value.strip(), flags=re.IGNORECASE).lower(),
                              params.get("PARTSTAT", "").upper()))
        elif name == "EXDATE":
            for part in value.split(","):
                exdates.append(_parse_time(part, params))
        elif name not in first:
            first[name] = (params, value)
    if "DTSTART" not in first:
        return None
    parsed = _parse_time(first["DTSTART"][1], first["DTSTART"][0])
    if parsed is None:
        return None
    wall, tz, all_day = parsed
    start = _localize(wall, tz)
    end = None
    end_wall = None
    if "DTEND" in first:
        p = _parse_time(first["DTEND"][1], first["DTEND"][0])
        if p is not None:
            end_wall, end_tz, _ = p
            end = _localize(end_wall, end_tz)
    if end is None and "DURATION" in first:
        dur = _parse_duration(first["DURATION"][1])
        if dur is not None:
            end_wall = wall + dur
            end = _localize(end_wall, tz)
    if end is None:
        end_wall = wall + (timedelta(days=1) if all_day else timedelta(0))
        end = _localize(end_wall, tz)
    if end < start:
        end = start
    recurrence_id = None
    if "RECURRENCE-ID" in first:
        rp = _parse_time(first["RECURRENCE-ID"][1], first["RECURRENCE-ID"][0])
        if rp is not None:
            recurrence_id = _localize(rp[0], rp[1])
    uid = first["UID"][1].strip() if "UID" in first else f"nouid-{start.isoformat()}"
    text = lambda k: _unescape(first[k][1]).strip() if k in first else None  # noqa: E731
    location, description = text("LOCATION"), text("DESCRIPTION")
    join_url = find_join_url(first["URL"][1].strip() if "URL" in first else None, location, description,
                             first["X-GOOGLE-CONFERENCE"][1].strip() if "X-GOOGLE-CONFERENCE" in first else None,
                             first["X-MICROSOFT-SKYPETEAMSMEETINGURL"][1].strip()
                             if "X-MICROSOFT-SKYPETEAMSMEETINGURL" in first else None)
    ex_instants = {_localize(p[0], p[1]).timestamp() for p in exdates if p is not None}
    return {
        "uid": uid, "subject": text("SUMMARY") or "(no title)", "location": location or None,
        "join_url": join_url, "all_day": all_day, "status": (text("STATUS") or "").upper(),
        "attendees": attendees, "start": start, "end": end, "wall": wall, "tz": tz,
        "end_wall": end_wall, "recurrence_id": recurrence_id, "exdates": ex_instants,
        "rrule": _parse_rrule(first["RRULE"][1]) if "RRULE" in first else None,
    }


def _parse_rrule(value: str) -> Optional[dict]:
    rule = {}
    for part in value.split(";"):
        k, _, v = part.partition("=")
        rule[k.strip().upper()] = v.strip()
    return rule if rule.get("FREQ") else None


def _until(value: str, tz) -> Optional[datetime]:
    parsed = _parse_time(value, {})
    if parsed is None:
        return None
    wall, utz, all_day = parsed
    if all_day:
        return _localize(wall + timedelta(days=1), tz) - timedelta(seconds=1)
    return _localize(wall, utz if utz is not None else tz)


def _expand(info: dict, ws: datetime, we: datetime):
    rule = info["rrule"]
    freq = rule["FREQ"].upper()
    if freq not in ("DAILY", "WEEKLY"):
        log.debug("RRULE FREQ=%s is not expanded; only the first occurrence is used", freq)
        if _overlaps(info["start"], info["end"], ws, we) and info["start"].timestamp() not in info["exdates"]:
            yield info["start"], info["end"]
        return
    try:
        interval = max(1, int(rule.get("INTERVAL", "1")))
        count = int(rule["COUNT"]) if "COUNT" in rule else None
    except ValueError:
        return
    until = _until(rule["UNTIL"], info["tz"]) if "UNTIL" in rule else None
    byday = {re.sub(r"[^A-Z]", "", d.upper()) for d in rule.get("BYDAY", "").split(",") if d}
    byday_idx = {_WEEKDAYS.index(d) for d in byday if d in _WEEKDAYS}
    wall, tz, all_day = info["wall"], info["tz"], info["all_day"]
    start0 = wall.date()
    if freq == "WEEKLY" and not byday_idx:
        byday_idx = {start0.weekday()}
    week0 = start0 - timedelta(days=start0.weekday())
    all_day_span = (info["end_wall"].date() - start0) if all_day else None
    duration = info["end"] - info["start"]
    last_day = we.astimezone(tz).date() + timedelta(days=1) if tz is not None else we.astimezone().date() + timedelta(days=1)
    produced = 0
    day = start0
    for _ in range(MAX_DAY_STEPS):
        if day > last_day:
            return
        n = (day - start0).days
        if freq == "DAILY":
            ok = n % interval == 0 and (not byday_idx or day.weekday() in byday_idx)
        else:
            ok = ((day - week0).days // 7) % interval == 0 and day.weekday() in byday_idx
        if ok:
            occ_start = _localize(datetime.combine(day, wall.time()), tz)
            produced += 1
            if count is not None and produced > count:
                return
            if until is not None and occ_start > until:
                return
            if occ_start >= we:
                return
            occ_end = (_localize(datetime.combine(day + all_day_span, dtime()), tz)
                       if all_day else occ_start + duration)
            if occ_start.timestamp() not in info["exdates"] and _overlaps(occ_start, occ_end, ws, we):
                yield occ_start, occ_end
        day += timedelta(days=1)
