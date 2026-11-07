"""`--doctor` reports an "Ask Claude" line from actions.claude.status() (plan 7.5 M4-2)."""
import os
import subprocess
import sys

import pytest

from assistant import doctor
from assistant.actions import claude
from conftest import REPO_ROOT


@pytest.mark.parametrize("status,expected", [
    ({"available": True, "version": "9.9.9 (Claude Code)", "reason": None}, "available, claude 9.9.9 (Claude Code)"),
    ({"available": True, "version": "", "reason": None}, "available, claude version unknown"),
    ({"available": False, "version": None, "reason": "Claude Code not found on this machine"},
     "Claude Code not found on this machine"),
    ({"available": False, "version": None, "reason": None}, "not available"),
])
def test_state_text(monkeypatch, status, expected):
    monkeypatch.setattr(claude, "status", lambda refresh=False: status)
    assert doctor._ask_claude_state() == expected


def test_a_probe_crash_is_reported_not_raised(monkeypatch):
    def boom(refresh=False):
        raise OSError("x")

    monkeypatch.setattr(claude, "status", boom)
    assert doctor._ask_claude_state() == "unavailable (OSError)"
    monkeypatch.setattr(claude, "status", lambda refresh=False: "weird")
    assert doctor._ask_claude_state() == "unavailable"


def test_doctor_output_has_the_line(tmp_path):
    env = dict(os.environ, ASSISTANT_HOME=str(tmp_path))
    done = subprocess.run([sys.executable, "-m", "assistant", "--doctor"], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    lines = [l for l in done.stdout.splitlines() if l.startswith("Ask Claude")]
    assert len(lines) == 1, done.stdout
    assert lines[0].removeprefix("Ask Claude").strip(), "the line must carry a state"
