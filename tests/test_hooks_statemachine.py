"""Claude Code hook receiver and per-session state machine (plan 7.1 M1-4; AC-CS-3 .. AC-CS-9).

Payloads: tests/fixtures/hook_payloads/*.json are the recorded P6 POSTs (Claude Code 2.1.317, CLI -p),
with paths replaced by placeholders. Events P6 did NOT observe (StopFailure, Notification, SessionStart,
unknown names) are built in this file from the PRD and are labelled SYNTHETIC; their field names are
assumptions until an interactive P6 run records them.

Each test uses a fresh session_id and a fresh cwd basename, so cards are found by title and tests do not
interfere with each other on the shared running app.
"""
import copy
import json
import os
import re
import time
import uuid

import pytest

from conftest import REPO_ROOT, wait_until

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "hook_payloads"
HOOK = "/v1/hooks/claude-code"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def hooks_api(api):
    status, _ = api.post(HOOK, {"hook_event_name": "SessionStart", "session_id": "probe-" + uuid.uuid4().hex[:6],
                                "cwd": r"C:\Users\<user>\projects\probe"})
    if status == 404:
        pytest.skip("POST /v1/hooks/claude-code is not served yet: routes_hooks.py + claude_sessions.py (M1-4) missing")
    if api.get("/v1/cards")[0] == 404:
        pytest.skip("GET /v1/cards is not served yet (M1-4/M1-6)")
    return api


class Session:
    """One fake Claude Code session: unique id and project folder, payloads derived from a fixture."""

    def __init__(self, api):
        self.api = api
        self.sid = str(uuid.uuid4())
        self.project = "proj-" + uuid.uuid4().hex[:8]
        self.cwd = rf"C:\Users\<user>\projects\{self.project}"

    def payload(self, fixture: str | dict, **over) -> dict:
        p = copy.deepcopy(load(fixture) if isinstance(fixture, str) else fixture)
        p.update(session_id=self.sid, cwd=self.cwd)
        p.update(over)
        return p

    def send(self, fixture: str | dict, **over):
        t0 = time.monotonic()
        status, body = self.api.post(HOOK, self.payload(fixture, **over))
        return status, body, time.monotonic() - t0

    def cards(self):
        status, body = self.api.get("/v1/cards")
        assert status == 200
        return [c for c in body if c["title"] == self.project or self.project in (c.get("title") or "")]

    def of_kind(self, kind):
        return [c for c in self.cards() if c["kind"] == kind]

    def cleanup(self):
        for c in self.cards():
            self.api.request("DELETE", f"/v1/cards/{c['id']}")


@pytest.fixture
def sess(hooks_api):
    s = Session(hooks_api)
    yield s
    s.cleanup()


def _detail(card):
    return card.get("detail") or ""


# ---- response contract (AC-CS-3, PRD section 8) -------------------------------------

@pytest.mark.parametrize("fixture", ["permission_request_write", "permission_request_bash", "user_prompt_submit",
                                     "post_tool_use", "stop", "session_end"])
def test_every_recorded_event_gets_200_empty_object_in_under_1s_with_no_decision(sess, fixture):
    status, body, elapsed = sess.send(fixture)
    assert status == 200
    assert body == {}
    assert elapsed < 1.0, elapsed


def test_response_never_contains_a_decision_key(sess):
    status, body, _ = sess.send("permission_request_bash")
    flat = json.dumps(body).lower()
    for word in ("decision", "behavior", "allow", "deny", "permissiondecision", "hookspecificoutput"):
        assert word not in flat


def test_hook_post_without_token_is_401_and_creates_no_card(hooks_api, sess):
    status, _ = hooks_api.post(HOOK, sess.payload("permission_request_write"), token=False)
    assert status == 401
    time.sleep(0.3)
    assert sess.cards() == []


def test_garbage_body_does_not_crash_the_receiver(hooks_api):
    status, _ = hooks_api.request("POST", HOOK, raw=b"\x00not json", headers={"Content-Type": "application/json"})
    assert status < 500
    status, _ = hooks_api.request("POST", HOOK, raw=b"[1,2,3]", headers={"Content-Type": "application/json"})
    assert status < 500
    assert hooks_api.get("/v1/health")[0] == 200


# ---- PermissionRequest -> waiting card (AC-CS-4, 5) ---------------------------------

def test_permission_request_makes_one_waiting_card_titled_with_the_cwd_basename(sess):
    sess.send("permission_request_write")
    cards = wait_until(lambda: sess.of_kind("waiting"), timeout=2.0)
    assert len(cards) == 1
    card = cards[0]
    assert card["title"] == sess.project
    assert "Write" in _detail(card)
    assert card["source"] == "hooks"
    assert card["timer_started_at"] is not None
    assert abs(card["timer_started_at"] - time.time()) < 3, "timer starts at the event's arrival"


def test_bash_permission_request_detail_summarises_the_command(sess):
    sess.send("permission_request_bash")
    card = wait_until(lambda: sess.of_kind("waiting"), timeout=2.0)[0]
    assert "Bash" in _detail(card)
    assert "curl" in _detail(card)


def test_second_permission_request_for_the_same_session_updates_never_duplicates(sess):
    sess.send("permission_request_write")
    first = wait_until(lambda: sess.of_kind("waiting"), timeout=2.0)[0]
    sess.send("permission_request_bash")
    assert wait_until(lambda: "Bash" in _detail(sess.of_kind("waiting")[0]), timeout=2.0)
    cards = sess.of_kind("waiting")
    assert len(cards) == 1
    assert cards[0]["id"] == first["id"]


def test_two_sessions_get_two_cards(hooks_api, sess):
    other = Session(hooks_api)
    try:
        sess.send("permission_request_write")
        other.send("permission_request_write")
        assert wait_until(lambda: sess.of_kind("waiting") and other.of_kind("waiting"), timeout=2.0)
        assert sess.of_kind("waiting")[0]["id"] != other.of_kind("waiting")[0]["id"]
    finally:
        other.cleanup()


def test_session_title_wins_over_cwd_when_a_client_sends_one(sess):
    sess.send("permission_request_write", session_title="My Named Session")
    status, body = sess.api.get("/v1/cards")
    try:
        titled = [c for c in body if c["title"] == "My Named Session"]
        assert len(titled) == 1
    finally:
        for c in body:
            if c["title"] == "My Named Session":
                sess.api.request("DELETE", f"/v1/cards/{c['id']}")


# ---- clearing (AC-CS-6) -------------------------------------------------------------

@pytest.mark.parametrize("clearing", ["post_tool_use", "user_prompt_submit", "session_end"])
def test_a_later_event_from_the_same_session_clears_the_waiting_card_within_2s(sess, clearing):
    sess.send("permission_request_write")
    assert wait_until(lambda: sess.of_kind("waiting"), timeout=2.0)
    sess.send(clearing)
    assert wait_until(lambda: not sess.of_kind("waiting"), timeout=2.0), "waiting card was not cleared"
    assert sess.cards() == []


def test_an_event_from_another_session_does_not_clear_it(hooks_api, sess):
    other = Session(hooks_api)
    sess.send("permission_request_write")
    assert wait_until(lambda: sess.of_kind("waiting"), timeout=2.0)
    other.send("post_tool_use")
    time.sleep(0.4)
    assert len(sess.of_kind("waiting")) == 1


def test_clearing_event_with_no_open_card_is_harmless(sess):
    status, body, _ = sess.send("post_tool_use")
    assert status == 200 and body == {}
    assert sess.cards() == []


# ---- Stop -> done card (AC-CS-7) ----------------------------------------------------

def test_stop_makes_a_green_done_card_with_160_chars_and_elapsed_time(sess):
    long_msg = "A" * 400
    sess.send("user_prompt_submit")
    sess.send("stop", last_assistant_message=long_msg)
    cards = wait_until(lambda: sess.of_kind("done"), timeout=2.0)
    assert len(cards) == 1
    detail = _detail(cards[0])
    assert "A" * 160 in detail and "A" * 161 not in detail
    assert re.search(r"Task completed in \d+", detail), detail
    assert cards[0]["title"] == sess.project


def test_stop_without_a_prior_prompt_has_no_elapsed_time_but_still_a_card(sess):
    sess.send("stop")
    card = wait_until(lambda: sess.of_kind("done"), timeout=2.0)[0]
    assert "P6-CASE-A" in _detail(card)
    assert "Task completed in" not in _detail(card)


def test_stop_clears_a_pending_waiting_card_and_leaves_exactly_one_done_card(sess):
    sess.send("permission_request_write")
    assert wait_until(lambda: sess.of_kind("waiting"), timeout=2.0)
    sess.send("stop")
    assert wait_until(lambda: sess.of_kind("done") and not sess.of_kind("waiting"), timeout=2.0)
    assert len(sess.cards()) == 1


def test_stop_with_background_work_says_paused_not_finished(sess):
    sess.send("stop", background_tasks=[{"id": "bg1", "command": "npm run dev"}])
    card = wait_until(lambda: sess.of_kind("done"), timeout=2.0)[0]
    assert "paused" in _detail(card).lower() and "background" in _detail(card).lower()


def test_stop_with_empty_background_lists_does_not_say_paused(sess):
    sess.send("stop", background_tasks=[], session_crons=[])
    card = wait_until(lambda: sess.of_kind("done"), timeout=2.0)[0]
    assert "paused" not in _detail(card).lower()


def test_second_stop_updates_the_same_done_card(sess):
    sess.send("stop")
    first = wait_until(lambda: sess.of_kind("done"), timeout=2.0)[0]
    sess.send("stop", last_assistant_message="second answer")
    assert wait_until(lambda: "second answer" in _detail(sess.of_kind("done")[0]), timeout=2.0)
    assert len(sess.of_kind("done")) == 1 and sess.of_kind("done")[0]["id"] == first["id"]


# ---- SYNTHETIC events (not observed by P6; shapes assumed from the PRD) -------------

def test_synthetic_stop_failure_makes_a_waiting_card_naming_the_error_class(sess):
    sess.send({"hook_event_name": "StopFailure", "error": "rate_limit",
               "error_type": "rate_limit", "error_class": "rate_limit"})
    cards = wait_until(lambda: sess.of_kind("waiting"), timeout=2.0)
    assert len(cards) == 1
    assert "rate" in _detail(cards[0]).lower()


def test_synthetic_stop_failure_clears_on_the_next_user_prompt(sess):
    sess.send({"hook_event_name": "StopFailure", "error": "overloaded"})
    assert wait_until(lambda: sess.of_kind("waiting"), timeout=2.0)
    sess.send("user_prompt_submit")
    assert wait_until(lambda: not sess.of_kind("waiting"), timeout=2.0)


def test_synthetic_notification_updates_the_existing_card_never_a_second(sess):
    sess.send("permission_request_write")
    assert wait_until(lambda: sess.of_kind("waiting"), timeout=2.0)
    sess.send({"hook_event_name": "Notification", "notification_type": "idle_prompt",
               "message": "Claude is waiting for your input"})
    time.sleep(0.5)
    assert len(sess.cards()) == 1
    assert len(sess.of_kind("waiting")) == 1


def test_synthetic_unknown_event_is_one_unknown_wait_card_cleared_by_the_next_event(sess):
    """Plan 14.3: an unmapped hook event is an 'unknown wait': one waiting card naming the event."""
    sess.send({"hook_event_name": "SomeFutureAskEvent"})
    cards = wait_until(lambda: sess.of_kind("waiting"), timeout=2.0)
    assert len(cards) == 1
    assert "SomeFutureAskEvent" in _detail(cards[0])
    sess.send({"hook_event_name": "SomeFutureAskEvent"})
    time.sleep(0.4)
    assert len(sess.of_kind("waiting")) == 1, "never a second card"
    sess.send("post_tool_use")
    assert wait_until(lambda: not sess.of_kind("waiting"), timeout=2.0)


def test_synthetic_session_start_makes_no_card(sess):
    sess.send({"hook_event_name": "SessionStart", "source": "startup"})
    time.sleep(0.4)
    assert sess.cards() == []


# ---- fixtures themselves ------------------------------------------------------------

def test_fixtures_carry_no_personal_paths():
    text = "".join(p.read_text(encoding="utf-8") for p in FIXTURES.glob("*.json"))
    assert text
    # The machine's own user name is read at runtime so no personal identifier
    # is written into the repo; the fixed needles are path fragments only.
    needles = ["AI Projects", "p6cwd", "AppData", os.environ.get("USERNAME", "")]
    for needle in [n for n in needles if n]:
        assert needle.lower() not in text.lower(), needle


def test_fixtures_cover_each_event_p6_recorded():
    events = {json.loads(p.read_text(encoding="utf-8"))["hook_event_name"] for p in FIXTURES.glob("*.json")}
    assert events == {"UserPromptSubmit", "PostToolUse", "PermissionRequest", "Stop", "SessionEnd"}
