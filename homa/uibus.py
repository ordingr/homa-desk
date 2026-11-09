import asyncio
import logging
import queue
from concurrent.futures import Future, InvalidStateError
from typing import Any, Callable

log = logging.getLogger("homa.uibus")

_TICK_MS = 20
_IDLE_MS = 200
_MAX_PER_TICK = 50

_queue: "queue.SimpleQueue[tuple[Callable[..., Any], tuple]]" = queue.SimpleQueue()
_root = None
_running = False
_delay = _TICK_MS


def post(fn: Callable[..., Any], *args: Any) -> None:
    _queue.put((fn, args))


def submit(fn: Callable[[], Any]) -> Future:
    """Run fn on the UI thread; the Future carries its result or exception back to the caller."""
    future: Future = Future()

    def run() -> None:
        if future.done():
            return
        try:
            result = fn()
        except BaseException as exc:
            try:
                future.set_exception(exc)
            except (InvalidStateError, asyncio.InvalidStateError):
                pass
            log.warning("ui call failed: %s", type(exc).__name__)
            return
        try:
            future.set_result(result)
        except (InvalidStateError, asyncio.InvalidStateError):
            pass

    post(run)
    return future


def drain() -> bool:
    """Run up to _MAX_PER_TICK queued callables; True when at least one ran."""
    ran = False
    for _ in range(_MAX_PER_TICK):
        try:
            fn, args = _queue.get_nowait()
        except queue.Empty:
            break
        ran = True
        try:
            fn(*args)
        except Exception:
            log.exception("uibus callable failed")
    return ran


def _tick() -> None:
    global _delay
    if not _running:
        return
    _delay = _TICK_MS if drain() else min(_IDLE_MS, _delay * 2)
    _root.after(_delay, _tick)


def start(root) -> None:
    global _root, _running, _delay
    _root = root
    _running = True
    _delay = _TICK_MS
    root.after(_TICK_MS, _tick)


def stop() -> None:
    global _running
    _running = False
