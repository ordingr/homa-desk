"""Review 14 regressions that live outside migrate.py: the hooks port parser (R14-N4), the notice card text (R14-N7)
and paths.looks_like_ours, which decides whether a lingering ~/.assistant is Homa's data at all."""
import json
import os
import types
from pathlib import Path

import pytest

from homa import app as app_mod, hooks_installer, migrate, paths
from homa.cards.model import MAX_DETAIL, MAX_TITLE


@pytest.fixture
def profile(tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    drive, rest = os.path.splitdrive(str(user))
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("HOMEDRIVE", drive)
    monkeypatch.setenv("HOMEPATH", rest)
    for name in ("HOMA_HOME", "ASSISTANT_HOME"):
        monkeypatch.delenv(name, raising=False)
    assert Path.home() == user
    return user


def write_hook_url(user: Path, url: str):
    settings = user / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    handler = {"type": "http", "url": url, "headers": {}}
    settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [handler]}]}}), encoding="utf-8")


# ---- R14-N4 --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("port", ["²", "١٢٣", "99999", "0", "", "1\x1b[31m", "17171@evil"])
def test_a_hook_url_with_an_odd_port_is_ignored_not_fatal(profile, port):
    write_hook_url(profile, f"http://127.0.0.1:{port}{hooks_installer.ROUTE}")
    assert hooks_installer.installed_ports() == set()
    assert hooks_installer.mismatch(17171, "tok") is None


def test_a_hook_url_with_a_normal_port_is_still_read(profile):
    write_hook_url(profile, f"http://127.0.0.1:62480{hooks_installer.ROUTE}")
    assert hooks_installer.installed_ports() == {62480}
    assert "62480" in hooks_installer.mismatch(17171, "tok")


class _Store:
    def __init__(self):
        self.cards = []

    def upsert(self, card, source):
        self.cards.append(card)


def _fake_app():
    app = app_mod.App.__new__(app_mod.App)
    app.store, app.settings, app._token = _Store(), types.SimpleNamespace(port=17171), "tok"
    return app


def _raise(*args):
    raise ValueError("odd")


def test_startup_hook_check_that_raises_is_logged_and_does_not_abort(monkeypatch):
    app = _fake_app()
    monkeypatch.setattr(migrate, "consume_notice", lambda: None)
    monkeypatch.setattr(hooks_installer, "mismatch", _raise)
    app._show_migration_cards()
    assert app.store.cards == []


# ---- R14-N7 --------------------------------------------------------------------------------------------------------

def test_migration_notice_card_is_stripped_of_control_characters_and_capped(monkeypatch):
    app = _fake_app()
    evil = {"title": "\x1b[31mEvil\x00" + "a" * 3000, "detail": "d\x07" + "b" * 3000}
    monkeypatch.setattr(migrate, "consume_notice", lambda: evil)
    monkeypatch.setattr(hooks_installer, "mismatch", lambda *a: None)
    app._show_migration_cards()
    (card,) = app.store.cards
    assert card.id == "migrated" and len(card.title) <= MAX_TITLE and len(card.detail) <= MAX_DETAIL
    assert "\x1b" not in card.title and "\x00" not in card.title and "\x07" not in card.detail
    assert card.title.endswith("a")


def test_update_notice_card_gets_the_same_treatment(monkeypatch):
    from homa import update
    app = _fake_app()
    monkeypatch.setattr(update, "consume_notice", lambda: {"title": "t\x00" + "a" * 3000, "detail": "d\x1b" * 900})
    app._show_update_notice()
    (card,) = app.store.cards
    assert len(card.title) <= MAX_TITLE and len(card.detail) <= MAX_DETAIL
    assert "\x00" not in card.title and "\x1b" not in card.detail


# ---- paths.looks_like_ours -------------------------------------------------------------------------------------------

def test_looks_like_ours_needs_a_token_or_settings_json(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    assert not paths.looks_like_ours(folder)
    (folder / "logs").mkdir()
    (folder / "foreign-tool.cfg").write_text("x", encoding="utf-8")
    assert not paths.looks_like_ours(folder)
    (folder / "token").write_text("t", encoding="ascii")
    assert paths.looks_like_ours(folder)
    (folder / "token").unlink()
    (folder / "settings.json").write_text("{}", encoding="utf-8")
    assert paths.looks_like_ours(folder)


def test_a_foreign_dot_assistant_is_never_adopted_as_the_home(profile):
    (profile / ".assistant").mkdir()
    (profile / ".assistant" / "foreign-tool.cfg").write_text("x", encoding="utf-8")
    assert paths.home() == profile / ".homa"


def test_ensure_layout_next_to_a_foreign_dot_assistant_builds_dot_homa_and_leaves_it_alone(profile):
    foreign = profile / ".assistant"
    foreign.mkdir()
    (foreign / "foreign-tool.cfg").write_text("x", encoding="utf-8")
    root = paths.ensure_layout()
    assert root == profile / ".homa" and (root / "logs").is_dir()
    assert sorted(p.name for p in foreign.iterdir()) == ["foreign-tool.cfg"]
