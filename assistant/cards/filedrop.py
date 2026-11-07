from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

from .. import paths
from ..scheduler import Handle, Scheduler
from ..server.routes_cards import parse_json
from .model import Card, CardValidationError
from .store import CardStore

log = logging.getLogger("assistant.cards.filedrop")

SOURCE = "file"
MAX_FILE_BYTES = 65_536
MAX_PER_POLL = 100
SETTLE_SECONDS = 0.25
POLL_SECONDS = 0.5
IDLE_POLL_SECONDS = 1.5


class FileDrop:
    """Turns <events dir>/*.json into cards: consumed files are deleted, bad ones go to rejected/."""

    def __init__(self, store: CardStore, events_dir: Optional[Path] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._dir = Path(events_dir) if events_dir is not None else paths.home() / "events"
        self._clock = clock
        self._stuck: set = set()
        self._busy = False

    def start(self, scheduler: Scheduler, interval: float = POLL_SECONDS,
              idle_interval: float = IDLE_POLL_SECONDS) -> Handle:
        handle = Handle()

        def tick() -> None:
            if handle.cancelled:
                return
            self._busy = False
            try:
                self.poll()
            except Exception:
                log.exception("file-drop poll failed")
                self._busy = True
            if not handle.cancelled:
                scheduler.at(time.time() + (interval if self._busy else idle_interval), tick)

        scheduler.at(time.time() + interval, tick)
        return handle

    def poll(self) -> int:
        """Process settled files once; returns how many became cards."""
        try:
            entries = [e for e in os.scandir(self._dir) if e.name.lower().endswith(".json")]
        except OSError:
            return 0
        made = 0
        now = self._clock()
        for entry in sorted(entries, key=lambda e: e.name)[:MAX_PER_POLL]:
            try:
                if entry.is_symlink() or not entry.is_file():
                    continue
                stat = entry.stat()
            except OSError:
                continue
            if 0 <= now - stat.st_mtime < SETTLE_SECONDS:
                self._busy = True
                continue
            key = (entry.name, stat.st_mtime_ns)
            if key in self._stuck:
                continue
            path = Path(entry.path)
            try:
                card = self._load(path, stat.st_size)
            except _Rejected as exc:
                self._reject(path, str(exc))
                continue
            except OSError as exc:
                log.warning("cannot read dropped file %r (%s)", entry.name[:120], type(exc).__name__)
                continue
            self._store.upsert(card, SOURCE)
            made += 1
            self._busy = True
            try:
                path.unlink()
            except OSError:
                log.warning("could not delete consumed file %r", entry.name[:120])
                self._stuck.add(key)
        return made

    def _load(self, path: Path, size: int) -> Card:
        if size > MAX_FILE_BYTES:
            raise _Rejected(f"File is larger than {MAX_FILE_BYTES} bytes.")
        with open(path, "rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise _Rejected(f"File is larger than {MAX_FILE_BYTES} bytes.")
        try:
            body = parse_json(raw)
        except (ValueError, RecursionError):
            raise _Rejected("The file is not valid JSON.")
        try:
            return Card.from_dict(body)
        except CardValidationError as exc:
            where = f" (field: {exc.field})" if exc.field else ""
            raise _Rejected(f"{exc.message}{where}")

    def _reject(self, path: Path, reason: str) -> None:
        rejected = self._dir / "rejected"
        try:
            rejected.mkdir(parents=True, exist_ok=True)
            target = rejected / path.name
            if target.exists():
                target = rejected / f"{path.stem}.{int(self._clock() * 1000)}{path.suffix}"
            os.replace(path, target)
            (rejected / f"{target.name}.error.txt").write_text(reason + "\n", encoding="utf-8")
            log.warning("rejected dropped file %r: %s", path.name[:120], reason)
        except OSError:
            log.exception("could not move a rejected file aside")
            self._stuck.add((path.name, self._mtime_ns(path)))

    @staticmethod
    def _mtime_ns(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0


class _Rejected(Exception):
    pass
