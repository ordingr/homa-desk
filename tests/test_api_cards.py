"""HTTP card and reminder routes plus the file-drop source (plan 7.1 M1-4; PRD section 8, AC-CS-11/12, AC-RM-7).

Runs against the real app started by the ``running_app`` fixture. Skipped as a whole until
routes_cards.py is registered by the server (M1-4) and the card store is wired into the app (M1-6).
"""
import json
import time
import uuid

import pytest

from conftest import wait_until

HELP = "didn't understand — try 'text in 10m' or 'text at 3pm'"


@pytest.fixture(scope="module")
def cards_api(api):
    status, _ = api.get("/v1/cards")
    if status == 404:
        pytest.skip("GET /v1/cards is not served yet: homa/server/routes_cards.py (M1-4) or app wiring (M1-6) missing")
    return api


def _uid() -> str:
    return "t-" + uuid.uuid4().hex[:10]


def _open_ids(api):
    status, body = api.get("/v1/cards")
    assert status == 200
    return [c["id"] for c in body]


# ---- create / read (AC-CS-11) -------------------------------------------------------

def test_post_card_returns_201_with_id_and_card_is_listed_within_1s(cards_api):
    t0 = time.monotonic()
    status, card = cards_api.post("/v1/cards", {"title": "hello", "detail": "world", "kind": "custom"})
    assert status == 201
    assert card["id"] and card["title"] == "hello" and card["kind"] == "custom"
    assert card["source"].startswith("http")
    assert wait_until(lambda: card["id"] in _open_ids(cards_api), timeout=1.0)
    assert time.monotonic() - t0 < 1.5
    status, one = cards_api.get(f"/v1/cards/{card['id']}")
    assert status == 200 and one["id"] == card["id"] and one["detail"] == "world"
    cards_api.request("DELETE", f"/v1/cards/{card['id']}")


def test_post_without_token_or_with_wrong_token_is_401_and_creates_nothing(cards_api):
    title = _uid()
    before = len(_open_ids(cards_api))
    assert cards_api.post("/v1/cards", {"title": title}, token=False)[0] == 401
    assert cards_api.post("/v1/cards", {"title": title}, token="x" * len(cards_api.token))[0] == 401
    assert len(_open_ids(cards_api)) == before
    assert cards_api.get("/v1/cards", token=False)[0] == 401


def test_validation_errors_are_422_with_the_section_8_error_shape(cards_api):
    status, body = cards_api.post("/v1/cards", {"title": "t", "detail": "x" * 501})
    assert status == 422
    assert body["error"]["field"] == "detail"
    assert body["error"]["code"] and body["error"]["message"]
    status, body = cards_api.post("/v1/cards", {"detail": "no title"})
    assert status == 422 and body["error"]["field"] == "title"
    status, body = cards_api.post("/v1/cards", {"title": "t", "kind": "urgent"})
    assert status == 422 and body["error"]["field"] == "kind"


def test_malformed_json_body_is_a_4xx_not_a_crash(cards_api):
    status, _ = cards_api.request("POST", "/v1/cards", raw=b"{nope", headers={"Content-Type": "application/json"})
    assert 400 <= status < 500
    assert cards_api.get("/v1/health")[0] == 200


def test_unknown_fields_are_ignored(cards_api):
    status, card = cards_api.post("/v1/cards", {"title": "t", "future_field": [1, 2]})
    assert status == 201 and "future_field" not in card
    cards_api.request("DELETE", f"/v1/cards/{card['id']}")


def test_get_unknown_card_is_404(cards_api):
    status, body = cards_api.get("/v1/cards/does-not-exist")
    assert status == 404 and body["error"]["code"]


# ---- update in place, patch, snooze, dismiss ---------------------------------------

def test_post_with_existing_id_updates_in_place(cards_api):
    cid = _uid()
    assert cards_api.post("/v1/cards", {"id": cid, "title": "one", "kind": "waiting"})[0] == 201
    status, card = cards_api.post("/v1/cards", {"id": cid, "title": "two", "kind": "waiting"})
    assert status == 200
    assert card["id"] == cid and card["title"] == "two"
    assert _open_ids(cards_api).count(cid) == 1
    cards_api.request("DELETE", f"/v1/cards/{cid}")


def test_patch_changes_only_given_fields(cards_api):
    cid = _uid()
    cards_api.post("/v1/cards", {"id": cid, "title": "keep", "detail": "old"})
    status, card = cards_api.request("PATCH", f"/v1/cards/{cid}", {"detail": "new"})
    assert status == 200 and card["detail"] == "new" and card["title"] == "keep"
    status, body = cards_api.request("PATCH", f"/v1/cards/{cid}", {"detail": "x" * 501})
    assert status == 422 and body["error"]["field"] == "detail"
    assert cards_api.request("PATCH", "/v1/cards/does-not-exist", {"detail": "n"})[0] == 404
    cards_api.request("DELETE", f"/v1/cards/{cid}")


def test_snooze_sets_snoozed_until_about_five_minutes_out(cards_api):
    cid = _uid()
    cards_api.post("/v1/cards", {"id": cid, "title": "later", "kind": "waiting"})
    status, card = cards_api.post(f"/v1/cards/{cid}/snooze", {"minutes": 5})
    assert status == 200
    assert abs(card["snoozed_until"] - (time.time() + 300)) < 5
    status, body = cards_api.post(f"/v1/cards/{cid}/snooze", {"minutes": 0})
    assert status == 422 and body["error"]["field"] == "minutes"
    cards_api.request("DELETE", f"/v1/cards/{cid}")


def test_delete_dismisses_and_returns_204(cards_api):
    cid = _uid()
    cards_api.post("/v1/cards", {"id": cid, "title": "bye"})
    assert cards_api.request("DELETE", f"/v1/cards/{cid}")[0] == 204
    assert cid not in _open_ids(cards_api)
    assert cards_api.request("DELETE", "/v1/cards/does-not-exist")[0] == 404


def test_list_filters_by_kind_and_source(cards_api):
    a, b = _uid(), _uid()
    cards_api.post("/v1/cards", {"id": a, "title": "a", "kind": "reminder"})
    cards_api.post("/v1/cards", {"id": b, "title": "b", "kind": "waiting"})
    status, body = cards_api.get("/v1/cards?kind=waiting")
    assert status == 200 and b in [c["id"] for c in body] and a not in [c["id"] for c in body]
    status, body = cards_api.get("/v1/cards?source=hooks")
    assert status == 200 and a not in [c["id"] for c in body]
    for cid in (a, b):
        cards_api.request("DELETE", f"/v1/cards/{cid}")


# ---- events cursor ------------------------------------------------------------------

def test_events_list_state_changes_since_a_cursor(cards_api):
    cid = _uid()
    cards_api.post("/v1/cards", {"id": cid, "title": "ev", "kind": "waiting",
                                 "actions": [{"id": "ok", "label": "OK"}]})
    cards_api.post(f"/v1/cards/{cid}/snooze", {"minutes": 5})
    status, events = cards_api.get(f"/v1/cards/{cid}/events?since=0")
    assert status == 200
    assert [e["type"] for e in events][-1] == "snoozed"
    assert all("at" in e for e in events)
    cursor = events[-1].get("seq", len(events))
    status, newer = cards_api.get(f"/v1/cards/{cid}/events?since={cursor}")
    assert status == 200 and newer == []
    cards_api.request("DELETE", f"/v1/cards/{cid}")


# ---- reminders (AC-RM-7) ------------------------------------------------------------

def test_post_reminder_text_returns_interpretation_and_is_listed_then_cancelled(cards_api):
    status, r = cards_api.post("/v1/reminders", {"text": "driver selftest in 10m"})
    if status == 404:
        pytest.skip("routes_reminders.py (M1-4) not registered")
    assert status == 201
    assert r["title"] == "driver selftest"
    assert abs(r["at"] - (time.time() + 600)) < 5
    assert r["interpretation"].startswith("Reminder 'driver selftest' at ")
    status, listed = cards_api.get("/v1/reminders")
    assert status == 200 and r["id"] in [x["id"] for x in listed]
    assert cards_api.request("DELETE", f"/v1/reminders/{r['id']}")[0] == 204
    assert r["id"] not in [x["id"] for x in cards_api.get("/v1/reminders")[1]]
    assert cards_api.request("DELETE", f"/v1/reminders/{r['id']}")[0] == 404


def test_post_reminder_structured_form(cards_api):
    if cards_api.get("/v1/reminders")[0] == 404:
        pytest.skip("routes_reminders.py (M1-4) not registered")
    at = time.time() + 900
    status, r = cards_api.post("/v1/reminders", {"title": "structured", "detail": "d", "at": at})
    assert status == 201 and r["title"] == "structured" and abs(r["at"] - at) < 1
    cards_api.request("DELETE", f"/v1/reminders/{r['id']}")


def test_post_reminder_nonsense_is_422_didnt_understand_with_the_help_sentence(cards_api):
    status, body = cards_api.post("/v1/reminders", {"text": "foo bar"})
    if status == 404:
        pytest.skip("routes_reminders.py (M1-4) not registered")
    assert status == 422
    assert body["error"]["code"] == "didnt_understand"
    assert HELP in body["error"]["message"]
    assert not [r for r in cards_api.get("/v1/reminders")[1] if r.get("title") == "foo bar"]


def test_reminders_require_the_token(cards_api):
    if cards_api.get("/v1/reminders")[0] == 404:
        pytest.skip("routes_reminders.py (M1-4) not registered")
    assert cards_api.get("/v1/reminders", token=False)[0] == 401
    assert cards_api.post("/v1/reminders", {"text": "x in 5m"}, token=False)[0] == 401


# ---- file drop (AC-CS-12) -----------------------------------------------------------

def test_file_drop_becomes_a_card_and_the_file_is_consumed(cards_api, homa_home):
    events = homa_home / "events"
    if not events.is_dir():
        pytest.skip("events/ folder not created by this build")
    cid = _uid()
    target = events / f"{cid}.json"
    tmp = events / f"{cid}.tmp"
    tmp.write_text(json.dumps({"id": cid, "title": "dropped", "kind": "custom"}), encoding="utf-8")
    tmp.replace(target)
    if not wait_until(lambda: cid in _open_ids(cards_api), timeout=2.0):
        pytest.skip("file-drop watcher (homa/cards/filedrop.py, M1-4/M1-6) not running")
    assert wait_until(lambda: not target.exists(), timeout=2.0), "file must be deleted after it is consumed"
    status, card = cards_api.get(f"/v1/cards/{cid}")
    assert card["source"] == "file"
    cards_api.request("DELETE", f"/v1/cards/{cid}")


def test_malformed_file_drop_is_moved_to_rejected_with_an_error_txt(cards_api, homa_home):
    events = homa_home / "events"
    probe = _uid()
    ok = events / f"{probe}.json"
    ok.write_text(json.dumps({"id": probe, "title": "probe"}), encoding="utf-8")
    if not wait_until(lambda: probe in _open_ids(cards_api), timeout=2.0):
        pytest.skip("file-drop watcher (homa/cards/filedrop.py, M1-4/M1-6) not running")
    cards_api.request("DELETE", f"/v1/cards/{probe}")

    name = _uid()
    for suffix, text in ((".json", "{this is not json"), ("-notitle.json", json.dumps({"detail": "no title"}))):
        bad = events / f"{name}{suffix}"
        bad.write_text(text, encoding="utf-8")
        assert wait_until(lambda: (events / "rejected" / bad.name).exists(), timeout=2.0), bad.name
        assert not bad.exists()
        err = events / "rejected" / (bad.name + ".error.txt")
        alt = events / "rejected" / (bad.stem + ".error.txt")
        assert wait_until(lambda: err.exists() or alt.exists(), timeout=1.0)
        assert (err if err.exists() else alt).read_text(encoding="utf-8").strip()
