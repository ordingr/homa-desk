"""The ``assistant`` compatibility shim (rename plan 4.3): ``python -m assistant`` and ``python -m assistant.mcp_stdio``
keep working for 0.2.x, say so on stderr only, and must not break when stderr does not exist (pythonw)."""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from conftest import REPO_ROOT

DOCTOR_NOTICE = "python -m assistant is now python -m homa (this alias is removed in 0.3.0)"
STDIO_NOTICE = "python -m assistant.mcp_stdio is now python -m homa.mcp_stdio (this alias is removed in 0.3.0)"
INIT = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "qa", "version": "0"}}})


def scratch_env(tmp_path: Path, **extra) -> dict:
    user = tmp_path / "user"
    user.mkdir(exist_ok=True)
    local = tmp_path / "local"
    local.mkdir(exist_ok=True)
    drive, rest = os.path.splitdrive(str(user))
    env = dict(os.environ, HOMA_HOME=str(tmp_path / "home"), USERPROFILE=str(user), HOME=str(user),
               HOMEDRIVE=drive, HOMEPATH=rest, LOCALAPPDATA=str(local), **extra)
    env.pop("ASSISTANT_HOME", None)
    return env


def run(args, env, **kw):
    return subprocess.run([sys.executable, *args], cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                          timeout=120, **kw)


def normalise(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith(("index size and age", "port ")))


def test_assistant_doctor_matches_homa_doctor_and_notice_is_on_stderr(tmp_path):
    env = scratch_env(tmp_path)
    old = run(["-m", "assistant", "--doctor"], env)
    new = run(["-m", "homa", "--doctor"], env)
    assert old.returncode == 0 and new.returncode == 0, (old.stderr, new.stderr)
    assert DOCTOR_NOTICE in old.stderr
    assert DOCTOR_NOTICE not in old.stdout and "now python -m homa" not in new.stderr
    assert normalise(old.stdout) == normalise(new.stdout)
    assert old.stdout.splitlines()[0].startswith("homa")


def test_assistant_home_env_still_works_through_the_shim_and_warns_on_stderr(tmp_path):
    env = scratch_env(tmp_path)
    env.pop("HOMA_HOME")
    env["ASSISTANT_HOME"] = str(tmp_path / "legacy-home")
    result = run(["-m", "assistant", "--doctor"], env)
    assert result.returncode == 0
    assert str(tmp_path / "legacy-home") in result.stdout
    assert "deprecated" in result.stderr or DOCTOR_NOTICE in result.stderr


def test_shim_without_stderr_does_not_crash(tmp_path):
    """sys.stderr is None under pythonw: the notice must be skipped, not raise."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import runpy, sys, traceback\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "real_stderr = sys.stderr\n"
        "sys.stderr = None\n"
        "sys.argv = ['assistant', '--doctor']\n"
        "try:\n"
        "    runpy.run_module('assistant', run_name='__main__', alter_sys=True)\n"
        "except SystemExit:\n"
        "    raise\n"
        "except BaseException:\n"
        "    traceback.print_exc(file=real_stderr)\n"
        "    raise\n", encoding="utf-8")
    result = run([str(probe)], scratch_env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0].startswith("homa")
    assert "Traceback" not in result.stderr


def test_shim_under_real_pythonw_runs_init(tmp_path):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pytest.skip("no pythonw.exe next to this interpreter")
    env = scratch_env(tmp_path)
    proc = subprocess.run([str(pythonw), "-m", "assistant", "--init"], cwd=REPO_ROOT, env=env, timeout=60)
    assert proc.returncode == 0
    assert (tmp_path / "home" / "token").is_file(), "pythonw (stderr is None) must get through the shim and run --init"


def test_assistant_mcp_stdio_answers_initialize_with_nothing_else_on_stdout(mcp_live):
    env = dict(os.environ, HOMA_HOME=str(mcp_live.home))
    env.pop("ASSISTANT_HOME", None)
    proc = subprocess.Popen([sys.executable, "-m", "assistant.mcp_stdio"], cwd=REPO_ROOT, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = [], []
    reader = threading.Thread(target=lambda: out.extend(proc.stdout.read().decode("utf-8", "replace").splitlines()),
                              daemon=True)
    err_reader = threading.Thread(target=lambda: err.append(proc.stderr.read().decode("utf-8", "replace")), daemon=True)
    reader.start(), err_reader.start()
    try:
        proc.stdin.write((INIT + "\n").encode("utf-8"))
        proc.stdin.flush()
        import time
        deadline = time.monotonic() + 20
        while not out and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    reader.join(5), err_reader.join(5)
    assert len(out) == 1, out
    reply = json.loads(out[0])
    assert reply["id"] == 1 and reply["result"]["serverInfo"]["name"] == "homa"
    assert STDIO_NOTICE in err[0]
