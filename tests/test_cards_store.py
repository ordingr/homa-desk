"""Card model and store (plan 7.1 M1-1; PRD section 7, AC-OV-6/9/10/12/13, AC-CS-11 validation).

Time is injected: nothing here sleeps. Interface under test (plan 7.1):
CardStore.upsert/get/list/patch/snooze/dismiss/mark_seen/press/events_since/set_mute/subscribe.
"""
import json
import logging
import math
import threading
import uuid

import pytest

cards = pytest.importorskip("homa.cards", reason="homa/cards (M1-1) not present")
store_mod = pytest.importorskip("homa.cards.store", reason="homa/cards/store.py (M1-1) not present")

Card = cards.Card
CardStore = store_mod.CardStore
CardValidationError = cards.CardValidationError

T0 = 1_800_000_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "state.json"


@pytest.fixture
def store(state_path, clock):
    return CardStore(state_path, clock=clock)


def _ids(store_):
    return [c.id for c in store_.list()]


# ---- defaults by kind, update in place (PRD section 7) ------------------------------

@pytest.mark.parametrize("kind,priority,sticky", [
    ("waiting", 4, True), ("meeting", 4, True), ("done", 2, False),
    ("reminder", 3, False), ("custom", 3, False),
])
def test_kind_defaults(store, kind, priority, sticky):
    card = store.upsert(Card(title="t", kind=kind), "http:test")
    assert card.id
    assert card.effective_priority == priority
    assert card.effective_sticky is sticky
    assert card.source == "http:test"
    assert card.created_at == T0


def test_default_kind_is_custom(store):
    assert store.upsert(Card(title="x"), "mcp").kind == "custom"


def test_explicit_sticky_and_priority_override_kind_defaults(store):
    c = store.upsert(Card(title="x", kind="done", sticky=True, priority=5), "file")
    assert c.effective_sticky is True and c.effective_priority == 5


def test_upsert_with_existing_id_updates_in_place_and_keeps_seen_state(store, clock):
    store.upsert(Card(id="s1", title="first", kind="waiting"), "hooks")
    clock.advance(3)
    store.mark_seen("s1")
    clock.advance(2)
    updated = store.upsert(Card(id="s1", title="second", detail="d", kind="waiting"), "hooks")
    assert _ids(store) == ["s1"]
    assert updated.title == "second" and updated.detail == "d"
    assert updated.seen_at == T0 + 3, "update must not reset seen state unless renotify"
    assert updated.created_at == T0


def test_upsert_renotify_resets_seen(store, clock):
    store.upsert(Card(id="s1", title="first", kind="waiting"), "hooks")
    store.mark_seen("s1")
    again = store.upsert(Card(id="s1", title="again", kind="waiting"), "hooks", renotify=True)
    assert again.seen_at is None


def test_get_returns_a_copy_not_the_live_object(store):
    store.upsert(Card(id="a", title="orig"), "http:x")
    got = store.get("a")
    got.title = "mutated"
    assert store.get("a").title == "orig"


def test_get_unknown_id_raises_card_not_found(store):
    with pytest.raises(store_mod.CardNotFound):
        store.get("nope")


# ---- fade of done / reminder / custom cards (AC-OV-6) -------------------------------

def test_done_card_expires_20s_after_mark_seen_not_before(store, clock):
    store.upsert(Card(id="d", title="finished", kind="done"), "hooks")
    clock.advance(600)
    assert _ids(store) == ["d"], "an unseen done card never fades"
    store.mark_seen("d")
    clock.advance(19.5)
    assert _ids(store) == ["d"]
    clock.advance(1.0)
    assert _ids(store) == []


@pytest.mark.parametrize("kind", ["reminder", "custom"])
def test_reminder_and_custom_fade_too(store, clock, kind):
    store.upsert(Card(id="r", title="t", kind=kind), "http:x")
    store.mark_seen("r")
    clock.advance(20)
    assert _ids(store) == []


@pytest.mark.parametrize("kind", ["waiting", "meeting"])
def test_waiting_and_meeting_never_fade(store, clock, kind):
    store.upsert(Card(id="w", title="t", kind=kind), "hooks")
    store.mark_seen("w")
    clock.advance(3600)
    assert _ids(store) == ["w"]


def test_sticky_done_card_stays(store, clock):
    store.upsert(Card(id="d", title="pinned", kind="done", sticky=True), "hooks")
    store.mark_seen("d")
    clock.advance(3600)
    assert _ids(store) == ["d"]


def test_expires_at_dismisses_the_card_even_if_sticky(store, clock):
    store.upsert(Card(id="m", title="meeting", kind="meeting", expires_at=T0 + 100), "mcp")
    clock.advance(99)
    assert _ids(store) == ["m"]
    clock.advance(2)
    assert _ids(store) == []


# ---- snooze / dismiss (AC-OV-9) -----------------------------------------------------

def test_snooze_5_returns_at_plus_300s_and_blinks_as_new(store, clock):
    store.upsert(Card(id="w", title="t", kind="waiting"), "hooks")
    store.mark_seen("w")
    store.snooze("w", 5)
    assert store.get("w").snoozed_until == T0 + 300
    clock.advance(299)
    assert store.get("w").snoozed_until == T0 + 300, "still snoozed at +299 s"
    clock.advance(2)
    back = store.get("w")
    assert back.snoozed_until is None
    assert back.seen_at is None, "a returning snoozed card is new again (blinks and sounds)"


def test_snooze_15_minutes(store):
    store.upsert(Card(id="w", title="t", kind="waiting"), "hooks")
    assert store.snooze("w", 15).snoozed_until == T0 + 900


def test_dismiss_during_snooze_means_it_never_returns(store, clock):
    store.upsert(Card(id="w", title="t", kind="waiting"), "hooks")
    store.snooze("w", 5)
    store.dismiss("w")
    clock.advance(1000)
    assert _ids(store) == []
    assert store.get("w").snoozed_until is None or store.get("w").dismissed_at is not None


def test_update_during_snooze_reshows_immediately_with_new_content(store, clock):
    store.upsert(Card(id="w", title="old", kind="waiting"), "hooks")
    store.snooze("w", 15)
    clock.advance(10)
    c = store.upsert(Card(id="w", title="new", kind="waiting"), "hooks")
    assert c.snoozed_until is None and c.title == "new"


def test_patch_during_snooze_also_reshows(store):
    store.upsert(Card(id="w", title="old", kind="waiting"), "hooks")
    store.snooze("w", 15)
    assert store.patch("w", detail="more").snoozed_until is None


def test_snooze_rejects_nonpositive_minutes(store):
    store.upsert(Card(id="w", title="t"), "hooks")
    with pytest.raises(CardValidationError) as e:
        store.snooze("w", 0)
    assert e.value.field == "minutes"


def test_dismiss_removes_from_open_list_and_is_idempotent(store):
    store.upsert(Card(id="a", title="a"), "file")
    store.upsert(Card(id="b", title="b"), "file")
    store.dismiss("a")
    store.dismiss("a")
    assert _ids(store) == ["b"]


def test_open_count_excludes_snoozed_and_dismissed(store):
    """AC-OV-10: the icon's red number counts open (visible, not snoozed, not dismissed) cards."""
    if not hasattr(store, "open_count"):
        pytest.skip("CardStore.open_count is not part of the fixed interface")
    for n in "abc":
        store.upsert(Card(id=n, title=n, kind="waiting"), "hooks")
    assert store.open_count() == 3
    store.snooze("a", 5)
    store.dismiss("b")
    assert store.open_count() == 1


def test_list_is_oldest_first(store, clock):
    for n in "abc":
        store.upsert(Card(id=n, title=n), "file")
        clock.advance(1)
    assert _ids(store) == ["a", "b", "c"]


def test_reusing_a_dismissed_id_creates_a_fresh_card(store, clock):
    store.upsert(Card(id="a", title="one", kind="waiting"), "hooks")
    store.mark_seen("a")
    store.dismiss("a")
    clock.advance(5)
    c = store.upsert(Card(id="a", title="two", kind="waiting"), "hooks")
    assert c.seen_at is None and c.dismissed_at is None and c.created_at == T0 + 5
    assert _ids(store) == ["a"]


# ---- mute (AC-OV-12) ----------------------------------------------------------------

def test_cards_arriving_while_muted_are_queued_and_counted(store, clock):
    store.set_mute(T0 + 1800)
    store.upsert(Card(id="a", title="a", kind="waiting"), "hooks")
    store.upsert(Card(id="b", title="b", kind="done"), "hooks")
    assert _ids(store) == ["a", "b"], "list() counts muted cards"
    if hasattr(store, "visible"):
        assert store.visible() == [], "nothing is shown while muted"
    if hasattr(store, "queued_count"):
        assert store.queued_count() == 2


def test_timed_mute_ends_by_itself_and_queued_cards_become_visible(store, clock):
    store.set_mute(T0 + 1800)
    store.upsert(Card(id="a", title="a", kind="waiting"), "hooks")
    clock.advance(1799)
    if hasattr(store, "is_muted"):
        assert store.is_muted() is True
    clock.advance(2)
    if hasattr(store, "is_muted"):
        assert store.is_muted() is False
    if hasattr(store, "visible"):
        assert [c.id for c in store.visible()] == ["a"]


def test_set_mute_none_unmutes(store):
    store.set_mute(math.inf)
    store.upsert(Card(id="a", title="a"), "hooks")
    store.set_mute(None)
    if hasattr(store, "visible"):
        assert [c.id for c in store.visible()] == ["a"]


def test_mute_survives_restart(state_path, clock):
    s1 = CardStore(state_path, clock=clock)
    s1.set_mute(T0 + 3600)
    s1.upsert(Card(id="a", title="a", kind="waiting"), "hooks")
    s2 = CardStore(state_path, clock=clock)
    assert [c.id for c in s2.list()] == ["a"]
    if hasattr(s2, "is_muted"):
        assert s2.is_muted() is True
        clock.advance(3601)
        assert s2.is_muted() is False


# ---- persistence (AC-OV-13) ---------------------------------------------------------

def test_reload_reproduces_open_cards_with_seen_and_snooze_state(state_path, clock):
    s1 = CardStore(state_path, clock=clock)
    s1.upsert(Card(id="seen", title="seen", kind="waiting", detail="d",
                   actions=[{"id": "go", "label": "Go"}], url="https://example.test/x"), "hooks")
    clock.advance(0.1)
    s1.upsert(Card(id="snz", title="snoozed", kind="meeting"), "mcp")
    clock.advance(0.1)
    s1.upsert(Card(id="fresh", title="fresh", kind="custom"), "http:cli")
    clock.advance(0.1)
    s1.upsert(Card(id="gone", title="gone", kind="custom"), "file")
    clock.advance(4)
    s1.mark_seen("seen")
    s1.snooze("snz", 15)
    s1.dismiss("gone")

    s2 = CardStore(state_path, clock=clock)
    assert [c.id for c in s2.list()] == ["seen", "snz", "fresh"]
    seen, snz, fresh = (s2.get(i) for i in ("seen", "snz", "fresh"))
    assert seen.seen_at == pytest.approx(T0 + 4.3) and seen.detail == "d" and seen.url == "https://example.test/x"
    assert seen.actions == [{"id": "go", "label": "Go"}]
    assert seen.source == "hooks"
    assert snz.snoozed_until == pytest.approx(T0 + 4.3 + 900)
    assert fresh.seen_at is None
    assert seen.created_at == T0


def test_state_json_is_written_in_the_assistant_home_layout(state_path, clock):
    CardStore(state_path, clock=clock).upsert(Card(id="a", title="a"), "file")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert "a" in json.dumps(data)


def test_snooze_that_expired_while_the_app_was_down_returns_on_start(state_path, clock):
    s1 = CardStore(state_path, clock=clock)
    s1.upsert(Card(id="w", title="t", kind="waiting"), "hooks")
    s1.snooze("w", 5)
    clock.advance(1000)
    s2 = CardStore(state_path, clock=clock)
    assert s2.get("w").snoozed_until is None


def test_corrupt_state_file_is_set_aside_and_store_starts_empty(state_path, clock):
    state_path.write_text("{definitely not json", encoding="utf-8")
    s = CardStore(state_path, clock=clock)
    assert s.list() == []
    s.upsert(Card(id="a", title="a"), "file")
    assert _ids(CardStore(state_path, clock=clock)) == ["a"]


def test_unknown_keys_in_state_json_are_ignored(state_path, clock):
    """Plan 7.1 rollback note: state.json is forward-compatible."""
    CardStore(state_path, clock=clock).upsert(Card(id="a", title="a"), "file")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    data["future_feature"] = {"x": 1}
    for c in data["cards"]:
        c["future_card_field"] = 7
    state_path.write_text(json.dumps(data), encoding="utf-8")
    assert _ids(CardStore(state_path, clock=clock)) == ["a"]


# ---- validation (AC-CS-11, PRD section 7) -------------------------------------------

def test_501_char_detail_is_rejected_with_field_detail():
    with pytest.raises(CardValidationError) as e:
        Card.from_dict({"title": "t", "detail": "x" * 501})
    assert e.value.field == "detail"


def test_500_char_detail_is_accepted():
    assert len(Card.from_dict({"title": "t", "detail": "x" * 500}).detail) == 500


def test_title_is_required_and_capped_at_120():
    with pytest.raises(CardValidationError) as e:
        Card.from_dict({"detail": "no title"})
    assert e.value.field == "title"
    with pytest.raises(CardValidationError) as e:
        Card.from_dict({"title": "x" * 121})
    assert e.value.field == "title"
    assert Card.from_dict({"title": "x" * 120}).title == "x" * 120


@pytest.mark.parametrize("body,field", [
    ({"title": "t", "kind": "urgent"}, "kind"),
    ({"title": "t", "priority": 0}, "priority"),
    ({"title": "t", "priority": 6}, "priority"),
    ({"title": "t", "priority": "high"}, "priority"),
    ({"title": "t", "sticky": "yes"}, "sticky"),
    ({"title": "t", "actions": [{"id": str(i), "label": "l"} for i in range(4)]}, "actions"),
    ({"title": "t", "actions": [{"label": "no id"}]}, "actions"),
    ({"title": "t", "expires_at": "tomorrow"}, "expires_at"),
])
def test_field_level_validation_errors(body, field):
    with pytest.raises(CardValidationError) as e:
        Card.from_dict(body)
    assert e.value.field == field
    assert e.value.to_dict()["error"]["field"] == field


def test_non_object_body_is_rejected():
    with pytest.raises(CardValidationError):
        Card.from_dict(["not", "an", "object"])


def test_patch_validates_fields(store):
    store.upsert(Card(id="a", title="a"), "file")
    with pytest.raises(CardValidationError) as e:
        store.patch("a", detail="x" * 501)
    assert e.value.field == "detail"
    assert store.get("a").detail in (None, "")


def test_unknown_fields_are_ignored_and_logged_once(caplog):
    name = "zz_" + uuid.uuid4().hex[:8]
    with caplog.at_level(logging.DEBUG, logger="homa.cards"):
        a = Card.from_dict({"title": "t", name: 1})
        b = Card.from_dict({"title": "t", name: 2})
    assert a.title == b.title == "t"
    assert not hasattr(a, name)
    hits = [r for r in caplog.records if name in r.getMessage()]
    assert len(hits) == 1


def test_host_fields_cannot_be_forged_by_a_client():
    c = Card.from_dict({"title": "t", "source": "hooks", "created_at": 5, "dismissed_at": 9})
    assert c.source is None and c.created_at is None and c.dismissed_at is None


# ---- events cursor (PRD section 8 GET /v1/cards/{id}/events) ------------------------

def test_events_since_returns_only_newer_events_in_order(store, clock):
    store.upsert(Card(id="a", title="a", actions=[{"id": "ok", "label": "OK"}]), "http:x")
    store.mark_seen("a")
    first = store.events_since("a", 0)
    assert [e["type"] for e in first] == ["seen"]
    cursor = first[-1]["seq"]
    store.press("a", "ok")
    store.snooze("a", 5)
    later = store.events_since("a", cursor)
    assert [e["type"] for e in later] == ["pressed", "snoozed"]
    assert later[0]["action_id"] == "ok"
    assert all("at" in e for e in later)
    assert store.events_since("a", later[-1]["seq"]) == []


def test_dismiss_is_recorded_and_readable_after_the_card_is_gone(store):
    store.upsert(Card(id="a", title="a"), "http:x")
    store.dismiss("a")
    assert "dismissed" in [e["type"] for e in store.events_since("a", 0)]


def test_press_unknown_action_is_rejected(store):
    store.upsert(Card(id="a", title="a", actions=[{"id": "ok", "label": "OK"}]), "http:x")
    with pytest.raises(CardValidationError):
        store.press("a", "nope")


def test_events_since_unknown_card_raises(store):
    with pytest.raises(store_mod.CardNotFound):
        store.events_since("nope", 0)


# ---- subscribe ----------------------------------------------------------------------

def test_subscribers_run_on_the_callers_thread_and_see_changes(store):
    seen = []
    thread_ids = []

    def cb(event, card):
        seen.append((event, card.id if card else None))
        thread_ids.append(threading.get_ident())

    store.subscribe(cb)
    store.upsert(Card(id="a", title="a"), "file")
    store.mark_seen("a")
    store.dismiss("a")
    assert [e for e, _ in seen] == ["added", "seen", "dismissed"]
    assert set(thread_ids) == {threading.get_ident()}


def test_a_failing_subscriber_does_not_break_the_store_or_other_subscribers(store):
    got = []

    def bad(event, card):
        raise RuntimeError("expected by the test")

    store.subscribe(bad)
    store.subscribe(lambda event, card: got.append(event))
    store.upsert(Card(id="a", title="a"), "file")
    assert got == ["added"]
    assert _ids(store) == ["a"]


def test_store_is_usable_from_several_threads(store):
    def work(n):
        for i in range(25):
            store.upsert(Card(id=f"{n}-{i}", title="t"), "http:x")

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(store.list()) == 100
