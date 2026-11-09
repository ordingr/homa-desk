"""Read the Windows clipboard the way File Explorer's Paste does, with plain ctypes (independent of pywin32, which the
product uses to write it): CF_HDROP through DragQueryFileW and the "Preferred DropEffect" DWORD (1 copy, 2 cut).

Also saves and restores the clipboard text, best effort, so a test run leaves the user's text where it was.

``probe()`` returns "" when the clipboard can be opened, else the reason (for pytest.skip). The module also runs as a
script for the browser drivers: ``python -m tests.fixtures.clipboard read|probe|settext <text>`` prints JSON.
"""
from __future__ import annotations

import ctypes
import json
import sys
import time
from ctypes import wintypes

CF_UNICODETEXT, CF_HDROP = 13, 15
GMEM_MOVEABLE = 0x0002

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_sh32 = ctypes.WinDLL("shell32")
_u32.OpenClipboard.argtypes = [wintypes.HWND]
_u32.OpenClipboard.restype = wintypes.BOOL
_u32.CloseClipboard.restype = wintypes.BOOL
_u32.EmptyClipboard.restype = wintypes.BOOL
_u32.GetClipboardData.argtypes = [wintypes.UINT]
_u32.GetClipboardData.restype = wintypes.HANDLE
_u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_u32.SetClipboardData.restype = wintypes.HANDLE
_u32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
_u32.RegisterClipboardFormatW.restype = wintypes.UINT
_u32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_u32.IsClipboardFormatAvailable.restype = wintypes.BOOL
_k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_k32.GlobalSize.argtypes = [wintypes.HGLOBAL]
_k32.GlobalSize.restype = ctypes.c_size_t
_k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_k32.GlobalAlloc.restype = wintypes.HGLOBAL
_sh32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
_sh32.DragQueryFileW.restype = wintypes.UINT


class ClipboardDenied(RuntimeError):
    pass


def _open(tries: int = 20) -> None:
    err = 0
    for _ in range(tries):
        if _u32.OpenClipboard(None):
            return
        err = ctypes.get_last_error()
        time.sleep(0.05)
    raise ClipboardDenied(f"OpenClipboard failed, Windows error {err}"
                          + (" (Access is denied)" if err == 5 else ""))


def probe() -> str:
    try:
        _open(tries=5)
    except ClipboardDenied as exc:
        return str(exc)
    _u32.CloseClipboard()
    return ""


def _text() -> str | None:
    h = _u32.GetClipboardData(CF_UNICODETEXT)
    if not h:
        return None
    p = _k32.GlobalLock(h)
    try:
        return ctypes.wstring_at(p)
    finally:
        _k32.GlobalUnlock(h)


def read() -> dict:
    """{"files": [...], "effect": int | None, "text": str | None, "hdrop_raw": bytes | None}."""
    _open()
    try:
        out: dict = {"files": [], "effect": None, "text": None, "hdrop_raw": None}
        h = _u32.GetClipboardData(CF_HDROP)
        if h:
            n = _sh32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
            for i in range(n):
                size = _sh32.DragQueryFileW(h, i, None, 0) + 1
                buf = ctypes.create_unicode_buffer(size)
                _sh32.DragQueryFileW(h, i, buf, size)
                out["files"].append(buf.value)
            p = _k32.GlobalLock(h)
            try:
                out["hdrop_raw"] = ctypes.string_at(p, _k32.GlobalSize(h))
            finally:
                _k32.GlobalUnlock(h)
        h = _u32.GetClipboardData(_u32.RegisterClipboardFormatW("Preferred DropEffect"))
        if h:
            p = _k32.GlobalLock(h)
            try:
                out["effect"] = ctypes.c_uint32.from_address(p).value
            finally:
                _k32.GlobalUnlock(h)
        out["text"] = _text()
        return out
    finally:
        _u32.CloseClipboard()


def save_text() -> str | None:
    _open()
    try:
        return _text()
    finally:
        _u32.CloseClipboard()


def set_text(value: str | None) -> None:
    """Replace the clipboard with `value` as text (None: leave it empty)."""
    _open()
    try:
        _u32.EmptyClipboard()
        if value is None:
            return
        data = (value + "\0").encode("utf-16-le")
        h = _k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        p = _k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        _k32.GlobalUnlock(h)
        _u32.SetClipboardData(CF_UNICODETEXT, h)
    finally:
        _u32.CloseClipboard()


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "read"
    try:
        if action == "probe":
            print(json.dumps({"denied": probe()}))
        elif action == "settext":
            set_text(sys.argv[2])
            print(json.dumps({"ok": True}))
        else:
            got = read()
            got.pop("hdrop_raw", None)
            print(json.dumps(got))
    except ClipboardDenied as exc:
        print(json.dumps({"error": str(exc)}))
