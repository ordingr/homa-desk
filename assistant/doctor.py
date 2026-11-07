import importlib
import importlib.metadata
import socket
import sys
from typing import List, Tuple

from . import __version__, paths, settings
from .security import token

_DEPS = ("mcp", "starlette", "uvicorn", "pydantic", "pywin32")
_WHEELS = ("pydantic_core", "cryptography", "rpds", "win32gui")
_NOT_YET = (
    "hotkey",
    "hooks installed / Claude Code version",
    "Outlook COM",
    "index size and age",
    "plugins",
)


def _dep_versions() -> str:
    parts = []
    for name in _DEPS:
        try:
            parts.append(f"{name} {importlib.metadata.version(name)}")
        except importlib.metadata.PackageNotFoundError:
            parts.append(f"{name} MISSING")
    return ", ".join(parts)


def _wheels() -> str:
    parts = []
    for name in _WHEELS:
        try:
            importlib.import_module(name)
            parts.append(f"{name} ok")
        except Exception as exc:
            parts.append(f"{name} FAIL ({type(exc).__name__})")
    return ", ".join(parts)


def _port_state(port: int) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return f"{port} free"
    except OSError:
        return f"{port} in use"
    finally:
        sock.close()


def report() -> List[Tuple[str, str]]:
    version = sys.version_info
    lines = [
        ("assistant", __version__),
        ("home", str(paths.home())),
        ("python", f"{version.major}.{version.minor}.{version.micro}"
                   + ("" if version >= (3, 10) else " (3.10+ required)")),
        ("dependencies", _dep_versions()),
        ("compiled wheels", _wheels()),
        ("port", _port_state(settings.peek_port())),
        ("token file", "present" if token.present() else "missing"),
    ]
    lines.extend((name, "n/a") for name in _NOT_YET)
    return lines


def run() -> int:
    lines = report()
    width = max(len(k) for k, _ in lines)
    for key, value in lines:
        print(f"{key.ljust(width)}  {value}")
    failed = any("MISSING" in v or "FAIL" in v or "required" in v for _, v in lines)
    return 1 if failed else 0
