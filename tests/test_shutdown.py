"""Shutdown: teardown steps run side by side under one shared deadline, the pid file is released, no cancel traceback.

`App._run_steps` is exercised on a bare App (no UI); the end-to-end cases launch private test apps (own home, own port,
throw-away USERPROFILE via conftest.launch_app) with a synthetic index of a few ten thousand files.
"""
import http.client
import os
import threading
import time

import pytest

from homa import app as app_mod
from conftest import Api, _free_port, launch_app, wait_index

FILES = 24_000


def bare_app():
    return app_mod.App.__new__(app_mod.App)


def test_a_stuck_step_is_left_behind_after_the_shared_deadline_and_logged(caplog):
    release = threading.Event()
    ran = []
    steps = [("stuck", lambda: release.wait(60)), ("quick", lambda: ran.append("quick")),
             ("also stuck", lambda: release.wait(60))]
    caplog.set_level("INFO", logger="homa.app")
    started = time.monotonic()
    try:
        bare_app()._run_steps(steps)
        took = time.monotonic() - started
    finally:
        release.set()
    assert app_mod.SHUTDOWN_STEPS_S - 0.3 <= took < app_mod.SHUTDOWN_STEPS_S + 1.0, f"{took:.2f} s (one shared deadline, not one per step)"
    assert ran == ["quick"]
    text = caplog.text
    assert "shutdown step stuck still running" in text and "shutdown step also stuck still running" in text
    assert "shutdown step quick still running" not in text


def test_steps_run_in_parallel_so_slow_but_finishing_steps_cost_the_slowest_not_the_sum():
    done = []
    steps = [(f"s{i}", (lambda i=i: (time.sleep(1.0), done.append(i)))) for i in range(4)]
    started = time.monotonic()
    bare_app()._run_steps(steps)
    assert time.monotonic() - started < 2.0 and sorted(done) == [0, 1, 2, 3]


def test_a_step_that_raises_is_logged_and_does_not_stop_the_others(caplog):
    ran = []

    def boom():
        raise RuntimeError("step blew up")
    caplog.set_level("INFO", logger="homa.app")
    bare_app()._run_steps([("bad", boom), ("good", lambda: ran.append(1))])
    assert ran == [1] and "shutdown step bad failed" in caplog.text


def test_the_hard_exit_is_only_armed_from_main_and_the_cancel_filter_drops_only_cancellations():
    assert app_mod.App.__new__(app_mod.App).__dict__.get("hard_exit") in (None, False)
    import asyncio
    import logging
    flt = app_mod._ShutdownCancelFilter()

    def record(exc):
        try:
            raise exc
        except BaseException:
            import sys
            return logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, "Exception in ASGI application",
                                     None, sys.exc_info())
    assert flt.filter(record(asyncio.CancelledError())) is False
    assert flt.filter(record(RuntimeError("real problem"))) is True
    assert flt.filter(logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, "plain", None, None)) is True


@pytest.fixture(scope="module")
def big_tree(tmp_path_factory):
    root = tmp_path_factory.mktemp("shutdown_tree") / "root"
    for d in range(240):
        folder = root / f"dir{d:03d}"
        folder.mkdir(parents=True)
        for f in range(FILES // 240):
            (folder / f"doc{f:04d}.txt").write_bytes(b"")
    return root


@pytest.mark.timeout(300)
def test_a_busy_app_shuts_down_within_5_seconds_frees_its_pid_file_and_relaunches_at_once(tmp_path, big_tree):
    home, port = tmp_path / "home", _free_port()
    settings = {"index": {"roots": [str(big_tree)]}}
    lingering = None
    with launch_app(home, port, settings, test_mode=True) as (base, token):
        index = wait_index(base, token, minimum=FILES // 2)
        assert isinstance(index, dict) and index["entries"] >= FILES // 2, index
        lingering = http.client.HTTPConnection("127.0.0.1", port, timeout=10)  # an open keep-alive connection
        lingering.request("GET", "/v1/health", headers={"Authorization": f"Bearer {token}"})
        lingering.getresponse().read()
        pid_file = home / "homa.pid"
        assert pid_file.is_file()
        begun = time.monotonic()
        status, _ = Api(base, token).post("/v1/shutdown", {})
        assert status == 202
        deadline = begun + 5.0
        while pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        released = time.monotonic() - begun
        assert not pid_file.exists(), f"pid file still there after {released:.1f} s"
        assert released < 5.0
        from conftest import _health_ok
        gone_at = None
        while time.monotonic() < deadline + 3:
            if not _health_ok(port, token):
                gone_at = time.monotonic() - begun
                break
            time.sleep(0.05)
        assert gone_at is not None and gone_at < 5.0, f"the server still answered {gone_at}"
    lingering.close()
    log = (home / "logs" / "app.log").read_text(encoding="utf-8", errors="replace")
    assert "shutting down" in log
    assert "CancelledError" not in log and "Traceback" not in log, log[-1500:]
    # the same home and the same port start again immediately
    with launch_app(home, port, settings, test_mode=True) as (base2, token2):
        assert Api(base2, token2).get("/v1/health")[0] == 200
        assert (home / "homa.pid").is_file()
    assert not (home / "homa.pid").exists()


def test_a_second_instance_on_a_live_pid_file_still_refuses_to_bind(tmp_path):
    home, port = tmp_path / "home", _free_port()
    with launch_app(home, port, test_mode=True) as (base, token):
        pid_before = (home / "homa.pid").read_text(encoding="ascii")
        import subprocess
        import sys
        from conftest import REPO_ROOT
        env = dict(os.environ, HOMA_HOME=str(home))
        env.pop("HOMA_TEST_MODE", None)
        done = subprocess.run([sys.executable, "-m", "homa"], cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                              timeout=60)
        assert done.returncode == 1, (done.stdout, done.stderr)
        assert (home / "homa.pid").read_text(encoding="ascii") == pid_before, "the live instance keeps its pid file"
        assert Api(base, token).get("/v1/health")[0] == 200
