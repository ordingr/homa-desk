"""Search popup and hotkey (plan 7.3 M3a-4; AC-SH-1 .. AC-SH-6, AC-SH-8, AC-SH-9, AC-FI-14 .. AC-FI-17, AC-MM-2).

Layer used: IN-PROCESS. A real Tk root, the real SearchPopup / ModeRegistry / FilesMode / AskNotesMode over the fixture
tree and notes vault, a real Hotkey thread, driven through the popup's public methods and ``debug_state()``. No
running app is involved: ``/v1/_debug/ui`` does not expose the popup until M3a-7 wires it, and the last test
(``test_running_app_debug_route_exposes_the_search_popup``) skips until it does. The hotkey press is a posted WM_HOTKEY to
the hotkey thread (a synthesized message, not physical key input); real SendInput of the chord is the driver's job
once the app is wired.

Needs a desktop session for Tk.
"""
import os
import shutil
import time
import tkinter as tk

import pytest

from conftest import NOTES_FIXTURE, wait_until, walk_store

search_ui = pytest.importorskip("homa.overlay.search_ui", reason="homa/overlay/search_ui.py (M3a-4) not present")
hotkey_mod = pytest.importorskip("homa.overlay.hotkey", reason="homa/overlay/hotkey.py (M3a-4) not present")
modes_mod = pytest.importorskip("homa.search.modes", reason="homa/search/modes.py (M3a-4) not present")
pytest.importorskip("homa.index.search", reason="homa/index/search.py (M3a-3) not present")
notes_bm25 = pytest.importorskip("homa.search.notes_bm25", reason="homa/search/notes_bm25.py (M3a-6) not present")

from homa import uibus  # noqa: E402
from homa.search.saved import SavedSearches  # noqa: E402

KNOWN = {"md", "py", "html", "txt", "yml", "pdf", "docx", "xlsx", "csv", "png", "json", "js", "ts"}
FREE_COMBO = "ctrl+alt+f10"


class IconStub:
    size = 60

    def position(self):
        return (900, 400)


class Settings:
    def __init__(self, **data):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)


@pytest.fixture(scope="module")
def root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no Tk display: {exc}")
    r.withdraw()
    uibus.start(r)
    yield r
    r.destroy()


def pump(root, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        uibus.drain()
        time.sleep(0.01)


def settle(root, popup, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        pump(root, 0.05)
        if not popup.pending:
            return
    raise AssertionError("search did not finish: " + str(popup.debug_state()))


@pytest.fixture
def popup(root, search_tree, tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    shutil.copytree(NOTES_FIXTURE, vault)
    monkeypatch.delenv("HOMA_NOTES_DIR", raising=False)
    store = walk_store(search_tree)
    notes = notes_bm25.AskNotesMode(settings=Settings(notes={"dir": str(vault)}), index_path=tmp_path / "notes.json.gz")
    notes.index().wait_ready(30)
    registry = modes_mod.default_registry(lambda: (store, None, KNOWN), notes)
    actions = []
    pop = search_ui.SearchPopup(root, IconStub(), registry, Settings(), saved=SavedSearches(tmp_path / "saved.json"),
                                on_action=lambda verb, row: actions.append((verb, row.name)),
                                on_fullpage=lambda q, h: actions.append(("fullpage", q)))
    pop.actions = actions
    yield pop
    pop.close()
    pop.win.destroy()
    notes.close()


def type_and_enter(root, popup, text):
    popup.var.set(text)
    pump(root, 0.05)
    popup.submit()
    settle(root, popup)
    return popup.debug_state()


# ---- AC-SH-1/3: open, badge, press-to-box ------------------------------------------------------------------------------

def test_open_shows_the_box_beside_the_icon_with_the_files_local_badge(root, popup):
    t0 = time.perf_counter()
    popup.toggle(press_ts=t0)
    pump(root, 0.4)
    st = popup.debug_state()
    assert st["open"] and st["state"] == "input" and st["anim_done"]
    assert st["badge"] == "Files · local"
    assert st["placement"] == "icon" and st["rect"][2] <= 900 + 5, "the box sits to the left of the icon"
    assert st["press_to_box_ms"] is not None and st["press_to_box_ms"] <= 300, st["press_to_box_ms"]
    print(f"\n[popup] press -> box {st['press_to_box_ms']} ms (target <= 300)")


def test_toggle_twice_closes(root, popup):
    popup.toggle()
    pump(root, 0.2)
    popup.toggle()
    assert popup.debug_state()["open"] is False


# ---- AC-SH-3, AC-FI-17, AC-SH-9, AC-FI-18: Enter grows into results -------------------------------------------------------------

def test_design_md_enter_shows_results_header_details_and_interpretation(root, popup):
    popup.open()
    pump(root, 0.3)
    st = type_and_enter(root, popup, "design md")
    assert st["state"] == "results" and st["result_count"] == 2 and st["selected_name"] == "designer.md"
    assert "2 file(s)" in st["header"] and "index from" in st["header"] and " ms" in st["header"]
    assert 'name or path contains "design"' in st["header"] and "ext: md" in st["header"]
    assert st["interpretation"].endswith("ext: md")
    print("details line:", repr(st["details"]))
    assert "designer.md" in st["details"] and "800" in st["details"]
    assert st["badge"] == "Files · local" and st["row_kind"] == "file"


def test_plain_design_lists_every_match_and_up_down_move_the_selection(root, popup):
    popup.open()
    pump(root, 0.3)
    st = type_and_enter(root, popup, "design")
    assert st["result_count"] == 7 and st["selected"] == 0
    popup.move(1)
    popup.move(1)
    assert popup.debug_state()["selected"] == 2
    popup.move(-5)
    assert popup.debug_state()["selected"] == 0, "selection is clamped at the top"
    popup.move(99)
    assert popup.debug_state()["selected"] == 6, "and at the bottom"


def test_enter_on_unchanged_results_opens_the_selected_row(root, popup):
    popup.open()
    pump(root, 0.3)
    type_and_enter(root, popup, "design md")
    popup.submit()
    assert ("open", "designer.md") in popup.actions


def test_escape_walks_results_to_input_to_closed(root, popup):
    popup.open()
    pump(root, 0.3)
    type_and_enter(root, popup, "design")
    assert popup.escape() == "input"
    assert popup.debug_state()["open"] and popup.debug_state()["result_count"] == 0
    assert popup.escape() == "closed"
    assert popup.debug_state()["open"] is False


def test_protected_and_pruned_files_never_reach_the_popup(root, popup):
    popup.open()
    pump(root, 0.3)
    for q in (".env", "id_rsa", "kdbx", "probe_nm"):
        assert type_and_enter(root, popup, q)["result_count"] == 0, q
        popup.escape()


def test_unknown_text_shows_zero_results_not_an_error(root, popup):
    popup.open()
    pump(root, 0.3)
    st = type_and_enter(root, popup, "qqqq-no-such-file")
    assert st["state"] == "results" and st["result_count"] == 0 and not st["error"]
    assert st["header"].startswith("0 file(s)")


# ---- AC-SH-4: modes ------------------------------------------------------------------------------------------------------------

def test_tab_cycles_to_ask_notes_and_back(root, popup):
    popup.open()
    pump(root, 0.3)
    popup.cycle_mode()
    st = popup.debug_state()
    assert st["badge"] == "Ask notes · local" and st["mode"] == "?"
    popup.cycle_mode()
    assert popup.debug_state()["badge"] == "Files · local"


def test_ask_notes_results_are_passages_with_the_local_footer(root, popup):
    popup.open()
    pump(root, 0.3)
    popup.cycle_mode()
    st = type_and_enter(root, popup, "zebrafish migration")
    assert st["result_count"] >= 1 and st["row_kind"] == "note"
    assert st["badge"] == "Ask notes · local"
    assert "search over your notes; nothing left this PC" in st["footer"]
    assert "zebrafish" in st["passage_text"].lower() or "zebrafish" in st["details"].lower()


def test_leading_question_mark_switches_mode_for_that_query_only(root, popup):
    popup.open()
    pump(root, 0.3)
    popup.var.set("?zebrafish")
    pump(root, 0.05)
    st = popup.debug_state()
    assert st["prefix_switched"] is True and st["query"] == "zebrafish" and st["badge"] == "Ask notes · local"
    popup.submit()
    settle(root, popup)
    assert popup.debug_state()["row_kind"] == "note"
    popup.escape()
    popup.var.set("design md")
    pump(root, 0.05)
    assert popup.debug_state()["badge"] == "Files · local", "the next query is back in Files mode"


def test_files_mode_without_an_index_says_so_instead_of_crashing(root, tmp_path):
    registry = modes_mod.default_registry(lambda: None, None)
    pop = search_ui.SearchPopup(root, IconStub(), registry, Settings(), saved=SavedSearches(tmp_path / "s.json"))
    try:
        pop.open()
        pump(root, 0.3)
        st = type_and_enter(root, pop, "design")
        assert st["error"] and "index" in st["error"].lower() and st["result_count"] == 0
    finally:
        pop.close()
        pop.win.destroy()


# ---- AC-FI-14/15: chips and modified ---------------------------------------------------------------------------------------------

def test_chips_rerun_the_search_and_show_in_the_interpretation(root, popup):
    popup.open()
    pump(root, 0.3)
    type_and_enter(root, popup, "design")
    popup.toggle_chip("folders")
    settle(root, popup)
    st = popup.debug_state()
    assert st["chips"] == ["folders"] and st["result_count"] == 2 and "Folders" in st["interpretation"]
    popup.toggle_chip("docs")
    settle(root, popup)
    assert popup.debug_state()["result_count"] == 4, "chips OR together"
    popup.toggle_chip("folders")
    popup.toggle_chip("docs")
    settle(root, popup)
    assert popup.debug_state()["result_count"] == 7


def test_modified_filter_narrows_and_clears(root, popup):
    popup.open()
    pump(root, 0.3)
    type_and_enter(root, popup, "design")
    popup.set_modified("7d")
    settle(root, popup)
    st = popup.debug_state()
    assert st["modified"] == "7d" and "designer.md" not in st["header"] and 0 < st["result_count"] < 7
    popup.set_modified("")
    settle(root, popup)
    assert popup.debug_state()["result_count"] == 7


# ---- AC-FI-16: saved searches ------------------------------------------------------------------------------------------------------------

def test_saved_search_is_listed_before_typing_and_runs(root, popup):
    popup.open()
    pump(root, 0.3)
    type_and_enter(root, popup, "design md")
    popup.toggle_chip("docs")
    settle(root, popup)
    assert popup.save_current("my docs")
    popup.close()
    popup.open()
    pump(root, 0.3)
    st = popup.debug_state()
    assert st["state"] == "input" and st["saved_visible"] and "my docs" in st["saved"]
    popup.run_saved("my docs")
    settle(root, popup)
    st = popup.debug_state()
    assert st["state"] == "results" and st["result_count"] == 2 and st["chips"] == ["docs"] and st["text"] == "design md"
    popup.close()
    popup.open()
    pump(root, 0.3)
    assert popup.remove_saved("my docs") and "my docs" not in popup.debug_state()["saved"]


def test_saved_searches_survive_a_new_popup_instance(root, popup, tmp_path):
    popup.open()
    pump(root, 0.3)
    type_and_enter(root, popup, "report")
    assert popup.save_current("reports")
    assert "reports" in [s.name for s in SavedSearches(tmp_path / "saved.json").list()]


# ---- AC-SH-2 / AC-MM-2: placement ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("placement", ["icon", "mouse", "focused", "primary", "last"])
def test_every_placement_option_opens_on_screen(root, search_tree, tmp_path, placement):
    registry = modes_mod.default_registry(lambda: None, None)
    pop = search_ui.SearchPopup(root, IconStub(), registry, Settings(search_placement=placement),
                                saved=SavedSearches(tmp_path / "s.json"))
    try:
        pop.open()
        pump(root, 0.4)
        st = pop.debug_state()
        assert st["placement"] == placement and st["open"]
        left, top, right, bottom = st["rect"]
        assert right > left and bottom > top
        from homa.overlay import monitors
        assert monitors.is_visible(left + 5, top + 5, 5, 5), f"{placement}: box must be on a monitor, got {st['rect']}"
    finally:
        pop.close()
        pop.win.destroy()


def test_primary_placement_is_centred_on_the_primary_monitor(root, tmp_path):
    from homa.overlay import monitors
    registry = modes_mod.default_registry(lambda: None, None)
    pop = search_ui.SearchPopup(root, IconStub(), registry, Settings(search_placement="primary"),
                                saved=SavedSearches(tmp_path / "s.json"))
    try:
        pop.open()
        pump(root, 0.4)
        left, _, right, _ = pop.debug_state()["rect"]
        wl, _, wr, _ = monitors.primary_monitor().work
        assert abs((left + right) / 2 - (wl + wr) / 2) < 60
    finally:
        pop.close()
        pop.win.destroy()


# ---- hotkey ------------------------------------------------------------------------------------------------------------------------------------------

def post_hotkey(hk):
    import win32api  # noqa: F401
    import win32con
    import win32gui
    win32gui.PostThreadMessage(hk.thread_id, win32con.WM_HOTKEY, getattr(hotkey_mod, "HOTKEY_ID", 1), 0)


def test_free_combo_registers_and_a_posted_wm_hotkey_calls_back():
    got = []
    hk = hotkey_mod.Hotkey(FREE_COMBO, lambda: got.append(time.perf_counter()))
    try:
        assert hk.start() == "registered" and hk.status() == "registered"
        post_hotkey(hk)
        assert wait_until(lambda: got, 2.0)
        assert hk.presses == 1 and hk.last_press is not None
    finally:
        hk.stop()
    assert hk.status() == "disabled"


def test_occupied_combo_reports_already_registered_by_another_app():
    holder = hotkey_mod.Hotkey(FREE_COMBO, lambda: None)
    other = hotkey_mod.Hotkey(FREE_COMBO, lambda: None)
    try:
        assert holder.start() == "registered"
        assert other.start() == "already registered by another app"
        assert other.last_error == 1409
    finally:
        holder.stop()
        other.stop()


def test_remap_releases_the_old_key_and_bad_combos_are_unsupported():
    hk = hotkey_mod.Hotkey(FREE_COMBO, lambda: None)
    try:
        assert hk.start() == "registered"
        assert hk.set_combo("ctrl+alt+f11") == "registered"
        probe = hotkey_mod.Hotkey(FREE_COMBO, lambda: None)
        try:
            assert probe.start() == "registered", "the old combo must be free again after a remap"
        finally:
            probe.stop()
        assert hk.set_combo("not+a+combo") == "unsupported combo"
        assert hk.set_combo("") == "disabled"
    finally:
        hk.stop()


def test_fullscreen_guard_drops_the_press_when_skip_when_is_true():
    got = []
    block = [True]
    hk = hotkey_mod.Hotkey(FREE_COMBO, lambda: got.append(1), skip_when=lambda: block[0])
    try:
        hk.start()
        post_hotkey(hk)
        assert wait_until(lambda: hk.presses == 1, 2.0)
        time.sleep(0.2)
        assert got == [], "AC-SH-8: ignored while a full-screen app is in front"
        block[0] = False
        post_hotkey(hk)
        assert wait_until(lambda: got, 2.0)
    finally:
        hk.stop()


def test_hotkey_to_visible_box_is_within_300_ms(root, popup):
    """Hotkey thread -> uibus -> popup.toggle, timed from the hotkey thread's own press timestamp."""
    holder = {}
    hk = hotkey_mod.Hotkey(FREE_COMBO, lambda: uibus.post(popup.toggle, holder["hk"].last_press))
    holder["hk"] = hk
    try:
        assert hk.start() == "registered"
        post_hotkey(hk)
        assert wait_until(lambda: (pump(root, 0.05) or popup.debug_state()["open"]), 3.0)
        pump(root, 0.3)
        ms = popup.debug_state()["press_to_box_ms"]
        print(f"\n[popup] hotkey press -> box visible {ms} ms (posted WM_HOTKEY; target <= 300)")
        assert ms is not None and ms <= 300
    finally:
        hk.stop()


def test_running_app_debug_route_exposes_the_search_popup(app_factory):
    import json as _json
    with app_factory(test_mode=True) as (base_url, token):
        from conftest import Api
        status, body = Api(base_url, token).get("/v1/_debug/ui")
        if status != 200 or "search" not in (body or {}):
            pytest.skip("GET /v1/_debug/ui has no 'search' entry yet: popup not wired into the app (M3a-7)")
        assert "open" in body["search"], _json.dumps(body["search"])[:200]
