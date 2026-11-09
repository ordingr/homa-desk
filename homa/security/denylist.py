"""Secrets deny-list (SEC-3).

``matches(path)`` decides from the path text alone (the file is never opened, so it works on
paths that do not exist yet). Matching is case-insensitive and separator-agnostic. Name patterns
look at the leaf only; path patterns look at trailing segments (``.aws/credentials``) or at a
directory anywhere in the path (browser profiles, password-manager data).

``content_has_private_key(text)`` is the content check for preview text.

Callers: the index flags matching entries ``FLAG_DENIED`` at crawl time. Only the local search UI
may list a denied entry by name (setting ``index.show_protected_by_name``); previews, MCP results
and Ask Claude must never honour that setting.
"""
import fnmatch
import re
from typing import Any

NAME_PATTERNS = (
    ".env*", "*.pem", "*.key", "*.pfx", "*.p12", "*.ppk", "*.kdbx",
    "id_rsa*", "id_ed25519*",
    ".git-credentials", ".netrc", ".npmrc", ".pypirc",
    "kubeconfig", "*.kubeconfig",
    "id_ecdsa*", "id_dsa*", "*.asc", "*.p8", "*.jks", ".pgpass", ".htpasswd",
    ".claude.json", "*.keystore",
)

TAIL_PATTERNS = (
    ".aws/credentials",
    ".docker/config.json",
    ".kube/config",
    ".claude/settings.json",
    ".claude/settings.local.json",
    "appdata/roaming/claude/claude_desktop_config.json",
)

DIR_PATTERNS = (
    ".homa",
    ".assistant",
    ".ssh",
    ".gnupg",
    ".azure",
    ".config/gh",
    "appdata/local/google/chrome/user data",
    "appdata/local/microsoft/edge/user data",
    "appdata/local/homa",
    "appdata/local/assistant",
    "appdata/local/bravesoftware/brave-browser/user data",
    "appdata/local/vivaldi/user data",
    "appdata/roaming/opera software",
    "appdata/roaming/mozilla/firefox/profiles",
    "appdata/local/mozilla/firefox/profiles",
    "appdata/roaming/keepass",
    "appdata/roaming/keepassxc",
    "appdata/local/keepassxc",
    "appdata/roaming/bitwarden",
    "appdata/local/bitwarden",
    "appdata/roaming/1password",
    "appdata/local/1password",
    "appdata/roaming/lastpass",
    "appdata/local/lastpass",
    "appdata/roaming/dashlane",
    "appdata/local/dashlane",
    "appdata/roaming/enpass",
    "appdata/local/enpass",
    "appdata/roaming/nordpass",
    "appdata/local/nordpass",
    "appdata/roaming/keeper",
    "appdata/local/keeper",
)

_NAME_RE = re.compile("|".join(fnmatch.translate(p) for p in NAME_PATTERNS))
_TAIL = tuple("/" + p for p in TAIL_PATTERNS)
_DIRS = tuple("/" + p + "/" for p in DIR_PATTERNS)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")


def matches(path: Any) -> bool:
    if not isinstance(path, str) or not path:
        return False
    norm = path.replace("\\", "/").casefold().rstrip("/")
    if not norm:
        return False
    leaf = norm.rsplit("/", 1)[-1]
    if _NAME_RE.match(leaf):
        return True
    rooted = "/" + norm
    if rooted.endswith(_TAIL):
        return True
    probe = rooted + "/"
    return any(d in probe for d in _DIRS)


def content_has_private_key(text: Any) -> bool:
    return isinstance(text, str) and bool(_PRIVATE_KEY.search(text))
