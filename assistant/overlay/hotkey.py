"""Global hotkey on its own thread: RegisterHotKey is per-thread, so the thread that registers
the key also runs the GetMessage loop. Never a keyboard hook."""
import logging
import threading
import time
from typing import Callable, Optional

import pywintypes
import win32api
import win32con
import win32gui

log = logging.getLogger("assistant.overlay.hotkey")

ERROR_HOTKEY_ALREADY_REGISTERED = 1409
HOTKEY_ID = 0x4153

REGISTERED = "registered"
TAKEN = "already registered by another app"
UNSUPPORTED = "unsupported combo"
DISABLED = "disabled"

_MODS = {"ctrl": win32con.MOD_CONTROL, "control": win32con.MOD_CONTROL, "alt": win32con.MOD_ALT,
         "shift": win32con.MOD_SHIFT, "win": win32con.MOD_WIN, "super": win32con.MOD_WIN}
_KEYS = {"space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
         "backspace": 0x08, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
         "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}
_RESERVED = {(win32con.MOD_ALT, 0x73), (win32con.MOD_ALT, 0x09), (win32con.MOD_WIN, 0x4C),
             (win32con.MOD_CONTROL | win32con.MOD_ALT, 0x2E), (win32con.MOD_CONTROL, 0x1B)}
_SHELL_CLASSES ={"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def parse_combo(combo: str) -> Optional[tuple]:
    """'ctrl+alt+space' -> (modifier mask, virtual-key code); None when unusable.
    At least one modifier is required: a bare key as a system-wide hotkey would steal typing."""
    if not isinstance(combo, str):
        return None
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods, key = 0, parts[-1]
    for part in parts[:-1]:
        if part not in _MODS:
            return None
        mods |= _MODS[part]
    if key in _KEYS:
        vk = _KEYS[key]
    elif len(key) == 1 and key.isalnum() and key.isascii():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif len(key) == 1 and key.isascii() and key.isprintable():
        scan = win32api.VkKeyScan(key)
        if scan == -1:
            return None
        vk = scan & 0xFF
    else:
        return None
    if mods == win32con.MOD_WIN or (mods, vk) in _RESERVED:
        return None
    return mods, vk


def foreground_is_fullscreen() -> bool:
    """True when the foreground window covers its whole monitor and is not the desktop or taskbar."""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or win32gui.GetClassName(hwnd) in _SHELL_CLASSES:
            return False
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        mon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        ml, mt, mr, mb = win32api.GetMonitorInfo(mon)["Monitor"]
        return l <= ml and t <= mt and r >= mr and b >= mb
    except Exception:
        return False


class Hotkey:
    def __init__(self, combo: str, on_press: Callable[[], None],
                 skip_when: Optional[Callable[[], bool]] = None) -> None:
        """`on_press` runs on the hotkey thread: have it `uibus.post(...)`, never touch tkinter.
        `skip_when()` true drops the press (AC-SH-8 full-screen guard)."""
        self.combo = combo
        self.on_press = on_press
        self.skip_when = skip_when
        self.thread_id: Optional[int] = None
        self.last_press: Optional[float] = None
        self.presses = 0
        self.last_error = 0
        self._status = DISABLED if not combo else UNSUPPORTED
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def status(self) -> str:
        return self._status

    def start(self) -> str:
        with self._lock:
            self._stop_locked()
            return self._start_locked(self.combo)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()
            self._status = DISABLED

    def set_combo(self, combo: str) -> str:
        """Remap: unregister the old key, register the new one; returns the new status."""
        with self._lock:
            self._stop_locked()
            self.combo = combo
            return self._start_locked(combo)

    def _start_locked(self, combo: str) -> str:
        self.last_error = 0
        if not combo:
            self._status = DISABLED
            return self._status
        parsed = parse_combo(combo)
        if parsed is None:
            self._status = UNSUPPORTED
            return self._status
        self._ready.clear()
        self._status = UNSUPPORTED
        self._thread = threading.Thread(target=self._run, args=(parsed,), name="assistant-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return self._status

    def _stop_locked(self) -> None:
        thread, tid = self._thread, self.thread_id
        self._thread = None
        if thread is not None and thread.is_alive() and tid:
            try:
                win32api.PostThreadMessage(tid, win32con.WM_QUIT, 0, 0)
            except pywintypes.error:
                log.warning("could not post WM_QUIT to the hotkey thread")
            thread.join(2.0)
        self.thread_id = None
        if self.combo:
            self._status = UNSUPPORTED

    def _run(self, parsed: tuple) -> None:
        mods, vk = parsed
        win32gui.PeekMessage(0, 0, 0, win32con.PM_NOREMOVE)
        self.thread_id = win32api.GetCurrentThreadId()
        registered = False
        try:
            try:
                win32gui.RegisterHotKey(None, HOTKEY_ID, mods | win32con.MOD_NOREPEAT, vk)
                registered = True
                self._status = REGISTERED
            except pywintypes.error as exc:
                self.last_error = exc.winerror
                self._status = TAKEN if exc.winerror == ERROR_HOTKEY_ALREADY_REGISTERED else UNSUPPORTED
                log.warning("hotkey %r not registered (win32 error %s)", self.combo, exc.winerror)
        finally:
            self._ready.set()
        if not registered:
            return
        try:
            while True:
                rc, msg = win32gui.GetMessage(None, 0, 0)
                if rc <= 0:
                    break
                if msg[1] == win32con.WM_HOTKEY:
                    self._fire()
        finally:
            try:
                win32gui.UnregisterHotKey(None, HOTKEY_ID)
            except pywintypes.error:
                pass

    def _fire(self) -> None:
        self.last_press = time.perf_counter()
        self.presses += 1
        if self.skip_when is not None:
            try:
                if self.skip_when():
                    return
            except Exception:
                log.exception("hotkey skip check failed")
        try:
            self.on_press()
        except Exception:
            log.exception("hotkey callback failed")
