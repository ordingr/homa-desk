"""Search popup shortcut binding, menu accelerator labels, the "Send whole file…" confirmation, and the Markdown tag
colours (plan 7.5 M4-3 / M4-3b; AC-AK-2, AC-AK-3, AC-PS-1).

Layer: IN-PROCESS. A real Tk root and a real SearchPopup; the dialogs are the popup's own modal Toplevels, answered by a
scheduled click on the real button. ``claude.status`` is stubbed so opening the popup never runs a ``claude`` binary
(the popup's open() starts a status probe thread), and ``on_action`` is a recorder: nothing here can reach Claude.

Needs a desktop session for Tk.
"""
import time
import tkinter as tk

import pytest

from conftest import wait_until

search_ui = pytest.importorskip("assistant.overlay.search_ui", reason="assistant/overlay/search_ui.py not present")
modes_mod = pytest.importorskip("assistant.search.modes", reason="assistant/search/modes.py not present")

from assistant import uibus  # noqa: E402
from assistant.actions import claude  # noqa: E402
from assistant.overlay import ask_claude_ui, gfx, mdrender_tk, theme  # noqa: E402
from assistant.search.modes import Row  # noqa: E402
from assistant.search.saved import SavedSearches  # noqa: E402
from assistant.settings import DEFAULT_SHORTCUTS  # noqa: E402


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


@pytest.fixture(autouse=True)
def no_claude_binary(monkeypatch):
    stub = {"available": True, "version": "stub", "reason": None}
    monkeypatch.setattr(claude, "status", lambda refresh=False: stub)
    monkeypatch.setattr(claude, "cached_status", lambda: stub)
    monkeypatch.setattr(claude, "account", lambda: None)


def make_popup(root, tmp_path, **settings):
    registry = modes_mod.default_registry(lambda: None, None)
    cfg = Settings(**settings)
    pop = search_ui.SearchPopup(root, IconStub(), registry, cfg, saved=SavedSearches(tmp_path / "saved.json"))
    pop.cfg = cfg
    return pop


@pytest.fixture
def popups(root, tmp_path):
    made = []

    def factory(**settings):
        pop = make_popup(root, tmp_path, **settings)
        made.append(pop)
        return pop

    yield factory
    for pop in made:
        try:
            if pop.answer_window is not None:
                pop.answer_window.win.destroy()
        except tk.TclError:
            pass
        pop.close()
        pop.win.destroy()


def bound(pop, sequence):
    return bool(pop.entry.bind(sequence))


# ---- chord_sequences ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("chord, expected", [
    ("Ctrl+Shift+S", ["<Control-S>"]),
    ("ctrl+shift+s", ["<Control-S>"]),
    ("Alt+Enter", ["<Alt-Return>", "<Alt-KP_Enter>"]),
    ("F3", ["<F3>"]),
    ("f12", ["<F12>"]),
    ("Ctrl+Enter", ["<Control-Return>", "<Control-KP_Enter>"]),
    ("ctrl+shift+enter", ["<Control-Shift-Return>", "<Control-Shift-KP_Enter>"]),
    ("Ctrl+C", ["<Control-c>"]),
    ("Ctrl+Alt+Q", ["<Control-Alt-q>"]),
    ("Shift+F10", ["<Shift-F10>"]),
    ("Delete", ["<Delete>"]),
    ("Escape", ["<Escape>"]),
    ("Tab", ["<Tab>"]),
    ("Ctrl+5", ["<Control-5>"]),
])
def test_chord_sequences_maps_settings_chords_to_tk_sequences(chord, expected):
    assert search_ui.chord_sequences(chord) == expected


@pytest.mark.parametrize("chord", ["", "   ", "Ctrl+", "+", "Hyper+X", "Win+E", "esc", "F25", "F0", "Ctrl+Plus", "Ctrl+Shift", None,
                                   "Ctrl+Ctrl+"])
def test_chord_sequences_returns_nothing_for_unusable_chords(chord):
    assert search_ui.chord_sequences(chord) == []


# ---- _apply_shortcuts ----------------------------------------------------------------------------------------------------

def test_defaults_are_bound_on_open(root, popups):
    pop = popups()
    pop.open()
    pump(root, 0.1)
    for action, chord in DEFAULT_SHORTCUTS.items():
        for seq in search_ui.chord_sequences(chord):
            assert bound(pop, seq), (action, chord, seq)
    for seq in ("<Up>", "<Down>", "<Prior>", "<Next>", "<Home>", "<End>", "<KP_Enter>", "<App>", "<Shift-Tab>"):
        assert bound(pop, seq), seq


def test_settings_overrides_replace_the_default_chord(root, popups):
    pop = popups(shortcuts={"rename": "f3", "properties": "alt+p", "reveal": "ctrl+shift+enter", "cycle_sort": "Ctrl+Y"})
    pop.open()
    pump(root, 0.1)
    assert bound(pop, "<F3>") and not bound(pop, "<F2>")
    assert bound(pop, "<Alt-p>") and not bound(pop, "<Alt-Return>")
    assert bound(pop, "<Control-Shift-Return>") and bound(pop, "<Control-Shift-KP_Enter>")
    assert not bound(pop, "<Control-Return>"), "the old reveal chord is released"
    assert bound(pop, "<Control-y>") and not bound(pop, "<Control-s>")
    assert pop.chord_label("rename") == "f3"
    assert pop.chord_label("open") == "Enter"
    assert pop.chord_label("nonsense", "fallback") == "fallback"


def test_invalid_entries_fall_back_to_the_defaults(root, popups):
    pop = popups(shortcuts={"rename": "Hyper+F2", "recycle": "Ctrl+", "md_vault": 5, "folder_listing": None,
                            "close": "esc", "bogus_action": "F9", "properties": ["Alt+P"], "reveal": "ctrl+shift+enter"})
    pop.open()
    pump(root, 0.1)
    assert bound(pop, "<F2>"), "unknown modifier -> default F2"
    assert bound(pop, "<Delete>"), "'Ctrl+' -> default Delete"
    assert bound(pop, "<Control-m>"), "non-string -> default Ctrl+M"
    assert bound(pop, "<Control-f>"), "None -> default Ctrl+F"
    assert bound(pop, "<Escape>"), "'esc' is not a key name -> default Escape"
    assert bound(pop, "<Alt-Return>"), "list -> default Alt+Enter"
    assert not bound(pop, "<F9>"), "an unknown action name is ignored"
    assert bound(pop, "<Control-Shift-Return>"), "the valid entry beside the bad ones still applies"
    assert pop.chord_label("rename") == "F2" and pop.chord_label("recycle") == "Delete"


@pytest.mark.parametrize("junk", [None, "text", ["F3"], 7])
def test_a_non_dict_shortcuts_setting_means_all_defaults(root, popups, junk):
    pop = popups(shortcuts=junk)
    pop.open()
    pump(root, 0.1)
    assert bound(pop, "<F2>") and bound(pop, "<Alt-Return>")


def test_a_settings_object_that_raises_means_defaults(root, popups):
    class Broken:
        def get(self, key, default=None):
            raise RuntimeError("settings unavailable")

    pop = popups()
    pop.settings = Broken()
    pop.open()
    pump(root, 0.1)
    assert bound(pop, "<F2>")


def test_changed_settings_rebind_on_reopen_and_unchanged_ones_do_not(root, popups):
    pop = popups()
    pop.open()
    pump(root, 0.1)
    assert bound(pop, "<F2>") and not bound(pop, "<F3>")
    first = pop._bound_seqs
    pop.close()
    pop.open()
    pump(root, 0.1)
    assert pop._bound_seqs is first, "same settings: nothing is rebound"
    pop.close()
    pop.cfg.data["shortcuts"] = {"rename": "F3"}
    pop.open()
    pump(root, 0.1)
    assert bound(pop, "<F3>") and not bound(pop, "<F2>"), "the old chord is unbound, the new one bound"
    pop.close()
    pop.cfg.data["shortcuts"] = {}
    pop.open()
    pump(root, 0.1)
    assert bound(pop, "<F2>") and not bound(pop, "<F3>"), "removing the override restores the default"


def test_fixed_keys_are_bound_only_when_no_action_claims_them(root, popups, monkeypatch):
    pop = popups()
    pop.open()
    pump(root, 0.1)
    assert pop._bound_seqs.count("<Up>") == 1 and pop._bound_seqs.count("<KP_Enter>") == 1
    assert "<Shift-Tab>" in pop._bound_seqs, "the reverse-cycle key goes with Tab as the cycle chord"

    moved, opened = [], []
    monkeypatch.setattr(search_ui.SearchPopup, "move", lambda self, step: moved.append(step))
    monkeypatch.setattr(search_ui.SearchPopup, "submit", lambda self, *a, **k: opened.append(1))
    claimed = popups(shortcuts={"open": "Up", "cycle_mode": "Ctrl+Y"})
    claimed.open()
    pump(root, 0.1)
    assert claimed._bound_seqs.count("<Up>") == 1, "bound once, by the action that claimed it"
    assert "<Shift-Tab>" not in claimed._bound_seqs, "Shift+Tab only accompanies Tab"
    claimed.entry.focus_force()
    pump(root, 0.1)
    claimed.entry.event_generate("<Up>")
    pump(root, 0.1)
    assert opened == [1] and moved == [], "Up runs the claiming action, not the fixed row mover"
    claimed.entry.event_generate("<Down>")
    pump(root, 0.1)
    assert moved == [1], "an unclaimed fixed key keeps its default job"


# ---- menu accelerator labels ----------------------------------------------------------------------------------------------

def menu_labels(pop, monkeypatch):
    monkeypatch.setattr(tk.Menu, "tk_popup", lambda self, x, y, entry="": None)
    row = Row(name="a.txt", folder=r"C:\x")
    pop.on_action = lambda verb, row, **kw: {"ok": True, "message": "ok"}
    pop.show_context_menu(row)
    return {item["verb"]: item["key"] for item in pop._menu_items}


def test_menu_accelerators_show_the_default_chords(root, popups, monkeypatch):
    pop = popups()
    pop.open()
    pump(root, 0.1)
    keys = menu_labels(pop, monkeypatch)
    assert keys["open"] == "Enter" and keys["reveal"] == "Ctrl+Enter" and keys["copy_path"] == "Ctrl+C"
    assert keys["rename"] == "F2" and keys["properties"] == "Alt+Enter"
    assert keys["md_vault"] == "Ctrl+M" and keys["recycle"] == "Delete"
    assert keys["open_with"] == "" and keys["ask_claude"] == "" and keys["copy_path_quoted"] == ""


def test_menu_accelerators_follow_the_settings_chords_and_ignore_invalid_ones(root, popups, monkeypatch):
    pop = popups(shortcuts={"rename": "F3", "properties": "alt+p", "reveal": "ctrl+shift+enter",
                            "recycle": "Ctrl+", "md_vault": "Hyper+M"})
    pop.open()
    pump(root, 0.1)
    keys = menu_labels(pop, monkeypatch)
    assert keys["rename"] == "F3" and keys["properties"] == "alt+p" and keys["reveal"] == "ctrl+shift+enter"
    assert keys["recycle"] == "Delete" and keys["md_vault"] == "Ctrl+M", "invalid chords show the default"
    assert keys["open"] == "Enter"


# ---- the "Send whole file…" confirmation ------------------------------------------------------------------------------------

def widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from widgets(child)


class Dialogs:
    """Answer each modal confirmation by clicking a named button after the dialog is on screen."""

    def __init__(self, pop, answers):
        self.seen, self.answers, self.original = [], list(answers), pop._run_dialog
        pop._run_dialog = self.run

    def run(self, dlg, focus):
        labels = [w.cget("text") for w in widgets(dlg) if w.winfo_class() == "Label"]
        buttons = {w.cget("text"): w for w in widgets(dlg) if w.winfo_class() == "Button"}
        self.seen.append({"labels": labels, "buttons": list(buttons), "text": "\n".join(labels)})
        want = self.answers.pop(0)
        dlg.after(300, buttons[want].invoke)
        self.original(dlg, focus)


def summary(mode, send, size, **extra):
    return {"path": r"C:\x\notes.txt", "bytes_to_send": send, "file_bytes": size, "whole_file": mode == "whole_file",
            "what": "the whole file's text" if mode == "whole_file" else "the first part of the file's text",
            "mode": mode, "office_text": False, "available": True, "reason": None, "account": None, **extra}


class Recorder:
    def __init__(self, replies):
        self.calls, self.replies = [], list(replies)

    def __call__(self, verb, row, **kwargs):
        self.calls.append((verb, kwargs))
        return self.replies.pop(0)


PREVIEW = {"ok": False, "message": "would send", "needs_confirmation": True, "summary": summary("preview_text", 1000, 5000)}
WHOLE = {"ok": False, "message": "would send", "needs_confirmation": True, "summary": summary("whole_file", 5000, 5000)}
ANSWER = {"ok": True, "message": "Claude answered", "answer": "The answer", "logged": True,
          "summary": summary("whole_file", 5000, 5000)}


def ask(pop, rec, answers):
    pop.on_action = rec
    dialogs = Dialogs(pop, answers)
    pop.open()
    return dialogs, pop.invoke("ask_claude", Row(name="notes.txt", folder=r"C:\x", ext="txt", size=5000))


def test_choosing_send_whole_file_confirms_twice_then_sends_with_confirm_and_whole_file(root, popups):
    pop = popups()
    rec = Recorder([PREVIEW, WHOLE, ANSWER])
    dialogs, handled = ask(pop, rec, ["Send whole file…", "Send"])
    assert handled is True
    first, second = dialogs.seen
    assert "Sends: 1000 bytes (preview text)" in first["text"] and "The file is 5000 bytes; the rest is not sent." in first["text"]
    assert "Send whole file…" in first["buttons"] and {"Send", "Cancel"} <= set(first["buttons"])
    assert "Sends: 5000 bytes (whole file)" in second["text"]
    assert "Send whole file…" not in second["buttons"], "the offer is made once"
    assert wait_until(lambda: (pump(root, 0.05), len(rec.calls) == 3)[1], timeout=10), rec.calls
    assert rec.calls == [("ask_claude", {}), ("ask_claude", {"whole_file": True}),
                         ("ask_claude", {"confirm": True, "whole_file": True})]
    assert wait_until(lambda: (pump(root, 0.05), pop.answer_window is not None)[1], timeout=10)
    assert pop.last_action["verb"] == "ask_claude" and pop.last_action["result"]["answer"] == "The answer"


def test_plain_send_after_a_preview_confirmation_sends_the_preview_only(root, popups):
    pop = popups()
    rec = Recorder([PREVIEW, ANSWER])
    dialogs, _ = ask(pop, rec, ["Send"])
    assert wait_until(lambda: (pump(root, 0.05), len(rec.calls) == 2)[1], timeout=10), rec.calls
    assert rec.calls == [("ask_claude", {}), ("ask_claude", {"confirm": True})], "no whole_file unless chosen"
    assert len(dialogs.seen) == 1


def test_a_file_that_already_goes_whole_gets_no_whole_file_button(root, popups):
    pop = popups()
    rec = Recorder([WHOLE, ANSWER])
    dialogs, _ = ask(pop, rec, ["Send"])
    assert "Send whole file…" not in dialogs.seen[0]["buttons"]
    assert "Sends: 5000 bytes (whole file)" in dialogs.seen[0]["text"]
    assert wait_until(lambda: (pump(root, 0.05), len(rec.calls) == 2)[1], timeout=10)
    assert rec.calls[1] == ("ask_claude", {"confirm": True})


def test_cancelling_either_confirmation_sends_nothing(root, popups):
    pop = popups()
    rec = Recorder([PREVIEW])
    dialogs, _ = ask(pop, rec, ["Cancel"])
    pump(root, 0.3)
    assert rec.calls == [("ask_claude", {})]
    assert pop.last_action["result"]["cancelled"] is True and pop.details == "Cancelled."

    pop2 = popups()
    rec2 = Recorder([PREVIEW, WHOLE])
    dialogs2, _ = ask(pop2, rec2, ["Send whole file…", "Cancel"])
    pump(root, 0.3)
    assert rec2.calls == [("ask_claude", {}), ("ask_claude", {"whole_file": True})], "cancel stops before any confirm=True"
    assert not any(kwargs.get("confirm") for _, kwargs in rec2.calls)
    assert pop2.last_action["result"]["cancelled"] is True


def test_confirm_text_reports_the_bytes_that_will_go(root):
    text = ask_claude_ui.confirm_text(PREVIEW)
    assert "Sends: 1000 bytes (preview text)" in text and "The file is 5000 bytes" in text
    text = ask_claude_ui.confirm_text(WHOLE)
    assert "Sends: 5000 bytes (whole file)" in text and "the rest is not sent" not in text
    assert text.endswith("Nothing is sent until you click Send.")


# ---- Markdown tags resolve the theme per call ------------------------------------------------------------------------------

@pytest.fixture
def restore_theme():
    before = theme._mode
    yield
    theme.set_mode(before if before in ("dark", "light") else "dark")


def test_configure_tags_defaults_follow_the_theme_set_before_each_call(root, restore_theme):
    text = tk.Text(root)
    theme.set_mode("dark")
    dark = {n: getattr(gfx, n) for n in ("PANEL_FG", "PANEL_DIM", "PANEL_BG", "INPUT_BG", "PANEL_LINK")}
    mdrender_tk.configure_tags(text)
    assert text.cget("fg").lower() == dark["PANEL_FG"].lower() and text.cget("bg").lower() == dark["PANEL_BG"].lower()
    assert text.tag_cget("md_dim", "foreground").lower() == dark["PANEL_DIM"].lower()
    assert text.tag_cget("md_code", "background").lower() == dark["INPUT_BG"].lower()
    assert text.tag_cget("md_link", "foreground").lower() == dark["PANEL_LINK"].lower()

    theme.set_mode("light")
    light = {n: getattr(gfx, n) for n in dark}
    assert light != dark, "the two palettes differ, otherwise this test proves nothing"
    mdrender_tk.configure_tags(text)
    assert text.cget("fg").lower() == light["PANEL_FG"].lower() and text.cget("bg").lower() == light["PANEL_BG"].lower()
    assert text.tag_cget("md_dim", "foreground").lower() == light["PANEL_DIM"].lower()
    assert text.tag_cget("md_quote", "foreground").lower() == light["PANEL_DIM"].lower()
    assert text.tag_cget("md_code", "background").lower() == light["INPUT_BG"].lower()
    assert text.tag_cget("md_codeblock", "background").lower() == light["INPUT_BG"].lower()
    assert text.tag_cget("md_link", "foreground").lower() == light["PANEL_LINK"].lower()

    theme.set_mode("dark")
    mdrender_tk.configure_tags(text)
    assert text.cget("fg").lower() == dark["PANEL_FG"].lower(), "and back again without reimporting anything"


def test_configure_tags_explicit_colours_win_over_the_theme(root, restore_theme):
    text = tk.Text(root)
    theme.set_mode("light")
    mdrender_tk.configure_tags(text, fg="#112233", dim="#445566", bg="#778899", hit_bg="#AA0000")
    assert text.cget("fg") == "#112233" and text.cget("bg") == "#778899"
    assert text.tag_cget("md_dim", "foreground") == "#445566"
    assert text.tag_cget("md_hit", "background") == "#AA0000"
