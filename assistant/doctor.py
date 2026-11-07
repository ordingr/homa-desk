import importlib
import importlib.metadata
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from typing import List, Tuple

from . import __version__, hooks_installer, netinfo, paths, settings
from .security import token

_DEPS = ("mcp", "starlette", "uvicorn", "pydantic", "pywin32")
_WHEELS = ("pydantic_core", "cryptography", "rpds", "win32gui")
_NOT_YET = (
    "hotkey",
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
        owner = netinfo.listener_pid(port)
        if owner is None:
            return f"{port} in use"
        image = netinfo.process_image(owner)
        return f"{port} in use by pid {owner}" + (f" ({os.path.basename(image)})" if image else "")
    finally:
        sock.close()


def _claude_version() -> str:
    exe = shutil.which("claude")
    if not exe:
        return "claude not found on PATH"
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return "claude --version failed"
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    if not match:
        return "version not recognised"
    found = tuple(int(g) for g in match.groups())
    shown = ".".join(map(str, found))
    minimum = ".".join(map(str, hooks_installer.MIN_CLAUDE))
    if found >= hooks_installer.MIN_CLAUDE:
        return f"{shown} (meets minimum {minimum})"
    return f"{shown} (below minimum {minimum}, FAIL)"


def _outlook_com() -> str:
    try:
        from .calendar.outlook import OutlookSource
        return "available" if OutlookSource().available() else "unavailable"
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"


def _mcp_mount() -> str:
    try:
        importlib.import_module("assistant.server.mcp")
    except Exception as exc:
        return f"FAIL ({type(exc).__name__})"
    return "mounted at /mcp (static: module imports; the running app serves it behind the token)"


def _calendars_configured() -> str:
    try:
        raw = json.loads(paths.settings_file().read_text(encoding="utf-8"))
        cal = raw.get("calendars") if isinstance(raw, dict) else None
    except (OSError, ValueError):
        cal = None
    cal = cal if isinstance(cal, dict) else {}
    urls = cal.get("ics_urls")
    count = len(urls) if isinstance(urls, list) else 0
    outlook = cal.get("outlook", False) is not False
    return f"{count} ICS feed(s), Outlook {'on' if outlook else 'off'}"


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
        ("hooks installed", hooks_installer.installed()),
        ("Claude Code", _claude_version()),
        ("Outlook COM", _outlook_com()),
        ("MCP", _mcp_mount()),
        ("calendars configured", _calendars_configured()),
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
