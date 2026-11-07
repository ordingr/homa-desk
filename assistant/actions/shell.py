"""Result actions on files (PRD 6.7, SEC-6, SEC-7).

Every action is vetted first: the path must resolve inside an indexed root, must not be the Assistant's own
data or a root itself (rename, recycle), and must be an index entry. Protected files (secrets deny-list) are the
user's own files, so Open, Open with, Properties, Reveal, Copy path, Rename, Recycle, MD Vault and the folder
listing work on them; Ask Claude never does, and folder listings omit protected children.

The popup calls ``dispatch`` in-process and ``POST /v1/actions/<verb>`` calls the same function, so both
write the same ``action_log``. The only call in this package that removes anything from disk is the
Recycle Bin move in ``_recycle_item``; there is no permanent-delete path.
"""
import builtins
import ctypes
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Optional

from .. import paths
from ..preview import extract
from ..security import denylist, pathcheck
from ..server import mcp as _mcp
from . import claude, names

log = logging.getLogger("assistant.actions.shell")

EXECUTABLE_EXTS = frozenset(
    ".lnk .url .exe .com .scr .bat .cmd .ps1 .vbs .vbe .js .jse .wsf .hta .msi .msp .reg .jar .cpl .pif "
    ".py .pyw .wsh .wsc .sct .vb .ws .psm1 .psd1 .ps1xml .cdxml .inf .ins .isp .mst .shs .shb .application "
    ".appref-ms .appinstaller .diagcab .chm .library-ms .searchconnector-ms .settingcontent-ms .msc .gadget "
    ".ahk .rb .pl .php .sh .command .desklink .mapimail .scf".split()
)
SCRIPT_HOSTS = frozenset(
    "py pyw python pythonw wscript cscript powershell pwsh cmd mshta rundll32".split())
SIZE_WALK_CAP = 200_000
ASK_CLAUDE_BYTES = 32_768
LISTING_DEFAULT_LIMIT = 500
LOG_MAX = 500
VERBS = ("open", "open_with", "properties", "reveal", "copy_path", "copy_path_quoted", "rename", "recycle",
         "md_vault", "folder_listing", "ask_claude")

_SEE_MASK_INVOKEIDLIST = 0x0000000C
_SEE_MASK_FLAG_NO_UI = 0x00000400
_FOF_SILENT, _FOF_ALLOWUNDO, _FOF_NOERRORUI, _FOF_WANTNUKEWARNING = 0x0004, 0x0040, 0x0400, 0x4000
_FO_DELETE = 3
_DRIVE_FIXED = 3
_REPARSE_ATTR = 0x400
_ASSOCSTR_COMMAND = 1
_BITBUCKET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"
_SVSI_SELECT_VISIBLE_FOCUS = 1 | 4 | 8 | 16

_ACTION_LOG: deque = deque(maxlen=LOG_MAX)


def action_log() -> list:
    """Oldest first: {"t", "verb", "path", "result"} per action, for /v1/_debug/ui in test mode."""
    return list(_ACTION_LOG)


def _record(verb: str, path: Any, result: str) -> None:
    _ACTION_LOG.append({"t": time.time(), "verb": verb, "path": str(path)[:1024], "result": result})


def _res(ok: bool, message: str, kind: Optional[str] = None, **extra: Any) -> dict:
    """kind: refused, invalid, not_found, error, not_implemented; absent on success and on needs_confirmation."""
    out: dict = {"ok": ok, "message": message}
    if kind:
        out["kind"] = kind
    out.update(extra)
    return out


class _Stop(Exception):
    def __init__(self, result: dict) -> None:
        super().__init__(result["message"])
        self.result = result


def _vet(index: Any, path: Any, *, protected_ok: bool, not_root: bool = False) -> str:
    """The resolved path, or raises _Stop. Same checks and texts as the MCP `_vet`, deny-list optional."""
    def refuse(text: str) -> _Stop:
        return _Stop(_res(False, text, "refused"))

    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise refuse("refused: not a usable path")
    path = pathcheck._plain(path)
    store, delta = index.current() if index is not None else (None, None)
    if store is None:
        raise refuse("unavailable: the file index is not ready yet")
    roots = [r["path"] for r in index.status().get("roots", [])]
    if not os.path.isabs(path):
        raise refuse("refused: give an absolute path")
    if _mcp._is_unc(path) and not any(_mcp._is_unc(r) for r in roots):
        raise refuse("refused: the path is outside the indexed roots")
    resolved = pathcheck.resolve_in_roots(path, roots)
    if resolved is None:
        raise refuse("refused: the path is outside the indexed roots")
    own = pathcheck._resolve(str(paths.home()))
    if own and pathcheck._inside(resolved, own):
        raise refuse("refused: the Assistant's own data")
    if not protected_ok and (denylist.matches(path) or denylist.matches(resolved)):
        raise refuse("refused: protected file (secrets deny-list)")
    if not os.path.lexists(resolved):
        raise _Stop(_res(False, "That file or folder no longer exists.", "not_found"))
    if not any(_mcp._indexed(store, delta, s) for s in _mcp._root_spellings(resolved, roots)):
        raise refuse("refused: the path is not in the index (excluded or not indexed yet)")
    if not_root:
        for root in roots:
            real = pathcheck._resolve(root)
            if real and os.path.normcase(real).rstrip("\\/") == os.path.normcase(resolved).rstrip("\\/"):
                raise refuse("refused: that is an indexed root folder, not a file inside one")
    return resolved


class _Com:
    def __enter__(self):
        import pythoncom
        self._pc = pythoncom
        pythoncom.CoInitialize()
        return self

    def __exit__(self, *exc):
        self._pc.CoUninitialize()


def _is_dir(path: str) -> bool:
    return os.path.isdir(path)


def _assoc_command(ext: str) -> str:
    buf = ctypes.create_unicode_buffer(2048)
    size = ctypes.c_uint32(len(buf))
    try:
        hr = ctypes.windll.shlwapi.AssocQueryStringW(0, _ASSOCSTR_COMMAND, ext, None, buf, ctypes.byref(size))
    except (OSError, AttributeError):
        return ""
    return buf.value if hr == 0 else ""


def _runs_code(ext: str) -> bool:
    """True for the fixed list, and for any extension whose Open command is a script host."""
    if ext in EXECUTABLE_EXTS:
        return True
    command = _assoc_command(ext).strip()
    if not command:
        return False
    exe = command[1:command.find('"', 1)] if command.startswith('"') and command.find('"', 1) > 0 \
        else command.split(None, 1)[0]
    stem = os.path.splitext(exe.replace("/", "\\").rsplit("\\", 1)[-1])[0].casefold()
    return stem in SCRIPT_HOSTS


def _shell_execute(verb: str, path: str, mask: int) -> None:
    from win32com.shell import shell
    shell.ShellExecuteEx(fMask=mask, hwnd=0, lpVerb=verb, lpFile=path,
                         lpDirectory=path if _is_dir(path) else os.path.dirname(path), nShow=1)


def open(path: str, *, confirm: bool = False, index: Any = None) -> dict:
    resolved = _vet(index, path, protected_ok=True)
    ext = os.path.splitext(resolved)[1].casefold()
    if ext and _runs_code(ext) and not _is_dir(resolved) and not confirm:
        name = os.path.basename(resolved)
        return _res(False, f"{name} is a program or script ({ext}). Opening it runs it. Run it?",
                    needs_confirmation=True)
    with _Com():
        _shell_execute("open", resolved, _SEE_MASK_FLAG_NO_UI)
    return _res(True, "Opened.")


def _openas(path: str) -> None:
    try:
        with _Com():
            _shell_execute("openas", path, 0)
    except Exception as exc:
        log.warning("open with failed (%s)", type(exc).__name__)


def open_with(path: str, *, index: Any = None) -> dict:
    resolved = _vet(index, path, protected_ok=True)
    if _is_dir(resolved):
        return _res(False, "Open with needs a file, not a folder.", "invalid")
    threading.Thread(target=_openas, args=(resolved,), name="open_with", daemon=True).start()
    return _res(True, "Choose an app in the Windows dialog.")


def _pump_properties(path: str) -> None:
    import pythoncom
    import win32gui
    pythoncom.CoInitialize()
    try:
        _shell_execute("properties", path, _SEE_MASK_INVOKEIDLIST)
        title = os.path.basename(path.rstrip("\\/")) + " Properties"
        deadline, seen = time.time() + 600, False
        while time.time() < deadline:
            win32gui.PumpWaitingMessages()
            shown = bool(win32gui.FindWindow("#32770", title))
            seen = seen or shown
            if seen and not shown:
                break
            if not seen and time.time() > deadline - 570:
                break
            time.sleep(0.1)
    except Exception as exc:
        log.warning("properties failed (%s)", type(exc).__name__)
    finally:
        pythoncom.CoUninitialize()


def properties(path: str, *, index: Any = None) -> dict:
    resolved = _vet(index, path, protected_ok=True)
    threading.Thread(target=_pump_properties, args=(resolved,), name="properties", daemon=True).start()
    return _res(True, "Properties window requested.")


def _explorer_windows() -> list:
    import win32com.client
    out = []
    for win in win32com.client.Dispatch("Shell.Application").Windows():
        try:
            if os.path.basename(win.FullName).casefold() != "explorer.exe":
                continue
            out.append((win, win.Document.Folder.Self.Path))
        except Exception:
            continue
    return out


def cabinet_window_count() -> int:
    import win32gui
    found: list = []
    win32gui.EnumWindows(lambda h, _: found.append(h) if win32gui.GetClassName(h) == "CabinetWClass" else None, None)
    return len(found)


def _reuse_window(folder: str, name: str) -> bool:
    import win32gui
    want = os.path.normcase(os.path.normpath(folder))
    for win, shown in _explorer_windows():
        if os.path.normcase(os.path.normpath(shown)) != want:
            continue
        try:
            item = win.Document.Folder.ParseName(name)
            if item is None:
                continue
            win.Document.SelectItem(item, _SVSI_SELECT_VISIBLE_FOCUS)
        except Exception:
            continue
        try:
            win32gui.ShowWindow(win.HWND, 9)
            win32gui.SetForegroundWindow(win.HWND)
        except Exception:
            pass
        return True
    return False


def reveal(path: str, *, index: Any = None) -> dict:
    resolved = _vet(index, path, protected_ok=True)
    folder, name = os.path.split(resolved.rstrip("\\/"))
    if not folder:
        return _res(False, "A drive root cannot be revealed.", "invalid")
    with _Com():
        if _reuse_window(folder, name):
            return _res(True, "Selected in the Explorer window that was already open.")
        from win32com.shell import shell
        import pythoncom
        parent = shell.SHParseDisplayName(folder, 0)[0]
        parent_folder = shell.SHGetDesktopFolder().BindToObject(parent, None, shell.IID_IShellFolder)
        child = parent_folder.ParseDisplayName(0, None, name)[1]
        shell.SHOpenFolderAndSelectItems(parent, [child], 0)
    return _res(True, "Shown in Explorer.")


def _set_clipboard(text: str) -> None:
    import win32clipboard
    import win32con
    for attempt in range(5):
        try:
            win32clipboard.OpenClipboard()
            break
        except Exception:
            if attempt == 4:
                raise
            time.sleep(0.05)
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def copy_path(path: str, *, quoted: bool = False, index: Any = None) -> dict:
    _vet(index, path, protected_ok=True)
    plain = pathcheck._plain(path)
    both = {"plain": plain, "quoted": f'"{plain}"'}
    try:
        _set_clipboard(both["quoted"] if quoted else plain)
    except Exception as exc:
        log.warning("clipboard unavailable (%s)", type(exc).__name__)
        return _res(False, "Windows would not let Assistant use the clipboard; the path is in this result.",
                    "error", **both)
    return _res(True, "Copied the quoted path." if quoted else "Copied the path.", **both)


def _link_or_target(index: Any, path: Any, resolved: str) -> str:
    """The given path when it names a symlink or junction that itself sits inside an indexed root (R9-N8), so
    rename and recycle act on the link the row showed; otherwise the resolved path."""
    given = pathcheck._plain(path)
    if ".." in given.replace("/", "\\").split("\\"):
        return resolved
    try:
        if not getattr(os.lstat(given), "st_file_attributes", 0) & _REPARSE_ATTR:
            return resolved
    except OSError:
        return resolved
    roots = [r["path"] for r in index.status().get("roots", [])] if index is not None else []
    parent = os.path.dirname(given.rstrip("\\/"))
    home = pathcheck._resolve(str(paths.home()))
    real_parent = pathcheck.resolve_in_roots(parent, roots) if parent else None
    if real_parent is None or (home and pathcheck._inside(real_parent, home)):
        return resolved
    if denylist.matches(given):
        return resolved
    return given


def rename(path: str, new_name: Any, *, index: Any = None) -> dict:
    resolved = _vet(index, path, protected_ok=True, not_root=True)
    resolved = _link_or_target(index, path, resolved)
    reason = names.validate(new_name)
    if reason:
        return _res(False, reason, "invalid")
    target = os.path.join(os.path.dirname(resolved.rstrip("\\/")), new_name)
    if os.path.lexists(target) and os.path.normcase(target) != os.path.normcase(resolved):
        return _res(False, "Something with that name already exists in this folder.", "refused")
    if os.path.normcase(os.path.basename(resolved)) == os.path.normcase(new_name) and \
            os.path.basename(resolved) == new_name:
        return _res(True, "The name is unchanged.", path=resolved)
    long = lambda p: "\\\\?\\" + p if len(p) >= 248 and not p.startswith("\\\\") else p
    os.rename(long(resolved), long(target))
    return _res(True, f"Renamed to {new_name}.", path=target)


def _volume_problem(path: str) -> Optional[str]:
    if path.startswith("\\\\"):
        return "it is on a network location"
    drive = os.path.splitdrive(path)[0] + "\\"
    kind = ctypes.windll.kernel32.GetDriveTypeW(drive)
    if kind != _DRIVE_FIXED:
        what = {2: "a removable drive", 4: "a network drive", 5: "an optical drive", 6: "a RAM disk"}.get(
            kind, "a drive of unknown type")
        return f"it is on {what}"
    fs = ctypes.create_unicode_buffer(32)
    if not ctypes.windll.kernel32.GetVolumeInformationW(drive, None, 0, None, None, None, fs, 32):
        return "the drive's file system could not be read"
    if fs.value.upper() not in ("NTFS", "REFS"):
        return f"the drive uses {fs.value or 'an unknown file system'}, not NTFS"
    return None


def _bin_settings(path: str) -> tuple:
    """(MaxCapacity in MB, NukeOnDelete) of the Recycle Bin of the volume holding `path`; None where unreadable."""
    try:
        import winreg
        drive = os.path.splitdrive(path)[0] + "\\"
        buf = ctypes.create_unicode_buffer(64)
        if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(drive, buf, 64):
            return None, None
        guid = buf.value.rstrip("\\").rsplit("\\", 1)[-1][len("Volume"):].lower()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _BITBUCKET_KEY + "\\" + guid) as key:
            values = []
            for name in ("MaxCapacity", "NukeOnDelete"):
                try:
                    values.append(int(winreg.QueryValueEx(key, name)[0]))
                except (OSError, TypeError, ValueError):
                    values.append(None)
            return values[0], values[1]
    except OSError:
        return None, None


def _no_recycle_policy() -> bool:
    """True when group policy (HKCU or HKLM Policies\\Explorer\\NoRecycleFiles) says files are deleted immediately."""
    try:
        import winreg
    except ImportError:
        return False
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer") as key:
                if int(winreg.QueryValueEx(key, "NoRecycleFiles")[0]) == 1:
                    return True
        except (OSError, TypeError, ValueError):
            continue
    return False


def _item_size(path: str) -> tuple:
    """(bytes, complete): file sizes under `path` without following links, stopping after SIZE_WALK_CAP entries."""
    try:
        if not os.path.isdir(path) or os.path.islink(path):
            return os.lstat(path).st_size, True
    except OSError:
        return 0, True
    total, seen, pending = 0, 0, [path]
    while pending:
        try:
            with os.scandir(pending.pop()) as it:
                for entry in it:
                    seen += 1
                    if seen > SIZE_WALK_CAP:
                        return total, False
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if not entry.is_symlink():
                                pending.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total, True


def _bin_problem(path: str) -> Optional[str]:
    """Why the Recycle Bin would delete `path` permanently instead of holding it, or None."""
    if _no_recycle_policy():
        return "a policy on this PC turns the Recycle Bin off"
    capacity_mb, nuke = _bin_settings(path)
    if nuke:
        return "this drive's Recycle Bin is set to delete files immediately"
    if capacity_mb is None:
        return None
    size, complete = _item_size(path)
    if not complete:
        return f"it holds more than {SIZE_WALK_CAP} items, too many to check against the Recycle Bin's {capacity_mb} MB limit"
    if size > capacity_mb * 1_048_576:
        return f"it is {size // 1_048_576} MB and the Recycle Bin of this drive holds at most {capacity_mb} MB"
    return None


def _recycle_item(path: str) -> None:
    from win32com.shell import shell
    flags = _FOF_ALLOWUNDO | _FOF_WANTNUKEWARNING | _FOF_SILENT | _FOF_NOERRORUI
    code, aborted = shell.SHFileOperation((0, _FO_DELETE, path, None, flags, None, None))
    if code or aborted:
        raise OSError(f"the Recycle Bin move did not complete (code {code}, aborted {bool(aborted)})")


def recycle(path: str, *, confirm: bool = False, index: Any = None) -> dict:
    resolved = _vet(index, path, protected_ok=True, not_root=True)
    resolved = _link_or_target(index, path, resolved)
    problem = _volume_problem(resolved)
    if problem:
        return _res(False, f"Not moved: {problem}, so there is no Recycle Bin to hold it. "
                           "Assistant never deletes permanently.", "refused")
    problem = _bin_problem(resolved)
    if problem:
        return _res(False, f"Not moved: {problem}, so it would be deleted permanently; Assistant never does that.",
                    "refused")
    name = os.path.basename(resolved.rstrip("\\/"))
    if not confirm:
        return _res(False, f"Move {name} to the Recycle Bin?", needs_confirmation=True)
    _recycle_item(resolved)
    if os.path.lexists(resolved):
        return _res(False, "The item is still there; it was not moved to the Recycle Bin.", "error")
    return _res(True, f"Moved {name} to the Recycle Bin.")


def md_vault(path: str, *, confirm: bool = False, index: Any = None, settings: Any = None) -> dict:
    resolved = _vet(index, path, protected_ok=True)
    if _is_dir(resolved):
        return _res(False, "MD Vault opens files, not folders.", "invalid")
    cfg = settings.get("md_vault") if settings is not None else None
    url = cfg.get("url") if isinstance(cfg, dict) else None
    if isinstance(url, str) and url.lower().startswith(("http://", "https://")):
        with _Com():
            _shell_execute("open", url, _SEE_MASK_FLAG_NO_UI)
        return _res(True, "Opened MD Vault. It has no open-file link, so pick the note from its folder inside MD Vault.")
    ext = os.path.splitext(resolved)[1].casefold()
    if ext and _runs_code(ext) and not confirm:
        return _res(False, f"{os.path.basename(resolved)} is a program or script ({ext}). MD Vault has no "
                           "open-file link, so it would be run with its default app. Run it?",
                    needs_confirmation=True)
    os.startfile(resolved)
    return _res(True, "MD Vault has no open-file link and none is configured, so the note was opened with its default app.")


def _exclusion_check(index: Any, folder: str):
    """A predicate for scandir entries the index's exclude rules would leave out (node_modules, .git, ...)."""
    roots = [r["path"] for r in index.status().get("roots", [])] if index is not None else []
    root = next((r for r in roots if (real := pathcheck._resolve(r)) and pathcheck._inside(folder, real)), None)
    real_root = pathcheck._resolve(root) if root else None
    excluded = getattr(index, "_excluded", None)
    if real_root is None:
        return lambda entry: False
    if callable(excluded):
        return lambda entry: bool(excluded(real_root, entry.path, entry.is_dir(follow_symlinks=False)))
    from ..index.excludes import DEFAULT_EXCLUDES, Excludes
    rules = Excludes(DEFAULT_EXCLUDES)
    return lambda entry: bool(rules.matching_rule(
        entry.path, os.path.relpath(entry.path, real_root).replace("\\", "/"), entry.name,
        entry.is_dir(follow_symlinks=False)))


def folder_listing(path: str, *, limit: int = LISTING_DEFAULT_LIMIT, index: Any = None) -> dict:
    resolved = _vet(index, path, protected_ok=True)
    folder = resolved if _is_dir(resolved) else os.path.dirname(resolved.rstrip("\\/"))
    limit = max(1, min(int(limit), 5000))
    rows, hidden, total = [], 0, 0
    skip = _exclusion_check(index, folder)
    try:
        with os.scandir(folder) as it:
            entries = list(it)
    except OSError as exc:
        return _res(False, f"The folder could not be read ({type(exc).__name__}).", "error")
    entries = [e for e in entries if not skip(e)]
    for entry in entries:
        if denylist.matches(entry.path):
            hidden += 1
            continue
        total += 1
    entries = [e for e in entries if not denylist.matches(e.path)]
    entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.casefold()))
    for entry in entries[:limit]:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        attrs = getattr(st, "st_file_attributes", 0)
        rows.append({"path": entry.path, "name": entry.name, "size": st.st_size, "mtime": st.st_mtime,
                     "is_dir": bool(attrs & 0x10) or entry.is_dir(follow_symlinks=False),
                     "placeholder": bool(attrs & _mcp._PLACEHOLDER_ATTRS)})
    return _res(True, f"{len(rows)} of {total} items in {folder}", folder=folder, rows=rows, total=total,
                truncated=total > len(rows), hidden_protected=hidden)


_OFFICE_KINDS = {"docx": "docx", "xlsx": "xlsx", "xlsm": "xlsx", "pptx": "pptx"}


def _ask_text(resolved: str, ext: str, whole_file: bool) -> tuple:
    """(text, total_text_bytes, file_bytes, office); raises _Stop on a refusal. Only text ever leaves this function."""
    office = _OFFICE_KINDS.get(ext)
    if ext in _mcp._BINARY_EXTS and not office:
        raise _Stop(_res(False, f"refused: .{ext} is a binary file type, only text can be sent", "refused"))
    cap = claude.STDIN_CAP - 4096 if whole_file else ASK_CLAUDE_BYTES
    try:
        attrs = getattr(os.stat(resolved), "st_file_attributes", 0)
        if attrs & _mcp._PLACEHOLDER_ATTRS:
            raise _Stop(_res(False, "refused: the file is a cloud placeholder that is not downloaded; it is never "
                                    "fetched", "refused"))
        size = os.path.getsize(resolved)
        with builtins.open(resolved, "rb") as fh:
            if office:
                try:
                    text = extract.result_text(getattr(extract, f"extract_{office}")(fh))
                except extract.ExtractError as exc:
                    raise _Stop(_res(False, f"refused: the Office file could not be read as text ({exc})",
                                     "refused"))
                head = text.encode("utf-8")
            else:
                head = fh.read(_mcp.PREVIEW_SCAN_BYTES)
                if _mcp._looks_binary(head):
                    raise _Stop(_res(False, "refused: the file content looks binary, only text can be sent",
                                     "refused"))
                if whole_file and size > len(head):
                    if size > cap:
                        raise _Stop(_res(False, f"refused: the file is {size} bytes, over the {cap} byte limit "
                                                "for sending the whole file", "refused"))
                    head += fh.read(size - len(head))
    except OSError:
        raise _Stop(_res(False, "unavailable: the file could not be read", "error"))
    if denylist.content_has_private_key(head.decode("utf-8", "replace")):
        raise _Stop(_res(False, "refused: the file contains a private key (secrets deny-list)", "refused"))
    total = len(head)
    cut = head[:cap]
    text = cut.decode("utf-8", "ignore" if len(cut) < total else "replace")
    return text, total, size, bool(office)


def ask_claude(path: str, *, confirm: bool = False, index: Any = None, whole_file: bool = False) -> dict:
    resolved = _vet(index, path, protected_ok=False)
    if _is_dir(resolved):
        return _res(False, "refused: that is a folder, not a file", "refused")
    ext = os.path.splitext(resolved)[1].lstrip(".").casefold()
    text, total, size, office = _ask_text(resolved, ext, whole_file)
    send = len(text.encode("utf-8"))
    if send == 0:
        return _res(False, "refused: the file has no text to send", "refused")
    name = os.path.basename(resolved)
    found = claude.status()
    complete = send >= total
    if office:
        what = ("the extracted text; the file itself is not sent" if complete
                else "the first part of the extracted text; the file itself is not sent")
    else:
        what = "the whole file's text" if complete else "the first part of the file's text"
    summary = {"path": resolved, "bytes_to_send": send, "file_bytes": size, "whole_file": complete,
               "what": what, "mode": "whole_file" if complete else "preview_text", "office_text": office,
               "available": found["available"], "reason": found["reason"], "account": claude.account()}
    if not confirm:
        if not found["available"]:
            return _res(False, str(found["reason"]), needs_confirmation=True, summary=summary)
        return _res(False, f"Ask Claude would send {send} bytes from {name} to Claude: {what}"
                           f"{' using your ' + summary['account'] + ' sign-in' if summary['account'] else ''}. "
                           "Nothing is sent until you confirm.", needs_confirmation=True, summary=summary)
    if not found["available"]:
        return _res(False, str(found["reason"]), "unavailable", summary=summary)
    sent = claude.send(resolved, name, text)
    if not sent["ok"]:
        return _res(False, sent["message"], "error", summary=summary)
    return _res(True, f"Claude answered about {name} ({sent['bytes']} bytes sent).", answer=sent["answer"],
                logged=True, summary=summary)


def dispatch(verb: str, path: Any, new_name: Any = None, confirm: bool = False, *, index: Any = None,
             settings: Any = None, limit: int = LISTING_DEFAULT_LIMIT, whole_file: bool = False) -> dict:
    """Run one action; always returns a result dict and appends one action_log line. Raises KeyError on an unknown verb."""
    if verb not in VERBS:
        raise KeyError(verb)
    try:
        if verb == "open":
            result = open(path, confirm=confirm, index=index)
        elif verb == "open_with":
            result = open_with(path, index=index)
        elif verb == "properties":
            result = properties(path, index=index)
        elif verb == "reveal":
            result = reveal(path, index=index)
        elif verb in ("copy_path", "copy_path_quoted"):
            result = copy_path(path, quoted=verb == "copy_path_quoted", index=index)
        elif verb == "rename":
            result = rename(path, new_name, index=index)
        elif verb == "recycle":
            result = recycle(path, confirm=confirm, index=index)
        elif verb == "md_vault":
            result = md_vault(path, confirm=confirm, index=index, settings=settings)
        elif verb == "folder_listing":
            result = folder_listing(path, limit=limit, index=index)
        else:
            result = ask_claude(path, confirm=confirm, index=index, whole_file=whole_file)
    except _Stop as stop:
        result = stop.result
    except Exception as exc:
        detail = getattr(exc, "strerror", None) or getattr(exc, "args", [""])[-1] or type(exc).__name__
        log.warning("action %s failed (%s)", verb, type(exc).__name__)
        result = _res(False, f"The action failed: {detail}", "error")
    outcome = "ok" if result["ok"] else "needs_confirmation" if result.get("needs_confirmation") else \
        result["message"] if result.get("kind") == "refused" else f"{result.get('kind', 'failed')}: {result['message']}"
    _record(verb, path, outcome[:300])
    return result

