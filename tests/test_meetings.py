"""Meeting cards from calendar sources (plan 7.2 M2-1; AC-MT-1, AC-MT-3, AC-MT-4, AC-MT-5, AC-MT-6, AC-MT-7).

Real components throughout: a real CardStore, a real Scheduler thread, real Settings (on the isolated
in-process HOMA_HOME from conftest), and real IcsSource objects fetching from a local HTTP server.
The only fakes are ``FakeSource`` (to inject all-day / declined / failing feeds, which the ICS builder
cannot express as cheaply) and the injected clock in the one-hour staleness test.

Timing: an event is created ``lead*60 + 3`` s ahead so the card is due ~3 s after the poll; the cards
must appear within +-5 s of T-lead (plan Done cell) and must not appear earlier.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import build_ics, wait_until

meetings = pytest.importorskip("homa.calendar.meetings", reason="homa/calendar/meetings.py (M2-1) not present")
ics = pytest.importorskip("homa.calendar.ics", reason="homa/calendar/ics.py (M2-1) not present")

from homa import uibus  # noqa: E402
from homa.cards.store import CardStore  # noqa: E402
from homa.scheduler import Scheduler  # noqa: E402
from homa.settings import Settings  # noqa: E402


class FakeSource:
    """In-memory source: ``events`` is returned by fetch; ``fail`` makes fetch raise a SourceError."""

    kind = "fake"

    def __init__(self, label, events=()):
        self.label = label
        self.events = list(events)
        self.fail = None

    def fetch(self, window_start, window_end):
        if self.fail:
            raise meetings.SourceError(self.fail)
        return list(self.events)


def event(uid, subject, start, minutes=30, **kw):
    s = datetime.fromtimestamp(start, timezone.utc)
    return meetings.Event(uid=uid, subject=subject, start=s, end=s + timedelta(minutes=minutes),
                          location=kw.pop("location", None), join_url=kw.pop("join_url", None),
                          all_day=kw.pop("all_day", False), declined=kw.pop("declined", False),
                          source_label=kw.pop("source_label", ""))


def set_calendar(settings, **fields):
    """Change calendars.* the way the Settings window does (nested object), then pump the UI-thread listeners."""
    settings.set("calendars", {**settings.get("calendars", {}), **fields})
    uibus.drain()


@pytest.fixture
def env(tmp_path):
    store = CardStore(path=tmp_path / "state.json")
    scheduler = Scheduler()
    scheduler.start()
    settings = Settings()
    settings.load()
    set_calendar(settings, lead_minutes=5, poll_minutes=5)
    made = []

    class Env:
        pass

    e = Env()
    e.store, e.scheduler, e.settings = store, scheduler, settings

    def make(sources, **kw):
        ms = meetings.MeetingScheduler(sources, store, scheduler, settings, **kw)
        made.append(ms)
        return ms

    e.make = make
    yield e
    for ms in made:
        ms.stop()
    scheduler.stop()


def meeting_cards(store):
    return [c for c in store.list(open_only=False) if c.kind == "meeting"]


def open_meeting_cards(store):
    return [c for c in store.list(open_only=True) if c.kind == "meeting"]


# ---- the golden path ------------------------------------------------------------------------------

def test_ics_feed_event_raises_one_blue_card_at_lead_time_with_title_and_expiry(env, ics_server):
    now = time.time()
    start, end = now + 5 * 60 + 3, now + 5 * 60 + 3 + 45 * 60
    body = build_ics([{"uid": "g1@example.test", "subject": "Design sync", "start": start, "end": end,
                       "location": "https://example.zoom.us/j/123456789?pwd=placeholder"}])
    source = ics.IcsSource(ics_server.serve("/feed/golden.ics", body), "Work")
    ms = env.make([source])
    ms.poll()
    assert open_meeting_cards(env.store) == [], "the card must not appear before T-lead"
    assert wait_until(lambda: open_meeting_cards(env.store), timeout=9, step=0.1), "no card within 9 s"
    appeared = time.time()
    assert abs(appeared - (start - 5 * 60)) <= 5, appeared - (start - 300)
    card = open_meeting_cards(env.store)[0]
    assert card.title == "Design sync starts in 5 minutes"
    assert card.id.startswith("meeting-")
    assert card.kind == "meeting"
    assert card.url == "https://example.zoom.us/j/123456789?pwd=placeholder"
    assert abs(card.expires_at - end) <= 1.5, "AC-MT-4: auto-dismiss at the event's end"
    assert card.detail and "Zoom" in card.detail


def test_card_id_is_stable_and_a_dismissed_card_is_not_resurrected_by_the_next_poll(env, ics_server):
    now = time.time()
    body = build_ics([{"uid": "g2@example.test", "subject": "One-off", "start": now + 303, "end": now + 2000}])
    ms = env.make([ics.IcsSource(ics_server.serve("/feed/dismiss.ics", body), "Work")])
    ms.poll()
    assert wait_until(lambda: open_meeting_cards(env.store), timeout=9, step=0.1)
    card_id = open_meeting_cards(env.store)[0].id
    env.store.dismiss(card_id)
    ms.poll()
    ms.poll()
    time.sleep(0.3)
    assert open_meeting_cards(env.store) == []
    assert [c.id for c in meeting_cards(env.store)] == [card_id], "same event, same id, never a second card"
    second = env.make([ics.IcsSource(ics_server.url("/feed/dismiss.ics"), "Work")])
    second.poll()
    time.sleep(0.3)
    assert open_meeting_cards(env.store) == [], "a restarted scheduler must not re-show a dismissed meeting"


# ---- lead time applies live ---------------------------------------------------------------------

def test_changing_the_lead_time_reschedules_the_pending_card_without_restart(env, ics_server):
    now = time.time()
    start = now + 3 * 60 + 3
    body = build_ics([{"uid": "g3@example.test", "subject": "Planning", "start": start, "end": start + 1800}])
    set_calendar(env.settings, lead_minutes=1)
    ms = env.make([ics.IcsSource(ics_server.serve("/feed/live.ics", body), "Work")])
    ms.start()
    assert wait_until(lambda: ms.status()["calendars"][0]["last_ok"], timeout=5), ms.status()
    time.sleep(0.5)
    assert open_meeting_cards(env.store) == [], "lead 1 min: due at T-1, which is 2 minutes away"
    set_calendar(env.settings, lead_minutes=3)
    assert wait_until(lambda: open_meeting_cards(env.store), timeout=8, step=0.1), "lead 3 min must make the card due now"
    assert open_meeting_cards(env.store)[0].title == "Planning starts in 3 minutes"


# ---- dedupe, all-day, declined -----------------------------------------------------------------

def test_same_meeting_in_two_sources_gives_one_card_even_with_different_uids_and_30s_skew(env, ics_server):
    now = time.time()
    start = now + 303
    a = build_ics([{"uid": "a-1@example.test", "subject": "Budget review", "start": start, "end": start + 1800}])
    b = build_ics([{"uid": "b-9@example.test", "subject": "budget  REVIEW", "start": start + 30, "end": start + 1830,
                    "location": "https://example.zoom.us/j/555000111"}])
    sources = [ics.IcsSource(ics_server.serve("/feed/a.ics", a), "Work"),
               ics.IcsSource(ics_server.serve("/feed/b.ics", b), "Personal")]
    ms = env.make(sources)
    ms.poll()
    assert wait_until(lambda: open_meeting_cards(env.store), timeout=9, step=0.1)
    time.sleep(1.0)
    cards = meeting_cards(env.store)
    assert len(cards) == 1, [(c.id, c.title) for c in cards]
    assert cards[0].url == "https://example.zoom.us/j/555000111", "the join link from either copy survives the merge"


def test_same_subject_two_minutes_apart_stays_two_cards(env):
    now = time.time()
    src = FakeSource("Work", [event("x1", "Sync", now + 100), event("x2", "Sync", now + 220)])
    env.make([src]).poll()
    assert wait_until(lambda: len(open_meeting_cards(env.store)) == 2, timeout=5, step=0.1),         [c.title for c in meeting_cards(env.store)]


def test_all_day_and_declined_events_make_no_card_while_a_normal_one_does(env):
    now = time.time()
    src = FakeSource("Work", [
        event("all", "Offsite", now + 303, all_day=True),
        event("dec", "Declined thing", now + 303, declined=True),
        event("ok", "Normal thing", now + 303)])
    env.make([src]).poll()
    assert wait_until(lambda: open_meeting_cards(env.store), timeout=9, step=0.1)
    time.sleep(0.5)
    assert [c.title for c in meeting_cards(env.store)] == ["Normal thing starts in 5 minutes"]


def test_event_that_already_ended_makes_no_card(env):
    now = time.time()
    env.make([FakeSource("Work", [event("old", "Yesterday", now - 7200, minutes=30)])]).poll()
    time.sleep(0.5)
    assert meeting_cards(env.store) == []


# ---- failing feeds -----------------------------------------------------------------------------

SECRET = "TOPSECRETFEEDPATH77"


def test_404_feed_sets_last_error_makes_no_card_and_keeps_the_path_out_of_logs_and_status(env, ics_server, caplog):
    caplog.set_level(logging.DEBUG)
    bad = ics.IcsSource(ics_server.url(f"/private/{SECRET}/gone.ics"), "Broken")
    ms = env.make([bad])
    ms.poll()
    (cal,) = ms.status()["calendars"]
    assert cal["label"] == "Broken"
    assert cal["last_error"] and "404" in cal["last_error"], cal
    assert cal["last_ok"] is None
    assert env.store.list(open_only=False) == []
    assert any(SECRET in p for p in ics_server.hits)
    assert SECRET not in caplog.text and SECRET not in repr(ms.status())


def test_one_broken_source_does_not_stop_a_good_one(env, ics_server):
    now = time.time()
    good = build_ics([{"uid": "ok@example.test", "subject": "Still works", "start": now + 303, "end": now + 2000}])
    ms = env.make([ics.IcsSource(ics_server.url("/feed/missing.ics"), "Broken"),
                   ics.IcsSource(ics_server.serve("/feed/good.ics", good), "Good")])
    ms.poll()
    assert wait_until(lambda: open_meeting_cards(env.store), timeout=9, step=0.1)
    labels = {c["label"]: c for c in ms.status()["calendars"]}
    assert labels["Broken"]["last_error"] and not labels["Good"]["last_error"]


def test_a_feed_failing_for_over_an_hour_raises_one_custom_card_and_its_stale_events_stop_producing_meeting_cards(env):
    base = time.time()
    clock = {"now": base}
    src = FakeSource("Work", [event("far", "Far away", base + 4 * 3600)])
    ms = env.make([src], clock=lambda: clock["now"])
    ms.poll()
    assert ms.status()["calendars"][0]["last_ok"] == base
    src.fail = "HTTP 500"
    src.events = [event("soon", "Soon", base + 3700 + 240)]
    clock["now"] = base + 1800
    ms.poll()
    assert [c for c in env.store.list(open_only=False) if c.kind == "custom"] == [], "under an hour: status line only"
    clock["now"] = base + 3700
    ms.poll()
    ms.poll()
    customs = [c for c in env.store.list(open_only=False) if c.kind == "custom"]
    assert len(customs) == 1 and "Work" in customs[0].title, customs
    assert meeting_cards(env.store) == [], "nothing may be raised from a feed we have not read for over an hour"
    src.fail = None
    ms.poll()
    assert env.store.list(open_only=True) == [] or all(c.kind != "custom" for c in env.store.list(open_only=True)), \
        "the failure card clears when the feed recovers"


def test_status_lists_every_source_with_label_and_a_successful_poll_time(env):
    one, two = FakeSource("Work"), FakeSource("Personal")
    ms = env.make([one, two])
    assert [c["label"] for c in ms.status()["calendars"]] == ["Work", "Personal"]
    ms.poll()
    assert all(c["last_ok"] and not c["last_error"] for c in ms.status()["calendars"])


# ---- Outlook ----------------------------------------------------------------------------------

def test_outlook_source_reports_availability_as_a_bool_without_raising():
    outlook = pytest.importorskip("homa.calendar.outlook", reason="homa/calendar/outlook.py (M2-1) not present")
    from homa.overlay.settings_ui import outlook_classic_present
    if outlook_classic_present():
        pytest.skip("classic Outlook is installed here; QA does not start Outlook (AC-MT-2 is a personal-laptop check)")
    assert outlook.OutlookSource().available() is False


# ---- M3-0: lead range and UID dedupe in the real scheduler ---------------------------------------------------------

@pytest.mark.parametrize("lead,accepted", [(0, True), (120, True), (121, False)])
def test_lead_minutes_range_is_0_to_120(env, lead, accepted):
    cal = env.settings.get("calendars")
    if accepted:
        env.settings.set("calendars", {**cal, "lead_minutes": lead})
        uibus.drain()
        assert env.settings.get("calendars")["lead_minutes"] == lead
    else:
        with pytest.raises(ValueError):
            env.settings.set("calendars", {**cal, "lead_minutes": lead})


def test_same_uid_in_two_feeds_with_different_subjects_makes_one_card(env):
    t = time.time() + 5 * 60 + 2
    a = FakeSource("A", [event("shared@example.test", "Planning", t)])
    b = FakeSource("B", [event("SHARED@example.test", "Planning (invite copy)", t + 20)])
    env.make([a, b]).start()
    assert wait_until(lambda: [c for c in env.store.visible() if c.kind == "meeting"], timeout=10, step=0.2)
    time.sleep(1.0)
    assert len([c for c in env.store.visible() if c.kind == "meeting"]) == 1
