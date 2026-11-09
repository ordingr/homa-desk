"""homa.pid is written before the HTTP server starts, so it exists whenever /v1/health answers (plan 19.3 M4-0 item 2).

Before M4-0 ``_write_pid()`` ran after ``server.start()``; a client that saw a healthy server could read a missing pid
file (the old flake). Each cycle starts a private app (``conftest.launch_app``: own home, own port), checks the file the
moment health answers, then shuts the app down and checks the file is gone. The bind-failure case (no pid file left
behind) is ``test_m1_hardening.test_bind_failure_exits_1_and_names_the_port_and_its_owner``.
"""
import os
import shutil

import pytest

from conftest import _free_port, launch_app

from homa import netinfo


def _one_cycle(home, port):
    with launch_app(home, port, test_mode=True) as (base_url, token):
        pid_file = home / "homa.pid"
        assert pid_file.is_file(), "the pid file must exist as soon as /v1/health answers"
        pid_text, port_text = pid_file.read_text(encoding="ascii").split()
        assert int(port_text) == port
        assert netinfo.process_image(int(pid_text)) is not None, f"pid {pid_text} in the pid file is not a live process"
    assert not pid_file.exists(), "a graceful shutdown must remove the pid file"


def test_pid_file_exists_when_health_answers_and_is_removed_on_shutdown(tmp_path):
    for i in range(3):
        _one_cycle(tmp_path / f"home{i}", _free_port())


@pytest.mark.slow
@pytest.mark.timeout(900)
def test_pid_file_is_there_every_time_over_20_start_stop_cycles(tmp_path):
    home = tmp_path / "home"
    port = _free_port()
    for _ in range(20):
        shutil.rmtree(home, ignore_errors=True)
        _one_cycle(home, port)


ORDER_PROBE = r"""
import os, sys
from pathlib import Path
from homa import app as app_mod
pid_file = Path(os.environ["HOMA_HOME"]) / "homa.pid"
seen = {}
def fake_start(self):
    seen["at_start"] = pid_file.is_file()
    seen["content"] = pid_file.read_text(encoding="ascii").split() if pid_file.is_file() else None
    return False
app_mod.ServerThread.start = fake_start
app_mod.App._report_bind_failure = lambda self: None
code = app_mod.App().run()
print("RESULT", code, seen["at_start"], seen["content"] is not None and seen["content"][0] == str(os.getpid()),
      pid_file.is_file())
"""


def test_pid_file_is_written_before_the_server_starts_and_removed_when_the_bind_fails(tmp_path):
    import subprocess
    import sys
    from conftest import REPO_ROOT
    port = _free_port()
    (tmp_path / "settings.json").write_text('{"schema": 1, "port": %d}' % port, encoding="utf-8")
    env = dict(os.environ, HOMA_HOME=str(tmp_path))
    env.pop("HOMA_TEST_MODE", None)
    done = subprocess.run([sys.executable, "-c", ORDER_PROBE], cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                          timeout=60)
    line = next((l for l in done.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert line, (done.stdout[-500:], done.stderr[-800:])
    _, code, at_start, ours, left_behind = line.split()
    assert code == "1"
    assert at_start == "True" and ours == "True", "homa.pid must exist, with this pid, when server.start() is called"
    assert left_behind == "False", "a bind failure must not leave homa.pid behind"
