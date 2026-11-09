"""ICS feed parsing (plan 7.2 M2-1; AC-MT-1, AC-MT-3, AC-MT-6, AC-MT-7, SEC-12).

Every case goes through the public path a feed takes in the app: a real HTTP fetch from a local server
(conftest ``ics_server``) into ``IcsSource.fetch``. Feeds are the files in tests/fixtures/calendars/, built
only from placeholders (example.test). Windows have fixed dates, so results do not depend on today.

Notes on what the fixtures cannot prove: the Python on this laptop has no ``tzdata`` package, so the
TZID cases only pass if the parser honours the feed's own VTIMEZONE blocks (or a Windows-zone table)
rather than relying on the OS zone database.
"""
import logging
from datetime import datetime, timedelta, timezone

import pytest

from conftest import REPO_ROOT

ics = pytest.importorskip("homa.calendar.ics", reason="homa/calendar/ics.py (M2-1) not present")

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "calendars"
UTC = timezone.utc
NOV = (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))
DAY = (datetime(2026, 11, 10, tzinfo=UTC), datetime(2026, 11, 11, tzinfo=UTC))


def fetch(ics_server, name, window=DAY, label="Work", path=None):
    url = ics_server.serve(path or f"/cal/{name}", (FIXTURES / name).read_text(encoding="utf-8"))
    return ics.IcsSource(url, label).fetch(*window)


def by_subject(events, subject):
    found = [e for e in events if e.subject == subject]
    assert len(found) == 1, (subject, [e.subject for e in events])
    return found[0]


def as_utc(value):
    assert isinstance(value, datetime), type(value)
    return value.astimezone(UTC) if value.tzinfo else value.astimezone().astimezone(UTC)


# ---- recurrence -------------------------------------------------------------------------------

def test_weekly_event_expands_inside_the_window_and_skips_the_exdate(ics_server):
    events = fetch(ics_server, "weekly_exdate.ics", NOV)
    assert [as_utc(e.start) for e in events] == [datetime(2026, 11, d, 9, 0, tzinfo=UTC) for d in (2, 9, 23, 30)]
    assert {e.subject for e in events} == {"Weekly standup"}
    assert all(as_utc(e.end) - as_utc(e.start) == timedelta(minutes=30) for e in events)
    assert all(e.location == "Room 4" and not e.all_day and not e.declined for e in events)
    assert len({e.uid for e in events}) == len(events), "each occurrence needs its own uid (card ids are derived per event)"


def test_window_clips_recurrences_and_an_empty_window_gives_nothing(ics_server):
    two_weeks = fetch(ics_server, "weekly_exdate.ics", (datetime(2026, 11, 8, tzinfo=UTC), datetime(2026, 11, 15, tzinfo=UTC)))
    assert [as_utc(e.start).day for e in two_weeks] == [9]
    assert fetch(ics_server, "weekly_exdate.ics", (datetime(2026, 11, 3, tzinfo=UTC), datetime(2026, 11, 8, tzinfo=UTC))) == []


# ---- all-day, declined, cancelled ---------------------------------------------------------------

def test_all_day_event_is_flagged_and_a_timed_event_in_the_same_feed_is_not(ics_server):
    events = fetch(ics_server, "all_day.ics")
    assert by_subject(events, "Team offsite").all_day is True
    assert by_subject(events, "Timed control").all_day is False


def test_declined_attendee_status_is_flagged_and_accepted_is_not(ics_server):
    events = fetch(ics_server, "declined.ics")
    assert by_subject(events, "Declined review").declined is True
    assert by_subject(events, "Accepted review").declined is False


def test_cancelled_event_can_never_produce_a_card(ics_server):
    events = fetch(ics_server, "cancelled.ics")
    assert by_subject(events, "Confirmed sync").declined is False
    cancelled = [e for e in events if e.subject == "Cancelled sync"]
    assert all(e.declined for e in cancelled), "a STATUS:CANCELLED event must be dropped or marked declined"


# ---- join URLs, folding, escapes ----------------------------------------------------------------

def test_join_url_is_found_in_location(ics_server):
    event = by_subject(fetch(ics_server, "join_urls.ics"), "Zoom in location")
    assert event.join_url == "https://example.zoom.us/j/123456789?pwd=placeholder"


def test_join_url_is_found_in_a_folded_escaped_description_and_commas_are_unescaped(ics_server):
    event = by_subject(fetch(ics_server, "join_urls.ics"), "Planning, Q4 review")
    assert event.join_url == "https://example.zoom.us/j/987654321?pwd=placeholder"
    assert event.location == "Room 7"


def test_event_without_a_link_has_no_join_url(ics_server):
    event = by_subject(fetch(ics_server, "join_urls.ics"), "No link")
    assert not event.join_url
    assert event.location == "Room 9"


# ---- time zones ---------------------------------------------------------------------------------

def test_tzid_utc_and_floating_times_resolve_to_the_right_instants(ics_server):
    wide = (datetime(2026, 11, 9, tzinfo=UTC), datetime(2026, 11, 12, tzinfo=UTC))
    events = fetch(ics_server, "timezones.ics", window=wide)
    seven_pm_utc = datetime(2026, 11, 10, 19, 0, tzinfo=UTC)
    assert as_utc(by_subject(events, "UTC event").start) == seven_pm_utc
    assert as_utc(by_subject(events, "Zone event").start) == seven_pm_utc, "TZID=America/New_York 14:00 on 2026-11-10 is EST (UTC-5)"
    assert as_utc(by_subject(events, "Windows zone event").start) == seven_pm_utc, "TZID=Eastern Standard Time"
    floating = by_subject(events, "Floating event")
    assert as_utc(floating.start) == datetime(2026, 11, 10, 19, 0).astimezone().astimezone(UTC), \
        "floating time means the PC's own local time"
    for name in ("UTC event", "Zone event", "Windows zone event", "Floating event"):
        e = by_subject(events, name)
        assert as_utc(e.end) - as_utc(e.start) == timedelta(hours=1)


# ---- source plumbing ----------------------------------------------------------------------------

def test_source_label_is_stamped_on_every_event(ics_server):
    events = fetch(ics_server, "all_day.ics", label="Personal")
    assert events and {e.source_label for e in events} == {"Personal"}


def test_404_and_non_calendar_bodies_raise_instead_of_returning_an_empty_calendar(ics_server):
    missing = ics.IcsSource(ics_server.url("/nothing/here.ics"), "x")
    with pytest.raises(Exception):
        missing.fetch(*DAY)
    html = ics.IcsSource(ics_server.serve("/page.html", "<html>sign in</html>"), "x")
    with pytest.raises(Exception):
        html.fetch(*DAY)


def test_a_non_http_url_is_refused_at_construction():
    with pytest.raises(Exception):
        ics.IcsSource("file:///C:/secret.ics", "x")


SECRET = "S3CRETPATHTOKEN9"


def test_url_path_is_never_logged_or_repr_d_or_put_in_errors(ics_server, caplog):
    caplog.set_level(logging.DEBUG)
    good = ics.IcsSource(ics_server.serve(f"/private/{SECRET}/basic.ics", (FIXTURES / "all_day.ics").read_text(encoding="utf-8")), "Work")
    bad = ics.IcsSource(ics_server.url(f"/private/{SECRET}/gone.ics"), "Gone")
    good.fetch(*DAY)
    with pytest.raises(Exception) as err:
        bad.fetch(*DAY)
    texts = [caplog.text, repr(good), str(good), repr(bad), str(err.value), repr(err.value)]
    assert not any(SECRET in t for t in texts), [t for t in texts if SECRET in t]
    assert any(SECRET in p for p in ics_server.hits), "the fetch itself must have used the real path"


# ---- M3-0: R6-1 and MONTHLY/YEARLY through the real fetch path ---------------------------------------------------

def test_floating_until_in_1970_does_not_silence_the_feed_over_http(ics_server):
    body = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "BEGIN:VEVENT", "UID:bad@example.test", "DTSTAMP:20260101T000000Z", "DTSTART:20261105T100000",
        "DTEND:20261105T110000", "RRULE:FREQ=DAILY;UNTIL=19700101T000000", "SUMMARY:Bad", "END:VEVENT",
        "BEGIN:VEVENT", "UID:good@example.test", "DTSTAMP:20260101T000000Z", "DTSTART:20261110T100000Z",
        "DTEND:20261110T110000Z", "SUMMARY:Still here", "END:VEVENT", "END:VCALENDAR", ""])
    url = ics_server.serve("/cal/r61.ics", body)
    events = ics.IcsSource(url, "Work").fetch(*NOV)
    assert [e.subject for e in events] == ["Still here"]


def test_monthly_second_tuesday_is_expanded_through_the_fetch_path(ics_server):
    body = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT", "UID:m@example.test", "DTSTAMP:20260101T000000Z",
        "DTSTART:20260908T100000Z", "DTEND:20260908T110000Z", "RRULE:FREQ=MONTHLY;BYDAY=2TU",
        "SUMMARY:Monthly sync", "END:VEVENT", "END:VCALENDAR", ""])
    url = ics_server.serve("/cal/monthly.ics", body)
    events = ics.IcsSource(url, "Work").fetch(datetime(2026, 9, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
    assert [e.start.astimezone(UTC).date().isoformat() for e in events] == \
        ["2026-09-08", "2026-10-13", "2026-11-10", "2026-12-08"]
