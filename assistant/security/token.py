import logging
import os
import secrets

from .. import paths

log = logging.getLogger("assistant.security.token")

_MIN_LEN = 32


class TokenSecurityError(RuntimeError):
    pass


def _restrict_to_current_user(path) -> None:
    try:
        from . import acl
        acl.restrict_to_current_user(path)
    except Exception as exc:
        raise TokenSecurityError(type(exc).__name__) from None


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
            _restrict_to_current_user(paths.token_file())
            return read()
        except TokenSecurityError as exc:
            log.error("could not secure the token file (%s); refusing to start", exc)
            raise SystemExit(1) from None
        except (OSError, ValueError):
            log.warning("token file unreadable or malformed; regenerating")
    try:
        token = regenerate()
    except TokenSecurityError as exc:
        log.error("could not secure the token file (%s); refusing to start", exc)
        raise SystemExit(1) from None
    log.info("token created")
    return token


def present() -> bool:
    return paths.token_file().is_file()
