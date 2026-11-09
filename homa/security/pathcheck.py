"""Path resolution before any check (SEC-4).

``resolve_in_roots(path, roots)`` returns the fully resolved path (junctions, symlinks and ``..``
followed) when it lies inside one of the resolved roots, else ``None``. Callers must apply the
exclude rules and the deny-list to the returned path, never to the path they were given.
Alternate data streams, device paths and embedded NULs are refused.
"""
import ctypes
import msvcrt
import ntpath
import os
from ctypes import wintypes
from typing import Iterable, Optional


def _plain(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\" + path[7:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def final_path(fh) -> Optional[str]:
    """The path Windows resolved an open file object to (junctions and symlinks followed), without the
    extended-length prefix, or ``None`` when it cannot be asked. Compare it with the path that was vetted
    before trusting what was read."""
    try:
        handle = msvcrt.get_osfhandle(fh.fileno())
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(32768)
        n = k32.GetFinalPathNameByHandleW(handle, buf, len(buf), 0)
    except (OSError, ValueError, AttributeError):
        return None
    if n == 0 or n >= len(buf):
        return None
    return _plain(buf.value)


def _resolve(path: str) -> Optional[str]:
    if not isinstance(path, str) or not path or "\x00" in path:
        return None
    try:
        return _plain(os.path.realpath(path))
    except (OSError, ValueError, RuntimeError):
        return None


def _inside(path: str, root: str) -> bool:
    p, r = os.path.normcase(path), os.path.normcase(root).rstrip("\\/")
    return p.rstrip("\\/") == r or p.startswith(r + "\\")


def resolve_in_roots(path: str, roots: Iterable[str]) -> Optional[str]:
    if not isinstance(path, str) or not path or "\x00" in path:
        return None
    if path.startswith(("\\\\.\\", "//./")) or ":" in ntpath.splitdrive(path)[1]:
        return None
    resolved = _resolve(path)
    if resolved is None or ":" in ntpath.splitdrive(resolved)[1]:
        return None
    for root in roots or ():
        real = _resolve(root) if isinstance(root, str) else None
        if real and _inside(resolved, real):
            return resolved
    return None
