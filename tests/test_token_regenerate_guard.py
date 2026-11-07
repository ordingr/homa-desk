"""token.regenerate() rewrites the Claude Code hook entries only when they carry THIS instance's old token.

Real subprocesses with a throw-away USERPROFILE/HOME (the user's ~/.claude/settings.json is never touched) and two
separate ASSISTANT_HOMEs ("A" owns the installed hooks, "B" is a second instance, such as a test run).
"""
import json
import os
import subprocess
import sys

import pytest

from conftest import REPO_ROOT

REGEN = "from assistant.security import token; r = token.regenerate(); print(r.hooks); print(r.token)"
ENSURE = "from assistant.security import token; print(token.ensure())"


class World:
    def __init__(self, tmp_path):
        self.user = tmp_path / "user"
        (self.user / ".claude").mkdir(parents=True)
        self.settings = self.user / ".claude" / "settings.json"
        self.homes = {}
        for name in ("A", "B"):
            home = tmp_path / f"home_{name}"
            home.mkdir()
            (home / "settings.json").write_text(json.dumps({"schema": 1, "port": 17333}), encoding="utf-8")
            self.homes[name] = home

    def env(self, who):
        drive, rest = os.path.splitdrive(str(self.user))
        return dict(os.environ, USERPROFILE=str(self.user), HOME=str(self.user), HOMEDRIVE=drive, HOMEPATH=rest,
                    ASSISTANT_HOME=str(self.homes[who]))

    def py(self, who, code):
        done = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=self.env(who), capture_output=True,
                              text=True, timeout=60)
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout.strip().splitlines()

    def cli(self, who, *args):
        done = subprocess.run([sys.executable, "-m", "assistant", *args], cwd=REPO_ROOT, env=self.env(who),
                              capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout

    def token(self, who):
        return (self.homes[who] / "token").read_text(encoding="ascii").strip()

    def bearers(self):
        text = self.settings.read_text(encoding="utf-8") if self.settings.exists() else ""
        data = json.loads(text) if text else {}
        found = set()
        for groups in (data.get("hooks") or {}).values():
            for group in groups:
                for hook in group.get("hooks", []):
                    auth = (hook.get("headers") or {}).get("Authorization", "")
                    if auth.startswith("Bearer "):
                        found.add(auth[len("Bearer "):])
        return found


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_the_owner_instance_rewrites_its_hooks_to_the_new_token(world):
    world.cli("A", "hooks", "install")
    old = world.token("A")
    assert world.bearers() == {old}
    hooks, new = world.py("A", REGEN)
    assert hooks == "updated"
    assert new != old and world.token("A") == new
    assert world.bearers() == {new}, "every entry now carries the new token and none the old one"


def test_a_second_instance_leaves_the_users_hooks_alone(world):
    world.cli("A", "hooks", "install")
    owner = world.token("A")
    world.py("B", ENSURE)
    stranger_old = world.token("B")
    before = world.settings.read_bytes()
    hooks, stranger_new = world.py("B", REGEN)
    assert hooks == "belongs to another instance"
    assert stranger_new != stranger_old and world.token("B") == stranger_new, "B's own token is still regenerated"
    assert world.settings.read_bytes() == before, "settings.json is byte-for-byte unchanged"
    assert world.bearers() == {owner} and stranger_new not in world.settings.read_text(encoding="utf-8")


def test_the_owner_still_updates_after_a_stranger_tried(world):
    world.cli("A", "hooks", "install")
    world.py("B", ENSURE)
    assert world.py("B", REGEN)[0] == "belongs to another instance"
    hooks, new = world.py("A", REGEN)
    assert hooks == "updated" and world.bearers() == {new}


def test_no_hooks_installed_reports_not_installed_and_creates_no_settings(world):
    assert world.py("A", REGEN)[0] == "not installed"
    assert not world.settings.exists() or "Bearer" not in world.settings.read_text(encoding="utf-8")


def test_a_missing_old_token_file_is_treated_as_not_the_owner(world):
    world.cli("A", "hooks", "install")
    (world.homes["A"] / "token").unlink()
    before = world.settings.read_bytes()
    hooks, new = world.py("A", REGEN)
    assert hooks == "belongs to another instance" and world.settings.read_bytes() == before
    assert len(new) >= 32 and world.token("A") == new


def test_partial_hooks_of_the_owner_are_still_repaired(world):
    world.cli("A", "hooks", "install")
    data = json.loads(world.settings.read_text(encoding="utf-8"))
    data["hooks"].pop("Stop")
    world.settings.write_text(json.dumps(data, indent=2), encoding="utf-8")
    hooks, new = world.py("A", REGEN)
    assert hooks == "updated"
    assert "Stop" in json.loads(world.settings.read_text(encoding="utf-8"))["hooks"] and world.bearers() == {new}


def test_user_entries_next_to_ours_survive_the_owners_rewrite(world):
    world.settings.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "echo mine"}]}]}}), encoding="utf-8")
    world.cli("A", "hooks", "install")
    assert world.py("A", REGEN)[0] == "updated"
    data = json.loads(world.settings.read_text(encoding="utf-8"))
    assert data["model"] == "opus"
    assert {"hooks": [{"type": "command", "command": "echo mine"}]} in data["hooks"]["Stop"]


def test_carries_bearer_is_exact_and_false_for_unreadable_settings(world):
    world.cli("A", "hooks", "install")
    token = world.token("A")
    probe = ("from assistant import hooks_installer as h; import sys; "
             "print(h.carries_bearer(sys.argv[1]), h.carries_bearer(sys.argv[1] + 'x'), h.carries_bearer(''))")

    def ask(code_token):
        done = subprocess.run([sys.executable, "-c", probe, code_token], cwd=REPO_ROOT, env=world.env("A"),
                              capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr
        return done.stdout.split()
    assert ask(token) == ["True", "False", "False"]
    assert ask("someone-else") == ["False", "False", "False"]
    world.settings.write_text("{not json", encoding="utf-8")
    assert ask(token) == ["False", "False", "False"]
