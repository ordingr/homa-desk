"""--doctor lines added by the rename: the hook port/token mismatch suffix and the migration / old data folder lines
(rename plan 4.5 steps 7 and the decision table). Real CLI in a subprocess on a fake profile."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from homa import hooks_installer

from conftest import REPO_ROOT

TOKEN = "t" * 40


def doctor(user: Path, home: Path | None, local: Path) -> dict:
    drive, rest = os.path.splitdrive(str(user))
    env = dict(os.environ, USERPROFILE=str(user), HOME=str(user), HOMEDRIVE=drive, HOMEPATH=rest,
               LOCALAPPDATA=str(local))
    env.pop("ASSISTANT_HOME", None)
    env.pop("HOMA_HOME", None)
    if home is not None:
        env["HOMA_HOME"] = str(home)
    result = subprocess.run([sys.executable, "-m", "homa", "--doctor"], cwd=REPO_ROOT, env=env, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    lines = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("  ")
        lines[key.strip()] = value.strip()
    lines["_raw"] = result.stdout
    return lines


def write_hooks(user: Path, port: int, bearer: str = TOKEN):
    settings = user / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    handler = {"type": "http", "url": f"http://127.0.0.1:{port}{hooks_installer.ROUTE}",
               "headers": {"Authorization": f"Bearer {bearer}"}, "onFailure": "continue"}
    settings.write_text(json.dumps({"hooks": {e: [{"hooks": [handler]}] for e in hooks_installer.EVENTS}}),
                        encoding="utf-8")


@pytest.fixture
def prof(tmp_path):
    user, local, home = tmp_path / "user", tmp_path / "local", tmp_path / "home"
    for d in (user, local, home):
        d.mkdir()
    (home / "settings.json").write_text(json.dumps({"schema": 1, "port": 17171}), encoding="utf-8")
    (home / "token").write_text(TOKEN, encoding="ascii")
    return user, local, home


def test_hooks_at_another_port_are_reported_with_both_ports(prof):
    user, local, home = prof
    write_hooks(user, 62480)
    line = doctor(user, home, local)["hooks installed"]
    assert line == "yes (all 8 events) but at port 62480; settings port is 17171", line


def test_hooks_at_the_settings_port_with_the_right_bearer_have_no_suffix(prof):
    user, local, home = prof
    write_hooks(user, 17171, TOKEN)
    assert doctor(user, home, local)["hooks installed"] == "yes (all 8 events)"


def test_hooks_with_a_foreign_bearer_are_reported(prof):
    user, local, home = prof
    write_hooks(user, 17171, "someone-elses")
    line = doctor(user, home, local)["hooks installed"]
    assert line.startswith("yes (all 8 events) but ") and "token" in line, line


def test_both_folders_without_marker_shows_the_old_data_folder_line(prof, tmp_path):
    user, local, _ = prof
    (user / ".homa").mkdir()
    (user / ".assistant").mkdir()
    (user / ".assistant" / "token").write_text("t", encoding="ascii")
    report = doctor(user, None, local)
    assert report["migration"] == "not done: both folders exist"
    assert "were not merged" in report["old data folder"] and "Homa uses .homa" in report["old data folder"]


def test_pending_migration_is_reported_and_doctor_never_migrates(prof):
    user, local, _ = prof
    (user / ".assistant").mkdir()
    (user / ".assistant" / "token").write_text("t", encoding="ascii")
    report = doctor(user, None, local)
    assert report["migration"].startswith("pending")
    assert (user / ".assistant").is_dir() and not (user / ".homa").exists(), "--doctor must not move or create anything"


def test_explicit_home_reports_not_applicable(prof):
    user, local, home = prof
    assert doctor(user, home, local)["migration"].startswith("not applicable")
