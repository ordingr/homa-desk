"""Claude Code hooks installer / remover (plan 7.1 M1-5; AC-CS-1, AC-CS-2, AC-IN-3).

Driven through the CLI (``python -m homa hooks install|remove [--dry-run]``) in a subprocess whose
USERPROFILE/HOME and HOMA_HOME point at a temp dir, so the user-level settings file
``~/.claude/settings.json`` is a fixture and the real one is never touched. Skipped until
homa/hooks_installer.py (M1-5) exists.
"""
import json
import os
import subprocess
import sys

import pytest

from conftest import REPO_ROOT

pytest.importorskip("homa.hooks_installer", reason="homa/hooks_installer.py (M1-5) not present")

EVENTS = ["PermissionRequest", "Notification", "Stop", "StopFailure",
          "UserPromptSubmit", "PostToolUse", "SessionStart", "SessionEnd"]
ROUTE = "/v1/hooks/claude-code"

FOREIGN = {
    "model": "opus",
    "permissions": {"allow": ["Bash(git status)"]},
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo pre"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "echo user-stop-hook"}]}],
        "PermissionRequest": [{"matcher": "Write", "hooks": [{"type": "command", "command": "echo user-perm"}]}],
    },
}


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "user"
    (home / ".claude").mkdir(parents=True)
    homa_home = tmp_path / "homa_home"
    homa_home.mkdir()
    (homa_home / "settings.json").write_text(json.dumps({"schema": 1, "port": 17333}), encoding="utf-8")
    e = dict(os.environ, USERPROFILE=str(home), HOME=str(home), HOMA_HOME=str(homa_home))
    e["HOMEDRIVE"], e["HOMEPATH"] = os.path.splitdrive(str(home))[0], str(home)[len(os.path.splitdrive(str(home))[0]):]
    return e, home / ".claude" / "settings.json", homa_home


def run(env_, *args, check=True):
    result = subprocess.run([sys.executable, "-m", "homa", "hooks", *args], cwd=REPO_ROOT, env=env_,
                            capture_output=True, text=True, timeout=60)
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def write_foreign(path):
    path.write_text(json.dumps(FOREIGN, indent=2) + "\n", encoding="utf-8")
    return path.read_bytes()


def own_entries(settings: dict):
    out = {}
    for event, groups in settings.get("hooks", {}).items():
        for group in groups:
            for h in group.get("hooks", []):
                if h.get("type") == "http" and ROUTE in h.get("url", ""):
                    out.setdefault(event, []).append(h)
    return out


def test_dry_run_prints_the_eight_entries_and_writes_nothing(env):
    e, settings, home = env
    original = write_foreign(settings)
    result = run(e, "install", "--dry-run")
    for event in EVENTS:
        assert event in result.stdout
    assert ROUTE in result.stdout and "127.0.0.1" in result.stdout
    assert settings.read_bytes() == original
    assert list((home / "hooks-backup").glob("*")) == []


def test_install_adds_exactly_eight_http_entries_with_bearer_header_and_continue_on_failure(env):
    e, settings, home = env
    write_foreign(settings)
    run(e, "install")
    own = own_entries(json.loads(settings.read_text(encoding="utf-8")))
    assert sorted(own) == sorted(EVENTS)
    for event, hooks in own.items():
        assert len(hooks) == 1, event
        h = hooks[0]
        assert h["url"] == f"http://127.0.0.1:17333{ROUTE}"
        assert h["onFailure"] == "continue"
        auth = {k.lower(): v for k, v in h.get("headers", {}).items()}.get("authorization", "")
        assert auth.startswith("Bearer ") and len(auth) > len("Bearer ") + 16, "token header missing"


def test_install_leaves_every_foreign_entry_and_setting_untouched(env):
    e, settings, _ = env
    write_foreign(settings)
    run(e, "install")
    after = json.loads(settings.read_text(encoding="utf-8"))
    assert after["model"] == "opus" and after["permissions"] == FOREIGN["permissions"]
    assert after["hooks"]["PreToolUse"] == FOREIGN["hooks"]["PreToolUse"]
    for event in ("Stop", "PermissionRequest"):
        for group in FOREIGN["hooks"][event]:
            assert group in after["hooks"][event], f"{event}: pre-existing group was lost or altered"


def test_install_writes_a_timestamped_backup_that_is_byte_identical_to_the_original(env):
    e, settings, home = env
    original = write_foreign(settings)
    run(e, "install")
    backups = list((home / "hooks-backup").glob("*"))
    assert len(backups) == 1, backups
    assert backups[0].read_bytes() == original


def test_install_is_idempotent(env):
    e, settings, _ = env
    write_foreign(settings)
    run(e, "install")
    once = settings.read_bytes()
    run(e, "install")
    assert settings.read_bytes() == once
    assert all(len(v) == 1 for v in own_entries(json.loads(once)).values())


def test_install_creates_settings_when_none_exists(env):
    e, settings, _ = env
    assert not settings.exists()
    run(e, "install")
    assert sorted(own_entries(json.loads(settings.read_text(encoding="utf-8")))) == sorted(EVENTS)


def test_remove_restores_the_original_content_exactly(env):
    e, settings, _ = env
    original = write_foreign(settings)
    run(e, "install")
    run(e, "remove")
    assert own_entries(json.loads(settings.read_text(encoding="utf-8"))) == {}
    assert json.loads(settings.read_text(encoding="utf-8")) == FOREIGN
    assert settings.read_bytes() == original, "plan 7.1 M1-5: remove restores byte-identical content"


def test_remove_removes_only_our_entries_even_if_the_user_added_more_afterwards(env):
    e, settings, _ = env
    write_foreign(settings)
    run(e, "install")
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["hooks"].setdefault("PostToolUse", []).append(
        {"hooks": [{"type": "command", "command": "echo added-later"}]})
    data["theme"] = "dark"
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    run(e, "remove")
    after = json.loads(settings.read_text(encoding="utf-8"))
    assert own_entries(after) == {}
    assert after["theme"] == "dark"
    assert {"hooks": [{"type": "command", "command": "echo added-later"}]} in after["hooks"]["PostToolUse"]
    assert after["hooks"]["PreToolUse"] == FOREIGN["hooks"]["PreToolUse"]


def test_remove_with_nothing_installed_changes_nothing(env):
    e, settings, _ = env
    original = write_foreign(settings)
    run(e, "remove")
    assert settings.read_bytes() == original


def test_install_refuses_unreadable_settings_without_overwriting(env):
    e, settings, _ = env
    settings.write_text("{ this is not json", encoding="utf-8")
    result = run(e, "install", check=False)
    assert result.returncode != 0
    assert settings.read_text(encoding="utf-8") == "{ this is not json"
    assert "Traceback" not in result.stderr

