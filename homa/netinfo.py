import ctypes
import os
import socket
import struct
from ctypes import wintypes
from typing import Optional

_AF_INET = 2
_TCP_TABLE_OWNER_PID_LISTENER = 3
_NO_ERROR = 0
_ERROR_INSUFFICIENT_BUFFER = 122
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def listener_pid(port: int) -> Optional[int]:
    """Pid owning the IPv4 listening socket on `port` (loopback or wildcard), or None when unknown."""
    if os.name != "nt":
        return None
    try:
        iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
        size = wintypes.DWORD(0)
        for _ in range(4):
            buf = ctypes.create_string_buffer(size.value) if size.value else None
            rc = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, _AF_INET,
                                              _TCP_TABLE_OWNER_PID_LISTENER, 0)
            if rc == _NO_ERROR:
                break
            if rc != _ERROR_INSUFFICIENT_BUFFER:
                return None
        else:
            return None
        raw = buf.raw
        (count,) = struct.unpack_from("<I", raw, 0)
        found = None
        for i in range(count):
            _state, addr, lport, _raddr, _rport, pid = struct.unpack_from("<6I", raw, 4 + 24 * i)
            if socket.ntohs(lport & 0xFFFF) != port:
                continue
            if addr in (0, 0x0100007F):
                if addr == 0x0100007F:
                    return pid
                found = pid
        return found
    except (OSError, AttributeError, struct.error, ValueError):
        return None


def process_image(pid: int) -> Optional[str]:
    """Executable path of a running process, or None when it is gone or not queryable."""
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
                return None
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return None
            return buf.value
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError):
        return None


def message_box(title: str, text: str) -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except (OSError, AttributeError):
        pass
