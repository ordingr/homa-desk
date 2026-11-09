"""M4-0 "M3b leftovers" (plan 19.3; R9-N5/N6/N8/N9/N12, AC-AC-1, AC-AC-7, AC-SH-6, AC-FI-15, AC-PV-5 / A23).

Everything here runs without a desktop: the popup is exercised through ``SearchPopup.__new__`` and its pure-Python
methods (no Tk root), Edge is replaced at the ``subprocess``/window-enumeration seam, the registry read is replaced at
``winreg``, and the junction, zip and file-handle cases use real files in ``tmp_path``. Where a real app is needed the
shared ``preview_app`` fixture is used.
"""
import io
import json
import os
import subprocess
import threading
import time
import types
import zipfile

import pytest

from homa import settings as settings_mod
from homa.actions import shell
from homa.index import manager as manager_mod
from homa.overlay import fullpage, search_ui
from homa.preview import extract
from homa.security import denylist
from homa.server import routes_files
from homa.settings import DEFAULTS, Settings

USER = os.environ.get("USERNAME", "someone")


# ---- (5) popup: List folder label, typed custom range, Ctrl+D cycle, Esc closes the page first ----------------------

def test_the_folder_button_is_called_list_folder():
    labels = {verb: label for verb, label, _key in search_ui.BUTTONS}
    assert labels["folder_listing"] == "List folder"
    assert not any("Fastsearch" in label for label in labels.values())


@pytest.mark.parametrize("text, expected", [
    ("budget modified:2026-01-01..2026-01-31 q3", ("budget q3", "2026-01-01..2026-01-31")),
    ("2026-02-01..2026-02-28 notes", ("notes", "2026-02-01..2026-02-28")),
    ("MODIFIED:2026-03-01..2026-03-02", ("", "2026-03-01..2026-03-02")),
    ("a   modified:2026-03-01..2026-03-02    b", ("a b", "2026-03-01..2026-03-02")),
    ("2026-02-30..2026-03-01 x", ("2026-02-30..2026-03-01 x", "")),                # not a real day
    ("x2026-01-01..2026-01-02", ("x2026-01-01..2026-01-02", "")),                  # glued to a word
    ("2026-01-01..2026-01-02y", ("2026-01-01..2026-01-02y", "")),
    ("modified:today", ("modified:today", "")),                                    # presets are not ranges
    ("nothing here", ("nothing here", "")),
    ("", ("", "")),
])
def test_extract_range(text, expected):
    assert search_ui.extract_range(text) == expected


def test_extract_range_takes_the_first_valid_token_and_leaves_a_later_one_in_the_text():
    clean, rng = search_ui.extract_range("2026-02-30..2026-03-01 2026-04-01..2026-04-30 2026-05-01..2026-05-02")
    assert rng == "2026-04-01..2026-04-30"
    assert "2026-05-01..2026-05-02" in clean and "2026-02-30" in clean


class _Var:
    def __init__(self, text):
        self.text = text

    def get(self):
        return self.text

    def set(self, text):
        self.text = text


def _popup(bar=""):
    p = search_ui.SearchPopup.__new__(search_ui.SearchPopup)
    p.var = _Var(bar)
    p.modified = ""
    p.state = "input"
    p.menu_open = False
    p._listing_stack = []
    p.fullpage_is_open = None
    p.fullpage_close = None
    p.details, p.details_kind = "", ""
    p._render = lambda: None
    return p


def _cycle(p, times):
    seen = []
    for _ in range(times):
        p.cycle_modified()
        seen.append(p.modified)
    return seen


def test_ctrl_d_cycles_any_today_7d_30d_any_when_the_bar_holds_no_range():
    p = _popup("report q3")
    assert _cycle(p, 5) == ["today", "7d", "30d", "", "today"]
    assert p.var.get() == "report q3"


def test_ctrl_d_adds_custom_after_30_days_when_the_bar_holds_a_range_and_moves_it_into_the_filter():
    p = _popup("report modified:2026-01-01..2026-01-31")
    assert _cycle(p, 6) == ["today", "7d", "30d", "2026-01-01..2026-01-31", "", "today"]
    assert p.var.get() == "report", "the range token leaves the query text once it becomes the filter"


def test_ctrl_d_from_a_custom_range_goes_back_to_any_time():
    p = _popup("")
    p.modified = "2026-01-01..2026-01-31"
    p.cycle_modified()
    assert p.modified == ""


def test_ctrl_d_ignores_a_bar_range_that_is_not_a_real_date():
    p = _popup("2026-13-01..2026-13-09")
    assert _cycle(p, 4) == ["today", "7d", "30d", ""]


def _esc_popup(page_open, listing=False, menu=False):
    p = _popup()
    calls = []
    p.fullpage_is_open = lambda: page_open
    p.fullpage_close = lambda: calls.append("page closed")
    p.menu_open = menu
    p._close_menu = lambda: calls.append("menu closed")
    p._leave_listing = lambda: calls.append("listing left")
    p.close = lambda: calls.append("popup closed")
    p._listing_stack = [{"folder": "x"}] if listing else []
    return p, calls


def test_esc_with_the_full_page_open_closes_the_page_before_anything_else():
    p, calls = _esc_popup(page_open=True, listing=True)
    assert p._escape() == "break"
    assert calls == ["page closed"]


def test_esc_chain_page_then_listing_then_popup():
    p, calls = _esc_popup(page_open=True, listing=True)
    p._escape()
    p.fullpage_is_open = lambda: False
    p._escape()
    p._listing_stack = []
    p._escape()
    assert calls == ["page closed", "listing left", "popup closed"]


def test_an_open_context_menu_still_takes_esc_before_the_page():
    p, calls = _esc_popup(page_open=True, menu=True)
    p._escape()
    assert calls == ["menu closed"]


def test_esc_without_page_callbacks_or_when_they_raise_behaves_as_before():
    p, calls = _esc_popup(page_open=False)
    p.fullpage_is_open = p.fullpage_close = None
    p._escape()
    assert calls == ["popup closed"]
    p, calls = _esc_popup(page_open=False)
    p.fullpage_is_open = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    p._escape()
    assert calls == ["popup closed"]


def test_show_note_puts_one_line_in_the_details_area_only_while_the_popup_is_open():
    p = _popup()
    rendered = []
    p._render = lambda: rendered.append(1)
    p.show_note("Edge started but the full page window did not appear.")
    assert p.details == "Edge started but the full page window did not appear." and p.details_kind == "err"
    assert rendered == [1]
    p.show_note("")
    assert p.details.startswith("Edge started") and rendered == [1], "an empty note changes nothing"
    p.state = "closed"
    p.details = ""
    p.show_note("hidden popup")
    assert p.details == "" and rendered == [1]


# ---- (6) fullpage.open returns False with a reason; window matched by class AND title -------------------------------

class _Win:
    """A fake win32gui: {hwnd: (class, title)}."""

    def __init__(self, windows):
        self.windows = windows

    def GetClassName(self, h):
        return self.windows[h][0]

    def GetWindowText(self, h):
        return self.windows[h][1]


@pytest.fixture
def edge(monkeypatch, tmp_path):
    """fullpage with a fake msedge, a recording Popen, a fresh state and a short search window."""
    launched = []

    def popen(argv, **kw):
        launched.append(argv)
        return types.SimpleNamespace(pid=4321)

    monkeypatch.setattr(fullpage, "subprocess", types.SimpleNamespace(Popen=popen, DEVNULL=-3))
    monkeypatch.setattr(fullpage, "edge_path", lambda: r"C:\fake\msedge.exe")
    monkeypatch.setattr(fullpage, "FIND_TIMEOUT_S", 0.6)
    monkeypatch.setattr(fullpage, "FIND_POLL_S", 0.05)
    monkeypatch.setattr(fullpage, "_state", {"hwnd": None, "pid": None, "reason": ""})
    state = types.SimpleNamespace(launched=launched, windows={}, profile=tmp_path / "profile", listed=[{}])
    monkeypatch.setattr(fullpage, "win32gui", _Win(state.windows))
    monkeypatch.setattr(fullpage, "edge_windows", lambda: dict(state.listed[0]))
    return state


def _open(edge):
    return fullpage.open("q", 1234, lambda: "nonce", profile=edge.profile)


def test_open_returns_false_with_a_reason_when_edge_starts_but_no_window_appears(edge):
    t0 = time.monotonic()
    assert _open(edge) is False
    assert time.monotonic() - t0 >= 0.5, "it must really wait out the search window"
    assert "did not appear" in fullpage.last_failure()
    assert len(edge.launched) == 1


def test_open_does_not_adopt_a_window_with_the_wrong_title_or_class(edge):
    edge.windows.update({77: ("Chrome_WidgetWin_1", "Some other site - Microsoft Edge"),
                         78: ("Chrome_WidgetWin_0", "Homa - file search")})
    edge.listed[0] = {77: 1, 78: 2}
    assert _open(edge) is False
    assert fullpage.last_failure()
    assert fullpage._state["hwnd"] is None


def test_open_does_not_adopt_a_matching_window_that_was_already_open_before_the_launch(edge):
    edge.windows[90] = ("Chrome_WidgetWin_1", "Homa - file search")
    edge.listed[0] = {90: 5}
    assert _open(edge) is False


def test_open_returns_true_for_the_new_window_with_the_right_class_and_title_and_clears_the_reason(edge):
    fullpage._state["reason"] = "stale reason from an earlier failure"

    def appear():
        time.sleep(0.2)
        edge.windows[91] = ("Chrome_WidgetWin_1", "Homa - file search - Microsoft Edge")
        edge.listed[0] = {91: 4321}

    threading.Thread(target=appear, daemon=True).start()
    assert _open(edge) is True
    assert fullpage._state["hwnd"] == 91 and fullpage._state["pid"] == 4321
    assert fullpage.last_failure() == ""


def test_open_prefers_the_window_of_the_process_it_launched(edge):
    def appear():
        time.sleep(0.1)
        edge.windows[11] = ("Chrome_WidgetWin_1", "Homa - file search")
        edge.windows[12] = ("Chrome_WidgetWin_1", "Homa - file search")
        edge.listed[0] = {11: 999, 12: 4321}

    threading.Thread(target=appear, daemon=True).start()
    assert _open(edge) is True
    assert fullpage._state["hwnd"] == 12


def test_open_launches_edge_with_the_isolation_flags_and_its_own_profile(edge):
    _open(edge)
    argv = edge.launched[0]
    for flag in ("--disable-sync", "--disable-extensions", "--disable-background-networking", "--no-service-autorun"):
        assert flag in argv, flag
    assert any(a.startswith("--app=http://127.0.0.1:1234/app/?launch=nonce") for a in argv)
    assert f"--user-data-dir={edge.profile}" in argv
    assert tuple(fullpage.PROFILE_FLAGS) == ("--disable-sync", "--disable-extensions", "--disable-background-networking",
                                              "--no-service-autorun")


def test_open_returns_false_naming_edge_when_msedge_is_missing_or_will_not_start(edge, monkeypatch):
    monkeypatch.setattr(fullpage, "edge_path", lambda: None)
    assert _open(edge) is False
    assert "Edge was not found" in fullpage.last_failure()
    monkeypatch.setattr(fullpage, "edge_path", lambda: r"C:\fake\msedge.exe")

    def refuse(*a, **k):
        raise OSError("blocked by policy")

    monkeypatch.setattr(fullpage, "subprocess", types.SimpleNamespace(Popen=refuse, DEVNULL=-3))
    assert _open(edge) is False
    assert "could not be started" in fullpage.last_failure()


def _app_stub(monkeypatch, opened, reason, popup=True):
    from homa import app as app_mod
    posted = []
    monkeypatch.setattr(app_mod.uibus, "post", lambda fn, *a: posted.append((fn, a)))
    monkeypatch.setattr(app_mod.fullpage, "open", lambda *a, **k: opened)
    monkeypatch.setattr(app_mod.fullpage, "last_failure", lambda: reason)
    instance = app_mod.App.__new__(app_mod.App)
    instance.server = types.SimpleNamespace(port=1)
    instance.popup = types.SimpleNamespace(show_note=lambda text: None, close=lambda: None) if popup else None
    return app_mod, instance, posted


def test_app_posts_the_failure_reason_to_the_popup(monkeypatch):
    app_mod, instance, posted = _app_stub(monkeypatch, False, "Edge started but the full page window did not appear.")
    assert instance._open_fullpage("q") is False
    assert posted == [(instance.popup.show_note, ("Edge started but the full page window did not appear.",))]


def test_app_falls_back_to_a_generic_line_and_tolerates_no_popup(monkeypatch):
    app_mod, instance, posted = _app_stub(monkeypatch, False, "")
    assert instance._open_fullpage("q") is False
    assert posted[0][1] == ("The full page did not open.",)
    app_mod, instance, posted = _app_stub(monkeypatch, False, "x", popup=False)
    assert instance._open_fullpage("q") is False and posted == []
    app_mod, instance, posted = _app_stub(monkeypatch, True, "")
    assert instance._open_fullpage("q") is True and posted == [(instance.popup.close, ())]
    app_mod, instance, posted = _app_stub(monkeypatch, True, "", popup=False)
    assert instance._open_fullpage("q") is True and posted == []


def test_popup_close_is_posted_only_when_the_full_page_opened(monkeypatch):
    app_mod, instance, posted = _app_stub(monkeypatch, True, "")
    assert instance._open_fullpage("q") is True
    assert [fn for fn, _ in posted] == [instance.popup.close]
    assert posted[0][1] == ()
    app_mod, instance, posted = _app_stub(monkeypatch, False, "Edge missing")
    assert instance._open_fullpage("q") is False
    assert instance.popup.close not in [fn for fn, _ in posted], "a failed open leaves the popup up for the note"
    assert [fn for fn, _ in posted] == [instance.popup.show_note]


# ---- (4) md_vault.url ------------------------------------------------------------------------------------------------

def test_md_vault_default_is_an_empty_url():
    assert DEFAULTS["md_vault"] == {"url": ""}


@pytest.mark.parametrize("value", [{"url": ""}, {}, {"url": "https://vault.example.test/notes"}, {"url": "HTTP://x.test"}])
def test_md_vault_accepts_empty_and_http_urls(value):
    settings_mod._check("md_vault", value)


@pytest.mark.parametrize("value", [{"url": "ftp://x.test"}, {"url": "javascript:alert(1)"}, {"url": "http://a b"},
                                   {"url": "http://x.test/\n"}, {"url": 5}, {"url": None}, "https://x.test", None,
                                   {"url": "http://" + "a" * 5000}])
def test_md_vault_rejects_everything_else(value):
    with pytest.raises(ValueError):
        settings_mod._check("md_vault", value)


def test_a_bad_md_vault_url_on_disk_is_repaired_and_the_rest_of_the_file_survives(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMA_HOME", str(tmp_path))
    (tmp_path / "settings.json").write_text(json.dumps(
        {"schema": 1, "port": 40111, "md_vault": {"url": "javascript:1"}}), encoding="utf-8")
    s = Settings()
    s.load()
    assert s.get("md_vault") == {"url": ""}
    assert s.get("port") == 40111


def test_a_good_md_vault_url_round_trips_through_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMA_HOME", str(tmp_path))
    s = Settings()
    s.load()
    s.set("md_vault", {"url": "https://vault.example.test/"})
    again = Settings()
    again.load()
    assert again.get("md_vault") == {"url": "https://vault.example.test/"}
    with pytest.raises(ValueError):
        s.set("md_vault", {"url": "file:///etc/hosts"})
    assert s.get("md_vault") == {"url": "https://vault.example.test/"}


def test_shell_md_vault_opens_the_configured_address_and_ignores_an_invalid_one(tmp_path, monkeypatch):
    note = tmp_path / "n.md"
    note.write_text("x")
    opened = []
    monkeypatch.setattr(shell, "_vet", lambda index, path, **kw: str(note))
    monkeypatch.setattr(shell, "_shell_execute", lambda verb, target, flags: opened.append(target))
    monkeypatch.setattr(shell, "_Com", _NullCom)
    started = []
    monkeypatch.setattr(shell.os, "startfile", lambda p: started.append(p))
    result = shell.md_vault(str(note), settings=types.SimpleNamespace(get=lambda k: {"url": "https://vault.example.test/x"}))
    assert result["ok"] and opened == ["https://vault.example.test/x"] and started == []
    result = shell.md_vault(str(note), settings=types.SimpleNamespace(get=lambda k: {"url": "javascript:1"}))
    assert result["ok"] and opened == ["https://vault.example.test/x"] and started == [str(note)]


class _NullCom:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---- (7) R9-N8: rename / recycle act on the link, not its target -----------------------------------------------------

def _junction(link, target):
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if done.returncode != 0:
        pytest.skip(f"cannot create a junction here: {done.stdout}{done.stderr}")


class _Index:
    def __init__(self, *roots):
        self.roots = [os.path.realpath(r) for r in roots]

    def status(self):
        return {"roots": [{"path": r} for r in self.roots]}


@pytest.fixture
def linked(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMA_HOME", str(tmp_path / "ahome"))
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "inner").mkdir(parents=True)
    (root / "inner" / "f.txt").write_text("inner")
    outside.mkdir()
    (outside / "o.txt").write_text("outside")
    _junction(root / "to_outside", outside)
    _junction(root / "to_inner", root / "inner")
    real = os.path.realpath(root)
    return types.SimpleNamespace(root=real, outside=os.path.realpath(outside), index=_Index(root),
                                 to_outside=os.path.join(real, "to_outside"), to_inner=os.path.join(real, "to_inner"),
                                 inner=os.path.join(real, "inner"))


def test_a_junction_pointing_outside_the_root_is_acted_on_as_the_link(linked):
    resolved = linked.outside
    assert shell._link_or_target(linked.index, linked.to_outside, resolved) == linked.to_outside


def test_a_junction_pointing_inside_the_root_is_acted_on_as_the_link(linked):
    assert shell._link_or_target(linked.index, linked.to_inner, linked.inner) == linked.to_inner


def test_a_plain_file_and_a_plain_folder_keep_the_resolved_path(linked):
    f = os.path.join(linked.inner, "f.txt")
    assert shell._link_or_target(linked.index, f, f) == f
    assert shell._link_or_target(linked.index, linked.inner, linked.inner) == linked.inner


def test_a_file_reached_through_a_junction_is_not_mistaken_for_the_link(linked):
    through = os.path.join(linked.to_outside, "o.txt")
    resolved = os.path.join(linked.outside, "o.txt")
    assert shell._link_or_target(linked.index, through, resolved) == resolved


def test_dotdot_in_the_given_path_falls_back_to_the_resolved_path(linked):
    sneaky = os.path.join(linked.root, "inner", "..", "to_outside")
    assert shell._link_or_target(linked.index, sneaky, linked.outside) == linked.outside


def test_a_link_whose_folder_is_not_in_a_root_is_not_followed_as_a_link(linked, tmp_path):
    stray = tmp_path / "stray"
    stray.mkdir()
    _junction(stray / "j", linked.inner)
    assert shell._link_or_target(linked.index, str(stray / "j"), linked.inner) == linked.inner


def test_a_deny_listed_link_name_is_not_taken_as_a_link(linked, tmp_path):
    secret = tmp_path / "root" / "id_rsa"
    _junction(secret, linked.inner)
    given = os.path.join(linked.root, "id_rsa")
    assert denylist.matches(given), "precondition: id_rsa is on the deny-list"
    assert shell._link_or_target(linked.index, given, linked.inner) == linked.inner


def test_a_link_inside_the_assistants_own_home_is_not_taken_as_a_link(tmp_path, monkeypatch):
    home = tmp_path / "ahome"
    home.mkdir()
    monkeypatch.setenv("HOMA_HOME", str(home))
    target = tmp_path / "t"
    target.mkdir()
    _junction(home / "j", target)
    given = os.path.join(os.path.realpath(home), "j")
    assert shell._link_or_target(_Index(tmp_path), given, os.path.realpath(target)) == os.path.realpath(target)


def test_rename_and_recycle_end_to_end_act_on_the_link_and_leave_the_target_alone(linked, tmp_path, monkeypatch):
    from test_manager_churn import Rig
    rig = Rig(tmp_path / "root", tmp_path / "idx" / "files.idx").start()
    try:
        # a junction to another in-root folder: the junction gets the new name, the folder is untouched
        result = shell.rename(linked.to_inner, "renamed_link", index=rig.manager)
        assert result["ok"], result
        assert os.path.isdir(os.path.join(linked.root, "renamed_link"))
        assert not os.path.lexists(linked.to_inner)
        assert os.path.isfile(os.path.join(linked.inner, "f.txt")), "the target folder must be untouched"
        # recycle: the path handed to the Recycle Bin is the link
        sent = []
        monkeypatch.setattr(shell, "_recycle_item", lambda p: sent.append(p))
        monkeypatch.setattr(shell, "_volume_problem", lambda p: None)
        monkeypatch.setattr(shell, "_bin_problem", lambda p: None)
        link = os.path.join(linked.root, "renamed_link")
        shell.recycle(link, confirm=True, index=rig.manager)
        assert sent == [link], sent
        # a junction that leads out of every root is still refused by the root check (never acted on)
        sent.clear()
        with pytest.raises(shell._Stop):
            shell.recycle(linked.to_outside, confirm=True, index=rig.manager)
        assert sent == []
        assert os.path.isfile(os.path.join(linked.outside, "o.txt"))
    finally:
        rig.stop()


# ---- (7) R9-N8/N6: Recycle Bin policy, deny-list ---------------------------------------------------------------------

class _FakeReg:
    """winreg stand-in: ``values`` maps hive name -> NoRecycleFiles value (or absent)."""

    def __init__(self, monkeypatch, values):
        import winreg
        self.opened = []
        self.values = values

        class Key:
            def __init__(s, hive):
                s.hive = hive

            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

        def open_key(hive, sub, *a, **k):
            name = {winreg.HKEY_CURRENT_USER: "HKCU", winreg.HKEY_LOCAL_MACHINE: "HKLM"}[hive]
            self.opened.append((name, sub))
            if name not in values:
                raise FileNotFoundError(sub)
            return Key(name)

        def query(key, value_name):
            assert value_name == "NoRecycleFiles"
            return values[key.hive], 4

        monkeypatch.setattr(winreg, "OpenKey", open_key)
        monkeypatch.setattr(winreg, "QueryValueEx", query)


@pytest.mark.parametrize("values, expected", [
    ({"HKCU": 1}, True), ({"HKLM": 1}, True), ({"HKCU": 0, "HKLM": 1}, True), ({"HKCU": 0, "HKLM": 0}, False),
    ({}, False), ({"HKCU": "garbage"}, False), ({"HKCU": None, "HKLM": 0}, False),
])
def test_no_recycle_policy_reads_both_hives(monkeypatch, values, expected):
    reg = _FakeReg(monkeypatch, values)
    assert shell._no_recycle_policy() is expected
    assert all(sub.endswith(r"Policies\Explorer") for _, sub in reg.opened)


def test_recycle_refuses_with_the_policy_reason_before_asking_for_confirmation(monkeypatch):
    monkeypatch.setattr(shell, "_no_recycle_policy", lambda: True)
    assert "policy" in shell._bin_problem(os.getcwd())


@pytest.mark.parametrize("product", ["Homa", "Assistant"])
def test_appdata_local_homa_and_legacy_assistant_are_on_the_deny_list(product):
    base = rf"C:\Users\{USER}\AppData\Local\{product}"
    assert denylist.matches(base + r"\edge-profile\Default\Cookies")
    assert denylist.matches(base + "\\logs\\app.log")


# ---- (7) R9-N12: attributes re-read through the open handle ---------------------------------------------------------

def test_handle_attrs_reads_the_attributes_of_the_open_file(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    with open(f, "rb") as fh:
        attrs = routes_files._handle_attrs(fh)
    assert attrs & 0x20 or attrs == 0x80, hex(attrs)                   # archive / normal
    assert not attrs & (routes_files._PLACEHOLDER_ATTRS | routes_files._DIR_ATTR)


def test_handle_attrs_fails_closed_when_the_handle_cannot_be_queried():
    fh = io.BytesIO(b"x")                                              # no fileno()
    assert routes_files._handle_attrs(fh) & routes_files._PLACEHOLDER_ATTRS


@pytest.fixture
def preview_stub(tmp_path, monkeypatch):
    f = tmp_path / "plain.txt"
    f.write_text("just text\n")
    monkeypatch.setattr(routes_files.mcp, "_vet", lambda index, path: (str(f), None))
    monkeypatch.setattr(routes_files, "_handle_ok", lambda fh, roots: True)
    monkeypatch.setattr(routes_files, "_roots", lambda index: [str(tmp_path)])
    monkeypatch.setattr(routes_files, "_cap", lambda settings: 10 ** 9)

    def forbidden(*a, **k):
        raise AssertionError("content was read for a file the handle says is a placeholder")

    return types.SimpleNamespace(path=str(f), forbidden=forbidden)


def test_a_file_that_became_a_placeholder_after_the_lstat_is_answered_without_reading_it(preview_stub, monkeypatch):
    monkeypatch.setattr(routes_files, "_handle_attrs", lambda fh: 0x1000)       # RECALL_ON_OPEN
    monkeypatch.setattr(routes_files, "_text_answer", preview_stub.forbidden)
    answer = routes_files._preview_sync(None, None, preview_stub.path, None, "")
    assert answer["kind"] == "placeholder" and answer["name"] == "plain.txt"


def test_a_path_that_became_a_directory_after_the_lstat_gets_the_placeholder_answer(preview_stub, monkeypatch):
    monkeypatch.setattr(routes_files, "_handle_attrs", lambda fh: 0x10)
    monkeypatch.setattr(routes_files, "_text_answer", preview_stub.forbidden)
    assert routes_files._preview_sync(None, None, preview_stub.path, None, "")["kind"] == "placeholder"


def test_the_same_file_with_normal_handle_attributes_is_previewed(preview_stub):
    answer = routes_files._preview_sync(None, None, preview_stub.path, None, "")
    assert answer["kind"] in ("text", "markdown"), answer
    assert "just text" in json.dumps(answer)


def test_the_key_scan_overlap_covers_a_header_that_ends_well_before_the_window(tmp_path):
    assert routes_files.SCAN_OVERLAP >= 8192
    f = tmp_path / "big.txt"
    header = b"-----BEGIN RSA PRIVATE KEY-----\n"
    filler = b"a harmless line of text.\n"
    pad = filler * (2 * 1_048_576 // len(filler))                    # push the header beyond the first MiB
    start_of_window = len(pad) + len(header) + 5000
    f.write_bytes(pad + header + b"x" * 6000 + filler * 400)
    with open(f, "rb") as fh:
        body, scanned = routes_files._read_scanned(fh, start_of_window, 4096)
    assert len(body) == 4096
    assert denylist.content_has_private_key(scanned), "a key header 5,000 bytes before the window must still be seen"


def test_hydrate_parameter_is_ignored_by_the_preview_route(preview_app):
    status, plain = preview_app.preview("csv")
    if status == 404:
        pytest.skip("GET /v1/files/preview is not served")
    status2, hydrated = preview_app.preview("csv", hydrate="1")
    assert status == status2 == 200
    assert plain == hydrated


# ---- (9) docx / pptx member cap --------------------------------------------------------------------------------------

def _zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    buf.seek(0)
    return buf


def test_docx_member_over_the_cap_is_flagged_truncated_and_still_returns_the_text_it_read(monkeypatch):
    monkeypatch.setattr(extract, "MEMBER_CAP", 4096)
    xml = "<w:document><w:body>" + "".join(f"<w:p><w:r><w:t>line {i}</w:t></w:r></w:p>" for i in range(2000)) + \
          "</w:body></w:document>"
    result = extract.extract_docx(_zip({"word/document.xml": xml}))
    assert result["truncated"] is True
    assert "line 0" in result["text"]
    assert "line 1999" not in result["text"]


def test_docx_under_the_cap_is_not_flagged(monkeypatch):
    monkeypatch.setattr(extract, "MEMBER_CAP", 4096)
    xml = "<w:document><w:body><w:p><w:r><w:t>small</w:t></w:r></w:p></w:body></w:document>"
    result = extract.extract_docx(_zip({"word/document.xml": xml}))
    assert result == {"text": "small", "truncated": False} or (result["text"].strip() == "small" and not result["truncated"])


def test_pptx_slide_over_the_cap_flags_truncated_and_a_small_deck_does_not(monkeypatch):
    monkeypatch.setattr(extract, "MEMBER_CAP", 4096)
    big = "<p:sld>" + "".join(f"<a:p><a:r><a:t>point {i}</a:t></a:r></a:p>" for i in range(2000)) + "</p:sld>"
    small = "<p:sld><a:p><a:r><a:t>hello</a:t></a:r></a:p></p:sld>"
    result = extract.extract_pptx(_zip({"ppt/slides/slide1.xml": big}))
    assert result["truncated"] is True and "point 0" in result["text"]
    result = extract.extract_pptx(_zip({"ppt/slides/slide1.xml": small, "ppt/slides/slide2.xml": small}))
    assert result["truncated"] is False and "hello" in result["text"]


def test_a_docx_far_over_the_default_cap_is_bounded_in_time_and_memory():
    t0 = time.perf_counter()
    big = b"<w:t>a</w:t>" * (extract.MEMBER_CAP // 12 + 100_000)
    result = extract.extract_docx(_zip({"word/document.xml": big}))
    assert result["truncated"] is True
    assert time.perf_counter() - t0 < 20


# ---- (8) --doctor ----------------------------------------------------------------------------------------------------

def _doctor_lines():
    from homa import doctor
    return dict(doctor.report())


def test_doctor_reports_edge_static_folder_and_recycle_bin():
    lines = _doctor_lines()
    for key in ("Edge (full page)", "static folder", "Recycle Bin"):
        assert key in lines and lines[key], key
    assert "found at" in lines["Edge (full page)"] or "not found" in lines["Edge (full page)"]
    assert "index.html present" in lines["static folder"] or "MISSING" in lines["static folder"]
    assert "MaxCapacity" in lines["Recycle Bin"] or "no per-volume settings key" in lines["Recycle Bin"] \
        or "unavailable" in lines["Recycle Bin"]


def test_doctor_lines_follow_the_underlying_state(monkeypatch, tmp_path):
    from homa import doctor
    from homa.server import static
    monkeypatch.setattr(fullpage, "edge_path", lambda: None)
    assert "not found" in doctor._edge_state()
    monkeypatch.setattr(fullpage, "edge_path", lambda: r"C:\fake\msedge.exe")
    assert doctor._edge_state() == r"found at C:\fake\msedge.exe"

    (tmp_path / "index.html").write_text("<html></html>")
    monkeypatch.setenv(static.ENV_VAR, str(tmp_path))
    text = doctor._static_state()
    assert str(tmp_path) in text and "override" in text and "index.html present" in text
    (tmp_path / "index.html").unlink()
    assert "MISSING" in doctor._static_state()
    monkeypatch.delenv(static.ENV_VAR)
    assert "packaged" in doctor._static_state()

    monkeypatch.setattr(shell, "_bin_settings", lambda drive: (10240, 0))
    monkeypatch.setattr(shell, "_no_recycle_policy", lambda: False)
    text = doctor._recycle_bin_state()
    assert "MaxCapacity 10240 MB" in text and "NukeOnDelete 0" in text and "NoRecycleFiles" not in text
    monkeypatch.setattr(shell, "_bin_settings", lambda drive: (None, 1))
    monkeypatch.setattr(shell, "_no_recycle_policy", lambda: True)
    text = doctor._recycle_bin_state()
    assert "MaxCapacity unset" in text and "NukeOnDelete 1" in text and "NoRecycleFiles policy ON" in text
    monkeypatch.setattr(shell, "_bin_settings", lambda drive: (None, None))
    assert "no per-volume settings key" in doctor._recycle_bin_state()


def test_doctor_cli_prints_the_new_lines():
    done = subprocess.run([os.sys.executable, "-m", "homa", "--doctor"], capture_output=True, text=True, timeout=60,
                          cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert "Edge (full page)" in done.stdout and "static folder" in done.stdout and "Recycle Bin" in done.stdout, \
        (done.stdout, done.stderr)
