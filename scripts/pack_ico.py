"""Pack PNG frames into a Windows .ico (PNG-compressed entries, no Pillow).

Usage: python pack_ico.py out.ico frame16.png frame24.png ...
"""
import struct
import sys
from pathlib import Path


def png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return struct.unpack(">II", data[16:24])


def pack(out: Path, frames: list[Path]) -> None:
    blobs = [(png_size(d), d) for d in (f.read_bytes() for f in frames)]
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, body = b"", b""
    for (w, h), data in blobs:
        entries += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(data), offset)
        body += data
        offset += len(data)
    out.write_bytes(header + entries + body)


if __name__ == "__main__":
    pack(Path(sys.argv[1]), [Path(a) for a in sys.argv[2:]])
