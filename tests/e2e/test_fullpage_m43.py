"""Full page, M4-3 (plan 19.3 "Web half" and 7.5 M4-3; AC-AK-2, AC-AK-3, AC-AK-6, AC-AC-7, AC-PS-1 web).

Headless Edge (playwright-core) drives the COMMITTED build under ``assistant/static/app`` (the one users get) against a
private test-mode app; ``ASSISTANT_QA_WEB_BUILD`` points at another build folder instead. ``m43_driver.cjs`` holds the
checks (139: the 49 of the first M4-3 harness, the 37 of the M4-3b one and 53 for the M4-3c split/expand/zoom change - custom shortcuts, settings fallbacks, Continue in Claude Code, whole-file send -, extended here: status route 500 and the real status route, an
ask_claude send that can never reach the real ``claude``); this file runs it once, turns every PASS/FAIL line into an
assertion, and adds the parts a browser run cannot see (bundle contents, the real ``GET /v1/settings`` theme path).

Needs node, msedge and playwright-core (set ASSISTANT_QA_PLAYWRIGHT_DIR to a folder whose node_modules has it; the
fallbacks are ``web/node_modules`` and ``~/node_modules``); skips with the reason otherwise.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REAL_HOME, browser_env, REPO_ROOT, _free_port, launch_app, wait_index
from tests.fixtures.preview import pdf_bytes

pytestmark = pytest.mark.timeout(600)  # the one browser-driver run (about 100 s) happens inside the first test

WEB = REPO_ROOT / "web"
PACKAGED = REPO_ROOT / "assistant" / "static" / "app"
DRIVER = Path(__file__).with_name("m43_driver.cjs")
EDGE = [p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if os.path.exists(p)]
MIN_CHECKS = 165
ZOOMFIX_MD = (
    "# Zoomfix notes\n\n"
    + "Reading comfort prose, long enough to wrap across a few lines so the measure and the size can be checked. " * 3
    + "\n\n```markmap\n# Root\n## Alpha\n## Beta\n### Child\n```\n\n"
    + "```mermaid\ngraph TD\n  A[Start] --> B[Finish]\n```\n\nThe end.\n"
)


def _build():
    given = os.environ.get("ASSISTANT_QA_WEB_BUILD")
    build = Path(given) if given else PACKAGED
    if not (build / "index.html").is_file():
        pytest.skip(f"{build} has no index.html")
    return build


def _playwright_modules():
    candidates = [os.environ.get("ASSISTANT_QA_PLAYWRIGHT_DIR"), str(WEB), str(REAL_HOME)]
    for base in candidates:
        if base and (Path(base) / "node_modules" / "playwright-core").is_dir():
            return str(Path(base) / "node_modules")
    pytest.skip("needs playwright-core (set ASSISTANT_QA_PLAYWRIGHT_DIR to a folder whose node_modules has it)")


def _tooling():
    if not EDGE:
        pytest.skip("msedge.exe is not installed")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    return node, _playwright_modules()


def _tree(base):
    root = base / "root"
    (root / "docs" / "sub").mkdir(parents=True)
    (root / "docs" / "notes.md").write_text("# Notes\n\nhello alpha\n", encoding="utf-8")
    (root / "docs" / "alpha one.txt").write_text("alpha text\n", encoding="utf-8")
    (root / "docs" / "alpha two.txt").write_text("alpha two\n", encoding="utf-8")
    (root / "docs" / "sub" / "inner alpha.txt").write_text("x\n", encoding="utf-8")
    (root / "docs" / "zoomfix notes.md").write_bytes(ZOOMFIX_MD.encode("utf-8"))
    (root / "docs" / "zoomfix sample.pdf").write_bytes(pdf_bytes("Zoomfix PDF page"))
    return root


@pytest.fixture(scope="module")
def driver_run(tmp_path_factory):
    node, modules = _tooling()
    build = _build()
    base = tmp_path_factory.mktemp("m43")
    root = _tree(base)
    previous = os.environ.get("ASSISTANT_STATIC_DIR")
    os.environ["ASSISTANT_STATIC_DIR"] = str(build)
    try:
        with launch_app(base / "home", _free_port(), {"index": {"roots": [str(root)]}}, test_mode=True) \
                as (url, token):
            wait_index(url, token, minimum=6)
            done = subprocess.run([node, str(DRIVER), url, token, str(root)], env=browser_env(NODE_PATH=modules),
                                  capture_output=True, text=True, timeout=420)
    finally:
        if previous is None:
            os.environ.pop("ASSISTANT_STATIC_DIR", None)
        else:
            os.environ["ASSISTANT_STATIC_DIR"] = previous
    lines = done.stdout.splitlines()
    if any(l.startswith("ERROR ") for l in lines) and not any(l.startswith("TOTAL ") for l in lines):
        pytest.fail("the browser driver crashed:\n" + done.stdout[-1500:] + done.stderr[-800:])
    return {"lines": lines, "stdout": done.stdout, "stderr": done.stderr}


def _checks(run):
    return [(l.split(" ", 1)[0], l.split(" ", 1)[1].split("  | ")[0]) for l in run["lines"]
            if l.startswith(("PASS ", "FAIL "))]


def test_driver_ran_to_the_end_with_all_checks(driver_run):
    totals = [l for l in driver_run["lines"] if l.startswith("TOTAL ")]
    assert totals, driver_run["stdout"][-1500:]
    done, of = (int(x) for x in re.match(r"TOTAL (\d+)/(\d+)", totals[0]).groups())
    assert of >= MIN_CHECKS, f"only {of} checks ran; the harness lost some"
    assert len(_checks(driver_run)) == of


def test_every_browser_check_passes(driver_run):
    failed = [l for l in driver_run["lines"] if l.startswith("FAIL ")]
    assert not failed, "\n".join(failed)


@pytest.mark.parametrize("group, needle", [
    ("Ask Claude menu item disabled with a reason", "1a ask item disabled when status route 404s"),
    ("...and on a 500", "1a ask item disabled when status route errors (500)"),
    ("...and agrees with the real status route", "1a real status route: menu item agrees with it"),
    ("confirmation shows office text and account rows", "2a modal shows Office text flag"),
    ("answer renders as markdown", "2b answer rendered as markdown"),
    ("copy button", "2b copy button puts the answer text on the clipboard"),
    ("Send disabled when the summary says unavailable", "2d Send disabled and reason shown"),
    ("A23: no hydrate button", "3a no Download and preview button"),
    ("Ctrl+F folder listing", "4f Ctrl+F opens the folder listing"),
    ("Ctrl+D cycle", "4g Ctrl+D cycles"),
    ("Ctrl+Shift+S saves, Ctrl+S sorts", "4i Ctrl+S cycles sort"),
    ("Esc order", "4j Esc order"),
    ("theme follows the OS live", "5b live OS change"),
    ("toggle POSTs the theme", "5d the toggle POSTs /v1/settings/theme with the next theme"),
    ("successful POST leaves no stored preference", "5d after a successful POST the page keeps no stored preference"),
    ("the app really saved the theme", "5d the app really saved it"),
    ("one POST per click", "5e one POST per click, in cycle order"),
    ("saved theme survives reload", "5f after a reload the saved app setting applies"),
    ("real route default beats the OS", "5g default theme from the real settings route"),
    ("POST 404 falls back to localStorage", "5h POST 404: the toggle still works"),
    ("POST 404 preference survives reload", "5h POST 404: the stored preference survives a reload"),
    ("POST 500 restores the theme", "5i POST 500: the theme is restored"),
    ("POST 500 writes no localStorage", "5i POST 500: nothing is written to localStorage"),
    ("POST 500 shows a toast", "5i POST 500: an error toast is shown"),
    ("POST 401 writes no localStorage", "5j POST 401: nothing is written to localStorage"),
    ("typing fast is one request", "7a typing 'testing' fast sends exactly one search request"),
    ("live search needs no Enter", "7b live search lists results without Enter"),
    ("superseded search is aborted", "7c typing past the pause sends a second request and aborts the first"),
    ("stale rows never paint", "7c only the newest search's rows are shown"),
    ("URL carries q", "7d the URL carries q=alpha and no launch nonce"),
    ("replaceState adds no history", "7d replaceState: typing adds no history entries"),
    ("clearing removes q", "7d clearing the box removes q from the URL"),
    ("ghost shows the rest of the word", "7e the ghost shows the rest of the best row's word"),
    ("Tab accepts the ghost", "7e Tab accepts the ghost into the box"),
    ("Esc dismisses the ghost", "7e Esc dismisses the ghost"),
    ("Tab without a ghost moves focus", "7e Tab with no ghost moves focus on"),
    ("Enter searches at once", "7f Enter runs the search again at once"),
    ("custom shortcut rebinds rename", "1b rename=f3 (lower-case) opens Rename"),
    ("malformed chord falls back to the default", "1f malformed md_vault 'Ctrl+' falls back to Ctrl+M"),
    ("menu labels follow settings", "1n menu labels follow settings"),
    ("settings 404 gives defaults", "2 settings 404: defaults"),
    ("settings 500 gives defaults", "2 settings 500: defaults"),
    ("Continue posts only the path", "3b posted only {path} to /continue"),
    ("Continue 404 disables the button", "3c 404 route -> button disabled"),
    ("Continue 500 shows an error toast", "3d server error -> error toast"),
    ("whole-file checkbox re-requests with whole_file", "4b re-requested with"),
    ("capped whole-file shows the honest count", "4c-capped honest count"),
    ("whole-file send posts whole_file true", "4e Send posts whole_file:true"),
    ("uncheck restores the preview send", "4f uncheck restores preview details"),
    ("five new test ids", "6a the five new test ids exist exactly once"),
    ("prose measure", "6a prose is 16px with 1.6 line height"),
    ("drag widens the pane", "6b dragging the handle left widens the pane"),
    ("ratio survives reload", "6b the ratio survives a reload"),
    ("drag clamps both sides", "6b dragging far right keeps the pane at least 320px"),
    ("handle reset keys", "6b Home on the handle resets"),
    ("bad stored values", "6c junk stored values fall back too"),
    ("expand hides the list", "6d expand hides the result list"),
    ("Up/Down while expanded", "6d Down in expanded mode changes the selection"),
    ("Esc restores", "6d Esc returns to the list"),
    ("zoom steps", "6e Ctrl+= in the pane steps up"),
    ("zoom persists", "6e the zoom survives a reload"),
    ("storage throwing", "6f storage throwing: zoom still works in memory"),
    ("mermaid grows", "6g at 150% the mermaid SVG is bigger"),
    ("markmap grows", "6g at 150% the markmap box is 1.5x taller"),
    ("pdf canvas grows", "6h the PDF canvas is 1.5x wider"),
])
def test_named_checks_really_ran_and_passed(driver_run, group, needle):
    hits = [(verdict, name) for verdict, name in _checks(driver_run) if needle in name]
    assert hits, f"no check named like {needle!r} ran ({group})"
    assert all(v == "PASS" for v, _ in hits), hits


def test_no_ask_claude_send_reached_the_real_server(driver_run):
    """The driver answers every confirm:true ask_claude itself; a real one would run `claude` on a fixture file."""
    assert "FAIL" not in driver_run["stdout"].replace("KNOWN-FAIL", "")


def test_confirmation_form_row_agrees_with_what_is_sent(driver_run):
    line = next((l for l in driver_run["lines"] if l.startswith(("KNOWN-PASS 1c", "KNOWN-FAIL 1c"))), None)
    assert line, "the 1c check did not run"
    assert line.startswith("KNOWN-PASS"), line


def test_a_401_on_the_theme_post_leaves_the_page_theme_unchanged(driver_run):
    line = next((l for l in driver_run["lines"] if l.startswith(("KNOWN-PASS 5j", "KNOWN-FAIL 5j"))), None)
    assert line, "the 5j check did not run"
    if line.startswith("KNOWN-FAIL"):
        pytest.xfail("product: web/src/App.jsx chooseTheme() on 401 calls onExpired() in the same tick as "
                     "setThemePref(previousPref), so the Workspace unmounts first and <html data-theme> stays at the "
                     "optimistic new value (" + line.split("  | ", 1)[-1][:80] + ")")


# ---- what the browser cannot see: the committed bundle ----------------------------------------------------------------

def _bundle():
    build = _build()
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in (build / "assets").rglob("*.js")
                     if "pdf" not in p.name.lower())


def test_the_committed_bundle_has_the_m4_3_markers_and_not_the_removed_hydrate_button():
    text = _bundle()
    assert "sends-to-claude" in text
    assert "ask_claude/status" in text
    assert "Open downloads it" in text
    assert "Download and preview" not in text
    assert "preview-hydrate" not in text
    assert "prefers-color-scheme" in text


def test_the_bundle_still_calls_no_outside_host():
    text = _bundle()
    for host in ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com", "fonts.googleapis.com"):
        assert host not in text


# ---- theme against the REAL settings route ------------------------------------------------------------------------------

REAL_THEME_DRIVER = r"""
const { chromium } = require("playwright-core");
const [BASE, TOKEN, EXPECT] = process.argv.slice(2);
(async () => {
  const res = await fetch(BASE + "/v1/settings", { headers: { Authorization: "Bearer " + TOKEN } });
  const out = { status: res.status, body: res.status === 200 ? await res.json() : null, theme: null, afterOs: null };
  if (res.status === 200) {
    const browser = await chromium.launch({ channel: "msedge", headless: true });
    const ctx = await browser.newContext({ colorScheme: "dark" });
    const page = await ctx.newPage();
    await page.route("**/v1/session/exchange", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ token: TOKEN }) }));
    await page.goto(BASE + "/app/?launch=qa");
    await page.waitForSelector("[data-testid=query]");
    await page.waitForFunction((want) => document.documentElement.dataset.theme === want, EXPECT, { timeout: 8000 }).catch(() => {});
    out.theme = await page.evaluate(() => document.documentElement.dataset.theme);
    await page.emulateMedia({ colorScheme: "light" });
    await page.waitForTimeout(500);
    out.afterOs = await page.evaluate(() => document.documentElement.dataset.theme);
    await browser.close();
  }
  console.log("RESULT " + JSON.stringify(out));
})().catch((e) => { console.log("ERROR " + e.message); process.exit(1); });
"""


@pytest.mark.parametrize("setting, expected_first, expected_after_os_light", [
    ("light", "light", "light"),
    ("dark", "dark", "dark"),
    ("system", "dark", "light"),          # OS starts dark, then flips to light without a reload
])
def test_theme_setting_from_the_real_settings_route_drives_the_page(tmp_path, setting, expected_first,
                                                                    expected_after_os_light):
    node, modules = _tooling()
    build = _build()
    root = _tree(tmp_path)
    previous = os.environ.get("ASSISTANT_STATIC_DIR")
    os.environ["ASSISTANT_STATIC_DIR"] = str(build)
    try:
        with launch_app(tmp_path / "home", _free_port(), {"index": {"roots": [str(root)]}, "theme": setting},
                        test_mode=True) as (url, token):
            wait_index(url, token, minimum=6)
            script = tmp_path / "theme_driver.cjs"
            script.write_text(REAL_THEME_DRIVER, encoding="utf-8")
            done = subprocess.run([node, str(script), url, token, expected_first],
                                  env=browser_env(NODE_PATH=modules), capture_output=True, text=True, timeout=120)
    finally:
        if previous is None:
            os.environ.pop("ASSISTANT_STATIC_DIR", None)
        else:
            os.environ["ASSISTANT_STATIC_DIR"] = previous
    line = next((l for l in done.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert line, (done.stdout[-500:], done.stderr[-500:])
    result = json.loads(line[len("RESULT "):])
    if result["status"] == 404:
        pytest.skip("GET /v1/settings is not served yet (M4-2 routes_settings.py not wired); the 404 path is covered "
                    "by the driver's 5g check")
    assert result["status"] == 200, result
    assert result["body"]["theme"] == setting
    secrets = json.dumps(result["body"]).lower()
    for forbidden in ("token", "roots", "calendars", "ics"):
        assert forbidden not in secrets, f"/v1/settings leaks {forbidden}"
    assert result["theme"] == expected_first, result
    assert result["afterOs"] == expected_after_os_light, result
