import os
from pathlib import Path

ENV_VAR = "ASSISTANT_HOME"

_SUBDIRS = ("index", "index/notes", "events", "events/rejected", "plugins",
            "plugins-data", "hooks-backup", "logs")


def home() -> Path:
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".assistant"


def ensure_layout() -> Path:
    root = home()
    root.mkdir(parents=True, exist_ok=True)
    for sub in _SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def settings_file() -> Path:
    return home() / "settings.json"


def token_file() -> Path:
    return home() / "token"


def logs_dir() -> Path:
    return home() / "logs"
