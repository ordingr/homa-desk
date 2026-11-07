from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime

from .meetings import Event, SourceError, find_join_url

log = logging.getLogger("assistant.calendar.outlook")

OL_FOLDER_CALENDAR = 9
OL_RESPONSE_DECLINED = 4
OL_MEETING_CANCELED = (5, 7)
MAX_BODY_SCANNED = 4000
MAX_ITEMS = 500


@contextmanager
def _com():
    import pythoncom
    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


class OutlookSource:
    """The default calendar of classic Outlook through COM. Needs no sign-in; the new Outlook
    has no COM server, so ``available()`` is False there."""

    kind = "outlook"
    host = None

    def __init__(self, label: str = "Outlook calendar (this PC)"):
        self.label = label

    def available(self) -> bool:
        """Registry probe only; it does not start Outlook."""
        try:
            import winreg
            import win32com.client  # noqa: F401
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Outlook.Application\CLSID"):
                return True
        except Exception:
            return False

    def fetch(self, window_start: datetime, window_end: datetime) -> list:
        if not self.available():
            raise SourceError("classic Outlook is not installed")
        try:
            with _com():
                return self._read(window_start, window_end)
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(f"Outlook COM failed ({type(exc).__name__})") from None

    def _read(self, window_start: datetime, window_end: datetime) -> list:
        import win32com.client
        app = win32com.client.Dispatch("Outlook.Application")
        folder = app.GetNamespace("MAPI").GetDefaultFolder(OL_FOLDER_CALENDAR)
        items = folder.Items
        items.IncludeRecurrences = True
        items.Sort("[Start]")
        fmt = "%m/%d/%Y %I:%M %p"
        ws, we = window_start.astimezone().replace(tzinfo=None), window_end.astimezone().replace(tzinfo=None)
        restricted = items.Restrict(f"[Start] < '{we.strftime(fmt)}' AND [End] > '{ws.strftime(fmt)}'")
        out = []
        item = restricted.GetFirst()
        while item is not None and len(out) < MAX_ITEMS:
            try:
                ev = self._event(item)
                if ev is not None:
                    out.append(ev)
            except Exception as exc:
                log.debug("skipping an Outlook item (%s)", type(exc).__name__)
            item = restricted.GetNext()
        return out

    def _event(self, item) -> Event | None:
        if item.AllDayEvent:
            return None
        if item.ResponseStatus == OL_RESPONSE_DECLINED or item.MeetingStatus in OL_MEETING_CANCELED:
            return None
        start, end = _local(item.Start), _local(item.End)
        location = str(item.Location or "").strip() or None
        body = str(item.Body or "")[:MAX_BODY_SCANNED]
        return Event(uid="outlook:" + str(item.EntryID), subject=str(item.Subject or "").strip() or "(no title)",
                     start=start, end=end, location=location, join_url=find_join_url(location, body),
                     all_day=False, declined=False, source_label=self.label)


def _local(value) -> datetime:
    """COM datetimes carry Outlook's local wall-clock time; make them aware in the system zone."""
    naive = datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
    return naive.astimezone()
