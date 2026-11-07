import heapq
import itertools
import logging
import threading
import time
from typing import Callable, Optional

from . import uibus

log = logging.getLogger("assistant.scheduler")

MAX_WAIT_S = 3600.0


class Handle:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class Scheduler:
    def __init__(self) -> None:
        self._heap: list[tuple[float, int, Callable[[], None], Handle]] = []
        self._seq = itertools.count()
        self._cond = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._stopped = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._heap.clear()
            self._cond.notify_all()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None

    def at(self, ts: float, fn: Callable[[], None], ui: bool = False) -> Handle:
        handle = Handle()
        self._push(ts, fn, handle, ui)
        return handle

    def every(self, seconds: float, fn: Callable[[], None], ui: bool = False) -> Handle:
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        handle = Handle()

        def tick() -> None:
            if handle.cancelled:
                return
            self._push(time.time() + seconds, tick, handle, False)
            if ui:
                uibus.post(fn)
            else:
                fn()

        self._push(time.time() + seconds, tick, handle, False)
        return handle

    def _push(self, ts: float, fn: Callable[[], None], handle: Handle, ui: bool) -> None:
        job = (lambda: uibus.post(fn)) if ui else fn
        with self._cond:
            heapq.heappush(self._heap, (ts, next(self._seq), job, handle))
            self._cond.notify_all()

    def _run(self) -> None:
        while True:
            try:
                with self._cond:
                    if self._stopped:
                        return
                    if not self._heap:
                        self._cond.wait()
                        continue
                    delay = self._heap[0][0] - time.time()
                    if delay > 0:
                        self._cond.wait(timeout=min(delay, MAX_WAIT_S))
                        continue
                    _, _, job, handle = heapq.heappop(self._heap)
                if handle.cancelled:
                    continue
                job()
            except Exception:
                log.exception("scheduler loop error")
                time.sleep(0.1)
