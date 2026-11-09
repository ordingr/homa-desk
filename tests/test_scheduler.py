"""Scheduler thread (plan 4a, M1-2): at / every / cancel / stop, timing within +-0.5 s."""
import threading
import time

import pytest

scheduler = pytest.importorskip("homa.scheduler", reason="homa/scheduler.py not present")


@pytest.fixture
def sched():
    s = scheduler.Scheduler()
    s.start()
    yield s
    s.stop()


def _wait(event: threading.Event, timeout: float) -> bool:
    return event.wait(timeout)


def test_two_second_job_fires_within_half_a_second(sched):
    done = threading.Event()
    fired_at = []
    t0 = time.time()
    sched.at(t0 + 2.0, lambda: (fired_at.append(time.time()), done.set()))
    assert _wait(done, 4.0)
    assert abs(fired_at[0] - (t0 + 2.0)) <= 0.5, fired_at[0] - (t0 + 2.0)


def test_cancel_prevents_the_job(sched):
    ran = threading.Event()
    handle = sched.at(time.time() + 0.6, ran.set)
    handle.cancel()
    assert not _wait(ran, 1.2)


def test_jobs_run_in_time_order_even_when_added_out_of_order(sched):
    order = []
    done = threading.Event()
    now = time.time()
    sched.at(now + 0.6, lambda: (order.append("c"), done.set()))
    sched.at(now + 0.2, lambda: order.append("a"))
    sched.at(now + 0.4, lambda: order.append("b"))
    assert _wait(done, 3.0)
    assert order == ["a", "b", "c"]


def test_job_in_the_past_runs_promptly(sched):
    ran = threading.Event()
    sched.at(time.time() - 10, ran.set)
    assert _wait(ran, 1.0)


def test_job_added_while_waiting_on_a_far_job_still_fires_on_time(sched):
    sched.at(time.time() + 3600, lambda: None)
    ran = threading.Event()
    sched.at(time.time() + 0.3, ran.set)
    assert _wait(ran, 1.5)


def test_every_repeats_until_cancelled(sched):
    count = []
    handle = sched.every(0.2, lambda: count.append(time.time()))
    time.sleep(1.1)
    handle.cancel()
    n = len(count)
    assert 3 <= n <= 6, n
    time.sleep(0.6)
    assert len(count) <= n + 1  # at most one in-flight tick after cancel


def test_every_rejects_non_positive_interval(sched):
    with pytest.raises(ValueError):
        sched.every(0, lambda: None)


def test_a_raising_job_does_not_kill_the_thread(sched):
    def boom():
        raise RuntimeError("expected by the test")

    ok = threading.Event()
    sched.at(time.time() + 0.1, boom)
    sched.at(time.time() + 0.3, ok.set)
    assert _wait(ok, 2.0)


def test_stop_ends_the_thread_and_drops_pending_jobs():
    s = scheduler.Scheduler()
    s.start()
    ran = threading.Event()
    s.at(time.time() + 0.5, ran.set)
    s.stop()
    assert not _wait(ran, 1.0)
    assert not [t for t in threading.enumerate() if t.name == "scheduler" and t.is_alive()]
