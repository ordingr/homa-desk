"""Settings window and live apply (plan 7.2 M2-3/M2-4; AC-ST-2 .. AC-ST-5, AC-MT-1, AC-MT-7, SEC-12, R2-11).

Two layers:

* ``ui`` tests build the real ``SettingsWindow`` (tkinter, a real settings file in a scratch home) and operate
  its widgets: they need a desktop session but no running app.
* ``app`` tests start a private full app with a calendar feed served from a local HTTP server and observe
  cards, ``/v1/health`` ``calendars``, ``logs/app.log`` and the settings file.

Widgets are found by their visible text, not by attribute names, except ``PAGES``, ``pages``, ``show``,
``lead_var``/``poll_var`` which the module itself exposes.
"""
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk

import pytest

from conftest import REPO_ROOT, Api, build_ics, wait_until

settings_ui = pytest.importorskip("assistant.overlay.settings_ui", reason="assistant/overlay/settings_ui.py (M2-3) not present")

from assistant import paths, uibus  # noqa: E402
from assistant.settings import Settings  # noqa: E402

PAGES = ["General", "Cards", "Calendars", "Claude Code", "MCP", "About"]
PORT = 17171
TOKEN = "T" * 43
FEED_URL = "https://example.test/private/feed-123/basic.ics"


def pump(root, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        uibus.drain()
        time.sleep(0.02)


def widgets(w):
    yield w
    for child in w.winfo_children():
        yield from widgets(child)


def texts(w):
    out = []
    for x in widgets(w):
        try:
            if isinstance(x, tk.Entry):
                out.append(x.get())
            elif isinstance(x, tk.Text):
                out.append(x.get("1.0", "end"))
            else:
                out.append(str(x.cget("text")))
        except tk.TclError:
            pass
    return [t for t in out if t]


def checkbuttons(page):
    return [x for x in widgets(page) if isinstance(x, tk.Checkbutton)]


def find_check(page, needle):
    found = [c for c in checkbuttons(page) if needle.lower() in str(c.cget("text")).lower()]
    assert len(found) == 1, (needle, [str(c.cget("text")) for c in checkbuttons(page)])
    return found[0]


def read_file():
    return json.loads(paths.settings_file().read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tk_root():
    """One Tk interpreter for the module: creating and destroying several in one process makes Tcl lose its library path."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no usable tkinter in this session: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def ui(tk_root, tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "ui_home"))
    paths.ensure_layout()
    root = tk_root
    settings = Settings()
    settings.load()
    window = settings_ui.SettingsWindow(root, settings, lambda: TOKEN, PORT)
    pump(root, 0.2)
    yield root, settings, window
    try:
        window.close()
    except Exception:
        pass
    pump(root, 0.1)


# ---- the window ------------------------------------------------------------------------------------------

def test_window_has_the_six_pages_and_shows_each_without_error(ui):
    root, settings, window = ui
    assert list(settings_ui.PAGES) == PAGES and list(window.pages) == PAGES
    for name in PAGES:
        window.show(name)
        pump(root, 0.1)
        assert window.current == name


def test_cards_on_every_monitor_toggle_writes_the_file_within_one_second_and_flips_back(ui):
    root, settings, window = ui
    holder = [name for name in ("General", "Cards") if any("monitor" in str(c.cget("text")).lower()
                                                           for c in checkbuttons(window.pages[name]))]
    assert len(holder) == 1, "the every-monitor checkbox must be on exactly one of General/Cards"
    window.show(holder[0])
    box = find_check(window.pages[holder[0]], "monitor")
    before = settings.get("cards_every_monitor")
    t0 = time.monotonic()
    box.invoke()
    assert wait_until(lambda: (pump(root, 0.05), read_file()["cards_every_monitor"] is (not before))[1], timeout=1.0, step=0)
    assert time.monotonic() - t0 <= 1.2
    assert settings.get("cards_every_monitor") is (not before)
    box.invoke()
    pump(root, 0.3)
    assert read_file()["cards_every_monitor"] is before


def test_per_source_mute_toggles_exist_for_every_source_and_persist(ui):
    root, settings, window = ui
    window.show("Cards")
    page = window.pages["Cards"]
    for source in ("hooks", "http", "mcp", "file", "reminders"):
        find_check(page, f"({source})")
    find_check(page, "(mcp)").invoke()
    pump(root, 0.3)
    muted = read_file()["mute_sources"]
    assert muted["mcp"] is True and muted["hooks"] is False


def test_lead_time_edit_is_saved_within_one_second_and_bad_values_are_refused(ui):
    root, settings, window = ui
    window.show("Calendars")
    window.lead_var.set("3")
    t0 = time.monotonic()
    assert wait_until(lambda: (pump(root, 0.05), read_file()["calendars"]["lead_minutes"] == 3)[1], timeout=1.5, step=0)
    assert time.monotonic() - t0 <= 1.2, "AC-ST-3: written within 1 s"
    window.lead_var.set("abc")
    pump(root, 0.8)
    assert read_file()["calendars"]["lead_minutes"] == 3, "a non-number must not overwrite the saved value"
    window.lead_var.set("999")
    pump(root, 0.8)
    assert read_file()["calendars"]["lead_minutes"] == 3, "out of range must not overwrite the saved value"


def test_port_and_hotkey_say_takes_effect_after_restart(ui):
    root, settings, window = ui
    window.show("General")
    assert any("takes effect after restart" in t for t in texts(window.pages["General"]))


def test_mcp_page_shows_the_claude_code_command_and_the_claude_desktop_snippet(ui):
    root, settings, window = ui
    window.show("MCP")
    pump(root, 0.2)
    blob = "\n".join(texts(window.pages["MCP"]))
    assert f'claude mcp add --transport http assistant http://127.0.0.1:{PORT}/mcp --header "Authorization: Bearer ' in blob
    assert '"mcpServers"' in blob and "assistant.mcp_stdio" in blob


def test_claude_code_page_states_what_hooks_can_and_cannot_see(ui):
    root, settings, window = ui
    window.show("Claude Code")
    blob = "\n".join(texts(window.pages["Claude Code"])).lower()
    assert "permission" in blob and "cannot" in blob


def test_no_default_names_an_organisation_and_the_feed_list_starts_empty(ui):
    root, settings, window = ui
    assert settings.get("calendars")["ics_urls"] == []
    assert window.ics_list.size() == 0
    dumped = json.dumps(settings._data).lower()
    for word in ("notes_dir", "roots", "http://", "https://"):
        assert word not in dumped


def test_a_saved_feed_url_is_masked_in_the_window_and_the_settings_file_gets_a_user_only_acl(ui):
    root, settings, window = ui
    settings.set("calendars", {**settings.get("calendars"), "ics_urls": [FEED_URL]})
    window._refresh_ics()
    window.show("Calendars")
    pump(root, 0.2)
    blob = "\n".join(texts(window.pages["Calendars"]) + [str(window.ics_list.get(i)) for i in range(window.ics_list.size())])
    assert "example.test" in blob and "feed-123" not in blob, "the secret path must not be shown"
    out = subprocess.run(["icacls", str(paths.settings_file())], capture_output=True, text=True, check=True).stdout
    aces = [line for line in out.splitlines() if ":(" in line]
    assert len(aces) == 1 and "(F)" in aces[0], aces
    assert not any(w in line for line in aces for w in ("Everyone", "BUILTIN" + chr(92) + "Users", "Authenticated"))


def test_saving_is_atomic_readers_never_see_half_a_file_and_no_tmp_file_is_left(ui):
    root, settings, window = ui
    stop = threading.Event()
    errors = []

    def writer():
        flip = False
        while not stop.is_set():
            flip = not flip
            try:
                settings.set("sound_on", flip)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                return

    t = threading.Thread(target=writer)
    t.start()
    try:
        deadline = time.monotonic() + 1.5
        reads = 0
        while time.monotonic() < deadline:
            try:
                text = paths.settings_file().read_text(encoding="utf-8")
            except PermissionError:
                continue  # Windows: the replace is in flight; the reader may be refused but never sees a partial file
            json.loads(text)
            reads += 1
    finally:
        stop.set()
        t.join()
    assert reads > 50
    assert errors == [], ("Settings.save raised while another reader had the file open; a save must retry the replace: "
                          + errors[0][:200])
    uibus.drain()
    assert not paths.settings_file().with_name("settings.json.tmp").exists()


def test_listeners_hear_the_changed_key_through_the_ui_bus(ui):
    root, settings, window = ui
    heard = []
    settings.on_change("cards_every_monitor", lambda k, v: heard.append((k, v)))
    settings.set("cards_every_monitor", False)
    assert heard == [], "listeners run on the UI thread, not the caller's"
    pump(root, 0.2)
    assert heard == [("cards_every_monitor", False)]


def test_standalone_settings_window_starts_and_stays_up(tmp_path):
    home = tmp_path / "standalone_home"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"schema": 1, "port": 17999}), encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "assistant.overlay.settings_ui"], cwd=REPO_ROOT,
                            env=dict(os.environ, ASSISTANT_HOME=str(home)), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    try:
        time.sleep(4)
        assert proc.poll() is None, proc.stderr.read().decode("utf-8", "replace")
    finally:
        proc.kill()
        out, err = proc.communicate(timeout=10)
    assert b"Traceback" not in err, err.decode("utf-8", "replace")


# ---- the full app: feeds, health, live apply ------------------------------------------------------------------

SECRET = "PRIVATEFEEDSEGMENT42"


def cal_settings(url, **cal):
    return {"calendars": {"ics_urls": [url], "outlook": False, "lead_minutes": 5, "poll_minutes": 1, **cal}}


def meeting_cards(api):
    status, cards = api.get("/v1/cards")
    assert status == 200
    return [c for c in cards if c["kind"] == "meeting"]


def calendars(api):
    status, body = api.get("/v1/health")
    assert status == 200
    return body.get("calendars", [])


def test_feed_event_becomes_a_blue_meeting_card_at_lead_time_in_the_running_app(app_factory, ics_server):
    now = time.time()
    start = now + 5 * 60 + 5
    url = ics_server.serve(f"/p/{SECRET}/ok.ics", build_ics(
        [{"uid": "e1@example.test", "subject": "App level sync", "start": start, "end": start + 1800}]))
    with app_factory(cal_settings(url)) as (base, token):
        api = Api(base, token)
        assert wait_until(lambda: calendars(api) and calendars(api)[0].get("last_ok"), timeout=15), calendars(api)
        assert meeting_cards(api) == []
        assert wait_until(lambda: meeting_cards(api), timeout=15, step=0.25), "no meeting card"
        assert abs(time.time() - (start - 300)) <= 5
        card = meeting_cards(api)[0]
        assert card["title"] == "App level sync starts in 5 minutes"
        assert abs(card["expires_at"] - (start + 1800)) <= 1.5


def test_404_feed_shows_last_error_in_health_makes_no_card_and_the_path_is_not_in_app_log(app_factory, ics_server, tmp_path):
    url = ics_server.url(f"/p/{SECRET}/gone.ics")
    with app_factory(cal_settings(url)) as (base, token):
        api = Api(base, token)
        assert wait_until(lambda: calendars(api) and calendars(api)[0].get("last_error"), timeout=15), calendars(api)
        assert "404" in calendars(api)[0]["last_error"]
        assert api.get("/v1/cards")[1] == []
        assert SECRET not in json.dumps(api.get("/v1/health")[1])
    log = (tmp_path / "private_home" / "logs" / "app.log").read_text(encoding="utf-8")
    assert SECRET not in log
    assert any(SECRET in p for p in ics_server.hits)


@pytest.mark.timeout(150)
def test_lead_time_changed_in_settings_json_applies_to_the_next_card_without_restart(app_factory, ics_server, tmp_path):
    now = time.time()
    start = now + 3 * 60 + 20
    url = ics_server.serve("/p/live.ics", build_ics(
        [{"uid": "e2@example.test", "subject": "Live apply", "start": start, "end": start + 1800}]))
    with app_factory(cal_settings(url, lead_minutes=1)) as (base, token):
        api = Api(base, token)
        assert wait_until(lambda: calendars(api) and calendars(api)[0].get("last_ok"), timeout=15)
        assert meeting_cards(api) == [], "lead 1 min: not due for another ~2 minutes"
        path = tmp_path / "private_home" / "settings.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["calendars"]["lead_minutes"] = 3
        path.write_text(json.dumps(data), encoding="utf-8")
        cards = wait_until(lambda: meeting_cards(api), timeout=75, step=0.5)
        assert cards, ("the edited lead time was never applied; the app must notice settings.json edits "
                       "(or the plan's wording 'changing lead time' means only the Settings window, which is covered above)")
        assert cards[0]["title"] == "Live apply starts in 3 minutes"
        assert time.time() < start - 60, "the card must come at the new lead, not the old one"
