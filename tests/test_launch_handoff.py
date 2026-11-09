"""Popup -> full page handoff on the launch nonce (plan 2026-11-09 popup-to-fullpage-and-file-clipboard, sections 3.1 A
and 4.2; AC-H2).

The handoff (query, chips, modified, sort, select_path) is validated when the nonce is minted, travels only with the
nonce, is returned once by ``POST /v1/session/exchange`` and never appears in the launch URL. In-process: the real
``auth`` module, the real Starlette app from ``create_app`` with the real AuthMiddleware, driven over ASGI; the real
``routes_files.fullpage_open`` with a recording opener (no Edge window is ever launched here); ``App._open_fullpage``
with ``fullpage.open`` replaced by a recorder, because the real one starts Edge.
"""
import asyncio
import json
import types

import pytest

from homa.server import auth
from homa.server import app as server_app
from homa.server.routes_files import register_files_routes

TOKEN = "T" * 43
GOOD = {"query": "design md", "chips": ["docs", "pdf"], "modified": "30d", "sort": "name",
        "select_path": "C:\\Users\\someone\\Documents\\design notes.md"}


@pytest.fixture(autouse=True)
def fresh_nonces():
    with auth._nonce_lock:
        saved = dict(auth._nonces)
        auth._nonces.clear()
    yield
    with auth._nonce_lock:
        auth._nonces.clear()
        auth._nonces.update(saved)


def asgi(app, method, path, body=None, headers=None):
    payload = json.dumps(body).encode() if body is not None else b""
    sent, queue = [], [payload]

    async def receive():
        if queue:
            return {"type": "http.request", "body": queue.pop(0), "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    hdrs = [(b"host", b"127.0.0.1"), (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode())]
    hdrs += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": hdrs, "http_version": "1.1", "scheme": "http", "server": ("127.0.0.1", 1),
             "client": ("127.0.0.1", 2), "root_path": ""}
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], (json.loads(data) if data else None)


def exchange(app, nonce, **kw):
    return asgi(app, "POST", "/v1/session/exchange", {"nonce": nonce}, **kw)


# ---- mint / take ------------------------------------------------------------------------------------------------------

def test_take_nonce_returns_the_handoff_once():
    nonce = auth.mint_nonce(GOOD)
    assert auth.take_nonce(nonce) == (True, GOOD)
    assert auth.take_nonce(nonce) == (False, None)
    assert auth.consume_nonce(nonce) is False


def test_a_plain_nonce_still_works_and_carries_no_handoff():
    nonce = auth.mint_nonce()
    assert auth.take_nonce(nonce) == (True, None)
    other = auth.mint_nonce()
    assert auth.consume_nonce(other) is True and auth.consume_nonce(other) is False


def test_the_handoff_is_normalised_at_mint():
    nonce = auth.mint_nonce({"query": "x", "chips": ["pdf", "pdf", "docs"], "select_path": ""})
    ok, handoff = auth.take_nonce(nonce)
    assert ok and handoff == {"query": "x", "chips": ["pdf", "docs"], "modified": "", "sort": "",
                              "select_path": None}


def test_the_stored_handoff_is_a_copy_not_the_callers_dict():
    mine = json.loads(json.dumps(GOOD))
    nonce = auth.mint_nonce(mine)
    mine["chips"].append("code")
    mine["select_path"] = "C:\\elsewhere.txt"
    assert auth.take_nonce(nonce) == (True, GOOD)


@pytest.mark.parametrize("bad", [
    {"query": "q" * 1001},
    {"query": 5},
    {"chips": ["docs", "nope"]},
    {"chips": "docs"},
    {"chips": [5]},
    {"modified": "yesterday"},
    {"modified": "2026-13-01..2026-13-02"},
    {"modified": 7},
    {"sort": "size"},
    {"sort": "best"},
    {"select_path": "C:\\a\x00b"},
    {"select_path": "C:\\" + "x" * 32767},
    {"select_path": 5},
    {"extra": "field"},
    "not a dict",
    ["query"],
])
def test_invalid_handoffs_raise_at_mint_and_store_nothing(bad):
    with pytest.raises(ValueError):
        auth.mint_nonce(bad)
    assert auth._nonces == {}


@pytest.mark.parametrize("bad_sort", [[], ["name"], {"a": 1}])
def test_an_unhashable_sort_is_a_value_error_not_a_crash(bad_sort):
    with pytest.raises(ValueError):
        auth.check_handoff({"sort": bad_sort})


def test_a_dated_range_and_every_chip_are_accepted():
    from homa.search.modes import CHIPS
    got = auth.check_handoff({"modified": "2026-01-01..2026-02-01", "chips": list(CHIPS)})
    assert got["modified"] == "2026-01-01..2026-02-01" and got["chips"] == list(CHIPS)


# ---- over the real middleware (ASGI) --------------------------------------------------------------------------------------

def test_exchange_returns_token_and_handoff_once_then_403():
    app = server_app.create_app(lambda: TOKEN)
    nonce = auth.mint_nonce(GOOD)
    status, body = exchange(app, nonce)
    assert status == 200 and body == {"token": TOKEN, "handoff": GOOD}, body
    status, body = exchange(app, nonce)
    assert status == 403 and body["error"]["code"] == "invalid_nonce"
    assert "handoff" not in body and GOOD["select_path"] not in json.dumps(body) and TOKEN not in json.dumps(body)


def test_exchange_without_a_handoff_has_no_handoff_key():
    app = server_app.create_app(lambda: TOKEN)
    status, body = exchange(app, auth.mint_nonce())
    assert status == 200 and body == {"token": TOKEN}


def test_the_handoff_expires_with_the_nonce(monkeypatch):
    app = server_app.create_app(lambda: TOKEN)
    base = auth._clock()
    monkeypatch.setattr(auth, "_clock", lambda: base)
    early, late = auth.mint_nonce(GOOD), auth.mint_nonce(GOOD)
    monkeypatch.setattr(auth, "_clock", lambda: base + 29.0)
    assert exchange(app, early) == (200, {"token": TOKEN, "handoff": GOOD})
    monkeypatch.setattr(auth, "_clock", lambda: base + 31.0)
    status, body = exchange(app, late)
    assert status == 403 and "handoff" not in body


def test_a_foreign_origin_cannot_redeem_the_handoff_and_does_not_burn_it():
    app = server_app.create_app(lambda: TOKEN)
    nonce = auth.mint_nonce(GOOD)
    status, body = exchange(app, nonce, headers={"Origin": "https://evil.example"})
    assert status == 403 and body["error"]["code"] == "forbidden_origin"
    assert exchange(app, nonce) == (200, {"token": TOKEN, "handoff": GOOD})


def test_the_launch_url_holds_only_the_nonce_and_the_query():
    from homa.overlay import fullpage
    nonce = auth.mint_nonce(GOOD)
    url = fullpage.build_url(GOOD["query"], 50123, nonce)
    assert url.startswith("http://127.0.0.1:50123/app/?launch=")
    assert "select" not in url and "design%20notes" not in url and "Documents" not in url
    assert "chips" not in url and "sort" not in url and "modified" not in url


# ---- App._open_fullpage wires the handoff into the mint callable ---------------------------------------------------------

def _app_with_recorder(monkeypatch):
    from homa import app as app_mod
    calls = []

    def fake_open(query, port, mint):
        calls.append({"query": query, "port": port, "nonce": mint()})
        return True

    monkeypatch.setattr(app_mod.fullpage, "open", fake_open)
    monkeypatch.setattr(app_mod.uibus, "post", lambda fn, *a: None)
    instance = app_mod.App.__new__(app_mod.App)
    instance.server = types.SimpleNamespace(port=50123)
    instance.popup = None
    return instance, calls


def test_open_fullpage_mints_a_nonce_that_carries_the_handoff(monkeypatch):
    instance, calls = _app_with_recorder(monkeypatch)
    assert instance._open_fullpage("design md", dict(GOOD)) is True
    assert calls[0]["query"] == "design md" and calls[0]["port"] == 50123
    assert auth.take_nonce(calls[0]["nonce"]) == (True, GOOD)


def test_open_fullpage_without_a_handoff_mints_a_plain_nonce(monkeypatch):
    instance, calls = _app_with_recorder(monkeypatch)
    assert instance._open_fullpage("q") is True
    assert auth.take_nonce(calls[0]["nonce"]) == (True, None)


def test_open_fullpage_with_a_bad_handoff_opens_nothing(monkeypatch):
    instance, calls = _app_with_recorder(monkeypatch)
    assert instance._open_fullpage("q", {"sort": "size"}) is False
    assert calls == [] and auth._nonces == {}


# ---- POST /v1/fullpage/open carries the optional handoff fields ----------------------------------------------------------

@pytest.fixture
def opener_app():
    app = server_app.create_app(lambda: TOKEN)
    opened = []
    register_files_routes(app, None, None, closer=lambda: True, opener=lambda q, h: opened.append((q, h)) or True)
    return app, opened


def open_post(app, body):
    return asgi(app, "POST", "/v1/fullpage/open", body, headers={"Authorization": f"Bearer {TOKEN}"})


def test_fullpage_open_passes_the_validated_handoff_to_the_opener(opener_app):
    app, opened = opener_app
    body = dict(GOOD)
    status, got = open_post(app, body)
    assert status == 200 and got == {"ok": True, "opened": True}
    assert opened == [(GOOD["query"], GOOD)]


def test_fullpage_open_with_only_a_query_passes_no_handoff(opener_app):
    app, opened = opener_app
    assert open_post(app, {"query": "abc"})[0] == 200
    assert opened == [("abc", None)]


def test_fullpage_open_with_some_fields_fills_the_rest_with_defaults(opener_app):
    app, opened = opener_app
    assert open_post(app, {"query": "abc", "sort": "date"})[0] == 200
    assert opened == [("abc", {"query": "abc", "chips": [], "modified": "", "sort": "date", "select_path": None})]


@pytest.mark.parametrize("body, field", [
    ({"query": "q", "sort": "size"}, "sort"),
    ({"query": "q", "chips": ["nope"]}, "chips"),
    ({"query": "q", "modified": "yesterday"}, "modified"),
    ({"query": "q", "select_path": "C:\\a\x00"}, "select_path"),
    ({"query": "q" * 1001}, "query"),
    ({"query": 5}, "query"),
])
def test_fullpage_open_rejects_bad_fields_with_422_and_opens_nothing(opener_app, body, field):
    app, opened = opener_app
    status, got = open_post(app, body)
    assert status == 422 and got["error"]["field"] == field, (status, got)
    assert opened == []


@pytest.mark.parametrize("bad_sort", [[], ["name"], {"a": 1}])
def test_fullpage_open_rejects_an_unhashable_sort_with_422_not_500(opener_app, bad_sort):
    app, opened = opener_app
    status, got = open_post(app, {"query": "q", "sort": bad_sort})
    assert status == 422 and got["error"]["field"] == "sort", (status, got)
    assert opened == []


def test_fullpage_open_needs_the_bearer(opener_app):
    app, opened = opener_app
    status, _ = asgi(app, "POST", "/v1/fullpage/open", dict(GOOD))
    assert status == 401 and opened == []
