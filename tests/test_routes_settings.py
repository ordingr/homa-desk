"""GET /v1/settings (plan 7.5 M4-2; PRD section 8): a read-only safe subset, never secrets.

Runs against a private full app whose settings.json deliberately holds a calendar feed URL, index roots, a notes folder
and an update source, so a leak anywhere in the body is detectable.
"""
import json
import os

import pytest

from conftest import Api, wait_until

FEED = "https://example.test/private/feed-123/basic.ics"
UPDATE_SRC = "https://example.test/releases/secret-release-path/assistant.zip"
NOTES = "C:\\Users\\someone\\notes-folder-zz"
ROOT = "C:\\Data\\roots-marker-zz"
EXPECTED_KEYS = {"theme", "shortcuts", "search_placement", "hotkey", "md_vault", "index"}


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from conftest import launch_app, _free_port
    home = tmp_path_factory.mktemp("settings_route")
    settings = {"theme": "light", "hotkey": "ctrl+alt+k", "search_placement": "center",
                "calendars": {"ics_urls": [FEED]}, "index": {"roots": [ROOT], "known_exts": ["zzz", "md"]},
                "notes": {"dir": NOTES}, "update": {"source": UPDATE_SRC},
                "md_vault": {"url": "https://vault.example.test/open"},
                "shortcuts": {"rename": "F3"}}
    with launch_app(home / "h", _free_port(), settings) as (base, token):
        yield Api(base, token), token, home / "h"


def raw_get(api, path, headers=None):
    import http.client
    conn = http.client.HTTPConnection(api.host, int(api.port), timeout=10)
    try:
        conn.request("GET", path, headers=headers or {})
        r = conn.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        conn.close()


def test_exact_key_set_and_shapes(served):
    api, _, _ = served
    status, body = api.get("/v1/settings")
    assert status == 200, body
    assert set(body) == EXPECTED_KEYS
    assert body["theme"] == "light" and body["hotkey"] == "ctrl+alt+k" and body["search_placement"] == "center"
    assert body["md_vault"] == {"url": "https://vault.example.test/open"}
    assert set(body["index"]) == {"known_exts"} and "zzz" in body["index"]["known_exts"]
    assert body["index"]["known_exts"] == sorted(body["index"]["known_exts"])
    assert body["shortcuts"]["rename"] == "F3" and body["shortcuts"]["open"] == "Enter" and len(body["shortcuts"]) == 15


def test_no_secret_or_path_appears_anywhere_in_the_body(served):
    api, token, home = served
    status, text = raw_get(api, "/v1/settings", {"Authorization": f"Bearer {token}"})
    assert status == 200
    for needle in (FEED, "feed-123", "ics", UPDATE_SRC, "secret-release-path", "update", NOTES, "notes-folder-zz", ROOT,
                   "roots-marker-zz", token, "roots", "excludes", "calendars", "notes", "port", "mcp", "claude_code"):
        assert needle not in text, needle
    assert str(home) not in text and os.environ.get("USERNAME", "\0") not in text


def test_requires_the_bearer_token(served):
    api, _, _ = served
    assert api.get("/v1/settings", token=False)[0] == 401
    assert api.get("/v1/settings", token="wrong-token")[0] == 401


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_writes_are_refused(served, method):
    api, _, home = served
    before = (home / "settings.json").read_text(encoding="utf-8")
    status, _ = api.request(method, "/v1/settings", {"theme": "dark"})
    assert status == 405
    assert (home / "settings.json").read_text(encoding="utf-8") == before


def test_a_settings_file_edit_is_reflected_without_a_restart(served):
    api, _, home = served
    path = home / "settings.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["theme"] = "system"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert wait_until(lambda: api.get("/v1/settings")[1]["theme"] == "system", timeout=15)


def test_defaults_on_a_pristine_home(app_factory):
    with app_factory() as (base, token):
        api = Api(base, token)
        status, body = api.get("/v1/settings")
        assert status == 200 and set(body) == EXPECTED_KEYS
        assert body["theme"] == "dark" and body["md_vault"] == {"url": ""} and len(body["shortcuts"]) == 15
        assert body["index"]["known_exts"], "default known extensions are served"


def test_invalid_theme_in_the_file_is_served_as_dark(app_factory):
    with app_factory(settings={"theme": "neon"}) as (base, token):
        assert Api(base, token).get("/v1/settings")[1]["theme"] == "dark"
