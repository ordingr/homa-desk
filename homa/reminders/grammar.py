import re
from dataclasses import dataclass
from datetime import datetime, timedelta

HELP_SENTENCE = "didn't understand — try 'text in 10m' or 'text at 3pm'"

_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

_TIME = r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?"
_IN = re.compile(r"^(?P<title>.+?)\s+in\s+(?P<n>\d+)\s*(?P<u>m|h|d)$", re.I)
_AT = re.compile(rf"^(?P<title>.+?)\s+at\s+{_TIME}$", re.I)
_TOMORROW = re.compile(rf"^(?P<title>.+?)\s+tomorrow\s+{_TIME}$", re.I)
_WEEKDAY = re.compile(rf"^(?P<title>.+?)\s+(?P<day>[a-z]+)\s+{_TIME}$", re.I)

_MAX_UNITS = {"m": 60 * 24 * 366, "h": 24 * 366, "d": 366}


class DidntUnderstand(ValueError):
    def __init__(self, help_sentence: str = HELP_SENTENCE) -> None:
        super().__init__(help_sentence)
        self.help_sentence = help_sentence


@dataclass(frozen=True)
class Parsed:
    title: str
    detail: str
    at: float
    interpretation: str


def _clock(m: "re.Match[str]") -> tuple[int, int]:
    hour = int(m.group("h"))
    minute = int(m.group("m") or 0)
    ap = (m.group("ap") or "").lower()
    if minute > 59:
        raise DidntUnderstand()
    if ap:
        if not 1 <= hour <= 12:
            raise DidntUnderstand()
        hour = hour % 12 + (12 if ap == "pm" else 0)
    elif hour > 23:
        raise DidntUnderstand()
    return hour, minute


def _day_label(when: datetime, now: datetime) -> str:
    delta = (when.date() - now.date()).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    return f"on {when.strftime('%A')} {when.day} {when.strftime('%b')}"


def _result(title: str, when: datetime, now: datetime) -> Parsed:
    title = title.strip()
    interpretation = f"Reminder '{title}' at {when:%H:%M} {_day_label(when, now)}"
    return Parsed(title=title, detail="", at=when.timestamp(), interpretation=interpretation)


def parse(text: str, now: datetime) -> Parsed:
    s = " ".join((text or "").split())
    if not s:
        raise DidntUnderstand()

    m = _IN.match(s)
    if m:
        n = int(m.group("n"))
        unit = m.group("u").lower()
        if n < 1 or n > _MAX_UNITS[unit]:
            raise DidntUnderstand()
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
        return _result(m.group("title"), now + delta, now)

    m = _AT.match(s)
    if m:
        hour, minute = _clock(m)
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
        return _result(m.group("title"), when, now)

    m = _TOMORROW.match(s)
    if m:
        hour, minute = _clock(m)
        when = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return _result(m.group("title"), when, now)

    m = _WEEKDAY.match(s)
    if m and m.group("day").lower() in _WEEKDAYS:
        hour, minute = _clock(m)
        target = _WEEKDAYS[m.group("day").lower()]
        days = (target - now.weekday()) % 7
        when = (now + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=7)
        return _result(m.group("title"), when, now)

    raise DidntUnderstand()
