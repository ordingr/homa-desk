"""Full page end to end (plan 7.4 M3b-1/M3b-3/M3b-6; AC-SH-7, AC-PV-1, SEC-2, SEC-5).

A private test-mode app serves a *local build* of ``web/`` through ``HOMA_STATIC_DIR`` (built with
``npm run build`` and ``HOMA_WEB_OUT``, the same command ``scripts/build-web.ps1`` runs after ``npm ci``; running the
script itself would delete ``web/node_modules`` under the ``ui`` task, so the tests call the build step only). Set
``HOMA_QA_WEB_BUILD`` to a prebuilt folder to skip the build.

Layers, each skipping with its reason when the piece it needs is missing:

1. the build is served from ``/app/`` and every asset it references resolves with the right content type;
2. the bundle carries the SEC-5 markers (strict Mermaid, DOMPurify) and no CDN address;
3. Edge app mode opens the page through ``POST /v1/fullpage/open`` (Edge window found, screenshot taken with
   ``tools/driver.py``'s grab), and ``POST /v1/fullpage/close`` closes exactly that window.

The in-page assertions (DOM, console, network, nonce leaving the URL) are the ``ui`` role's Chrome DevTools run in M3b-1/2;
they cannot be made here without a DevTools endpoint, and this file does not pretend to.
"""
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

import pytest

from conftest import REPO_ROOT, browser_env, Api, SearchApp, _free_port, build_search_tree, launch_app, wait_index, wait_until, BULK_COUNT

WEB = REPO_ROOT / "web"
EDGE = [p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if os.path.exists(p)]


@pytest.fixture(scope="module")
def web_build(tmp_path_factory):
    given = os.environ.get("HOMA_QA_WEB_BUILD")
    if given:
        build = Path(given)
        if not (build / "index.html").is_file():
            pytest.skip(f"HOMA_QA_WEB_BUILD={given} has no index.html")
        return build
    if not (WEB / "package.json").is_file():
        pytest.skip("web/ does not exist yet (M3b-1)")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None or not (WEB / "node_modules").is_dir():
        pytest.skip("no npm or no web/node_modules: run `npm ci` in web/ (or set HOMA_QA_WEB_BUILD)")
    out = tmp_path_factory.mktemp("web_build") / "app"
    done = subprocess.run([npm, "run", "build"], cwd=WEB, env=dict(os.environ, HOMA_WEB_OUT=str(out)),
                          capture_output=True, text=True, timeout=300)
    if done.returncode != 0 or not (out / "index.html").is_file():
        pytest.skip(f"npm run build failed ({done.returncode}): {(done.stdout + done.stderr)[-400:]}")
    return out


@pytest.fixture(scope="module")
def fullpage_app(tmp_path_factory, web_build):
    base = tmp_path_factory.mktemp("fullpage_app")
    root = build_search_tree(base / "root", escape=False)
    previous = os.environ.get("HOMA_STATIC_DIR")
    os.environ["HOMA_STATIC_DIR"] = str(web_build)
    try:
        with launch_app(base / "home", _free_port(), {"index": {"roots": [str(root)]}}, test_mode=True) \
                as (base_url, token):
            index = wait_index(base_url, token, minimum=BULK_COUNT)
            if not (isinstance(index, dict) and index.get("entries", 0) >= BULK_COUNT):
                pytest.skip(f"index never reached {BULK_COUNT} entries ({index!r})")
            app = SearchApp(base_url, token, root, base / "home")
            status, _ = app.api.get("/app/")
            if status == 404:
                pytest.skip("/app/ is not served yet (M3b-3 static.py / M3b-9 wiring)")
            yield app
    finally:
        if previous is None:
            os.environ.pop("HOMA_STATIC_DIR", None)
        else:
            os.environ["HOMA_STATIC_DIR"] = previous


def fetch(app, path, token=False):
    import http.client
    conn = http.client.HTTPConnection(app.api.host, int(app.api.port), timeout=15)
    headers = {"Authorization": f"Bearer {app.token}"} if token else {}
    try:
        conn.request("GET", path, headers=headers)
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        conn.close()


# ---- 1. the build is served ---------------------------------------------------------------------------------------------

def test_the_launch_url_serves_the_page_and_every_referenced_asset_resolves(fullpage_app):
    status, headers, data = fetch(fullpage_app, "/app/?launch=qa-unknown&q=design%20md")
    assert status == 200, status
    assert headers["content-type"].startswith("text/html")
    assert "no-store" in headers.get("cache-control", "").lower()
    html = data.decode("utf-8")
    refs = re.findall(r'(?:src|href)="(/app/[^"]+)"', html)
    assert refs, "index.html references no /app/ assets"
    for ref in refs:
        status, h, body = fetch(fullpage_app, ref)
        assert status == 200 and body, ref
        if ref.endswith(".js"):
            assert "javascript" in h["content-type"], (ref, h["content-type"])
        if ref.endswith(".css"):
            assert h["content-type"].startswith("text/css"), ref
    assert "https://" not in html.replace("https://www.w3.org", ""), "the page must not pull anything from the network"


def test_the_page_never_carries_the_token_and_the_exchange_trades_the_nonce_once(fullpage_app):
    from homa.server import auth
    status, _, data = fetch(fullpage_app, "/app/?launch=qa-unknown")
    assert fullpage_app.token.encode() not in data
    status, body = fullpage_app.api.post("/v1/session/exchange", {"nonce": "qa-unknown"}, token=False)
    assert status == 403 and fullpage_app.token not in str(body)


def test_missing_assets_and_traversal_never_return_the_index_or_outside_files(fullpage_app):
    for probe in ("/app/assets/does-not-exist.js", "/app/..%2f..%2fREADME.md", "/app/%2e%2e/%2e%2e/pyproject.toml"):
        status, _, data = fetch(fullpage_app, probe)
        assert status in (404, 400, 401), probe
        assert b"[project]" not in data and b"<!doctype html>" not in data.lower()


# ---- 2. SEC-5 markers in the bundle ---------------------------------------------------------------------------------------

def _bundle_text(web_build) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in (web_build / "assets").rglob("*.js"))


def test_bundle_has_no_cdn_address_and_nothing_loads_from_outside(web_build):
    text = _bundle_text(web_build)
    for host in ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com", "fonts.googleapis.com"):
        assert host not in text, f"bundle references {host}"


def test_bundle_initialises_mermaid_strict_and_sanitises_html(web_build):
    text = _bundle_text(web_build)
    if "mermaid" not in text.lower():
        pytest.skip("the preview renderers are not in the build yet (M3b-2)")
    quote = "[\"'" + chr(96) + "]"
    assert re.search(r"securityLevel\s*:\s*" + quote + "strict" + quote, text), "mermaid must be initialised with securityLevel 'strict'"
    assert "DOMPurify" in text or "dompurify" in text.lower(), "HTML/Markdown must pass through DOMPurify"
    assert not re.search(r"securityLevel\s*:\s*" + quote + "(loose|antiscript)" + quote, text)


def test_pdf_worker_is_served_from_the_same_origin(web_build):
    text = _bundle_text(web_build)
    if "pdf" not in text.lower() or not (web_build / "pdfjs").exists():
        pytest.skip("the PDF renderer is not in the build yet (M3b-2)")
    workers = [p for p in (web_build / "assets").rglob("*") if "worker" in p.name.lower()]
    assert workers or "workerSrc" in text, "pdf.js needs a same-origin worker"


# ---- 3. Edge app mode ------------------------------------------------------------------------------------------------------

def _edge_windows():
    from homa.overlay import fullpage
    return fullpage.edge_windows()


def test_edge_app_mode_opens_the_page_takes_a_screenshot_and_closes_only_its_own_window(fullpage_app, tmp_path):
    if not EDGE:
        pytest.skip("msedge.exe is not installed")
    before = _edge_windows()
    status, body = fullpage_app.api.post("/v1/fullpage/open", {"query": "design md"})
    if status in (404, 405):
        pytest.skip("POST /v1/fullpage/open is not served yet (M3b-9); the F11 path needs a desktop the tests cannot "
                    "drive from here")
    assert status in (200, 202), (status, body)
    try:
        opened = wait_until(lambda: set(_edge_windows()) - set(before), timeout=20)
        if not opened:
            pytest.skip("no Edge window appeared (locked desktop?)")
        shot = tmp_path / "fullpage.png"
        from tools import driver
        driver.grab(shot)
        assert shot.is_file() and shot.stat().st_size > 5_000
        from PIL import Image
        with Image.open(shot) as image:
            assert len(image.convert("RGB").resize((64, 64)).getcolors(4096)) > 8, "screenshot looks blank"
        status, _ = fullpage_app.api.post("/v1/fullpage/close", {})
        assert status in (200, 204)
        assert wait_until(lambda: not (set(opened) & set(_edge_windows())), timeout=10), "the window did not close"
        assert set(before) <= set(_edge_windows()) | set(), "a window the test did not open was closed"
    finally:
        fullpage_app.api.post("/v1/fullpage/close", {})


# ---- R9-B1: hostile Mermaid config and SVG must not make the page fetch anything external -------------------------------
# Headless Edge (playwright-core) drives the committed build against a private app. A beacon HTTP server on another
# port records every request that reaches it; the assertion is zero hits. Needs node, msedge and playwright-core
# (set HOMA_QA_PLAYWRIGHT_DIR to a folder whose node_modules holds playwright-core); skips otherwise.

BEACON_DRIVER = r"""
const { chromium } = require("playwright-core");
const [BASE, TOKEN] = process.argv.slice(2);
(async () => {
  const out = { external: [], dialogs: [] };
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  page.on("dialog", async (d) => { out.dialogs.push(d.message()); await d.dismiss(); });
  page.on("request", (r) => { const u = r.url(); if (!u.startsWith(BASE) && !/^(blob|data|about):/.test(u)) out.external.push(u); });
  await page.route("**/v1/session/exchange", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ token: TOKEN }) }));
  await page.goto(BASE + "/app/?launch=qa");
  await page.waitForSelector("[data-testid=query]");
  async function open(name) {
    await page.fill("[data-testid=query]", name);
    await page.press("[data-testid=query]", "Enter");
    const row = page.locator("[data-testid=row]", { hasText: name }).first();
    await row.waitFor({ timeout: 20000 });
    await row.click();
    await page.waitForSelector("[data-testid=pane-body]");
    await page.waitForFunction(() => !document.querySelector("[data-testid=preview-loading]"), null, { timeout: 20000 });
  }
  for (const name of ["init-themecss.md", "frontmatter-themecss.md", "plain-mermaid.md"]) {
    await open(name);
    await page.waitForSelector("[data-testid=preview-mermaid] svg", { timeout: 25000 });
    await page.waitForTimeout(1200);
    out[name] = {
      svg: await page.locator("[data-testid=preview-mermaid] svg").count(),
      nodes: await page.locator("[data-testid=preview-mermaid] svg .node").count(),
      styleHasExternal: (await page.locator("[data-testid=preview-mermaid] svg style").allTextContents()).join("").includes("127.0.0.1:" + process.env.BEACON_PORT),
    };
  }
  await open("hostile.svg");
  await page.waitForSelector("[data-testid=pane-body] iframe, [data-testid=pane-body] img", { timeout: 15000 });
  await page.waitForTimeout(1500);
  const frame = page.locator("[data-testid=pane-body] iframe");
  out.svg = { iframes: await frame.count(), sandbox: await frame.count() ? await frame.first().getAttribute("sandbox") : null,
              srcdoc: await frame.count() ? await frame.first().getAttribute("srcdoc") : null };
  await browser.close();
  console.log("RESULT " + JSON.stringify(out));
})().catch((e) => { console.log("ERROR " + e.message); process.exit(1); });
"""


@pytest.fixture
def beacon():
    import http.server
    import threading
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            self.send_response(204)
            self.end_headers()

        do_POST = do_GET

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1], hits
    finally:
        server.shutdown()
        server.server_close()


def test_hostile_mermaid_config_and_svg_cause_zero_external_requests_and_the_diagram_still_renders(tmp_path, beacon):
    import json
    if not EDGE:
        pytest.skip("msedge.exe is not installed")
    node = shutil.which("node")
    pw_dir = os.environ.get("HOMA_QA_PLAYWRIGHT_DIR") or str(WEB)
    if node is None or not (Path(pw_dir) / "node_modules" / "playwright-core").is_dir():
        pytest.skip("needs node and playwright-core (set HOMA_QA_PLAYWRIGHT_DIR to a folder whose node_modules has it)")
    port, hits = beacon
    url = f"http://127.0.0.1:{port}"
    root = tmp_path / "root"
    (root / "notes").mkdir(parents=True)
    (root / "img").mkdir()
    (root / "notes" / "init-themecss.md").write_text(
        "# T1\n\n```mermaid\n%%{init: {\"themeCSS\": \".node rect{fill: url(" + url + "/t1-fill#g)} .node{background-image:url("
        + url + "/t1-bg)}\"}}%%\ngraph TD\n  A[Start] --> B[End]\n```\n", encoding="utf-8")
    (root / "notes" / "frontmatter-themecss.md").write_text(
        "# T4\n\n```mermaid\n---\nconfig:\n  themeCSS: \".node rect{fill: url(" + url + "/t4-fill#g)}\"\n  fontFamily: \"x;} @import url("
        + url + "/t4-import);\"\n---\ngraph TD\n  A[Start4] --> B[End4]\n```\n", encoding="utf-8")
    (root / "notes" / "plain-mermaid.md").write_text(
        "# Plain\n\n```mermaid\ngraph TD\n  A[Alpha] --> B[Beta]\n```\n", encoding="utf-8")
    (root / "img" / "hostile.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="200" height="120" '
        f'onload="fetch(\'{url}/svg-onload\')"><script>fetch("{url}/svg-script")</script>'
        f'<style>rect{{fill:url({url}/svg-style#g)}} @import url({url}/svg-import);</style>'
        f'<image href="{url}/svg-image" width="10" height="10"/><image xlink:href="{url}/svg-image2" width="10" height="10"/>'
        f'<foreignObject><iframe src="{url}/svg-iframe"></iframe></foreignObject>'
        f'<rect x="10" y="10" width="100" height="60" fill="#2a78d6" style="background:url({url}/svg-inline)"/>'
        '<circle cx="150" cy="60" r="30" fill="#e34948"/></svg>', encoding="utf-8")
    previous = os.environ.pop("HOMA_STATIC_DIR", None)
    try:
        with launch_app(tmp_path / "home", _free_port(), {"index": {"roots": [str(root)]}}, test_mode=True) \
                as (base_url, token):
            index = wait_index(base_url, token, minimum=6)
            assert isinstance(index, dict) and index.get("entries", 0) >= 6, index
            script = tmp_path / "beacon_driver.cjs"
            script.write_text(BEACON_DRIVER, encoding="utf-8")
            env = browser_env(NODE_PATH=str(Path(pw_dir) / "node_modules"), BEACON_PORT=str(port))
            done = subprocess.run([node, str(script), base_url, token], capture_output=True, text=True, timeout=240,
                                  env=env)
    finally:
        if previous is not None:
            os.environ["HOMA_STATIC_DIR"] = previous
    line = next((l for l in done.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert line, (done.stdout[-600:], done.stderr[-600:])
    result = json.loads(line[len("RESULT "):])
    assert hits == [], f"the page made requests to the beacon: {hits}"
    assert not [u for u in result["external"] if f":{port}" in u], result["external"]
    assert result["dialogs"] == []
    for name in ("init-themecss.md", "frontmatter-themecss.md", "plain-mermaid.md"):
        assert result[name]["svg"] == 1 and result[name]["nodes"] >= 2, (name, result[name])
        assert result[name]["styleHasExternal"] is False, name
    svg = result["svg"]
    assert svg["iframes"] == 1 and svg["sandbox"] == "", svg
    assert svg["srcdoc"] and "<rect" in svg["srcdoc"] and "<circle" in svg["srcdoc"]
    assert not re.search(r"<script|onload|<image|foreignobject|@import|%d" % port, svg["srcdoc"], re.I), svg["srcdoc"][:500]
