"""The Assistant -> Homa rename is complete (rename plan 6.2): no stale name outside a justified allowlist, and the
cross-file contracts the rename created (window title, icon, version) hold."""
import glob
import os
import re
import struct
from pathlib import Path

import pytest

from conftest import REPO_ROOT

# plan 6.2 patterns, extended to backtick/single-quoted literals (the minified bundle uses backticks) and to the bare word
STALE = re.compile(r"\bAssistant\b|-m assistant\b|assistant\.mcp_stdio|assistant\.pid|assistant\.ico|ASSISTANT_[A-Z_]+"
                   r"|[\"'`]assistant\.|\.assistant\b|AssistantDisplayWatch|[\"'`]assistant[\"'`]|(?i:\bassistant\b)")

SCANNED = [
    "homa/**/*.py", "homa/static/app/index.html", "homa/static/app/assets/*.js", "web/index.html", "web/src/**/*",
    "web/vite.config.js", "web/package.json", "scripts/**/*", "install.ps1", "uninstall.ps1", "run.cmd", "demo.py",
    "pyproject.toml", "tools/driver.py", ".claude/skills/**/*", "README.md",
]
# Not scanned: assistant/** (the shim), tests/**, docs/**, CHANGELOG.md, tools/probes/**, web/node_modules, .venv.

EVERYTHING = r".+"
# file -> fragments that are removed from a line before it is checked. Each entry says why the old name must stay.
ALLOW = {
    # fallbacks to the pre-rename env prefix and folder (plan 4.2), and the files a migrated folder may contain
    "homa/paths.py": [EVERYTHING],
    # it IS the migration
    "homa/migrate.py": [EVERYTHING],
    # the migration / old-folder report lines (plan 6.2)
    "homa/doctor.py": [r".*(old data folder|migrat).*"],
    # a lingering pre-rename folder is never served, indexed or read (plan 4.6)
    "homa/security/denylist.py": [r'"\.assistant"', r'"appdata/local/assistant"'],
    "homa/index/manager.py": [r"\*\*/\.assistant/"],
    # an update zip never overwrites a lingering pre-rename folder
    "homa/update.py": [r"\.assistant\b"],
    # send/shutdown against an instance that still writes the old pid file
    "homa/send.py": [r'"assistant\.pid"'],
    # on-disk index format magic: changing it would invalidate every existing index file (deliberate)
    "homa/index/store.py": [r'FORMAT_MAGIC = "assistant-index"'],
    # legacy cleanup in the installers
    "install.ps1": [r".*legacy.*"],
    "uninstall.ps1": [r".*legacy.*"],
    # one-release fallback: a saved pre-rename localStorage value is still honoured (LEGACY_*_KEY constants)
    "web/src/App.jsx": [r"LEGACY_THEME_KEY = \"assistant\.theme\""],
    "web/src/components/SplitHandle.jsx": [r"LEGACY_SPLIT_KEY = \"assistant\.split\""],
    "web/src/preview/zoom.js": [r"LEGACY_ZOOM_KEY = \"assistant\.previewZoom\""],
    # the same three keys in the rebuilt bundle (minified: no constant names survive)
    "homa/static/app/assets/*.js": [r"`assistant\.(theme|split|previewZoom)`"],
}

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
README_OK_HEADING = re.compile(r"\(20\d\d-\d\d-\d\d\)|Upgrading from Assistant|\bM[123][ab]?(-\d+)?\b|^Status$", re.IGNORECASE)
# a real deny-list entry in the protected-folders list
README_OK_LINE = re.compile(r"`\.homa`, `\.assistant`")


def _files():
    seen = []
    for pattern in SCANNED:
        for name in glob.glob(str(REPO_ROOT / pattern), recursive=True):
            p = Path(name)
            if p.is_file() and "node_modules" not in p.parts and p not in seen:
                seen.append(p)
    return sorted(seen)


def _allow_for(rel: str):
    out = []
    for key, fragments in ALLOW.items():
        if rel == key or ("*" in key and Path(rel).match(key)):
            out += fragments
    return out


def _hits():
    hits = []
    for path in _files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        fragments = _allow_for(rel)
        heading = ""
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if rel == "README.md":
                m = HEADING.match(line)
                if m:
                    heading = m.group(2)
                if line.lstrip().startswith(">") or README_OK_HEADING.search(heading) or README_OK_LINE.search(line):
                    continue
            checked = line
            if rel == "README.md":
                # link targets are anchors and the real file names of archived docs, not prose a user reads
                checked = re.sub(r"\[docs/[^\]]*\]\(docs/[^)]*\)", "", checked)
                checked = re.sub(r"\]\(#[^)]*\)", "]()", checked)
            for fragment in fragments:
                checked = re.sub(fragment, "", checked)
            if STALE.search(checked):
                hits.append(f"{rel}:{number}: {line.strip()[:140]}")
    return hits


def test_no_stale_name_outside_the_allowlist():
    hits = _hits()
    assert not hits, "stale Assistant names:\n" + "\n".join(hits)


def test_the_scan_really_sees_files_and_catches_a_planted_name(tmp_path, monkeypatch):
    files = [p.relative_to(REPO_ROOT).as_posix() for p in _files()]
    assert "homa/app.py" in files and "README.md" in files and "web/src/App.jsx" in files
    assert any(f.startswith("homa/static/app/assets/") for f in files)
    for planted in ("python -m assistant", 'getLogger("assistant.x")', "set ASSISTANT_HOME=C:\\x", "Assistant starts",
                    "`assistant.zoom`", "~/.assistant/token"):
        assert STALE.search(planted), planted
    assert not STALE.search("last_assistant_message") and not STALE.search("python -m homa")


def test_no_assistant_icon_and_homa_ico_has_the_six_frames():
    assets = REPO_ROOT / "homa" / "assets"
    assert not list(assets.glob("assistant*")), list(assets.glob("assistant*"))
    data = (assets / "homa.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1)
    sizes = sorted((struct.unpack_from("<B", data, 6 + 16 * i)[0] or 256) for i in range(count))
    assert sizes == [16, 24, 32, 48, 64, 256], sizes
    for i in range(count):
        size, offset = struct.unpack_from("<II", data, 6 + 16 * i + 8)
        assert size > 0 and offset + size <= len(data)


def test_window_title_contract_between_python_and_the_web_page():
    from homa.overlay import fullpage
    assert fullpage.WINDOW_TITLE == "Homa - file search"
    for page in (REPO_ROOT / "web" / "index.html", REPO_ROOT / "homa" / "static" / "app" / "index.html"):
        assert f"<title>{fullpage.WINDOW_TITLE}</title>" in page.read_text(encoding="utf-8"), page


def test_packaging_names_and_version():
    import homa
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^name\s*=\s*"homa-desk"', text, re.M) and "homa.__version__" in text
    assert homa.__version__ == "0.2.0"
    assert 'include = ["homa*"]' in text


def test_the_shim_package_is_exactly_three_small_files():
    shim = sorted(p.name for p in (REPO_ROOT / "assistant").rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    assert shim == ["__init__.py", "__main__.py", "mcp_stdio.py"], shim
    for name in shim:
        assert len((REPO_ROOT / "assistant" / name).read_text(encoding="utf-8").splitlines()) <= 15


def test_runtime_never_uses_the_default_urllib_opener_in_send_or_migrate():
    for name in ("send.py", "migrate.py"):
        assert "urlopen" not in (REPO_ROOT / "homa" / name).read_text(encoding="utf-8"), name


def test_no_test_can_reach_the_real_profile_folders():
    """conftest points every variable that selects a user folder at a temp dir before any test runs."""
    import tempfile
    from conftest import REAL_HOME
    temp = Path(tempfile.gettempdir()).resolve()
    for var in ("USERPROFILE", "HOME", "LOCALAPPDATA", "HOMA_HOME"):
        value = os.environ.get(var)
        assert value, var
        assert Path(value).resolve().is_relative_to(temp), (var, value)
    assert Path.home() != REAL_HOME
    assert "ASSISTANT_HOME" not in os.environ
