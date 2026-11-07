"""Search popup: live search, ghost Tab completion, the purple pill, theme re-render, the chord guard.

Layer: IN-PROCESS. A real Tk root and a real SearchPopup over the fixture tree (real FilesMode and index); the
popup is driven through its public methods and ``debug_state()``. Needs a desktop session for Tk. ``claude.status`` is
stubbed so opening the popup never runs a ``claude`` binary.
"""
import time
import tkinter as tk

import pytest

from conftest import walk_store

search_ui = pytest.importorskip("assistant.overlay.search_ui", reason="assistant/overlay/search_ui.py not present")
modes_mod = pytest.importorskip("assistant.search.modes", reason="assistant/search/modes.py not present")

from assistant import uibus  # noqa: E402
from assistant.actions import claude  # noqa: E402
from assistant.overlay import gfx, theme  # noqa: E402
from assistant.search.saved import SavedSearches  # noqa: E402

KNOWN = {"md", "py", "html", "txt", "yml", "pdf", "docx", "xlsx", "csv", "png", "json", "js", "ts"}


class IconStub:
    size = 60

    def position(self):
        return (900, 400)


class Settings:
    def __init__(self, **data):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)


class OnlineMode:
    keyword = "online"
    label = "Online"
    local = False
    noun = "answer(s)"
    footer = ""

    def __init__(self):
        self.calls = []

    def run(self, text, chips=(), modified="", sort=""):
        self.calls.append(text)
        return modes_mod.SearchResult(notice="stub answer", footer="")


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


@pytest.fixture(autouse=True)
def no_claude_binary(monkeypatch):
    stub = {"available": True, "version": "stub", "reason": None}
    monkeypatch.setattr(claude, "status", lambda refresh=False: stub)
    monkeypatch.setattr(claude, "cached_status", lambda: stub)
    monkeypatch.setattr(claude, "account", lambda: None)


@pytest.fixture(autouse=True)
def dark_theme_after():
    theme.set_mode("dark")
    yield
    theme.set_mode("dark")


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
        if not popup.pending and popup._live_job is None:
            return
    raise AssertionError("search did not finish: " + str(popup.debug_state()))


@pytest.fixture
def make(root, search_tree, tmp_path):
    store = walk_store(search_tree)
    made = []

    def factory(*, online=None, saved=True, **settings):
        registry = modes_mod.default_registry(lambda: (store, None, KNOWN), None)
        if online is not None:
            registry.register(online)
        pop = search_ui.SearchPopup(root, IconStub(), registry, Settings(**settings),
                                    saved=SavedSearches(tmp_path / f"saved{len(made)}.json"),
                                    on_action=lambda verb, row: pop.actions.append((verb, row.name)))
        pop.actions = []
        pop.calls = []
        real = pop._call_run_search

        def counting(mode, query, chips, modified, sort=""):
            pop.calls.append((mode.keyword, query))
            return real(mode, query, chips, modified, sort)
        pop._call_run_search = counting
        made.append(pop)
        return pop

    yield factory
    for pop in made:
        pop.close()
        pop.win.destroy()


def open_popup(root, pop):
    pop.open()
    pump(root, 0.3)
    pop.entry.focus_force()
    pump(root, 0.05)


def type_keys(root, pop, text, gap=0.02):
    for ch in text:
        pop.var.set(pop.var.get() + ch)
        pump(root, gap)


# ---- pure completion functions -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("prefix, name, expected", [
    ("des", "designer.md", "igner"),
    ("design", "designer.md", "er"),
    ("designer", "designer.md", ".md"),
    ("DES", "designer.md", "igner"),
    ("sys", "design-system.html", "tem"),
    ("report", "my report final.txt", ""),
    ("final", "my report final.txt", ".txt"),
    ("x", "x", ""),
    ("", "designer.md", ""),
    ("zzz", "designer.md", ""),
    ("ign", "designer.md", ""),
])
def test_completion_continues_a_word_prefix_up_to_the_next_separator(prefix, name, expected):
    assert search_ui.completion(prefix, name) == expected


def test_completion_never_returns_a_continuation_for_names_that_change_length_when_folded():
    assert search_ui.completion("stra", "STRASSEß") == "", "casefold changes the length, so no ghost"
    assert search_ui.completion("é", "école") == "cole"


@pytest.mark.parametrize("text, expected", [
    ("des", "igner"),
    ("design ", ""),
    ("", ""),
    ('"des', ""),
    ("!des", ""),
    ("ext:de", ""),
    ("des*", ""),
    ("note des", "igner"),
    ("zzz", ""),
])
def test_suggestion_completes_the_last_word_only_when_it_is_plain(text, expected):
    assert search_ui.suggestion(text, ["designer.md", "design-system.html"]) == expected


def test_suggestion_takes_the_first_name_that_continues_the_prefix():
    assert search_ui.suggestion("sk", ["alpha", "sketch.txt", "skate.txt"]) == "etch"


# ---- live search ------------------------------------------------------------------------------------------------------------------

def test_local_mode_searches_by_itself_after_the_pause(root, make):
    pop = make()
    open_popup(root, pop)
    pop.var.set("design md")
    pump(root, 0.05)
    assert pop.debug_state()["state"] == "input", "nothing runs before the 120 ms pause"
    settle(root, pop)
    st = pop.debug_state()
    assert st["state"] == "results" and st["result_count"] == 2 and st["selected_name"] == "designer.md"
    assert pop.calls == [("files", "design md")]
    assert not pop.actions, "live search never opens anything"


def test_typing_fast_runs_one_search_not_one_per_key(root, make):
    pop = make()
    open_popup(root, pop)
    type_keys(root, pop, "design", gap=0.015)
    settle(root, pop)
    assert pop.calls == [("files", "design")], pop.calls
    assert pop.debug_state()["result_count"] == 7


def test_a_pause_in_typing_searches_the_partial_text_then_the_final_text(root, make):
    pop = make()
    open_popup(root, pop)
    type_keys(root, pop, "desi", gap=0.01)
    pump(root, 0.5)
    type_keys(root, pop, "gn", gap=0.01)
    settle(root, pop)
    assert pop.calls[0] == ("files", "desi") and pop.calls[-1] == ("files", "design")
    st = pop.debug_state()
    assert st["result_count"] == 7 and st["text"] == "design"


def test_online_modes_do_not_search_until_enter(root, make):
    online = OnlineMode()
    pop = make(online=online)
    open_popup(root, pop)
    pop.cycle_mode()
    pop.cycle_mode()
    assert pop.debug_state()["badge"] == "Online · online"
    pop.var.set("what is the plan")
    pump(root, 0.6)
    assert online.calls == [] and pop.debug_state()["state"] == "input", "typing alone must not send a query online"
    pop.submit()
    settle(root, pop)
    assert online.calls == ["what is the plan"] and pop.debug_state()["state"] == "results"


def test_clearing_the_box_returns_to_input_with_the_saved_searches(root, make):
    pop = make()
    open_popup(root, pop)
    pop.var.set("design md")
    settle(root, pop)
    assert pop.save_current("my design") and pop.debug_state()["state"] == "results"
    pop.var.set("")
    pump(root, 0.3)
    st = pop.debug_state()
    assert st["state"] == "input" and st["result_count"] == 0 and st["saved_visible"] and "my design" in st["saved"]
    assert pop._live_job is None


def test_clearing_before_the_pause_ends_cancels_the_pending_search(root, make):
    pop = make()
    open_popup(root, pop)
    pop.var.set("design")
    pump(root, 0.03)
    pop.var.set("")
    pump(root, 0.5)
    assert pop.calls == [] and pop.debug_state()["state"] == "input"


def test_enter_on_fresh_text_searches_and_enter_with_results_opens(root, make):
    pop = make()
    open_popup(root, pop)
    pop.var.set("design md")
    pump(root, 0.02)
    pop.submit()
    settle(root, pop)
    assert pop.debug_state()["state"] == "results" and not pop.actions, "first Enter searches"
    assert pop.calls == [("files", "design md")], "the pending live search was cancelled by Enter"
    pop.submit()
    assert pop.actions == [("open", "designer.md")], "second Enter opens the selected row"


def test_enter_after_editing_results_text_searches_again_instead_of_opening(root, make):
    pop = make()
    open_popup(root, pop)
    pop.var.set("design md")
    settle(root, pop)
    pop.var.set("config")
    pump(root, 0.02)
    pop.submit()
    settle(root, pop)
    assert not pop.actions
    assert pop.calls[-1] == ("files", "config") and pop.debug_state()["selected_name"] == "config.yml"


def test_live_search_keeps_the_rows_while_the_next_one_runs(root, make):
    pop = make()
    open_popup(root, pop)
    pop.var.set("design")
    settle(root, pop)
    first = pop.debug_state()["result_count"]
    pop.var.set("design md")
    pump(root, 0.02)
    assert pop.debug_state()["result_count"] == first, "rows stay up until the new result arrives"
    settle(root, pop)
    assert pop.debug_state()["result_count"] == 2


# ---- the purple pill ---------------------------------------------------------------------------------------------------------------

def test_the_pill_is_dark_purple_in_dark_theme_and_light_lilac_in_light_theme(root, make):
    pop = make()
    open_popup(root, pop)
    assert theme.current() == "dark"
    assert search_ui.field_colours()["bg"].upper() == "#6A3DB8"
    assert pop._pill_key[2].upper() == "#6A3DB8" and str(pop.entry.cget("bg")).upper() == "#6A3DB8"
    assert pop.canvas.find_withtag("pill"), "the stadium image is on the canvas"
    theme.set_mode("light")
    pump(root, 0.2)
    assert search_ui.field_colours()["bg"].upper() == "#E3D7F6"
    assert pop._pill_key[2].upper() == "#E3D7F6" and str(pop.entry.cget("bg")).upper() == "#E3D7F6"
    assert str(pop.entry.cget("fg")).upper() == "#2B1557"


def test_the_pill_image_is_a_stadium_with_the_fill_in_the_middle_and_the_panel_in_the_corners(root):
    img = search_ui.pill_image(root, 120, 36, "#6A3DB8", "#22252B")
    assert img.width() == 120 and img.height() == 36
    assert img.get(60, 18) == (0x6A, 0x3D, 0xB8)
    assert img.get(0, 0) == (0x22, 0x25, 0x2B) and img.get(119, 35) == (0x22, 0x25, 0x2B)
    left_mid, right_mid = img.get(1, 18), img.get(118, 18)
    assert left_mid != (0x22, 0x25, 0x2B) and left_mid == right_mid, "caps are mirrored and filled at mid height"


def test_the_placeholder_is_shown_only_while_the_box_is_empty(root, make):
    pop = make()
    open_popup(root, pop)

    def placeholders():
        return [pop.canvas.itemcget(i, "text") for i in pop.canvas.find_all()
                if pop.canvas.type(i) == "text" and "Type to search" in pop.canvas.itemcget(i, "text")]
    assert placeholders() == ["Type to search…"]
    item = pop.canvas.find_withtag("pill")
    assert item
    pop.var.set("x")
    pump(root, 0.05)
    assert placeholders() == []
    pop.var.set("")
    pump(root, 0.3)
    assert placeholders() == ["Type to search…"]
    dim = [pop.canvas.itemcget(i, "fill") for i in pop.canvas.find_all()
           if pop.canvas.type(i) == "text" and "Type to search" in pop.canvas.itemcget(i, "text")][0]
    assert dim.upper() == "#E4D8F7", "the placeholder uses the pill's own dim colour in dark"


# ---- ghost completion and Tab ---------------------------------------------------------------------------------------------------

def test_ghost_text_appears_after_a_search_and_tab_accepts_it_instead_of_cycling_the_mode(root, make):
    pop = make()
    open_popup(root, pop)
    pop.var.set("design md")
    settle(root, pop)
    pop.var.set("designe")
    settle(root, pop)
    pop.entry.icursor("end")
    pop._place_ghost()
    assert pop._ghost == "r", pop._ghost
    assert pop.canvas.find_withtag("ghost")
    mode_before = pop.debug_state()["mode"]
    assert pop._tab(None) == "break"
    assert pop.var.get() == "designer" and pop.debug_state()["mode"] == mode_before, "Tab accepted, mode unchanged"


def test_tab_without_a_ghost_cycles_the_mode_and_shift_tab_goes_back(root, make):
    pop = make()
    open_popup(root, pop)
    assert pop.suggestion_text() == ""
    assert pop.debug_state()["badge"] == "Files · local"
    assert pop._tab(None) == "break"
    assert pop.debug_state()["badge"] == "Ask notes · local"
    pop.entry.focus_force()
    pump(root, 0.05)
    pop.entry.event_generate("<Shift-Tab>")
    pump(root, 0.1)
    assert pop.debug_state()["badge"] == "Files · local"


def test_shift_tab_binding_is_unchanged(root, make):
    pop = make()
    assert pop.entry.bind("<Shift-Tab>") and pop.entry.bind("<Tab>")
    start = pop.debug_state()["badge"]
    pop.cycle_mode(1)
    pop.cycle_mode(-1)
    assert pop.debug_state()["badge"] == start


def test_ghost_uses_saved_search_names_and_queries_before_any_search(root, make):
    pop = make()
    open_popup(root, pop)
    pop.var.set("design md")
    settle(root, pop)
    assert pop.save_current("quarterly review")
    pop.var.set("")
    pump(root, 0.3)
    pop.var.set("quart")
    pop.entry.icursor("end")
    pop._place_ghost()
    assert pop._ghost == "erly"
    assert pop.accept_suggestion() and pop.var.get() == "quarterly"


def test_no_ghost_when_the_cursor_is_not_at_the_end_or_text_is_selected(root, make):
    pop = make()
    open_popup(root, pop)
    pop.var.set("design md")
    settle(root, pop)
    pop.var.set("designe")
    settle(root, pop)
    pop.entry.icursor(3)
    assert pop.suggestion_text() == ""
    pop.entry.icursor("end")
    pop.entry.selection_range(0, "end")
    assert pop.suggestion_text() == ""


# ---- theme re-render and the chord guard -----------------------------------------------------------------------------------

def canvas_fills(pop):
    return {str(pop.canvas.itemcget(i, "fill")).upper() for i in pop.canvas.find_all()
            if pop.canvas.type(i) in ("polygon", "rectangle", "oval", "text")}


def test_switching_theme_repaints_an_open_popup_without_closing_it(root, make):
    pop = make()
    open_popup(root, pop)
    pop.var.set("design md")
    settle(root, pop)
    dark_bg = theme.PALETTES["dark"]["PANEL_BG"].upper()
    assert dark_bg in canvas_fills(pop)
    theme.set_mode("light")
    pump(root, 0.2)
    st = pop.debug_state()
    assert st["open"] and st["state"] == "results" and st["result_count"] == 2
    fills = canvas_fills(pop)
    assert theme.PALETTES["light"]["PANEL_BG"].upper() in fills and dark_bg not in fills


def test_theme_change_after_the_popup_window_is_destroyed_is_harmless(root, make, caplog):
    pop = make()
    open_popup(root, pop)
    pop.win.destroy()
    theme.set_mode("light")
    pump(root, 0.1)
    assert not [r for r in caplog.records if "theme listener failed" in r.getMessage()]
    pop.state = "closed"


def test_an_unusable_custom_chord_falls_back_to_the_default_instead_of_raising(root, make, monkeypatch):
    real = search_ui.chord_sequences

    def fake(chord):
        return ["<Control-NoSuchKeysymQ>"] if chord == "Ctrl+Q" else real(chord)
    monkeypatch.setattr(search_ui, "chord_sequences", fake)
    pop = make(shortcuts={"rename": "Ctrl+Q"})
    assert "<F2>" in pop._bound_seqs, "rename fell back to its default chord"
    assert "<Control-NoSuchKeysymQ>" not in pop._bound_seqs
    assert pop.entry.bind("<F2>")


def test_chord_with_a_non_ascii_letter_is_not_a_shortcut():
    assert search_ui.chord_sequences("Ctrl+ı") == []
    assert search_ui.chord_sequences("Ctrl+é") == []
    assert search_ui.chord_sequences("Ctrl+i") == ["<Control-i>"]
