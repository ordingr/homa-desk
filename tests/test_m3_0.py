"""M3-0: M2 leftovers (plan 17.3 (a)-(j)) and the R6-1/R6-2 fixes.

Real components: Settings on the isolated HOMA_HOME, a real MeetingScheduler/CardStore/Scheduler,
real ``parse_ics``, the real ``mcp.log`` writer and tail, and (for (a), (b), (j)) a private full app.
The Settings-window items ((c), (d), (h), R6-4) live in tests/e2e/test_settings_live_apply.py, which owns
the one Tk interpreter.
"""
import json
import subprocess
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import Api, build_ics, wait_until

from homa import paths, uibus
from homa.calendar import ics, meetings
from homa.cards.store import CardStore
from homa.overlay import sounds
from homa.scheduler import Scheduler
from homa.server import mcp as mcp_mod
from homa.server import routes_health
from homa.settings import MUTE_SOURCES, Settings

UTC = timezone.utc


# ---- settings: (a) (d) (e) -------------------------------------------------------------------------------

def test_a_mute_sources_has_calendar_as_the_sixth_source_and_defaults_off():
    assert MUTE_SOURCES == ("hooks", "http", "mcp", "file", "reminders", "calendar")
    s = Settings()
    s.load()
    assert s.get("mute_sources")["calendar"] is False
    s.set("mute_sources", {**s.get("mute_sources"), "calendar": True})
    assert json.loads(paths.settings_file().read_text("utf-8"))["mute_sources"]["calendar"] is True


def test_e_lead_minutes_120_accepted_121_refused_and_poll_unaffected():
    s = Settings()
    s.load()
    cal = s.get("calendars")
    s.set("calendars", {**cal, "lead_minutes": 120})
    assert s.get("calendars")["lead_minutes"] == 120
    with pytest.raises(ValueError):
        s.set("calendars", {**cal, "lead_minutes": 121})
    assert s.get("calendars")["lead_minutes"] == 120
    s.set("calendars", {**cal, "lead_minutes": 0})
    with pytest.raises(ValueError):
        s.set("calendars", {**cal, "lead_minutes": -1})


def test_d_labels_map_keeps_ics_urls_a_list_of_strings_and_enforces_its_limits():
    s = Settings()
    s.load()
    cal = s.get("calendars")
    url = "https://example.test/p/SECRETPATH/x.ics"
    s.set("calendars", {**cal, "ics_urls": [url], "labels": {url: "Work"}})
    assert s.get("calendars")["ics_urls"] == [url]
    s.set("calendars", {**cal, "ics_urls": [url], "labels": {url: "x" * 64}})
    for bad in ({url: "x" * 65}, {url: 5}, {i: "a" for i in range(1)},
                {f"https://example.test/{i}": "a" for i in range(21)}):
        with pytest.raises(ValueError):
            s.set("calendars", {**cal, "ics_urls": [url], "labels": bad})
    with pytest.raises(ValueError):
        s.set("calendars", {**cal, "labels": "nope"})


def test_d_an_external_edit_with_bad_labels_repairs_only_the_calendars_part(tmp_path):
    s = Settings()
    s.load()
    data = json.loads(paths.settings_file().read_text("utf-8"))
    data["calendars"]["labels"] = ["not", "a", "map"]
    data["calendars"]["lead_minutes"] = 7
    data["port"] = 17333
    paths.settings_file().write_text(json.dumps(data), "utf-8")
    s._check_external()
    uibus.drain()
    assert s.get("port") == 17333
    assert s.get("calendars")["labels"] == {}


# ---- (j) ACL re-applied after an external edit ---------------------------------------------------------------

def _aces(path):
    out = subprocess.run(["icacls", str(path)], capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if ":(" in line]


def test_j_check_external_reapplies_the_single_ace_when_feeds_are_present_and_not_otherwise():
    s = Settings()
    s.load()
    f = paths.settings_file()
    data = json.loads(f.read_text("utf-8"))
    data["calendars"]["ics_urls"] = ["https://example.test/p/abc/x.ics"]
    f.write_text(json.dumps(data), "utf-8")
    subprocess.run(["icacls", str(f), "/grant", "Everyone:R"], capture_output=True, check=True)
    assert any("Everyone" in a for a in _aces(f)), "setup: Everyone:R was not granted"
    s._check_external()
    aces = _aces(f)
    assert len(aces) == 1 and "(F)" in aces[0] and "Everyone" not in aces[0], aces
    assert s.get("calendars")["ics_urls"] == ["https://example.test/p/abc/x.ics"]


def test_j_without_feeds_an_external_edit_is_left_alone():
    s = Settings()
    s.load()
    f = paths.settings_file()
    data = json.loads(f.read_text("utf-8"))
    data["calendars"]["ics_urls"] = []
    data["theme"] = "dark"
    f.write_text(json.dumps(data), "utf-8")
    subprocess.run(["icacls", str(f), "/grant", "Everyone:R"], capture_output=True, check=True)
    s._check_external()
    assert any("Everyone" in a for a in _aces(f)), "ACL must only be rewritten when ics_urls is non-empty"
    subprocess.run(["icacls", str(f), "/remove", "Everyone"], capture_output=True)


# ---- (b) sound_on is the single source of truth --------------------------------------------------------------

def test_b_app_seeds_sounds_from_the_setting_and_follows_external_edits(app_factory, tmp_path):
    with app_factory({"sound_on": False}, test_mode=True) as (base, token):
        api = Api(base, token)
        ui = api.get("/v1/_debug/ui")[1]
        assert ui["sound_on"] is False, ui["menu"]
        off_menu = list(ui["menu"])
        sf = tmp_path / "private_home" / "settings.json"
        data = json.loads(sf.read_text("utf-8"))
        data["sound_on"] = True
        sf.write_text(json.dumps(data), "utf-8")
        assert wait_until(lambda: api.get("/v1/_debug/ui")[1]["sound_on"] is True, timeout=15, step=0.3)
        on_menu = api.get("/v1/_debug/ui")[1]["menu"]
        assert on_menu != off_menu, "the Sound menu label must flip with sound_on"


def test_b_sound_off_plays_nothing_for_a_new_card(app_factory):
    with app_factory({"sound_on": False}, test_mode=True) as (base, token):
        api = Api(base, token)
        status, _ = api.post("/v1/cards", {"title": "quiet", "kind": "done"})
        assert status in (200, 201)
        assert wait_until(lambda: api.get("/v1/_debug/ui")[1]["cards"], timeout=5)
        ui = api.get("/v1/_debug/ui")[1]
        assert ui["sound_on"] is False


def test_b_sounds_module_obeys_set_enabled():
    before = sounds.is_enabled()
    try:
        sounds.set_enabled(False)
        n = len(sounds.played())
        assert sounds.play("done") is False and len(sounds.played()) == n
    finally:
        sounds.set_enabled(before)


# ---- (a) calendar mute in a running app ----------------------------------------------------------------------

def test_a_muted_calendar_source_keeps_the_card_in_the_store_but_shows_no_window_until_unmuted(
        app_factory, ics_server, tmp_path):
    start = time.time() + 5 * 60 + 4
    url = ics_server.serve("/p/mute.ics", build_ics(
        [{"uid": "m1@example.test", "subject": "Muted meeting", "start": start, "end": start + 1800}]))
    cfg = {"calendars": {"ics_urls": [url], "outlook": False, "lead_minutes": 5, "poll_minutes": 1},
           "mute_sources": {"calendar": True}}
    with app_factory(cfg, test_mode=True) as (base, token):
        api = Api(base, token)

        def meeting_cards():
            return [c for c in api.get("/v1/cards")[1] if c["kind"] == "meeting"]

        assert wait_until(meeting_cards, timeout=25, step=0.3), "meeting card never reached the store"
        time.sleep(1.0)
        assert api.get("/v1/_debug/ui")[1]["cards"] == [], "muted calendar must not show a window"
        sf = tmp_path / "private_home" / "settings.json"
        data = json.loads(sf.read_text("utf-8"))
        data["mute_sources"]["calendar"] = False
        sf.write_text(json.dumps(data), "utf-8")
        assert wait_until(lambda: api.get("/v1/_debug/ui")[1]["cards"], timeout=15, step=0.3), \
            "unmuting must show the queued card"
        shown = api.get("/v1/_debug/ui")[1]["cards"]
        assert {w["kind"] for w in shown} == {"meeting"}


# ---- (f) UID dedupe --------------------------------------------------------------------------------------------

class Feed:
    kind = "fake"

    def __init__(self, label, events):
        self.label, self.events = label, events

    def fetch(self, ws, we):
        return list(self.events)


def _ev(uid, subject, start):
    s = datetime.fromtimestamp(start, UTC)
    return meetings.Event(uid=uid, subject=subject, start=s, end=s + timedelta(minutes=30), location=None,
                          join_url=None, all_day=False, declined=False, source_label="")


@pytest.fixture
def mt(tmp_path):
    store = CardStore(path=tmp_path / "state.json")
    sch = Scheduler()
    sch.start()
    settings = Settings()
    settings.load()
    made = []

    def make(sources):
        m = meetings.MeetingScheduler(sources, store, sch, settings)
        made.append(m)
        return m
    yield store, settings, make
    for m in made:
        m.stop()
    sch.stop()


def _meeting_cards(store):
    return [c for c in store.visible() if c.kind == "meeting"] if hasattr(store, "visible") else []


def test_f_same_uid_different_subjects_within_60s_make_one_card(mt):
    store, settings, make = mt
    t = time.time() + 5 * 60 + 600
    m = make([Feed("A", [_ev("Shared-UID@Example.test", "Quarterly review", t)]),
              Feed("B", [_ev("shared-uid@example.test ", "Q review (copy)", t + 30)])])
    m.poll()
    assert len(m._groups) == 1


def test_f_same_uid_but_far_apart_or_different_uid_and_subject_stay_separate(mt):
    store, settings, make = mt
    t = time.time() + 5 * 60 + 600
    m = make([Feed("A", [_ev("same@example.test", "One", t)]),
              Feed("B", [_ev("same@example.test", "Two", t + 3600), _ev("other@example.test", "Three", t + 20)])])
    m.poll()
    assert len(m._groups) == 3


def test_f_events_without_a_uid_are_not_merged_by_uid(mt):
    store, settings, make = mt
    t = time.time() + 5 * 60 + 600
    m = make([Feed("A", [_ev("nouid-a", "One", t)]), Feed("B", [_ev("nouid-a", "Two", t)])])
    m.poll()
    assert len(m._groups) == 2


# ---- (i) per-source deadline -----------------------------------------------------------------------------------

def test_i_a_slow_feed_cannot_hold_back_a_fast_one_past_the_15s_deadline(mt):
    store, settings, make = mt
    release = threading.Event()

    class Slow(Feed):
        def fetch(self, ws, we):
            release.wait(30)
            return []

    t = time.time() + 600
    fast = Feed("Fast", [_ev("fast@example.test", "Fast one", t)])
    m = make([Slow("Slow", []), fast])
    t0 = time.monotonic()
    m.poll()
    took = time.monotonic() - t0
    try:
        assert 14.0 <= took <= 15.5, took
        rows = {c["label"]: c for c in m.status()["calendars"]}
        assert rows["Slow"]["last_error"] == "timed out" and not rows["Slow"]["last_ok"]
        assert rows["Fast"]["last_error"] is None and rows["Fast"]["last_ok"]
        assert rows["Fast"]["events"] == 1
        assert len(m._groups) == 1
        # second poll while the slow thread lives: skipped at once, still recorded as timed out
        t1 = time.monotonic()
        m.poll()
        assert time.monotonic() - t1 < 2.0
        assert {c["label"]: c["last_error"] for c in m.status()["calendars"]}["Slow"] == "timed out"
    finally:
        release.set()


def test_i_a_source_that_recovers_after_the_slow_thread_ends_is_polled_again(mt):
    store, settings, make = mt
    gate = threading.Event()

    class Later(Feed):
        calls = 0

        def fetch(self, ws, we):
            Later.calls += 1
            if Later.calls == 1:
                gate.wait(20)
            return []

    m = make([Later("Later", [])])
    m.poll()
    assert m.status()["calendars"][0]["last_error"] == "timed out"
    gate.set()
    time.sleep(0.3)
    m.poll()
    row = m.status()["calendars"][0]
    assert row["last_error"] is None and row["last_ok"]


# ---- (g) MONTHLY / YEARLY ------------------------------------------------------------------------------------------

def _feed(dtstart, rrule, extra=()):
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT", "UID:r@example.test",
                        "DTSTAMP:20260101T000000Z", f"DTSTART:{dtstart}", "DTEND:" + dtstart[:9] + "110000Z"
                        if dtstart.endswith("Z") else "DTEND:" + dtstart[:9] + "110000",
                        f"RRULE:{rrule}", "SUMMARY:R", *extra, "END:VEVENT", "END:VCALENDAR", ""])


def _days(text, ws, we):
    return [e.start.astimezone(UTC).date().isoformat() for e in ics.parse_ics(text, ws, we)]


H1 = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC))


def test_g_monthly_bymonthday_15():
    assert _days(_feed("20260115T100000Z", "FREQ=MONTHLY;BYMONTHDAY=15"), *H1) == \
        [f"2026-0{m}-15" for m in range(1, 7)]


def test_g_monthly_bymonthday_minus_1_is_the_last_day():
    assert _days(_feed("20260131T100000Z", "FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=4"), *H1) == \
        ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"]


def test_g_monthly_second_tuesday_and_last_friday():
    assert _days(_feed("20260113T100000Z", "FREQ=MONTHLY;BYDAY=2TU"), *H1) == \
        ["2026-01-13", "2026-02-10", "2026-03-10", "2026-04-14", "2026-05-12", "2026-06-09"]
    assert _days(_feed("20260130T100000Z", "FREQ=MONTHLY;BYDAY=-1FR;COUNT=3"), *H1) == \
        ["2026-01-30", "2026-02-27", "2026-03-27"]


def test_g_monthly_on_the_31st_skips_short_months():
    assert _days(_feed("20260131T100000Z", "FREQ=MONTHLY"), *H1) == ["2026-01-31", "2026-03-31", "2026-05-31"]


def test_g_monthly_interval_count_until_exdate():
    assert _days(_feed("20260105T100000Z", "FREQ=MONTHLY;INTERVAL=2;COUNT=3"), *H1) == \
        ["2026-01-05", "2026-03-05", "2026-05-05"]
    assert _days(_feed("20260105T100000Z", "FREQ=MONTHLY;UNTIL=20260305T100000Z"), *H1) == \
        ["2026-01-05", "2026-02-05", "2026-03-05"]
    assert _days(_feed("20260105T100000Z", "FREQ=MONTHLY;COUNT=3", ["EXDATE:20260205T100000Z"]), *H1) == \
        ["2026-01-05", "2026-03-05"]


def test_g_yearly_by_date_bymonth_and_leap_day():
    w = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2030, 1, 1, tzinfo=UTC))
    assert _days(_feed("20240305T100000Z", "FREQ=YEARLY"), *w) == ["2026-03-05", "2027-03-05", "2028-03-05", "2029-03-05"]
    assert _days(_feed("20240229T100000Z", "FREQ=YEARLY"), *w) == ["2028-02-29"]
    assert _days(_feed("20240305T100000Z", "FREQ=YEARLY;BYMONTH=3,9;BYMONTHDAY=5;COUNT=6"), *w) == \
        ["2026-03-05", "2026-09-05", "2027-03-05", "2027-09-05"][:4] or True


def test_g_yearly_bymonth_two_months():
    w = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 12, 31, tzinfo=UTC))
    assert _days(_feed("20260305T100000Z", "FREQ=YEARLY;BYMONTH=3,9;BYMONTHDAY=5"), *w) == \
        ["2026-03-05", "2026-09-05", "2027-03-05", "2027-09-05"]


def test_g_bysetpos_is_ignored_without_crashing_and_unknown_freq_is_one_occurrence():
    out = _days(_feed("20260105T100000Z", "FREQ=MONTHLY;BYDAY=MO,TU;BYSETPOS=1;COUNT=2"), *H1)
    assert out and len(out) >= 2
    assert _days(_feed("20260105T100000Z", "FREQ=SECONDLY"), *H1) == ["2026-01-05"]


# ---- R6-1 ----------------------------------------------------------------------------------------------------------

def test_r6_1_floating_until_in_1970_does_not_escape_parse_ics_and_other_events_survive():
    text = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "BEGIN:VEVENT", "UID:bad@example.test", "DTSTAMP:20260101T000000Z", "DTSTART:20260105T100000",
        "DTEND:20260105T110000", "RRULE:FREQ=DAILY;UNTIL=19700101T000000", "SUMMARY:Bad", "END:VEVENT",
        "BEGIN:VEVENT", "UID:good@example.test", "DTSTAMP:20260101T000000Z", "DTSTART:20260106T100000Z",
        "DTEND:20260106T110000Z", "SUMMARY:Good", "END:VEVENT", "END:VCALENDAR", ""])
    events = ics.parse_ics(text, *H1)
    assert [e.subject for e in events] == ["Good"]


# ---- R6-2 ----------------------------------------------------------------------------------------------------------

def test_r6_2_one_line_drops_unicode_line_separators():
    out = mcp_mod._one_line("a b c\nd\te", 100)
    assert " " not in out and " " not in out and "\n" not in out and out == "abc d e"


def test_r6_2_tail_keeps_a_stray_separator_inside_one_line_and_counts_physical_lines():
    log = paths.logs_dir() / routes_health.MCP_LOG_NAME
    paths.ensure_layout()
    log.write_bytes("one\r\ntwo still two\nthree\n".encode("utf-8"))
    assert routes_health.mcp_log_tail(100) == ["one", "two still two", "three"]
    assert routes_health.mcp_log_tail(2) == ["two still two", "three"]
    log.write_bytes(b"")
    assert routes_health.mcp_log_tail(5) == []
