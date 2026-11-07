"""Build a directory tree from a JSON spec (used by every file-search test).

Spec shape::

    {"entries": [
        {"path": "docs", "type": "dir"},
        {"path": "docs/a.txt", "type": "file", "size": 120, "mtime": "2024-03-01T10:00:00"},
        {"path": "link", "type": "junction", "target": "docs"},
        {"path": "cloud.docx", "type": "file", "size": 5, "offline": true}
    ]}

Every directory must be listed explicitly, so the number of entries in the spec
is the number of entries a crawl of the destination sees (the root itself is not
an entry). A junction counts as one entry and is never followed.

CLI: ``python -m tests.fixtures.tree <spec.json> <dest>`` prints that count.
"""
import _winapi
import argparse
import ctypes
import json
import os
import sys
from datetime import datetime
from pathlib import Path

FILE_ATTRIBUTE_OFFLINE = 0x1000
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
_kernel32.GetFileAttributesW.restype = ctypes.c_uint32
_kernel32.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
_kernel32.SetFileAttributesW.restype = ctypes.c_int


def _set_offline(path: Path) -> None:
    attrs = _kernel32.GetFileAttributesW(str(path))
    if attrs == INVALID_FILE_ATTRIBUTES:
        raise ctypes.WinError(ctypes.get_last_error())
    if not _kernel32.SetFileAttributesW(str(path), attrs | FILE_ATTRIBUTE_OFFLINE):
        raise ctypes.WinError(ctypes.get_last_error())


def _write_file(path: Path, size: int) -> None:
    with open(path, "wb") as fh:
        if size:
            fh.truncate(size)


def _apply_mtime(path: Path, mtime) -> None:
    ts = datetime.fromisoformat(mtime).timestamp()
    os.utime(path, (ts, ts))


def build_tree(spec, dest) -> int:
    """Create the spec under ``dest`` (created if missing) and return the entry count."""
    if isinstance(spec, (str, os.PathLike)):
        spec = json.loads(Path(spec).read_text(encoding="utf-8"))
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    seen = set()
    junctions = []
    offline = []
    mtimes = []
    for entry in spec["entries"]:
        rel = entry["path"].replace("\\", "/").strip("/")
        if rel in seen:
            raise ValueError(f"duplicate path in spec: {rel}")
        seen.add(rel)
        full = dest / rel
        kind = entry["type"]
        if kind == "dir":
            full.mkdir(parents=True, exist_ok=True)
        elif kind == "file":
            full.parent.mkdir(parents=True, exist_ok=True)
            _write_file(full, int(entry.get("size", 0)))
            if entry.get("offline"):
                offline.append(full)
            if "mtime" in entry:
                mtimes.append((full, entry["mtime"]))
        elif kind == "junction":
            junctions.append((full, dest / entry["target"]))
        else:
            raise ValueError(f"unknown entry type {kind!r} for {rel}")
    for link, target in junctions:
        link.parent.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(target), str(link))
    for path, mtime in mtimes:
        _apply_mtime(path, mtime)
    # OFFLINE is set last: a file with that attribute can no longer be opened for write.
    for path in offline:
        _set_offline(path)
    return len(seen)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.fixtures.tree")
    parser.add_argument("spec")
    parser.add_argument("dest")
    args = parser.parse_args(argv)
    print(build_tree(args.spec, args.dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
