"""Reminder grammar (PRD DEC 6, AC-RM-2, AC-RM-3) and the reminder store (AC-RM-5/6, M1-2)."""
import json
import time
from datetime import datetime, timedelta

import pytest

grammar = pytest.importorskip("homa.reminders.grammar", reason="homa/reminders/grammar.py (M1-2) not present")

HELP = "didn't understand — try 'text in 10m' or 'text at 3pm'"

# Wednesday 2026-11-04 14:22:10 local time (naive datetimes, as the app uses local time).
NOW = datetime(2026, 11, 4, 14, 22, 10)
assert NOW.weekday() == 2


def _at(parsed) -> datetime:
    return datetime.fromtimestamp(parsed.at)


# ---- shape 1: <text> in <N>[m|h|d] -------------------------------------------------

def test_in_minutes_exact_interpretation_from_the_prd():
    p = grammar.parse("driver selftest in 10m", NOW)
    assert p.title == "driver selftest"
    assert _at(p) == NOW + timedelta(minutes=10)
    assert p.interpretation == "Reminder 'driver selftest' at 14:32 today"


@pytest.mark.parametrize("text,delta", [
    ("call mum in 2h", timedelta(hours=2)),
    ("renew licence in 3d", timedelta(days=3)),
    ("stretch in 1m", timedelta(minutes=1)),
])
def test_in_hours_days_minutes(text, delta):
    assert _at(grammar.parse(text, NOW)) == NOW + delta


def test_title_keeps_inner_words_that_look_like_keywords():
    p = grammar.parse("meet the team at the pub at 6pm", NOW)
    assert p.title == "meet the team at the pub"
    assert _at(p) == NOW.replace(hour=18, minute=0, second=0)


# ---- shape 2: <text> at <h[:mm]>[am|pm] --------------------------------------------

def test_at_time_still_ahead_today():
    p = grammar.parse("standup at 3pm", NOW)
    assert _at(p) == NOW.replace(hour=15, minute=0, second=0, microsecond=0)
    assert p.interpretation.endswith("today")


def test_at_time_already_past_today_means_tomorrow():
    p = grammar.parse("standup at 9am", NOW)
    assert _at(p) == (NOW + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    assert "tomorrow" in p.interpretation


def test_at_24_hour_time_without_am_pm():
    p = grammar.parse("deploy at 17:45", NOW)
    assert _at(p) == NOW.replace(hour=17, minute=45, second=0, microsecond=0)
    assert _at(grammar.parse("deploy at 08:05", NOW)).day == NOW.day + 1  # 08:05 is past -> tomorrow


def test_at_with_minutes_and_am_pm():
    assert _at(grammar.parse("tea at 3:30pm", NOW)).strftime("%H:%M") == "15:30"
    assert _at(grammar.parse("tea at 12am", NOW)).hour == 0
    assert _at(grammar.parse("tea at 12pm", NOW + timedelta(hours=-3))).hour == 12


# ---- shape 3: <text> tomorrow <h[:mm]>[am|pm] --------------------------------------

def test_tomorrow():
    p = grammar.parse("dentist tomorrow 10:15am", NOW)
    assert _at(p) == (NOW + timedelta(days=1)).replace(hour=10, minute=15, second=0, microsecond=0)
    assert p.title == "dentist"


def test_tomorrow_24_hour():
    assert _at(grammar.parse("gym tomorrow 18:00", NOW)).hour == 18


# ---- shape 4: <text> <weekday> <h[:mm]>[am|pm] -------------------------------------

def test_weekday_is_next_occurrence():
    p = grammar.parse("review friday 9am", NOW)  # NOW is a Wednesday
    assert _at(p) == datetime(2026, 11, 6, 9, 0)


def test_weekday_today_if_time_still_ahead():
    p = grammar.parse("review wednesday 4pm", NOW)
    assert _at(p) == datetime(2026, 11, 4, 16, 0)


def test_weekday_today_but_time_passed_means_next_week():
    p = grammar.parse("review wednesday 9am", NOW)
    assert _at(p) == datetime(2026, 11, 11, 9, 0)


def test_weekday_earlier_in_week_wraps_to_next_week():
    assert _at(grammar.parse("retro monday 10:00", NOW)) == datetime(2026, 11, 9, 10, 0)


# ---- failures (AC-RM-3) ------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "foo bar",
    "",
    "   ",
    "just words",
    "call mum in",
    "call mum in tenm",
    "call mum in 10x",
    "call mum at",
    "call mum at 25:00",
    "call mum at 13pm",
    "call mum at 3:75pm",
    "call mum someday 3pm",
    "in 10m",
])
def test_unparseable_raises_with_the_exact_help_sentence(text):
    with pytest.raises(grammar.DidntUnderstand) as info:
        grammar.parse(text, NOW)
    assert str(info.value) == HELP
    assert info.value.help_sentence == HELP


def test_help_sentence_constant_matches_prd_wording():
    assert grammar.HELP_SENTENCE == HELP


def test_parse_is_pure_and_does_not_guess_a_time():
    """Two parses of the same text and clock give the same answer; failure never returns a guessed time."""
    assert grammar.parse("x in 5m", NOW) == grammar.parse("x in 5m", NOW)
    with pytest.raises(grammar.DidntUnderstand):
        grammar.parse("x soon", NOW)


# ---- reminders store: persistence and overdue handling (AC-RM-5/6) -----------------

class _Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def reminders_mod():
    return pytest.importorskip("homa.reminders.store", reason="homa/reminders/store.py (M1-2) not present")


@pytest.fixture
def sched():
    mod = pytest.importorskip("homa.scheduler", reason="homa/scheduler.py not present")
    s = mod.Scheduler()
    s.start()
    yield s
    s.stop()


def _store(mod, sched, path, fired, clock=time.time):
    return mod.ReminderStore(sched, fired.append, path=path, clock=clock)


def test_store_round_trips_reminders_json(reminders_mod, sched, tmp_path):
    path = tmp_path / "reminders.json"
    fired = []
    first = _store(reminders_mod, sched, path, fired)
    r = first.add("water plants", time.time() + 3600, detail="the fern", sticky=True)
    assert path.is_file()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "water plants" in json.dumps(on_disk)

    second = _store(reminders_mod, sched, path, fired)
    assert second.start() == []  # nothing overdue
    listed = second.list()
    assert [x.id for x in listed] == [r.id]
    assert listed[0].title == "water plants" and listed[0].detail == "the fern" and listed[0].sticky is True
    assert fired == []


def test_store_fires_at_due_time_and_forgets_it(reminders_mod, sched, tmp_path):
    fired = []
    store = _store(reminders_mod, sched, tmp_path / "reminders.json", fired)
    due = time.time() + 1.0
    store.add("tick", due)
    deadline = time.time() + 5
    while not fired and time.time() < deadline:
        time.sleep(0.02)
    assert [f.title for f in fired] == ["tick"]
    assert abs(time.time() - due) < 1.0
    assert store.list() == []
    assert json.loads((tmp_path / "reminders.json").read_text(encoding="utf-8"))["reminders"] == []


def test_overdue_reminder_fires_on_start_marked_was_due(reminders_mod, sched, tmp_path):
    path = tmp_path / "reminders.json"
    past = _store(reminders_mod, sched, path, [])
    due = time.time() + 600
    past.add("pay invoice", due)

    fired = []
    later = _store(reminders_mod, sched, path, fired, clock=_Clock(due + 120))
    later.start()
    assert len(fired) == 1
    assert fired[0].title.startswith("pay invoice (was due ")
    assert fired[0].title.endswith(")")
    assert datetime.fromtimestamp(due).strftime("%H:%M") in fired[0].title
    assert later.list() == []
    assert "pay invoice" not in path.read_text(encoding="utf-8")  # overdue one is consumed, not re-armed


def test_cancel_removes_and_persists(reminders_mod, sched, tmp_path):
    path = tmp_path / "reminders.json"
    fired = []
    store = _store(reminders_mod, sched, path, fired)
    r = store.add("never", time.time() + 1.0)
    assert store.cancel(r.id) is True
    assert store.cancel(r.id) is False
    time.sleep(1.4)
    assert fired == []
    assert "never" not in path.read_text(encoding="utf-8")


def test_add_text_uses_the_grammar_and_rejects_nonsense(reminders_mod, sched, tmp_path):
    store = _store(reminders_mod, sched, tmp_path / "reminders.json", [])
    r, parsed = store.add_text("driver selftest in 10m")
    assert r.title == "driver selftest"
    assert abs(r.at - (time.time() + 600)) < 5
    assert parsed.interpretation.startswith("Reminder 'driver selftest' at ")
    with pytest.raises(grammar.DidntUnderstand):
        store.add_text("foo bar")
    assert len(store.list()) == 1


def test_corrupt_reminders_json_is_set_aside_not_fatal(reminders_mod, sched, tmp_path):
    path = tmp_path / "reminders.json"
    path.write_text("{not json", encoding="utf-8")
    store = _store(reminders_mod, sched, path, [])
    assert store.start() == []
    assert store.list() == []
