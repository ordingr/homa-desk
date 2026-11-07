import importlib
import importlib.metadata
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from typing import List, Tuple

from . import __version__, hooks_installer, netinfo, paths, settings
from .security import token

_DEPS = ("mcp", "starlette", "uvicorn", "pydantic", "pywin32")
_WHEELS = ("pydantic_core", "cryptography", "rpds", "win32gui")
_NOT_YET = (
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


def _ask_claude_state() -> str:
    try:
        from .actions import claude
        status = claude.status()
    except ImportError:
        return "not in this build"
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"
    if not isinstance(status, dict):
        return "unavailable"
    if status.get("available"):
        return f"available, claude {status.get('version') or 'version unknown'}"
    return str(status.get("reason") or "not available")


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


def _settings_raw() -> dict:
    try:
        raw = json.loads(paths.settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _age(stamp: float) -> str:
    seconds = max(0, int(time.time() - stamp))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    if seconds < 172800:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} days"


def _hotkey_state() -> str:
    combo = _settings_raw().get("hotkey", "ctrl+alt+space")
    if not isinstance(combo, str) or not combo.strip():
        return "disabled (hotkey setting is empty)"
    try:
        from .overlay import hotkey
        probe = hotkey.Hotkey(combo.strip(), lambda: None)
        quiet = logging.getLogger(hotkey.log.name)
        previous, quiet.level = quiet.level, logging.CRITICAL
        try:
            status = probe.start()
        finally:
            quiet.level = previous
        probe.stop()
    except Exception as exc:
        return f"{combo} unavailable ({type(exc).__name__})"
    if status == hotkey.TAKEN and netinfo.listener_pid(settings.peek_port()) is not None:
        status += " (the running Assistant may be the holder)"
    return f"{combo}: {status}"


def _index_roots() -> str:
    index = _settings_raw().get("index")
    roots = index.get("roots") if isinstance(index, dict) else None
    roots = [r for r in roots if isinstance(r, str)] if isinstance(roots, list) else []
    return f"{len(roots)}: " + "; ".join(roots) if roots else "0 configured"


def _index_state() -> str:
    try:
        from .app import index_store_path
        from .index.store import IndexStore
        path = index_store_path()
        if not path.is_file():
            return "no index file yet"
        store = IndexStore.load(path)
        return (f"{store.entry_count:,} entries, built {_age(store.built_at)} ago, "
                f"file {path.stat().st_size / 1_048_576:.1f} MB")
    except Exception as exc:
        return f"index file unreadable ({type(exc).__name__})"


def _notes_state() -> str:
    try:
        from .search import notes_bm25
        folder = os.environ.get(notes_bm25.ENV_NOTES_DIR, "").strip()
        source = "ASSISTANT_NOTES_DIR"
        if not folder:
            notes = _settings_raw().get("notes")
            folder = notes.get("dir", "") if isinstance(notes, dict) else ""
            folder = folder.strip() if isinstance(folder, str) else ""
            source = "settings"
        if not folder:
            return "no notes folder set"
        state = f"{folder} ({source}), " + ("folder present" if os.path.isdir(folder) else "folder missing")
        saved = notes_bm25.default_index_path()
        if saved.is_file():
            state += f", index file {_age(saved.stat().st_mtime)} old"
        else:
            state += ", no index file yet"
        return state
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"


def _edge_state() -> str:
    try:
        from .overlay import fullpage
        path = fullpage.edge_path()
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"
    return f"found at {path}" if path else "msedge.exe not found (the full page cannot open)"


def _static_state() -> str:
    try:
        from .server import static
        folder = static.static_dir()
        source = f"{static.ENV_VAR} override" if os.environ.get(static.ENV_VAR) else "packaged"
        return f"{folder} ({source}), " + ("index.html present" if (folder / "index.html").is_file()
                                          else "index.html MISSING")
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"


def _recycle_bin_state() -> str:
    try:
        from .actions import shell
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        capacity, nuke = shell._bin_settings(drive)
        policy = shell._no_recycle_policy()
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"
    if capacity is None and nuke is None:
        state = f"{drive} has no per-volume settings key (never opened its Recycle Bin properties); size limit not checked"
    else:
        state = (f"{drive} MaxCapacity {capacity if capacity is not None else 'unset'} MB, "
                 f"NukeOnDelete {nuke if nuke is not None else 'unset'}")
    return state + (", NoRecycleFiles policy ON" if policy else "")


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
        ("Ask Claude", _ask_claude_state()),
        ("Outlook COM", _outlook_com()),
        ("MCP", _mcp_mount()),
        ("calendars configured", _calendars_configured()),
        ("hotkey", _hotkey_state()),
        ("index size and age", _index_state()),
        ("index roots", _index_roots()),
        ("notes index", _notes_state()),
        ("Edge (full page)", _edge_state()),
        ("static folder", _static_state()),
        ("Recycle Bin", _recycle_bin_state()),
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
