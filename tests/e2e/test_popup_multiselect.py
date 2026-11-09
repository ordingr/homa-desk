"""Search popup: multi-select, Copy / Cut of files, Copy path, and double-click to the full page (plan 2026-11-09
popup-to-fullpage-and-file-clipboard, section 4.1 behaviours 1-6; AC-H1, AC-H3, AC-M1, AC-C1, AC-C2).

Layer: IN-PROCESS. A real Tk root and a real SearchPopup over the fixture search tree (real FilesMode and index) and the
notes vault. Mouse input is synthesized on the real canvas with ``event_generate`` (ButtonPress/ButtonRelease pairs with
the Ctrl / Shift state bits; a double-click is two quick pairs), keys on the real entry. ``on_fullpage`` is a recorder
(the real one starts Edge). ``on_clipboard`` is the REAL ``shell.clipboard_files`` bound to an index of the fixture tree,
and the clipboard is read back with plain ctypes; when this session cannot open the clipboard those read-backs skip
and say so. The clipboard text present before each read-back test is put back afterwards (best effort).

Needs a desktop session for Tk.
"""
import os
import shutil
import time
import tkinter as tk

import pytest

from conftest import NOTES_FIXTURE, walk_store
from tests.fixtures import clipboard as cb

search_ui = pytest.importorskip("homa.overlay.search_ui", reason="homa/overlay/search_ui.py not present")
modes_mod = pytest.importorskip("homa.search.modes", reason="homa/search/modes.py not present")
notes_bm25 = pytest.importorskip("homa.search.notes_bm25", reason="homa/search/notes_bm25.py not present")

from homa import uibus  # noqa: E402
from homa.actions import claude, shell  # noqa: E402
from homa.search.saved import SavedSearches  # noqa: E402

KNOWN = {"md", "py", "html", "txt", "yml", "pdf", "docx", "xlsx", "csv", "png", "json", "js", "ts"}
CTRL, SHIFT = 0x4, 0x1


class IconStub:
    size = 60

    def position(self):
        return (900, 400)


class Settings:
    def __init__(self, **data):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)


class FakeIndex:
    def __init__(self, store, root):
        self._store, self._root = store, str(root)

    def current(self):
        return self._store, None

    def status(self):
        return {"roots": [{"path": self._root}]}


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


@pytest.fixture(scope="module")
def store_and_index(search_tree):
    store = walk_store(search_tree)
    return store, FakeIndex(store, search_tree)


@pytest.fixture
def popup(root, store_and_index, search_tree, tmp_path, monkeypatch):
    store, index = store_and_index
    vault = tmp_path / "vault"
    shutil.copytree(NOTES_FIXTURE, vault)
    monkeypatch.delenv("HOMA_NOTES_DIR", raising=False)
    notes = notes_bm25.AskNotesMode(settings=Settings(notes={"dir": str(vault)}), index_path=tmp_path / "notes.json.gz")
    notes.index().wait_ready(30)
    registry = modes_mod.default_registry(lambda: (store, None, KNOWN), notes)
    calls = {"fullpage": [], "actions": [], "clipboard": []}

    def on_clipboard(paths, op):
        calls["clipboard"].append((list(paths), op))
        return shell.clipboard_files(paths, op, index=index)

    pop = search_ui.SearchPopup(root, IconStub(), registry, Settings(), saved=SavedSearches(tmp_path / "saved.json"),
                                on_action=lambda verb, row, **kw: calls["actions"].append((verb, row.name)) or
                                {"ok": True, "message": f"{verb} ok"},
                                on_fullpage=lambda q, h: calls["fullpage"].append((q, h)),
                                on_clipboard=on_clipboard)
    pop.calls = calls
    yield pop
    pop.close()
    pop.win.destroy()
    notes.close()


@pytest.fixture
def clip():
    reason = cb.probe()
    if reason:
        pytest.skip(f"clipboard read-back skipped: this session cannot open the clipboard ({reason})")
    saved = cb.save_text()
    yield cb
    try:
        cb.set_text(saved)
    except cb.ClipboardDenied:
        pass


def search(root, popup, text, minimum=5):
    popup.open()
    pump(root, 0.3)
    popup.var.set(text)
    pump(root, 0.05)
    popup.submit()
    settle(root, popup)
    pump(root, 0.1)
    st = popup.debug_state()
    assert st["state"] == "results" and st["result_count"] >= minimum, st
    return st


def row_xy(popup, index):
    box = popup.canvas.bbox(f"hit:row:{index}")
    assert box, f"row {index} is not drawn"
    return (box[0] + box[2]) // 2 + 20, (box[1] + box[3]) // 2


def click(root, popup, index, state=0):
    x, y = row_xy(popup, index)
    popup.canvas.event_generate("<ButtonPress-1>", x=x, y=y, state=state)
    popup.canvas.event_generate("<ButtonRelease-1>", x=x, y=y, state=state | 0x100)
    pump(root, 0.05)


def double_click(root, popup, index, state=0):
    x, y = row_xy(popup, index)
    for _ in range(2):
        popup.canvas.event_generate("<ButtonPress-1>", x=x, y=y, state=state)
        popup.canvas.event_generate("<ButtonRelease-1>", x=x, y=y, state=state | 0x100)
    pump(root, 0.1)


def key(root, popup, sequence):
    popup.entry.focus_force()
    pump(root, 0.05)
    popup.entry.event_generate(sequence)
    pump(root, 0.1)


def marked(popup):
    return popup.debug_state()["marked"]


def same(a, b) -> bool:
    return [os.path.normcase(x) for x in a] == [os.path.normcase(x) for x in b]


# ---- AC-M1: marking rows -------------------------------------------------------------------------------------------------

def test_plain_click_moves_the_cursor_and_marks_nothing(root, popup):
    search(root, popup, "design")
    click(root, popup, 2)
    st = popup.debug_state()
    assert st["selected"] == 2 and st["marked"] == [] and st["marked_count"] == 0


def test_ctrl_click_toggles_rows_and_shift_click_marks_the_range_from_the_anchor(root, popup):
    search(root, popup, "design")
    click(root, popup, 0, CTRL)
    click(root, popup, 2, CTRL)
    assert marked(popup) == [0, 2] and popup.debug_state()["selected"] == 2
    click(root, popup, 0, CTRL)
    assert marked(popup) == [2], "a second Ctrl+click unmarks"
    click(root, popup, 2, CTRL)
    click(root, popup, 2, CTRL)
    assert marked(popup) == [2], "unmark and mark again: row 2 is now the anchor"
    click(root, popup, 4, SHIFT)
    st = popup.debug_state()
    assert st["marked"] == [2, 3, 4] and st["selected"] == 4 and st["marked_count"] == 3
    click(root, popup, 0, SHIFT)
    assert marked(popup) == [0, 1, 2], "Shift+click keeps the anchor (Explorer rule)"
    click(root, popup, 1)
    assert marked(popup) == [], "a plain click clears the marks"


def test_marked_rows_are_drawn_with_their_own_fill(root, popup):
    search(root, popup, "design")
    click(root, popup, 1, CTRL)
    click(root, popup, 3, CTRL)
    tagged = {t for item in popup.canvas.find_withtag("marked") for t in popup.canvas.gettags(item)
              if t.startswith("hit:row:")}
    assert tagged == {"hit:row:1", "hit:row:3"}


def test_shift_down_and_up_extend_from_the_anchor(root, popup):
    search(root, popup, "design")
    click(root, popup, 1)
    key(root, popup, "<Shift-Down>")
    key(root, popup, "<Shift-Down>")
    st = popup.debug_state()
    assert st["marked"] == [1, 2, 3] and st["selected"] == 3, st
    key(root, popup, "<Shift-Up>")
    assert marked(popup) == [1, 2]


def test_ctrl_a_marks_every_row_and_a_second_ctrl_a_selects_the_query_text(root, popup):
    st = search(root, popup, "design")
    key(root, popup, "<Control-a>")
    assert marked(popup) == list(range(st["result_count"]))
    assert not popup.entry.selection_present()
    key(root, popup, "<Control-a>")
    assert marked(popup) == list(range(st["result_count"])), "marks stay"
    assert popup.entry.selection_present(), "the second Ctrl+A falls through to the entry (select all text)"


def test_ctrl_a_with_text_selected_in_the_entry_is_text_select_all(root, popup):
    search(root, popup, "design")
    popup.entry.focus_force()
    pump(root, 0.05)
    popup.entry.selection_range(0, 2)
    popup.entry.event_generate("<Control-a>")
    pump(root, 0.1)
    assert marked(popup) == []
    assert popup.entry.selection_present()


def test_a_new_search_clears_the_marks(root, popup):
    search(root, popup, "design")
    click(root, popup, 0, CTRL)
    click(root, popup, 1, CTRL)
    assert marked(popup) == [0, 1]
    popup.var.set("design md")
    pump(root, 0.05)
    popup.submit()
    settle(root, popup)
    assert marked(popup) == []


def test_entering_and_leaving_a_folder_listing_clears_the_marks(root, popup):
    search(root, popup, "design")
    click(root, popup, 0, CTRL)
    folder = popup.rows[0].folder
    popup._enter_listing({"ok": True, "folder": folder, "rows": [
        {"path": os.path.join(folder, "one.txt"), "name": "one.txt"},
        {"path": os.path.join(folder, "two.txt"), "name": "two.txt"}]})
    pump(root, 0.1)
    assert marked(popup) == []
    popup._leave_listing()
    pump(root, 0.1)
    assert marked(popup) == []


# ---- AC-H1: double-click -> full page --------------------------------------------------------------------------------------

def test_double_click_on_a_file_row_hands_the_search_to_the_full_page(root, popup):
    search(root, popup, "design")
    popup.toggle_chip("docs")
    settle(root, popup)
    popup.sort = "name"
    popup.modified = "30d"
    popup.var.set("design")
    popup.submit()
    settle(root, popup)
    pump(root, 0.1)
    rows = popup.rows
    assert len(rows) >= 2
    double_click(root, popup, 1)
    assert len(popup.calls["fullpage"]) == 1, popup.calls
    query, handoff = popup.calls["fullpage"][0]
    assert query == "design"
    assert handoff == {"query": "design", "chips": ["docs"], "modified": "30d", "sort": "name",
                       "select_path": rows[1].path}, handoff
    assert popup.debug_state()["selected"] == 1


def test_the_handoff_passes_check_handoff(root, popup):
    from homa.server import auth
    search(root, popup, "design")
    double_click(root, popup, 0)
    _, handoff = popup.calls["fullpage"][0]
    assert auth.check_handoff(handoff) == handoff


def test_a_single_click_does_not_open_the_full_page(root, popup):
    search(root, popup, "design")
    click(root, popup, 1)
    pump(root, 0.6)
    click(root, popup, 2)
    assert popup.calls["fullpage"] == []


def test_ctrl_double_click_only_toggles_marks(root, popup):
    search(root, popup, "design")
    double_click(root, popup, 1, CTRL)
    assert popup.calls["fullpage"] == []


def test_double_click_in_a_folder_listing_keeps_select_only(root, popup):
    search(root, popup, "design")
    folder = popup.rows[0].folder
    popup._enter_listing({"ok": True, "folder": folder, "rows": [
        {"path": os.path.join(folder, "one.txt"), "name": "one.txt"},
        {"path": os.path.join(folder, "two.txt"), "name": "two.txt"}]})
    pump(root, 0.2)
    double_click(root, popup, 1)
    assert popup.calls["fullpage"] == [] and popup.debug_state()["selected"] == 1


def test_double_click_on_ask_notes_rows_keeps_select_only(root, popup):
    popup.open()
    pump(root, 0.3)
    popup.cycle_mode()
    popup.var.set("zebrafish migration")
    pump(root, 0.05)
    popup.submit()
    settle(root, popup)
    pump(root, 0.1)
    assert popup.debug_state()["row_kind"] == "note"
    double_click(root, popup, 0)
    assert popup.calls["fullpage"] == []


def test_f11_sends_the_cursor_row_too(root, popup):
    search(root, popup, "design")
    popup.select(2)
    popup.fullpage()
    query, handoff = popup.calls["fullpage"][0]
    assert query == "design" and handoff["select_path"] == popup.rows[2].path


def test_f11_with_edited_text_sends_no_row(root, popup):
    search(root, popup, "design")
    popup.var.set("designer")
    pump(root, 0.05)
    popup.fullpage()
    query, handoff = popup.calls["fullpage"][-1]
    assert query == "designer" and handoff["select_path"] is None


def test_enter_still_opens_the_selected_file(root, popup):
    search(root, popup, "design md", minimum=2)
    popup.submit()
    assert ("open", "designer.md") in popup.calls["actions"]
    assert popup.calls["fullpage"] == []


# ---- AC-C1 / AC-C2: Copy, Cut, Copy path --------------------------------------------------------------------------------

def test_ctrl_c_puts_the_marked_files_on_the_clipboard_with_drop_effect_1(root, popup, clip):
    search(root, popup, "design")
    click(root, popup, 0, CTRL)
    click(root, popup, 2, CTRL)
    want = [popup.rows[0].path, popup.rows[2].path]
    key(root, popup, "<Control-c>")
    assert popup.calls["clipboard"] == [(want, "copy")]
    st = popup.debug_state()
    assert st["details"] == "Copied 2 files — paste in File Explorer", st["details"]
    got = clip.read()
    assert same(got["files"], want) and got["effect"] == 1, got


def test_ctrl_x_cuts_the_cursor_row_when_nothing_is_marked(root, popup, clip):
    search(root, popup, "design")
    click(root, popup, 3)
    want = [popup.rows[3].path]
    key(root, popup, "<Control-x>")
    assert popup.calls["clipboard"] == [(want, "cut")]
    assert popup.debug_state()["details"] == "Cut 1 file — paste in File Explorer to move them"
    got = clip.read()
    assert same(got["files"], want) and got["effect"] == 2
    assert os.path.exists(want[0]), "Cut never moves the file itself"


def test_ctrl_a_then_ctrl_c_copies_every_row(root, popup, clip):
    st = search(root, popup, "design")
    key(root, popup, "<Control-a>")
    key(root, popup, "<Control-c>")
    assert popup.debug_state()["details"].startswith(f"Copied {st['result_count']} files")
    assert len(clip.read()["files"]) == st["result_count"]


def test_ctrl_c_with_text_selected_in_the_entry_edits_text_not_files(root, popup):
    search(root, popup, "design")
    popup.entry.selection_range(0, "end")
    key(root, popup, "<Control-c>")
    assert popup.calls["clipboard"] == []


def test_ctrl_shift_c_copies_the_marked_paths_as_text(root, popup, clip):
    search(root, popup, "design")
    click(root, popup, 1, CTRL)
    click(root, popup, 3, CTRL)
    want = [popup.rows[1].path, popup.rows[3].path]
    key(root, popup, "<Control-C>")
    assert popup.calls["clipboard"] == [], "Copy path never puts files on the clipboard"
    got = clip.read()
    assert got["files"] == [] and got["text"].replace("\r", "") == "\n".join(want), got


def test_a_refused_file_is_reported_in_the_details_line(root, popup, search_tree):
    search(root, popup, "design")
    row = popup.rows[0]
    import dataclasses
    secret = dataclasses.replace(row, name=".env", folder=str(search_tree))
    popup.rows[0] = secret
    popup.select(0)
    key(root, popup, "<Control-c>")
    st = popup.debug_state()
    assert popup.calls["clipboard"] and st["details"], st
    assert "refused" in st["details"].lower() or "none of the paths" in st["details"].lower(), st["details"]


# ---- menu ----------------------------------------------------------------------------------------------------------------

def menu_items(popup, monkeypatch, row_index):
    monkeypatch.setattr(tk.Menu, "tk_popup", lambda self, x, y, entry="": None)
    popup.show_context_menu(popup.rows[row_index])
    return popup._menu_items


def test_menu_has_copy_and_cut_on_top_with_the_chords(root, popup, monkeypatch):
    search(root, popup, "design")
    items = menu_items(popup, monkeypatch, 0)
    assert [(i["verb"], i["key"], i["enabled"]) for i in items[:2]] == [("copy_files", "Ctrl+C", True),
                                                                        ("cut_files", "Ctrl+X", True)]
    keys = {i["verb"]: i["key"] for i in items}
    assert keys["copy_path"] == "Ctrl+Shift+C" and keys["open"] == "Enter"


def test_menu_labels_count_the_marked_files(root, popup, monkeypatch):
    search(root, popup, "design")
    for i in (0, 1, 2):
        click(root, popup, i, CTRL)
    items = menu_items(popup, monkeypatch, 1)
    labels = [i["label"] for i in items[:2]]
    assert labels[0].endswith("Copy 3 files") and labels[1].endswith("Cut 3 files"), labels


def test_menu_copy_and_cut_are_disabled_on_note_rows(root, popup, monkeypatch):
    popup.open()
    pump(root, 0.3)
    popup.cycle_mode()
    popup.var.set("zebrafish migration")
    pump(root, 0.05)
    popup.submit()
    settle(root, popup)
    items = {i["verb"]: i for i in menu_items(popup, monkeypatch, 0)}
    assert items["copy_files"]["enabled"] is False and items["cut_files"]["enabled"] is False
    assert "not for notes" in items["copy_files"]["reason"]


def test_menu_copy_cut_are_disabled_without_a_clipboard_callback(root, popup, monkeypatch):
    search(root, popup, "design")
    popup.on_clipboard = None
    items = {i["verb"]: i for i in menu_items(popup, monkeypatch, 0)}
    assert items["copy_files"]["enabled"] is False and items["cut_files"]["enabled"] is False
