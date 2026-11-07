"""Path resolution before any check (SEC-4).

``resolve_in_roots(path, roots)`` returns the fully resolved path (junctions, symlinks and ``..``
followed) when it lies inside one of the resolved roots, else ``None``. Callers must apply the
exclude rules and the deny-list to the returned path, never to the path they were given.
Alternate data streams, device paths and embedded NULs are refused.
"""
import ntpath
import os
from typing import Iterable, Optional


def _plain(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\" + path[7:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


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
