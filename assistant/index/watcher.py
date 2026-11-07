"""ReadDirectoryChangesW as a lossy hint stream for one root.

Hints are advisory: a lost or overflowed buffer (zero bytes returned, or
ERROR_NOTIFY_ENUM_DIR) is reported through ``on_overflow`` so the owner can
schedule a re-walk. The watcher never opens a file under the root.
"""
import logging
import os
import threading
from typing import Callable, Optional

import pywintypes
import win32con
import win32event
import win32file

log = logging.getLogger("assistant.index.watcher")

BUFFER_SIZE = 64 * 1024
FILE_LIST_DIRECTORY = 0x1
ERROR_NOTIFY_ENUM_DIR = 1022
RETRY_S = 5.0

ACTION_ADDED = 1
ACTION_REMOVED = 2
ACTION_MODIFIED = 3
ACTION_RENAMED_OLD = 4
ACTION_RENAMED_NEW = 5

NOTIFY_FILTER = (
    win32con.FILE_NOTIFY_CHANGE_FILE_NAME
    | win32con.FILE_NOTIFY_CHANGE_DIR_NAME
    | win32con.FILE_NOTIFY_CHANGE_SIZE
    | win32con.FILE_NOTIFY_CHANGE_LAST_WRITE
    | win32con.FILE_NOTIFY_CHANGE_ATTRIBUTES
)


class Watcher:
    def __init__(self, root: str, on_hint: Callable[[int, str], None],
                 on_overflow: Optional[Callable[[], None]] = None) -> None:
        self.root = os.path.abspath(root)
        self.on_hint = on_hint
        self.on_overflow = on_overflow
        self.overflows = 0
        self.last_error: Optional[str] = None
        self._stop_evt = win32event.CreateEvent(None, True, False, None)
        self._stopped = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="index-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        win32event.SetEvent(self._stop_evt)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)
        self._thread = None

    def _overflow(self) -> None:
        self.overflows += 1
        if self.on_overflow is not None:
            try:
                self.on_overflow()
            except Exception:
                log.exception("overflow callback failed")

    def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                self._watch()
            except pywintypes.error as exc:
                if exc.winerror == ERROR_NOTIFY_ENUM_DIR:
                    self._overflow()
                    continue
                self.last_error = f"{exc.winerror}"
                log.warning("watcher on a root stopped (win32 error %s); retrying", exc.winerror)
                self._overflow()
            except Exception as exc:
                self.last_error = type(exc).__name__
                log.exception("watcher failed")
                self._overflow()
            if self._stopped.wait(RETRY_S):
                return

    def _watch(self) -> None:
        handle = win32file.CreateFile(
            self.root, FILE_LIST_DIRECTORY,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
            None, win32con.OPEN_EXISTING,
            win32con.FILE_FLAG_BACKUP_SEMANTICS | win32con.FILE_FLAG_OVERLAPPED, None)
        done = win32event.CreateEvent(None, True, False, None)
        try:
            while not self._stopped.is_set():
                buf = win32file.AllocateReadBuffer(BUFFER_SIZE)
                ov = pywintypes.OVERLAPPED()
                ov.hEvent = done
                win32event.ResetEvent(done)
                win32file.ReadDirectoryChangesW(handle, buf, True, NOTIFY_FILTER, ov)
                rc = win32event.WaitForMultipleObjects([done, self._stop_evt], False, win32event.INFINITE)
                if rc != win32event.WAIT_OBJECT_0:
                    win32file.CancelIo(handle)
                    return
                try:
                    n = win32file.GetOverlappedResult(handle, ov, True)
                except pywintypes.error as exc:
                    if exc.winerror == ERROR_NOTIFY_ENUM_DIR:
                        self._overflow()
                        continue
                    raise
                if n == 0:
                    self._overflow()
                    continue
                for action, rel in win32file.FILE_NOTIFY_INFORMATION(buf, n):
                    try:
                        self.on_hint(action, os.path.join(self.root, rel))
                    except Exception:
                        log.exception("hint callback failed")
        finally:
            win32file.CloseHandle(handle)
