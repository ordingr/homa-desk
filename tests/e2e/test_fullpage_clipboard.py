"""Full page: popup handoff selection, Ctrl+A, Copy / Cut of files, Copy path (plan 2026-11-09
popup-to-fullpage-and-file-clipboard, section 4.1 behaviours 7-9; AC-H2, AC-M2, AC-C1, AC-C2, AC-C3).

Headless Edge (playwright-core, never a visible window) drives the COMMITTED build under ``homa/static/app`` against a
private test-mode app (scratch HOMA_HOME / USERPROFILE / LOCALAPPDATA / APPDATA, free port) whose root holds 160
``clipdemo NNN.txt`` files and a deny-listed ``.env``. ``clip_driver.cjs`` holds the checks; this file runs it once, turns
every PASS/FAIL line into an assertion and reports BLOCKED clipboard read-backs as a skip with the reason. The exchange
is mocked to return the real token plus a handoff (the real nonce path is ``test_launch_handoff.py``); every
``/v1/files/clipboard`` POST reaches the real server, which writes the real clipboard. The clipboard text present before
the run is put back afterwards (best effort).

Needs node, msedge and playwright-core (HOMA_QA_PLAYWRIGHT_DIR, ``web/node_modules`` or ``~/node_modules``).
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REAL_HOME, REPO_ROOT, _free_port, browser_env, launch_app, wait_index
from tests.fixtures import clipboard as cb

pytestmark = pytest.mark.timeout(600)

WEB = REPO_ROOT / "web"
PACKAGED = REPO_ROOT / "homa" / "static" / "app"
DRIVER = Path(__file__).with_name("clip_driver.cjs")
EDGE = [p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if os.path.exists(p)]
MIN_CHECKS = 25
COUNT = 160


def _tooling():
    if not EDGE:
        pytest.skip("msedge.exe is not installed")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    for base in (os.environ.get("HOMA_QA_PLAYWRIGHT_DIR"), str(WEB), str(REAL_HOME)):
        if base and (Path(base) / "node_modules" / "playwright-core").is_dir():
            return node, str(Path(base) / "node_modules")
    pytest.skip("needs playwright-core (set HOMA_QA_PLAYWRIGHT_DIR to a folder whose node_modules has it)")


def _build():
    given = os.environ.get("HOMA_QA_WEB_BUILD")
    build = Path(given) if given else PACKAGED
    if not (build / "index.html").is_file():
        pytest.skip(f"{build} has no index.html")
    return build


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    node, modules = _tooling()
    build = _build()
    base = Path(os.path.realpath(tmp_path_factory.mktemp("clipfp")))
    root = base / "root"
    (root / "docs").mkdir(parents=True)
    for n in range(1, COUNT + 1):
        (root / "docs" / f"clipdemo {n:03d}.txt").write_text(f"clipdemo file {n}\n", encoding="utf-8")
    (root / "docs" / ".env").write_text("SECRET=1\n", encoding="utf-8")
    roaming = base / "appdata_roaming"
    roaming.mkdir()
    saved_env = {k: os.environ.get(k) for k in ("HOMA_STATIC_DIR", "APPDATA")}
    os.environ["HOMA_STATIC_DIR"] = str(build)
    os.environ["APPDATA"] = str(roaming)
    clip_reason = cb.probe()
    saved_text = cb.save_text() if not clip_reason else None
    try:
        with launch_app(base / "home", _free_port(), {"index": {"roots": [str(root)]}}, test_mode=True) \
                as (url, token):
            assert not url.endswith(":17171")
            wait_index(url, token, minimum=COUNT)
            done = subprocess.run([node, str(DRIVER), url, token, str(root), sys.executable, str(REPO_ROOT)],
                                  env=browser_env(NODE_PATH=modules), capture_output=True, text=True, timeout=420,
                                  encoding="utf-8", errors="replace")
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if not cb.probe():
            try:
                cb.set_text(saved_text)
            except cb.ClipboardDenied:
                pass
    lines = done.stdout.splitlines()
    print(done.stdout)
    if not any(l.startswith("TOTAL ") for l in lines):
        pytest.fail("the browser driver did not finish:\n" + done.stdout[-2000:] + done.stderr[-1000:])
    return {"lines": lines, "root": root, "clip_reason": clip_reason}


def test_the_driver_ran_every_check(run):
    total = next(l for l in run["lines"] if l.startswith("TOTAL "))
    passed, of = (int(x) for x in re.match(r"TOTAL (\d+)/(\d+)", total).groups())
    checks = [l for l in run["lines"] if l.startswith(("PASS ", "FAIL "))]
    assert len(checks) == of and of + sum(l.startswith("BLOCKED ") for l in run["lines"]) >= MIN_CHECKS, total


def test_every_browser_check_passes(run):
    failed = [l for l in run["lines"] if l.startswith("FAIL ")]
    assert not failed, "\n".join(failed)


@pytest.mark.parametrize("needle", [
    "1 handoff row (matched case- and slash-insensitively) is the only selected and the active row",
    "1 the row came from page 2",
    "1 query, chips, date and sort come from the handoff",
    "1 the preview pane shows the file",
    "1 the URL carries no path",
    "2 not-found toast",
    "4 Ctrl+A selects every loaded row",
    "4 a second Ctrl+A selects the query text",
    "4 Ctrl+C posts op copy",
    "5 Ctrl+X posts op cut",
    "6 Ctrl+Shift+C copies the 2 paths as text",
    "7 menu: Copy 2 files / Cut 2 files / Copy path",
    "7 menu Cut posts op cut",
    "7 menu Copy on one row posts that row only",
    "8 403 all refused",
    "8 422",
    "9 unbound: no POST",
    "9 unbound: menu shows no chord",
    "10 Ctrl+C with default copy_files",
])
def test_named_check_ran_and_passed(run, needle):
    hits = [l for l in run["lines"] if l.startswith(("PASS ", "FAIL ")) and needle in l]
    assert hits, f"no check named like {needle!r} ran"
    assert all(l.startswith("PASS ") for l in hits), hits


def test_clipboard_read_backs(run):
    """The CF_HDROP / Preferred DropEffect read-backs of the files the page put on the clipboard (Ctrl+C, Ctrl+X, menu
    Cut, menu Copy). A BLOCKED one means this session or the server could not open the clipboard: skipped, not passed."""
    readbacks = [l for l in run["lines"] if "READBACK" in l]
    assert len(readbacks) >= 4, readbacks
    blocked = [l for l in readbacks if l.startswith("BLOCKED ")]
    assert not [l for l in readbacks if l.startswith("FAIL ")]
    if blocked:
        pytest.skip("clipboard read-backs skipped (" + str(len(blocked)) + "/" + str(len(readbacks)) + "): "
                    + " || ".join(blocked))


def test_no_file_was_moved_or_deleted(run):
    docs = run["root"] / "docs"
    names = sorted(p.name for p in docs.iterdir())
    assert len(names) == COUNT + 1 and ".env" in names


def test_the_committed_bundle_has_the_clipboard_calls():
    build = _build()
    index = (build / "index.html").read_text(encoding="utf-8")
    script = re.search(r'src="/app/assets/(index-[^"]+\.js)"', index)
    assert script, index
    text = (build / "assets" / script.group(1)).read_text(encoding="utf-8", errors="replace")
    assert "/v1/files/clipboard" in text
    assert "The file you picked is not in these results." in text
    assert "Copy {n} files" in text and "Cut {n} files" in text
