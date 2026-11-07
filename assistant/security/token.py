import logging
import os
import secrets
import subprocess

from .. import paths

log = logging.getLogger("assistant.security.token")

_MIN_LEN = 32


def _current_sid() -> str:
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True,
                         text=True, check=True, timeout=15).stdout.strip()
    sid = out.rsplit(",", 1)[-1].strip().strip('"')
    if not sid.startswith("S-1-"):
        raise RuntimeError("could not determine the current user SID")
    return sid


def _restrict_to_current_user(path) -> None:
    sid = _current_sid()
    subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
                   capture_output=True, text=True, check=True, timeout=15)


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
        os.replace(tmp, final)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def regenerate() -> str:
    token = secrets.token_urlsafe(32)
    _write_new(token)
    log.info("token regenerated")
    return token


def read() -> str:
    token = paths.token_file().read_text(encoding="ascii").strip()
    if len(token) < _MIN_LEN:
        raise ValueError("token file is malformed")
    return token


def ensure() -> str:
    if paths.token_file().exists():
        try:
            return read()
        except (OSError, ValueError):
            log.warning("token file unreadable or malformed; regenerating")
    token = regenerate()
    log.info("token created")
    return token


def present() -> bool:
    return paths.token_file().is_file()
