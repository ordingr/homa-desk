import logging
import queue
from typing import Any, Callable

log = logging.getLogger("assistant.uibus")

_TICK_MS = 20
_MAX_PER_TICK = 50

_queue: "queue.SimpleQueue[tuple[Callable[..., Any], tuple]]" = queue.SimpleQueue()
_root = None
_running = False


def post(fn: Callable[..., Any], *args: Any) -> None:
    _queue.put((fn, args))


def drain() -> None:
    for _ in range(_MAX_PER_TICK):
        try:
            fn, args = _queue.get_nowait()
        except queue.Empty:
            break
        try:
            fn(*args)
        except Exception:
            log.exception("uibus callable failed")


def _tick() -> None:
    if not _running:
        return
    drain()
    _root.after(_TICK_MS, _tick)


def start(root) -> None:
    global _root, _running
    _root = root
    _running = True
    root.after(_TICK_MS, _tick)


def stop() -> None:
    global _running
    _running = False
