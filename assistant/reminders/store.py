from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .. import paths
from ..scheduler import Handle, Scheduler
from .grammar import parse

log = logging.getLogger("assistant.reminders")


@dataclass(frozen=True)
class Reminder:
    id: str
    title: str
    detail: str
    at: float
    sticky: bool = False
    overdue: bool = False


def _was_due(at: float, now: float) -> str:
    when = datetime.fromtimestamp(at)
    if when.date() == datetime.fromtimestamp(now).date():
        return f"{when:%H:%M}"
    return f"{when:%a %d %b %H:%M}"


class ReminderStore:
    """Persists pending reminders and arms them on the scheduler.

    `fire` is called on the scheduler thread with the Reminder that is due; it
    must hand any UI work to uibus itself.
    """

    def __init__(self, scheduler: Scheduler, fire: Callable[[Reminder], None],
                 path: Optional[Path] = None, clock: Callable[[], float] = time.time) -> None:
        self._scheduler = scheduler
        self._fire = fire
        self._path = path
        self._clock = clock
        self._lock = threading.RLock()
        self._items: dict[str, Reminder] = {}
        self._handles: dict[str, Handle] = {}

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else paths.home() / "reminders.json"

    def start(self) -> list[Reminder]:
        """Load reminders.json, fire overdue ones now, arm the rest. Returns the overdue ones fired."""
        loaded = self._read()
        now = self._clock()
        overdue: list[Reminder] = []
        with self._lock:
            for r in loaded:
                if r.at <= now:
                    overdue.append(r)
                else:
                    self._items[r.id] = r
                    self._arm(r)
            if overdue:
                self._write()
        for r in overdue:
            self._deliver(replace(r, title=f"{r.title} (was due {_was_due(r.at, now)})", overdue=True))
        return overdue

    def add(self, title: str, at: float, detail: str = "", sticky: bool = False) -> Reminder:
        r = Reminder(id=uuid.uuid4().hex[:12], title=title, detail=detail, at=at, sticky=sticky)
        with self._lock:
            self._items[r.id] = r
            self._arm(r)
            self._write()
        return r

    def add_text(self, text: str, sticky: bool = False, now: Optional[datetime] = None):
        """Parse with the grammar and add. Raises DidntUnderstand. Returns (Reminder, Parsed)."""
        parsed = parse(text, now or datetime.fromtimestamp(self._clock()))
        return self.add(parsed.title, parsed.at, parsed.detail, sticky), parsed

    def get(self, rid: str) -> Optional[Reminder]:
        with self._lock:
            return self._items.get(rid)

    def list(self) -> list[Reminder]:
        with self._lock:
            return sorted(self._items.values(), key=lambda r: (r.at, r.id))

    def cancel(self, rid: str) -> bool:
        with self._lock:
            if self._items.pop(rid, None) is None:
                return False
            handle = self._handles.pop(rid, None)
            if handle is not None:
                handle.cancel()
            self._write()
            return True

    def _arm(self, r: Reminder) -> None:
        self._handles[r.id] = self._scheduler.at(r.at, lambda rid=r.id: self._due(rid))

    def _due(self, rid: str) -> None:
        with self._lock:
            r = self._items.pop(rid, None)
            self._handles.pop(rid, None)
            if r is None:
                return
            self._write()
        self._deliver(r)

    def _deliver(self, r: Reminder) -> None:
        try:
            self._fire(r)
        except Exception:
            log.exception("reminder fire callback failed")

    def _write(self) -> None:
        path = self.path
        payload = {"version": 1, "reminders": [asdict(r) for r in self._items.values()]}
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            log.exception("could not write %s", path)

    def _read(self) -> list[Reminder]:
        path = self.path
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out = []
            for d in data.get("reminders", []):
                out.append(Reminder(id=str(d["id"]), title=str(d["title"]), detail=str(d.get("detail", "")),
                                    at=float(d["at"]), sticky=bool(d.get("sticky", False))))
            return out
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            log.exception("could not read %s; keeping it as .corrupt", path)
            try:
                os.replace(path, path.with_name(path.name + ".corrupt"))
            except OSError:
                pass
            return []
