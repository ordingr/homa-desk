"""Card timeout settings, the dismiss-all route and the reminder dialog's waiting label.

Real Settings object against a scratch HOMA_HOME, a private full app for the HTTP route; nothing mocked.
"""
import json

import pytest

from conftest import Api
from homa import paths
from homa import settings as settings_mod
from homa.settings import DEFAULTS, Settings


@pytest.fixture
def scratch_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMA_HOME", str(tmp_path))
    return tmp_path


def _write(cards):
    paths.settings_file().write_text(json.dumps({"schema": 1, "cards": cards}), encoding="utf-8")


def _loaded():
    s = Settings()
    s.load()
    return s


# ---- settings: cards ---------------------------------------------------------------------------------------

def test_defaults(scratch_home):
    assert DEFAULTS["cards"] == {"info_seconds": 12, "waiting_minutes": 15}
    assert _loaded().get("cards") == {"info_seconds": 12, "waiting_minutes": 15}


@pytest.mark.parametrize("bad", [4, 121, True, 1.5, "12", None, -1])
def test_set_rejects_bad_info_seconds(scratch_home, bad):
    s = _loaded()
    with pytest.raises(ValueError):
        s.set("cards", {"info_seconds": bad, "waiting_minutes": 15})
    assert s.get("cards")["info_seconds"] == 12


@pytest.mark.parametrize("bad", [-1, 1441, True, 1.5, "15", None])
def test_set_rejects_bad_waiting_minutes(scratch_home, bad):
    s = _loaded()
    with pytest.raises(ValueError):
        s.set("cards", {"info_seconds": 12, "waiting_minutes": bad})
    assert s.get("cards")["waiting_minutes"] == 15


def test_set_rejects_a_non_object(scratch_home):
    with pytest.raises(ValueError):
        _loaded().set("cards", 12)


@pytest.mark.parametrize("info,waiting", [(5, 0), (120, 1440), (12, 15), (60, 1)])
def test_set_accepts_the_range_edges_and_persists(scratch_home, info, waiting):
    s = _loaded()
    s.set("cards", {"info_seconds": info, "waiting_minutes": waiting})
    assert json.loads(paths.settings_file().read_text(encoding="utf-8"))["cards"] == \
        {"info_seconds": info, "waiting_minutes": waiting}
    assert _loaded().get("cards") == {"info_seconds": info, "waiting_minutes": waiting}


def test_load_resets_only_the_invalid_sub_key(scratch_home):
    _write({"info_seconds": 999, "waiting_minutes": 30})
    assert _loaded().get("cards") == {"info_seconds": 12, "waiting_minutes": 30}
    _write({"info_seconds": 40, "waiting_minutes": "soon"})
    assert _loaded().get("cards") == {"info_seconds": 40, "waiting_minutes": 15}


def test_load_fills_a_missing_sub_key_and_survives_a_non_object(scratch_home):
    _write({"info_seconds": 20})
    assert _loaded().get("cards") == {"info_seconds": 20, "waiting_minutes": 15}
    _write("garbage")
    assert _loaded().get("cards") == {"info_seconds": 12, "waiting_minutes": 15}


def test_on_change_cards_is_registered_for_that_key_only(scratch_home):
    from homa import uibus
    uibus.drain()
    s = _loaded()
    heard, other = [], []
    s.on_change("cards", lambda k, v: heard.append((k, v)))
    s.on_change("sound_on", lambda k, v: other.append((k, v)))
    s.set("cards", {"info_seconds": 30, "waiting_minutes": 15})
    while uibus.drain():
        pass
    assert heard == [("cards", {"info_seconds": 30, "waiting_minutes": 15})]
    assert other == []
    s.set("sound_on", False)
    while uibus.drain():
        pass
    assert heard == [("cards", {"info_seconds": 30, "waiting_minutes": 15})], "a cards listener ignores other keys"
    assert other == [("sound_on", False)]


# ---- peek_waiting_minutes ----------------------------------------------------------------------------------

def test_peek_waiting_minutes_default_when_the_file_is_missing_and_writes_nothing(scratch_home):
    assert not paths.settings_file().exists()
    assert settings_mod.peek_waiting_minutes() == 15
    assert not paths.settings_file().exists()


@pytest.mark.parametrize("value,expected", [(0, 0), (1440, 1440), (45, 45), (1441, 15), (-2, 15), (True, 15), ("9", 15), (2.5, 15)])
def test_peek_waiting_minutes_reads_and_validates(scratch_home, value, expected):
    _write({"waiting_minutes": value})
    before = paths.settings_file().read_text(encoding="utf-8")
    assert settings_mod.peek_waiting_minutes() == expected
    assert paths.settings_file().read_text(encoding="utf-8") == before


@pytest.mark.parametrize("raw", ["{ not json", "[]", json.dumps({"cards": []}), json.dumps({"cards": None})])
def test_peek_waiting_minutes_survives_garbage(scratch_home, raw):
    paths.settings_file().write_text(raw, encoding="utf-8")
    assert settings_mod.peek_waiting_minutes() == 15


# ---- reminder dialog label ---------------------------------------------------------------------------------

def test_sticky_label_text():
    from homa.overlay.reminder_ui import sticky_label
    assert sticky_label(15) == "wait for me (stays up to 15 min)"
    assert sticky_label(0) == "wait for me (stays until dismissed)"
    assert sticky_label(1440) == "wait for me (stays up to 1440 min)"


# ---- HTTP: POST /v1/cards/dismiss_all ------------------------------------------------------------------------

def _open_ids(api):
    status, body = api.get("/v1/cards")
    assert status == 200
    return [c["id"] for c in body]


def test_dismiss_all_route(app_factory):
    with app_factory(test_mode=True) as (base, token):
        api = Api(base, token)
        assert api.post("/v1/cards/dismiss_all")[1] == {"dismissed": 0}
        for cid in ("x1", "x2", "x3"):
            assert api.post("/v1/cards", {"id": cid, "title": cid, "kind": "waiting"})[0] == 201
        assert api.post("/v1/cards/x3/snooze", {"minutes": 30})[0] == 200
        assert api.post("/v1/cards/dismiss_all", token=False)[0] == 401
        assert api.post("/v1/cards/dismiss_all", token="x" * len(token))[0] == 401
        assert api.post("/v1/cards/dismiss_all", headers={"Origin": "http://evil.test"})[0] == 403
        assert api.post("/v1/cards/dismiss_all", headers={"Host": "evil.test"})[0] == 403
        assert len(_open_ids(api)) == 3, "rejected calls dismissed nothing"
        status, body = api.post("/v1/cards/dismiss_all")
        assert (status, body) == (200, {"dismissed": 2})
        assert _open_ids(api) == ["x3"], "the snoozed card stays"
        assert api.post("/v1/cards/dismiss_all")[1] == {"dismissed": 0}


# ---- deeply nested settings.json (RecursionError in json.loads) --------------------------------------------

_DEPTH = 200_000


def _deep(key="cards"):
    return '{"%s": ' % key + "[" * _DEPTH + "]" * _DEPTH + "}"


def test_deep_nesting_really_raises_recursion_error_in_json():
    with pytest.raises(RecursionError):
        json.loads(_deep())


@pytest.mark.parametrize("key", ["cards", "port", "update"])
def test_peeks_return_defaults_for_a_deeply_nested_file(scratch_home, key):
    paths.settings_file().write_text(_deep(key), encoding="utf-8")
    before = paths.settings_file().read_text(encoding="utf-8")
    assert settings_mod.peek_waiting_minutes() == 15
    assert settings_mod.peek_port() == DEFAULTS["port"]
    assert settings_mod.peek_update_source() == ""
    assert paths.settings_file().read_text(encoding="utf-8") == before


def test_load_of_a_deeply_nested_file_uses_defaults(scratch_home):
    paths.settings_file().write_text(_deep(), encoding="utf-8")
    s = _loaded()
    assert s.get("cards") == {"info_seconds": 12, "waiting_minutes": 15}
    assert s.port == DEFAULTS["port"]


def test_external_edit_to_a_deeply_nested_file_keeps_current_settings(scratch_home):
    from homa import uibus
    uibus.drain()
    s = _loaded()
    s.set("cards", {"info_seconds": 40, "waiting_minutes": 3})
    while uibus.drain():
        pass
    heard = []
    s.on_change(lambda k, v: heard.append(k))
    paths.settings_file().write_text(_deep(), encoding="utf-8")
    s._check_external()
    while uibus.drain():
        pass
    assert s.get("cards") == {"info_seconds": 40, "waiting_minutes": 3}
    assert heard == []
