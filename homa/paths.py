import logging
import os
import re
from pathlib import Path
from typing import Optional

ENV_VAR = "HOMA_HOME"
LEGACY_ENV_PREFIX = "ASSISTANT_"
ENV_PREFIX = "HOMA_"

_warned_legacy: set = set()

_SUBDIRS = ("index", "index/notes", "events", "events/rejected", "plugins",
            "plugins-data", "hooks-backup", "logs")


def env(name: str) -> Optional[str]:
    value = os.environ.get(ENV_PREFIX + name)
    if value:
        return value
    value = os.environ.get(LEGACY_ENV_PREFIX + name)
    if value and name not in _warned_legacy:
        _warned_legacy.add(name)
        logging.getLogger("homa.paths").warning(
            "%s%s is deprecated and will stop working in 0.3.0; use %s%s", LEGACY_ENV_PREFIX, name, ENV_PREFIX, name)
    return value or None


def looks_like_ours(folder: Path) -> bool:
    return (folder / "token").exists() or (folder / "settings.json").exists()


def home() -> Path:
    override = env("HOME")
    if override:
        return Path(override).expanduser().resolve()
    new = Path.home() / ".homa"
    if new.exists():
        return new
    old = Path.home() / ".assistant"
    if old.exists() and looks_like_ours(old):
        return old
    return new


_KNOWN_FILES = re.compile(r"(token|token\.tmp|settings\.json|settings\.json\.tmp|hooks-install\.json|hooks-install\.json\.tmp|assistant\.pid|homa\.pid|migrated-from\.json|migrated-from\.json\.tmp|migration-notice\.json|migration-notice\.json\.tmp|reminders\.json|reminders\.json\.tmp|reminders\.json\.corrupt|console\.txt|state[^/\\]*\.json|state\.json\.tmp|state_ui\.json\.tmp|state\.json\.corrupt)",
                          re.IGNORECASE)
_KNOWN_DIRS = {"logs", "events", "hooks-backup", "index", "plugins", "plugins-data"}
_fresh_roots: set = set()


def _known_layout_only(root: Path) -> bool:
    for entry in root.iterdir():
        name = entry.name.lower()
        if entry.is_dir():
            if name not in _KNOWN_DIRS:
                return False
        elif not _KNOWN_FILES.fullmatch(entry.name):
            return False
    return True


def secure_home() -> None:
    root = home()
    wlog = logging.getLogger("homa.paths")
    if root.parent == root or root == Path.home().resolve():
        wlog.warning("refusing to restrict %r: not a dedicated Homa folder", str(root))
        return
    try:
        if root not in _fresh_roots and not _known_layout_only(root):
            wlog.warning("not restricting %r: it contains files that are not Homa's", str(root))
            return
        from .security import acl
        acl.restrict_to_current_user(root, inherit=True)
    except Exception as exc:
        wlog.warning("could not restrict the home folder to the current user (%s)", type(exc).__name__)


def ensure_layout() -> Path:
    root = home()
    if not root.exists():
        _fresh_roots.add(root)
    root.mkdir(parents=True, exist_ok=True)
    for sub in _SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    secure_home()
    return root


def settings_file() -> Path:
    return home() / "settings.json"


def token_file() -> Path:
    return home() / "token"


def logs_dir() -> Path:
    return home() / "logs"
