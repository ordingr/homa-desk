import contextlib
import io
import logging
import os
import secrets
import time
from dataclasses import dataclass, field

from .. import paths

log = logging.getLogger("assistant.security.token")

_MIN_LEN = 32
REPLACE_BUDGET_S = 5.0
REPLACE_FIRST_DELAY_S = 0.01
REPLACE_MAX_DELAY_S = 0.2


class TokenSecurityError(RuntimeError):
    pass


def _restrict_to_current_user(path) -> None:
    try:
        from . import acl
        acl.restrict_to_current_user(path)
    except Exception as exc:
        raise TokenSecurityError(type(exc).__name__) from None


def _replace(tmp, final) -> None:
    delay = REPLACE_FIRST_DELAY_S
    deadline = time.monotonic() + REPLACE_BUDGET_S
    while True:
        try:
            os.replace(tmp, final)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, REPLACE_MAX_DELAY_S)


def _read_text() -> str:
    """Reads without FILE_SHARE_DELETE being withheld, so an open read never blocks the replace in regenerate()."""
    path = paths.token_file()
    if os.name == "nt":
        try:
            import win32file
        except ImportError:
            win32file = None
        if win32file is not None:
            try:
                handle = win32file.CreateFile(
                    str(path), win32file.GENERIC_READ,
                    win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE | win32file.FILE_SHARE_DELETE,
                    None, win32file.OPEN_EXISTING, 0, None)
            except Exception as exc:
                winerror = getattr(exc, "winerror", None) or (exc.args[0] if getattr(exc, "args", None) else None)
                if winerror in (2, 3):
                    raise FileNotFoundError(winerror, "token file not found") from None
                if winerror in (5, 32):
                    raise PermissionError(winerror, "token file not readable") from None
                raise OSError(str(winerror)) from None
            try:
                _hr, data = win32file.ReadFile(handle, 4096)
            finally:
                handle.Close()
            return bytes(data).decode("ascii")
    return path.read_text(encoding="ascii")


def _write_new(token: str) -> None:
    final = paths.token_file()
    tmp = final.with_name(final.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        _restrict_to_current_user(tmp)
        with open(tmp, "w", encoding="ascii") as fh:
            fh.write(token)
        _replace(tmp, final)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


@dataclass
class Regenerated:
    token: str = field(repr=False)
    hooks: str = "not installed"


def _new_token() -> str:
    token = secrets.token_urlsafe(32)
    _write_new(token)
    return token


def regenerate() -> Regenerated:
    """Replace the token file, then rewrite the Claude Code hook entries if Assistant's are installed
    (they embed the token). `hooks` is "not installed", "updated" or "failed: <reason>"."""
    token = _new_token()
    log.info("token regenerated")
    from .. import hooks_installer
    try:
        state = hooks_installer.installed()
        if state.startswith(("yes", "partial")):
            with contextlib.redirect_stdout(io.StringIO()):
                code = hooks_installer.install()
            return Regenerated(token, "updated" if code == 0 else f"failed: installer exit {code}")
        if state == "settings unreadable":
            return Regenerated(token, "failed: Claude Code settings unreadable")
    except (hooks_installer.HooksError, OSError) as exc:
        log.warning("token regenerated but hook entries were not updated (%s)", type(exc).__name__)
        return Regenerated(token, f"failed: {type(exc).__name__}")
    return Regenerated(token)


def read() -> str:
    token = _read_text().strip()
    if len(token) < _MIN_LEN:
        raise ValueError("token file is malformed")
    return token


def ensure() -> str:
    if paths.token_file().exists():
        try:
            _restrict_to_current_user(paths.token_file())
            return read()
        except TokenSecurityError as exc:
            log.error("could not secure the token file (%s); refusing to start", exc)
            raise SystemExit(1) from None
        except (OSError, ValueError):
            log.warning("token file unreadable or malformed; regenerating")
    try:
        token = _new_token()
    except TokenSecurityError as exc:
        log.error("could not secure the token file (%s); refusing to start", exc)
        raise SystemExit(1) from None
    log.info("token created")
    return token


def present() -> bool:
    return paths.token_file().is_file()
