"""Shortcut defaults after Copy/Cut of files arrived (plan 2026-11-09 popup-to-fullpage-and-file-clipboard, section 5
D1 settings row, AC-C2): Ctrl+C = copy_files, Ctrl+X = cut_files, Ctrl+Shift+C = copy_path, Ctrl+A = select_all.

An install that saved the old default ``"copy_path": "Ctrl+C"`` migrates to Ctrl+Shift+C; a chord the user picked is
kept; when a saved chord collides with one of the newer defaults the user's chord wins and the new action gets no chord
(""). Real Settings objects against a scratch HOMA_HOME only; nothing mocked.
"""
import json

import pytest

from homa import paths
from homa.settings import DEFAULT_SHORTCUTS, UNBINDABLE_SHORTCUTS, Settings

# The shortcuts dict an install wrote to disk before 2026-11-09 (Settings.save stores the whole dict).
OLD_SAVED = {
    "open": "Enter", "reveal": "Ctrl+Enter", "copy_path": "Ctrl+C", "rename": "F2", "properties": "Alt+Enter",
    "recycle": "Delete", "md_vault": "Ctrl+M", "folder_listing": "Ctrl+F", "cycle_sort": "Ctrl+S",
    "save_search": "Ctrl+Shift+S", "cycle_date": "Ctrl+D", "cycle_mode": "Tab", "context_menu": "Shift+F10",
    "full_page": "F11", "close": "Escape",
}


@pytest.fixture(autouse=True)
def scratch_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMA_HOME", str(tmp_path))


def load(shortcuts):
    paths.settings_file().write_text(json.dumps({"schema": 1, "shortcuts": shortcuts}), encoding="utf-8")
    s = Settings()
    s.load()
    return s.get("shortcuts")


def test_the_new_defaults():
    assert DEFAULT_SHORTCUTS["copy_files"] == "Ctrl+C" and DEFAULT_SHORTCUTS["cut_files"] == "Ctrl+X"
    assert DEFAULT_SHORTCUTS["copy_path"] == "Ctrl+Shift+C" and DEFAULT_SHORTCUTS["select_all"] == "Ctrl+A"
    assert set(UNBINDABLE_SHORTCUTS) == {"copy_files", "cut_files", "copy_path", "select_all"}


def test_a_whole_pre_change_saved_dict_loads_as_exactly_the_new_defaults():
    assert load(OLD_SAVED) == DEFAULT_SHORTCUTS


def test_only_copy_path_ctrl_c_saved_migrates():
    got = load({"copy_path": "Ctrl+C"})
    assert got["copy_path"] == "Ctrl+Shift+C" and got["copy_files"] == "Ctrl+C"
    assert got["cut_files"] == "Ctrl+X" and got["select_all"] == "Ctrl+A"


@pytest.mark.parametrize("spelling", ["ctrl+c", "CTRL+C", "Ctrl+c"])
def test_the_old_default_is_recognised_in_any_case(spelling):
    got = load({"copy_path": spelling})
    assert got["copy_path"] == "Ctrl+Shift+C" and got["copy_files"] == "Ctrl+C"


def test_a_user_chosen_copy_path_is_kept():
    got = load({"copy_path": "Ctrl+Alt+P"})
    assert got["copy_path"] == "Ctrl+Alt+P" and got["copy_files"] == "Ctrl+C"


def test_ctrl_c_for_copy_path_chosen_after_the_change_is_not_migrated_again():
    """A file saved after the change holds copy_files too, so copy_path=Ctrl+C there is the user's own choice."""
    saved = dict(DEFAULT_SHORTCUTS, copy_path="Ctrl+C", copy_files="Ctrl+Alt+C")
    got = load(saved)
    assert got["copy_path"] == "Ctrl+C" and got["copy_files"] == "Ctrl+Alt+C"


def test_a_saved_chord_that_collides_with_a_new_default_wins_and_the_new_action_is_unbound():
    got = load({"rename": "Ctrl+X", "md_vault": "ctrl+a"})
    assert got["rename"] == "Ctrl+X" and got["cut_files"] == ""
    assert got["md_vault"] == "ctrl+a" and got["select_all"] == ""
    assert got["copy_files"] == "Ctrl+C" and got["copy_path"] == "Ctrl+Shift+C"


def test_modifier_order_does_not_hide_a_collision():
    got = load({"copy_path": "Ctrl+C", "save_search": "Shift+Ctrl+C"})
    assert got["save_search"] == "Shift+Ctrl+C"
    assert got["copy_path"] == "", "the migrated Copy path chord collides with the user's own chord, which wins"
    assert got["copy_files"] == "Ctrl+C"


def test_an_old_install_that_had_copy_path_and_rename_both_on_ctrl_c_keeps_rename():
    got = load({"rename": "Ctrl+C", "copy_path": "Ctrl+C"})
    assert got["rename"] == "Ctrl+C"
    assert got["copy_path"] == "Ctrl+Shift+C", "the old default still moves"
    assert got["copy_files"] == "", "Ctrl+C belongs to the user's rename"


def test_empty_string_unbinds_only_the_four_new_actions():
    got = load({"copy_files": "", "cut_files": "", "copy_path": "", "select_all": "", "rename": ""})
    assert got["copy_files"] == got["cut_files"] == got["copy_path"] == got["select_all"] == ""
    assert got["rename"] == "F2", "an empty chord on any other action is repaired to its default"


def test_set_accepts_an_unbound_new_action_and_rejects_an_unbound_old_one():
    s = Settings()
    s.load()
    s.set("shortcuts", dict(DEFAULT_SHORTCUTS, cut_files=""))
    again = Settings()
    again.load()
    assert again.get("shortcuts")["cut_files"] == ""
    with pytest.raises(ValueError):
        s.set("shortcuts", dict(DEFAULT_SHORTCUTS, rename=""))


def test_the_migration_is_idempotent_through_a_save_and_reload():
    paths.settings_file().write_text(json.dumps({"schema": 1, "shortcuts": OLD_SAVED}), encoding="utf-8")
    s = Settings()
    s.load()
    s.save()
    on_disk = json.loads(paths.settings_file().read_text(encoding="utf-8"))["shortcuts"]
    assert on_disk == DEFAULT_SHORTCUTS
    again = Settings()
    again.load()
    assert again.get("shortcuts") == DEFAULT_SHORTCUTS


def test_a_fresh_home_writes_the_new_defaults():
    s = Settings()
    s.load()
    assert json.loads(paths.settings_file().read_text(encoding="utf-8"))["shortcuts"] == DEFAULT_SHORTCUTS


def test_the_in_app_shortcut_list_shows_the_new_chords_and_none_for_unbound():
    import re
    import types
    settings_ui = pytest.importorskip("homa.overlay.settings_ui")
    s = Settings()
    s.load()
    text = settings_ui.SettingsWindow._shortcuts_text(types.SimpleNamespace(settings=s))
    rows = {m.group(1).strip(): m.group(2) for m in re.finditer(r"^(.+?)\s{2,}(\S.*)$", text, re.M)}
    assert rows["Copy files"] == "Ctrl+C" and rows["Cut files"] == "Ctrl+X"
    assert rows["Copy path"] == "Ctrl+Shift+C" and rows["Select all"] == "Ctrl+A"
    s.set("shortcuts", dict(DEFAULT_SHORTCUTS, cut_files=""))
    text = settings_ui.SettingsWindow._shortcuts_text(types.SimpleNamespace(settings=s))
    assert re.search(r"^Cut files\s+\(none\)$", text, re.M), text
