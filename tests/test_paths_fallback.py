"""paths.home() resolution order and the legacy ASSISTANT_* environment fallbacks (rename plan 4.2)."""
import logging
import os
from pathlib import Path

import pytest

from homa import paths


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """A fake user profile with every home-selecting variable cleared."""
    user = tmp_path / "user"
    user.mkdir()
    drive, rest = os.path.splitdrive(str(user))
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("HOMEDRIVE", drive)
    monkeypatch.setenv("HOMEPATH", rest)
    for name in ("HOMA_HOME", "ASSISTANT_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(paths, "_warned_legacy", set())
    assert Path.home() == user
    return user


def test_homa_home_wins_over_assistant_home(profile, monkeypatch, tmp_path):
    new, old = tmp_path / "new", tmp_path / "old"
    new.mkdir(), old.mkdir()
    monkeypatch.setenv("HOMA_HOME", str(new))
    monkeypatch.setenv("ASSISTANT_HOME", str(old))
    assert paths.home() == new.resolve()


def test_assistant_home_alone_is_used_and_warns_once(profile, monkeypatch, tmp_path, caplog):
    old = tmp_path / "old"
    old.mkdir()
    monkeypatch.setenv("ASSISTANT_HOME", str(old))
    with caplog.at_level(logging.WARNING, logger="homa.paths"):
        assert paths.home() == old.resolve()
        assert paths.home() == old.resolve()
    warnings = [r for r in caplog.records if "ASSISTANT_HOME is deprecated" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize("name", ["TEST_MODE", "STATIC_DIR", "NOTES_DIR"])
def test_other_legacy_env_names_fall_back_and_homa_wins(profile, monkeypatch, name):
    monkeypatch.delenv("HOMA_" + name, raising=False)
    monkeypatch.setenv("ASSISTANT_" + name, "legacy")
    assert paths.env(name) == "legacy"
    monkeypatch.setenv("HOMA_" + name, "current")
    assert paths.env(name) == "current"


def test_env_is_none_when_neither_is_set(profile, monkeypatch):
    monkeypatch.delenv("HOMA_NOTES_DIR", raising=False)
    monkeypatch.delenv("ASSISTANT_NOTES_DIR", raising=False)
    assert paths.env("NOTES_DIR") is None


def test_default_is_dot_homa_when_nothing_exists(profile):
    assert paths.home() == profile / ".homa"


def test_dot_homa_is_used_when_it_exists(profile):
    (profile / ".homa").mkdir()
    assert paths.home() == profile / ".homa"


def test_dot_assistant_is_used_only_when_dot_homa_is_absent(profile):
    (profile / ".assistant").mkdir()
    (profile / ".assistant" / "token").write_text("t", encoding="ascii")
    assert paths.home() == profile / ".assistant"
    (profile / ".homa").mkdir()
    assert paths.home() == profile / ".homa"


def test_ensure_layout_does_not_create_dot_homa_next_to_an_unmigrated_dot_assistant(profile):
    (profile / ".assistant").mkdir()
    (profile / ".assistant" / "settings.json").write_text("{}", encoding="utf-8")
    root = paths.ensure_layout()
    assert root == profile / ".assistant"
    assert not (profile / ".homa").exists()
    assert (profile / ".assistant" / "logs").is_dir()


def test_ensure_layout_creates_dot_homa_on_a_fresh_profile(profile):
    root = paths.ensure_layout()
    assert root == profile / ".homa" and (root / "events").is_dir()
    assert not (profile / ".assistant").exists()


def test_known_files_accept_a_migrated_folder():
    for name in ("homa.pid", "assistant.pid", "migrated-from.json", "migration-notice.json"):
        assert paths._KNOWN_FILES.fullmatch(name), name
