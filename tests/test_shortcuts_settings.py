"""Keyboard shortcuts in settings (plan 7.5 M4-2; AC-PS-4): defaults, validation, repair on load.

Real Settings object against a scratch HOMA_HOME; nothing mocked.
"""
import json

import pytest

from homa import paths
from homa.settings import DEFAULT_SHORTCUTS, DEFAULTS, Settings

ACTIONS = {"open", "reveal", "copy_path", "rename", "properties", "recycle", "md_vault", "folder_listing",
           "cycle_sort", "save_search", "cycle_date", "cycle_mode", "context_menu", "full_page", "close"}


@pytest.fixture(autouse=True)
def scratch_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMA_HOME", str(tmp_path))


def load(shortcuts):
    paths.settings_file().write_text(json.dumps({"schema": 1, "shortcuts": shortcuts}), encoding="utf-8")
    s = Settings()
    s.load()
    return s.get("shortcuts")


def test_defaults_cover_the_fifteen_actions_and_are_in_a_fresh_settings():
    assert set(DEFAULT_SHORTCUTS) == ACTIONS and len(DEFAULT_SHORTCUTS) == 15
    s = Settings()
    s.load()
    assert s.get("shortcuts") == DEFAULT_SHORTCUTS == DEFAULTS["shortcuts"]
    assert json.loads(paths.settings_file().read_text(encoding="utf-8"))["shortcuts"] == DEFAULT_SHORTCUTS


def test_default_get_is_a_copy_not_the_module_constant():
    s = Settings()
    s.load()
    s.get("shortcuts")["open"] = "X"
    assert s.get("shortcuts")["open"] == "Enter"
    assert DEFAULT_SHORTCUTS["open"] == "Enter"


def test_a_valid_override_is_kept_and_the_rest_default():
    got = load({"rename": "F3", "recycle": "Ctrl+Shift+Delete"})
    assert got["rename"] == "F3" and got["recycle"] == "Ctrl+Shift+Delete"
    assert got["open"] == "Enter" and set(got) == ACTIONS


def test_valid_unknown_extra_action_is_kept():
    got = load({"my_action": "Alt+Q"})
    assert got["my_action"] == "Alt+Q" and set(got) == ACTIONS | {"my_action"}


@pytest.mark.parametrize("bad", ["", "Ctrl+", "Ctrl+Ctrl", "Hyper+A", "F25", "F0", "Ctrl + C", "ab", "Ctrl+Shift+Alt+Win+A",
                                 "x" * 40, 5, None, ["F2"], {"a": 1}, True])
def test_invalid_chord_on_a_known_action_reverts_to_that_default(bad):
    got = load({"rename": bad, "copy_path": "Ctrl+Y"})
    assert got["rename"] == "F2"
    assert got["copy_path"] == "Ctrl+Y", "a sibling valid entry must survive"


@pytest.mark.parametrize("bad", ["", "Ctrl+", "zzzz", 5, None])
def test_invalid_chord_on_an_unknown_action_is_dropped(bad):
    got = load({"made_up": bad, "rename": "F4"})
    assert "made_up" not in got and got["rename"] == "F4"


@pytest.mark.parametrize("name", ["not an identifier", "1abc", "a-b", "", "x" * 40])
def test_unusable_action_names_are_dropped(name):
    got = load({name: "F5"})
    assert name not in got and set(got) == ACTIONS


@pytest.mark.parametrize("junk", ["F2", ["F2"], 3, None, True])
def test_non_dict_shortcuts_fall_back_to_all_defaults(junk):
    assert load(junk) == DEFAULT_SHORTCUTS


def test_the_devs_probe_mix_in_one_file():
    got = load({"open": "Enter!", "close": "Esc", "rename": "Ctrl+F13", "extra_ok": "Ctrl+Alt+X", "extra_bad": "???",
                "recycle": 9, "full_page": "F11"})
    assert got["open"] == "Enter" and got["close"] == "Escape"  # "Esc" is not a chord name
    assert got["rename"] == "Ctrl+F13"  # F13 is valid
    assert got["extra_ok"] == "Ctrl+Alt+X" and "extra_bad" not in got
    assert got["recycle"] == "Delete" and got["full_page"] == "F11"


@pytest.mark.parametrize("chord", ["a", "Z", "0", "F1", "f24", "ctrl+enter", "CTRL+SHIFT+S", "Alt+Enter", "Shift+F10",
                                   "Escape", "Delete", "Tab", "Space", "Backspace", "Insert", "Home", "End", "PageUp",
                                   "PageDown", "Up", "Down", "Left", "Right", "Menu"])
def test_chord_grammar_accepts(chord):
    s = Settings()
    s.load()
    s.set("shortcuts", {"rename": chord})
    assert s.get("shortcuts")["rename"] == chord


@pytest.mark.parametrize("bad", ["", "Ctrl", "Ctrl+", "Win+A", "F25", "Ctrl++"])
def test_set_rejects_what_load_would_repair(bad):
    s = Settings()
    s.load()
    with pytest.raises(ValueError):
        s.set("shortcuts", {"rename": bad})
    assert s.get("shortcuts")["rename"] == "F2"


def test_duplicate_chords_are_not_rejected_current_behaviour():
    """Recorded, not endorsed: two actions on one chord load as-is (dev reported this as a known gap)."""
    got = load({"rename": "Ctrl+C", "copy_path": "Ctrl+C"})
    assert got["rename"] == got["copy_path"] == "Ctrl+C"


def test_repair_is_idempotent_through_a_save_and_reload():
    paths.settings_file().write_text(json.dumps({"schema": 1, "shortcuts": {"rename": "??", "zz": "!!"}}),
                                     encoding="utf-8")
    s = Settings()
    s.load()
    s.save()
    again = Settings()
    again.load()
    assert again.get("shortcuts") == s.get("shortcuts") == DEFAULT_SHORTCUTS


@pytest.mark.parametrize("bad", ["Ctrl+\u0131", "Ctrl+\u00e9", "Ctrl+\u0663", "Alt+\u00df", "\u0661"])
def test_non_ascii_letters_and_digits_are_not_valid_chords(bad):
    got = load({"rename": bad, "copy_path": "Ctrl+Y"})
    assert got["rename"] == "F2", "the \w-style classes must not accept Unicode letters or digits"
    assert got["copy_path"] == "Ctrl+Y"


def test_the_ascii_chord_still_loads():
    assert load({"rename": "Ctrl+i"})["rename"] == "Ctrl+i"
