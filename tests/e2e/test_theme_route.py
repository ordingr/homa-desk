"""POST /v1/settings/theme end to end on a private test-mode app: the contract, auth, and that the live windows recolour.

A private ``python -m assistant`` (own port, own ASSISTANT_HOME, a throw-away USERPROFILE) is launched with
ASSISTANT_TEST_MODE=1. The recolour checks read the app's own ``/v1/_debug/ui`` (``theme.panel_bg``) and then the real
screen pixels of a card window and of the search popup (opened by a posted WM_HOTKEY), because the popup and the
cards draw with the active palette. Needs a desktop session. Nothing here touches the user's instance.
"""
import collections
import ctypes
import http.client
import json
import time
from ctypes import wintypes

import pytest

from conftest import Api, _free_port, launch_app, wait_until

DARK_BG, LIGHT_BG = "#22252B", "#F4F5F7"
DARK_CARD, LIGHT_CARD = (DARK_BG, "#3A3F48"), (LIGHT_BG, "#E6E9ED")  # an unseen card blinks between the two
COMBO = "ctrl+alt+f9"


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home = tmp_path_factory.mktemp("theme_route") / "h"
    with launch_app(home, _free_port(), {"theme": "dark", "hotkey": COMBO}, test_mode=True) as (base, token):
        yield Api(base, token), token, home


@pytest.fixture(autouse=True)
def back_to_dark(served):
    api = served[0]
    yield
    api.post("/v1/settings/theme", {"theme": "dark"})


def raw_post(api, body: bytes, headers=None, path="/v1/settings/theme"):
    conn = http.client.HTTPConnection(api.host, int(api.port), timeout=10)
    try:
        conn.request("POST", path, body=body, headers=headers or {})
        r = conn.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        conn.close()


def ui(api):
    status, body = api.get("/v1/_debug/ui")
    assert status == 200, body
    return body


# ---- the contract ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["light", "dark", "system"])
def test_valid_values_return_200_with_the_get_payload_and_persist(served, value):
    api, _, home = served
    status, body = api.post("/v1/settings/theme", {"theme": value})
    assert status == 200 and body["theme"] == value, body
    assert {"theme", "shortcuts", "search_placement", "hotkey", "md_vault", "index"} <= set(body)
    got_status, got = api.get("/v1/settings")
    assert got_status == 200 and got == body, "the POST answers with exactly what GET /v1/settings returns"
    assert json.loads((home / "settings.json").read_text(encoding="utf-8"))["theme"] == value


@pytest.mark.parametrize("payload", [{"theme": "blue"}, {"theme": "Light"}, {"theme": ""}, {"theme": 1}, {"theme": None},
                                     {"theme": ["dark"]}, {"theme": True}, {}, {"other": "dark"}, ["dark"], "dark", 5])
def test_bad_values_are_422_and_do_not_change_the_theme(served, payload):
    api, _, home = served
    api.post("/v1/settings/theme", {"theme": "light"})
    status, body = api.post("/v1/settings/theme", payload)
    assert status == 422, (payload, status, body)
    assert body["error"]["code"] == "invalid_parameter" if isinstance(body.get("error"), dict) else True
    assert api.get("/v1/settings")[1]["theme"] == "light"
    assert json.loads((home / "settings.json").read_text(encoding="utf-8"))["theme"] == "light"


def test_an_empty_body_is_422_and_malformed_json_is_400(served):
    api, token, _ = served
    hdr = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    assert raw_post(api, b"", hdr)[0] == 422
    assert raw_post(api, b"{not json", hdr)[0] == 400


def test_a_body_over_256_bytes_is_413(served):
    api, token, _ = served
    hdr = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    big = json.dumps({"theme": "dark", "pad": "x" * 300}).encode()
    assert len(big) > 256
    status, _ = raw_post(api, big, hdr)
    assert status == 413
    exactly = json.dumps({"theme": "dark", "pad": "x" * (256 - len('{"theme": "dark", "pad": ""}'))}).encode()
    assert len(exactly) == 256
    assert raw_post(api, exactly, hdr)[0] == 200, "256 bytes is still accepted"
    chunked = json.dumps({"theme": "dark", "pad": "y" * 400}).encode()
    conn = http.client.HTTPConnection(api.host, int(api.port), timeout=10)
    try:
        conn.putrequest("POST", "/v1/settings/theme")
        for k, v in hdr.items():
            conn.putheader(k, v)
        conn.putheader("Transfer-Encoding", "chunked")
        conn.endheaders()
        conn.send(f"{len(chunked):x}\r\n".encode() + chunked + b"\r\n0\r\n\r\n")
        assert conn.getresponse().status == 413, "no Content-Length does not dodge the cap"
    finally:
        conn.close()


def test_no_bearer_or_a_wrong_one_is_401_and_changes_nothing(served):
    api, token, _ = served
    api.post("/v1/settings/theme", {"theme": "light"})
    body = json.dumps({"theme": "dark"}).encode()
    for headers in ({"Content-Type": "application/json"},
                    {"Content-Type": "application/json", "Authorization": "Bearer wrong"},
                    {"Content-Type": "application/json", "Authorization": f"Basic {token}"}):
        assert raw_post(api, body, headers)[0] == 401, headers
    assert api.get("/v1/settings")[1]["theme"] == "light"


def test_get_and_other_methods_on_the_theme_route_are_not_allowed(served):
    api, token, _ = served
    hdr = {"Authorization": f"Bearer {token}"}
    for method in ("GET", "PUT", "DELETE"):
        conn = http.client.HTTPConnection(api.host, int(api.port), timeout=10)
        try:
            conn.request(method, "/v1/settings/theme", headers=hdr)
            assert conn.getresponse().status == 405, method
        finally:
            conn.close()


# ---- live windows recolour ---------------------------------------------------------------------------------------------------

_gdi = ctypes.WinDLL("gdi32", use_last_error=True)
_user = ctypes.WinDLL("user32", use_last_error=True)
_user.GetDC.argtypes = [wintypes.HWND]
_user.GetDC.restype = wintypes.HDC
_user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_gdi.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi.GetPixel.restype = wintypes.DWORD


def dominant_colour(rect, grid=7):
    """Most common screen colour on a grid inside ``rect`` (left, top, right, bottom), as '#RRGGBB'."""
    left, top, right, bottom = rect
    dc = _user.GetDC(0)
    try:
        counts = collections.Counter()
        for iy in range(grid):
            for ix in range(grid):
                x = left + 3 + (right - left - 6) * ix // (grid - 1)
                y = top + 3 + (bottom - top - 6) * iy // (grid - 1)
                px = _gdi.GetPixel(dc, int(x), int(y))
                if px == 0xFFFFFFFF:
                    continue
                counts["#%02X%02X%02X" % (px & 0xFF, (px >> 8) & 0xFF, (px >> 16) & 0xFF)] += 1
    finally:
        _user.ReleaseDC(0, dc)
    return counts.most_common(1)[0][0] if counts else ""


def close_to(a, b, tol=6):
    if not a:
        return False
    options = (b,) if isinstance(b, str) else b
    ra = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    return any(all(abs(x - int(o[i:i + 2], 16)) <= tol for x, i in zip(ra, (1, 3, 5))) for o in options)


def test_the_app_palette_follows_the_post(served):
    api, _, _ = served
    assert ui(api)["theme"]["resolved"] == "dark" and ui(api)["theme"]["panel_bg"].upper() == DARK_BG
    api.post("/v1/settings/theme", {"theme": "light"})
    assert wait_until(lambda: ui(api)["theme"]["panel_bg"].upper() == LIGHT_BG, 5), ui(api)["theme"]
    state = ui(api)["theme"]
    assert state["mode"] == "light" and state["resolved"] == "light" and state["polling"] is False
    api.post("/v1/settings/theme", {"theme": "system"})
    assert wait_until(lambda: ui(api)["theme"]["mode"] == "system" and ui(api)["theme"]["polling"], 5), ui(api)["theme"]


def test_cards_on_screen_recolour_after_the_post(served):
    api, _, _ = served
    status, card = api.post("/v1/cards", {"kind": "custom", "title": "Theme probe", "detail": "recolour me"})
    assert status in (200, 201), card
    cid = card["id"]

    def card_rect():
        wins = [w for w in ui(api).get("cards", []) if w["id"] == cid and not w["hidden"]]
        return wins[0]["rect"] if wins else None
    assert wait_until(card_rect, 8)
    time.sleep(0.8)  # slide-in
    before = dominant_colour(card_rect())
    assert close_to(before, DARK_CARD), f"dark card should be one of {DARK_CARD}, saw {before}"
    api.post("/v1/settings/theme", {"theme": "light"})
    assert wait_until(lambda: close_to(dominant_colour(card_rect()), LIGHT_CARD), 6), dominant_colour(card_rect())
    api.post("/v1/settings/theme", {"theme": "dark"})
    assert wait_until(lambda: close_to(dominant_colour(card_rect()), DARK_CARD), 6), dominant_colour(card_rect())
    api.request("DELETE", f"/v1/cards/{cid}")


def test_an_open_search_popup_recolours_after_the_post(served):
    api, _, _ = served
    import win32con
    import win32gui
    state = ui(api)
    assert state.get("hotkey") == "registered", state.get("hotkey")
    win32gui.PostThreadMessage(state["hotkey_thread_id"], win32con.WM_HOTKEY, 1, 0)
    assert wait_until(lambda: ui(api).get("search", {}).get("open"), 6), ui(api).get("search")
    time.sleep(0.6)

    def popup_rect():
        r = ui(api)["search"]["rect"]
        return [r[0] + 2, r[1] + 2, r[0] + 10, r[3] - 2] if r else None

    try:
        assert close_to(dominant_colour(popup_rect(), 8), DARK_BG), dominant_colour(popup_rect(), 8)
        api.post("/v1/settings/theme", {"theme": "light"})
        assert wait_until(lambda: close_to(dominant_colour(popup_rect(), 8), LIGHT_BG), 6), dominant_colour(popup_rect(), 8)
        assert ui(api)["search"]["open"], "the popup stays open through the switch"
        api.post("/v1/settings/theme", {"theme": "dark"})
        assert wait_until(lambda: close_to(dominant_colour(popup_rect(), 8), DARK_BG), 6)
    finally:
        win32gui.PostThreadMessage(state["hotkey_thread_id"], win32con.WM_HOTKEY, 1, 0)
        wait_until(lambda: not ui(api).get("search", {}).get("open"), 5)
