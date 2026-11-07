"""Add / remove Assistant's Claude Code hook entries in the user-level settings file."""
import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from . import paths, settings
from .security import token

EVENTS = ("PermissionRequest", "Notification", "Stop", "StopFailure",
          "UserPromptSubmit", "PostToolUse", "SessionStart", "SessionEnd")
ROUTE = "/v1/hooks/claude-code"
MIN_CLAUDE = (2, 1, 233)
_STATE_NAME = "hooks-install.json"


class HooksError(Exception):
    pass


def settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _state_path() -> Path:
    return paths.home() / _STATE_NAME


def _is_ours(handler) -> bool:
    return (isinstance(handler, dict) and handler.get("type") == "http"
            and isinstance(handler.get("url"), str)
            and handler["url"].startswith("http://127.0.0.1:") and ROUTE in handler["url"])


def build_entries(bearer: str) -> dict:
    url = f"http://127.0.0.1:{settings.peek_port()}{ROUTE}"
    handler = {"type": "http", "url": url,
               "headers": {"Authorization": f"Bearer {bearer}"}, "onFailure": "continue"}
    return {event: [{"hooks": [copy.deepcopy(handler)]}] for event in EVENTS}


def _load(path: Path):
    if not path.exists():
        return None, {}
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8-sig") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise HooksError(f"{path} is not valid JSON; leaving it untouched") from None
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise HooksError(f"{path} has an unexpected shape; leaving it untouched")
    for event, groups in data.get("hooks", {}).items():
        if not isinstance(groups, list):
            raise HooksError(f"{path}: hooks.{event} is not a list; leaving it untouched")
    return raw, data


def _strip_ours(data: dict) -> int:
    removed = 0
    hooks = data.get("hooks", {})
    for event in list(hooks):
        kept_groups = []
        for group in hooks[event]:
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                inner = [h for h in group["hooks"] if not _is_ours(h)]
                removed += len(group["hooks"]) - len(inner)
                if not inner:
                    continue
                group = dict(group, hooks=inner)
            kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if "hooks" in data and not hooks:
        del data["hooks"]
    return removed


def _without_ours(content: bytes) -> bytes:
    try:
        data = json.loads(content.decode("utf-8-sig") or "{}")
        if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
            return content
        if any(not isinstance(g, list) for g in data.get("hooks", {}).values()):
            return content
    except (ValueError, UnicodeDecodeError):
        return content
    return _dump(data) if _strip_ours(data) else content


def _ours_in(data: dict) -> dict:
    found = {}
    for event, groups in data.get("hooks", {}).items():
        for group in groups:
            if isinstance(group, dict):
                for h in group.get("hooks", []) if isinstance(group.get("hooks"), list) else []:
                    if _is_ours(h):
                        found.setdefault(event, []).append(group)
    return found


def carries_bearer(bearer: str) -> bool:
    """True when Assistant's installed hook entries authenticate with `bearer`, i.e. they belong to the
    instance that owns that token. A second instance (another ASSISTANT_HOME, a test run) must not rewrite
    entries that point at someone else's instance."""
    try:
        _, data = _load(settings_path())
    except (HooksError, OSError):
        return False
    want = f"Bearer {bearer}"
    for groups in _ours_in(data).values():
        for group in groups:
            for h in group.get("hooks", []):
                if _is_ours(h) and isinstance(h.get("headers"), dict) and h["headers"].get("Authorization") == want:
                    return True
    return False


def _dump(data: dict) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(content)
    os.replace(tmp, path)


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_state() -> Optional[dict]:
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else None
    except (OSError, ValueError):
        return None


def install(dry_run: bool = False) -> int:
    path = settings_path()
    if dry_run:
        shown = build_entries("<token redacted>")
        print(f"Would merge these entries into {path} (the real token is written at install):")
        print(json.dumps({"hooks": shown}, indent=2))
        return 0

    raw, data = _load(path)
    paths.ensure_layout()
    desired = build_entries(token.ensure())
    existing = _ours_in(data)
    if existing == desired and all(len(v) == 1 for v in existing.values()):
        print(f"already installed in {path}; nothing changed")
        return 0

    _strip_ours(data)
    hooks = data.setdefault("hooks", {})
    for event, groups in desired.items():
        hooks.setdefault(event, []).extend(groups)
    content = _dump(data)

    state = _read_state()
    if raw is not None and state and state.get("sha256") == _sha(raw):
        backup_name, created = state.get("backup"), state.get("created", False)
    else:
        created = raw is None
        backup_name = None
        if raw is not None:
            backup_dir = paths.home() / "hooks-backup"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1e6) % 1_000_000:06d}"
            backup = backup_dir / f"{stamp}.json"
            backup.write_bytes(raw)
            backup_name = backup.name
            print(f"backup: {backup}")
    _atomic_write(path, content)
    _atomic_write(_state_path(), json.dumps(
        {"settings": str(path), "sha256": _sha(content), "backup": backup_name,
         "created": created}, indent=2).encode("utf-8"))
    print(f"installed {len(EVENTS)} hook entries into {path}")
    return 0


def remove() -> int:
    path = settings_path()
    raw, data = _load(path)
    if raw is None:
        print(f"{path} does not exist; nothing to remove")
        return 0
    state = _read_state()
    if state and state.get("sha256") == _sha(raw) and state.get("settings") == str(path):
        backup = paths.home() / "hooks-backup" / str(state.get("backup") or "")
        if state.get("created"):
            path.unlink()
            _drop_state()
            print(f"removed {path} (Assistant created it)")
            return 0
        if state.get("backup") and backup.is_file():
            _atomic_write(path, _without_ours(backup.read_bytes()))
            _drop_state()
            print(f"restored {path} from {backup}")
            return 0
    removed = _strip_ours(data)
    if removed == 0:
        print("no Assistant hook entries found; nothing changed")
        _drop_state()
        return 0
    _atomic_write(path, _dump(data))
    _drop_state()
    print(f"removed {removed} Assistant hook entries from {path}")
    return 0


def _drop_state() -> None:
    try:
        _state_path().unlink()
    except OSError:
        pass


def installed() -> str:
    """Read-only status for --doctor."""
    try:
        _, data = _load(settings_path())
    except HooksError:
        return "settings unreadable"
    found = _ours_in(data)
    if not found:
        return "no"
    missing = [e for e in EVENTS if e not in found]
    return "yes (all 8 events)" if not missing else f"partial (missing {', '.join(missing)})"


def run(args) -> int:
    try:
        if args.action == "install":
            return install(args.dry_run)
        if args.dry_run:
            print("--dry-run is only for install", file=sys.stderr)
            return 2
        return remove()
    except HooksError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"could not update Claude Code settings: {type(exc).__name__}", file=sys.stderr)
        return 1
