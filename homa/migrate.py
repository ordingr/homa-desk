import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from . import __version__, netinfo, paths

log = logging.getLogger("homa.migrate")

MARKER_NAME = "migrated-from.json"
NOTICE_NAME = "migration-notice.json"
OLD_PID_NAME = "assistant.pid"
OLD_LNK = "Assistant.lnk"
NEW_LNK = "Homa.lnk"
SHORTCUT_DIRS_ENV = "HOMA_MIGRATE_SHORTCUT_DIRS"
STOP_TIMEOUT_S = 15.0

_CSIDL_DESKTOPDIRECTORY = 0x0010
_CSIDL_STARTUP = 0x0007



class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def old_home() -> Path:
    return Path.home() / ".assistant"


def new_home() -> Path:
    return Path.home() / ".homa"


def explicit_home() -> bool:
    return bool(os.environ.get("HOMA_HOME") or os.environ.get("ASSISTANT_HOME"))


def local_appdata() -> Optional[Path]:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) if base else None


def shortcut_dirs() -> List[Path]:
    """Desktop and Startup folders, following known-folder redirection (a OneDrive-redirected Desktop).
    HOMA_MIGRATE_SHORTCUT_DIRS (os.pathsep-separated) replaces them for tests."""
    override = os.environ.get(SHORTCUT_DIRS_ENV)
    if override:
        return [Path(p) for p in override.split(os.pathsep) if p]
    found: List[Path] = []
    try:
        from win32com.shell import shell
        for csidl in (_CSIDL_DESKTOPDIRECTORY, _CSIDL_STARTUP):
            folder = shell.SHGetFolderPath(0, csidl, 0, 0)
            if folder:
                found.append(Path(folder))
    except Exception as exc:
        log.warning("could not look up the shortcut folders (%s)", type(exc).__name__)
    return found


def _dir_size(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def _both_text() -> str:
    return (r"both %USERPROFILE%\.homa and %USERPROFILE%\.assistant exist and were not merged; Homa uses .homa. "
            "If settings or the index are missing, stop Homa, rename .homa away, start again.")


def status() -> Tuple[str, Optional[str]]:
    """Read-only, for --doctor: (migration line, old data folder line or None)."""
    if explicit_home():
        return "not applicable (HOMA_HOME or ASSISTANT_HOME is set; an explicit home is never migrated)", None
    old, new = old_home(), new_home()
    marker = new / MARKER_NAME
    if not old.exists():
        if marker.is_file():
            try:
                info = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return "done (marker unreadable)", None
            if not isinstance(info, dict):
                return "done (marker unreadable)", None
            src, when = info.get("from"), info.get("when")
            src = src if isinstance(src, str) else "the old folder"
            when = when if isinstance(when, str) else "an unknown date"
            return f"done: moved from {src} on {when}", None
        return "none needed", None
    if not paths.looks_like_ours(old):
        return (r"not applicable: %USERPROFILE%\.assistant has no token or settings.json, so it is not "
                "Assistant's data; left alone"), None
    if not new.exists():
        return r"pending: %USERPROFILE%\.assistant moves to .homa when Homa next starts", None
    if marker.is_file():
        megabytes = _dir_size(old) / 1_048_576
        return "done", (r"%USERPROFILE%\.assistant still present " f"({megabytes:.1f} MB); Homa does not read it; "
                        "delete it when you are sure")
    return "not done: both folders exist", _both_text()


def _report(text: str) -> None:
    log.error(text)
    if sys.stderr is not None:
        print(text, file=sys.stderr, flush=True)
    else:
        netinfo.message_box("Homa", text)


def _atomic_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_old_instance(old: Path) -> Optional[Tuple[int, int]]:
    try:
        fields = (old / OLD_PID_NAME).read_text(encoding="ascii").split()
        return int(fields[0]), int(fields[1])
    except (OSError, ValueError, IndexError):
        return None


def _running_old_instance(old: Path) -> Optional[Tuple[int, int]]:
    recorded = _read_old_instance(old)
    if recorded is None:
        return None
    pid, port = recorded
    image = netinfo.process_image(pid)
    if image is None or not os.path.basename(image).lower().startswith("python"):
        return None
    if netinfo.listener_pid(port) != pid:
        return None
    return pid, port


def _request_shutdown(old: Path, port: int) -> None:
    try:
        tok = (old / "token").read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        return
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/shutdown", data=b"{}", method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    try:
        with _opener.open(request, timeout=5.0) as resp:
            resp.read()
    except (urllib.error.URLError, OSError, ValueError):
        pass


def _stop_old_instance(old: Path) -> Optional[int]:
    """None when nothing is running or it stopped; else the pid that would not stop."""
    running = _running_old_instance(old)
    if running is None:
        return None
    pid, port = running
    log.info("asking the old instance (pid %d, port %d) to shut down", pid, port)
    _request_shutdown(old, port)
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while time.monotonic() < deadline:
        if netinfo.listener_pid(port) != pid and netinfo.process_image(pid) is None:
            return None
        time.sleep(0.25)
    return pid


def _move_edge_profile() -> str:
    base = local_appdata()
    if base is None:
        return "absent"
    parent = base / "Assistant"
    src, dst = parent / "edge-profile", base / "Homa" / "edge-profile"
    if not src.exists():
        return "absent"
    if dst.exists():
        return "skipped: destination exists"
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)
    except OSError as exc:
        return f"skipped: {type(exc).__name__}"
    try:
        parent.rmdir()
    except OSError:
        pass
    return "moved"


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _rewrite_shortcuts() -> List[str]:
    from . import update
    root = _norm(str(update.install_root()))
    done: List[str] = []
    dirs = [d for d in shortcut_dirs() if (d / OLD_LNK).is_file()]
    if not dirs:
        return done
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
    except Exception as exc:
        log.warning("could not open WScript.Shell (%s); shortcuts left as they are", type(exc).__name__)
        return done
    for folder in dirs:
        old_lnk, new_lnk = folder / OLD_LNK, folder / NEW_LNK
        try:
            link = shell.CreateShortCut(str(old_lnk))
            parts = (link.Arguments or "").split(None, 2)
            if parts[:2] != ["-m", "assistant"] or _norm(link.WorkingDirectory or "") != root:
                continue
            extra = f" {parts[2]}" if len(parts) > 2 else ""
            target, workdir = link.TargetPath, link.WorkingDirectory
            fresh = shell.CreateShortCut(str(new_lnk))
            fresh.TargetPath = target
            fresh.WorkingDirectory = workdir
            fresh.Arguments = "-m homa" + extra
            fresh.IconLocation = str(Path(workdir) / "homa" / "assets" / "homa.ico") + ",0"
            fresh.Description = "Homa"
            fresh.Save()
            old_lnk.unlink()
            done.append(f"{old_lnk} -> {new_lnk}")
        except Exception as exc:
            log.warning("could not rewrite %s (%s)", old_lnk, type(exc).__name__)
    return done


def _write_notice(new: Path, title: str, detail: str) -> None:
    try:
        _atomic_json(new / NOTICE_NAME, {"shown": False, "title": title, "detail": detail})
    except OSError:
        log.warning("could not write the migration notice")


def consume_notice() -> Optional[dict]:
    """Once per migration outcome: the card text to show at the first start, or None. Marks it shown."""
    path = paths.home() / NOTICE_NAME
    try:
        notice = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(notice, dict) or notice.get("shown") is not False:
        return None
    try:
        _atomic_json(path, dict(notice, shown=True))
    except OSError:
        return None
    title, detail = notice.get("title"), notice.get("detail")
    if not isinstance(title, str) or not isinstance(detail, str):
        return None
    return {"title": title, "detail": detail}


def run_once() -> Optional[int]:
    """Move %USERPROFILE%\\.assistant to .homa when that is pending. None: carry on starting. int: exit code."""
    if explicit_home():
        return None
    old, new = old_home(), new_home()
    if not old.exists():
        return None
    if not paths.looks_like_ours(old):
        log.warning("%s has no token or settings.json; not Assistant's data, leaving it alone", old)
        return None
    marker = new / MARKER_NAME
    if new.exists():
        if not marker.is_file() and not (new / NOTICE_NAME).exists():
            _write_notice(new, "Assistant data folder was not merged", _both_text())
        return None

    stuck = _stop_old_instance(old)
    if stuck is not None:
        _report(f"Homa could not stop the running Assistant (pid {stuck}). "
                "Close it from its icon menu and start Homa again.")
        return 1
    try:
        os.rename(old, new)
    except OSError as exc:
        _report(f"Homa could not move {old} to {new} ({type(exc).__name__}). Close any window or program "
                "that has that folder open, then start Homa again. Nothing was changed.")
        return 1
    log.info("moved %s to %s", old, new)

    try:
        (new / OLD_PID_NAME).unlink()
    except OSError:
        pass
    edge = _move_edge_profile()
    shortcuts = _rewrite_shortcuts()
    try:
        _atomic_json(marker, {"from": str(old), "when": datetime.now().astimezone().isoformat(timespec="seconds"),
                              "homa_version": __version__, "edge_profile": edge, "shortcuts": shortcuts})
    except OSError:
        log.warning("could not write %s", marker)
    kept = "moved" if edge == "moved" else "kept at old location" if edge.startswith("skipped") else "none to move"
    _write_notice(new, "Assistant is now Homa",
                  rf"Your data moved to %USERPROFILE%\.homa. Shortcuts: {len(shortcuts)} updated. Edge profile: {kept}.")
    return None
