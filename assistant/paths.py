import logging
import os
import re
from pathlib import Path

ENV_VAR = "ASSISTANT_HOME"

_SUBDIRS = ("index", "index/notes", "events", "events/rejected", "plugins",
            "plugins-data", "hooks-backup", "logs")


def home() -> Path:
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".assistant"


_KNOWN_FILES = re.compile(r"(token|token\.tmp|settings\.json|reminders\.json|console\.txt|state[^/\\]*\.json)",
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
    wlog = logging.getLogger("assistant.paths")
    if root.parent == root or root == Path.home().resolve():
        wlog.warning("refusing to restrict %r: not a dedicated assistant folder", str(root))
        return
    try:
        if root not in _fresh_roots and not _known_layout_only(root):
            wlog.warning("not restricting %r: it contains files that are not the assistant's", str(root))
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
