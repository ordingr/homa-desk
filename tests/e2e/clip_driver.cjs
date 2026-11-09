// Full page: popup handoff, Ctrl+A, Copy / Cut of files, Copy path (plan 2026-11-09 popup-to-fullpage-and-file-clipboard,
// section 4.1 behaviours 7-9). Run by test_fullpage_clipboard.py against a private test-mode app; headless Edge only.
// The exchange is mocked to return the real token plus a handoff; every /v1/files/clipboard POST reaches the real server,
// and the clipboard is read back by `python -m tests.fixtures.clipboard read` (plain ctypes).
// Output: PASS/FAIL lines, BLOCKED lines for read-backs this session could not do, then TOTAL n/m.
const { chromium } = require("playwright-core");
const { execFileSync } = require("child_process");
const [BASE, TOKEN, ROOT, PY, REPO] = process.argv.slice(2);
const clip = () => {
  try {
    return JSON.parse(execFileSync(PY, ["-m", "tests.fixtures.clipboard", "read"], { cwd: REPO, stdio: ["ignore", "pipe", "pipe"] }).toString());
  } catch (e) {
    return { error: String(e.stderr || e.message).trim() };
  }
};
const results = [];
const check = (name, ok, detail) => { results.push(ok); console.log((ok ? "PASS " : "FAIL ") + name + (detail !== undefined ? "  | " + JSON.stringify(detail) : "")); };
const BUSY = "Windows would not let Homa use the clipboard.";
const blocked = (name, why) => console.log("BLOCKED " + name + "  | " + why);
const clipCheck = (name, toastText, cb, ok, detail) => {
  if (toastText === BUSY) return blocked(name, "the server could not open the clipboard (500)");
  if (cb.error) return blocked(name, "this session cannot open the clipboard: " + cb.error);
  check(name, ok, detail);
};
const DOCS = ROOT + "\\docs\\";
const file = (n) => DOCS + `clipdemo ${String(n).padStart(3, "0")}.txt`;
const lower = (list) => (list || []).map((p) => p.toLowerCase());

(async () => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const errors = [];
  async function newPage({ handoff, settings } = {}) {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: "dark",
      permissions: ["clipboard-read", "clipboard-write"] });
    const page = await ctx.newPage();
    const posts = [];
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push("pageerror " + e.message));
    page.on("request", (r) => { if (r.url().includes("/v1/files/clipboard")) posts.push(JSON.parse(r.postData())); });
    await page.route("**/v1/session/exchange", (r) => r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify(handoff ? { token: TOKEN, handoff } : { token: TOKEN }) }));
    if (settings) await page.route("**/v1/settings", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(settings) }));
    await page.goto(BASE + "/app/?launch=qa");
    await page.waitForSelector("[data-testid=query]");
    return { page, ctx, posts };
  }
  const toastNow = async (page) => ((await page.locator("[data-testid=toast]").count()) ? page.locator("[data-testid=toast]").innerText() : "");
  const waitToast = (page, re) => page.waitForFunction((src) => new RegExp(src).test(document.querySelector("[data-testid=toast]")?.innerText || ""), re.source, { timeout: 10000 });
  const selectedPaths = (page) => page.$$eval("[data-testid=row][aria-selected=true]", (els) => els.map((e) => e.dataset.path));

  // 1: handoff for a row on page 2 -> that row only selected, active, in view, pane open, filters applied
  {
    const target = file(140);
    const { page, ctx } = await newPage({ handoff: { query: "clipdemo", chips: ["docs"], modified: "30d", sort: "name", select_path: target.toUpperCase().replace(/\\/g, "/") } });
    await page.waitForSelector("[data-testid=row].active", { timeout: 20000 });
    await page.waitForSelector("[data-testid=pane-body]");
    const sel = await selectedPaths(page);
    const active = await page.$eval("[data-testid=row].active", (e) => e.dataset.path);
    const rowCount = await page.locator("[data-testid=row]").count();
    const inView = await page.$eval("[data-testid=row].active", (e) => { const b = e.getBoundingClientRect(); const l = e.closest(".listwrap").getBoundingClientRect(); return b.top >= l.top && b.bottom <= l.bottom; });
    check("1 handoff row (matched case- and slash-insensitively) is the only selected and the active row", sel.length === 1 && sel[0] === target && active === target, { sel: sel.map((p) => p.split("\\").pop()) });
    check("1 the row came from page 2 (more than 100 rows loaded)", rowCount > 100, rowCount);
    check("1 the row is scrolled into view", inView);
    check("1 query, chips, date and sort come from the handoff",
      (await page.inputValue("[data-testid=query]")) === "clipdemo" && (await page.getAttribute("[data-testid=chip-docs]", "aria-pressed")) === "true"
      && (await page.inputValue("[data-testid=modified]")) === "30d" && (await page.getAttribute("[data-testid=sort-name]", "aria-pressed")) === "true");
    check("1 the preview pane shows the file", /clipdemo 140/.test(await page.locator("[data-testid=pane]").innerText()));
    check("1 the URL carries no path and no launch nonce", !/select|clipdemo%20140|launch=/.test(page.url()), page.url());
    await ctx.close();
  }
  // 2: handoff for a file that is not in the results -> toast, no selection, at most 300 rows paged
  {
    const { page, ctx } = await newPage({ handoff: { query: "clipdemo", chips: [], modified: "", sort: "", select_path: DOCS + "nowhere.txt" } });
    await waitToast(page, /not in these results/);
    check("2 not-found toast", (await toastNow(page)) === "The file you picked is not in these results.");
    check("2 no selection", (await selectedPaths(page)).length === 0);
    check("2 paged up to 300 rows at most", (await page.locator("[data-testid=row]").count()) <= 300, await page.locator("[data-testid=row]").count());
    await ctx.close();
  }
  // 3: launch without a handoff behaves as before (no selection forced, no toast)
  {
    const { page, ctx } = await newPage({});
    await page.waitForTimeout(800);
    check("3 no handoff: no toast, nothing selected", (await toastNow(page)) === "" && (await selectedPaths(page)).length === 0);
    await ctx.close();
  }
  // 4-7: Ctrl+A, Ctrl+C, Ctrl+X, Ctrl+Shift+C, context menu Copy/Cut
  {
    const { page, ctx, posts } = await newPage({ handoff: { query: "clipdemo 00", chips: [], modified: "", sort: "name", select_path: file(1) } });
    await page.waitForSelector("[data-testid=row].active", { timeout: 20000 });
    const all = await page.$$eval("[data-testid=row]", (els) => els.map((e) => e.dataset.path));
    await page.focus("[data-testid=query]");
    await page.keyboard.press("Control+a");
    check("4 Ctrl+A selects every loaded row", (await selectedPaths(page)).length === all.length && all.length >= 9, all.length);
    await page.keyboard.press("Control+a");
    const picked = await page.$eval("[data-testid=query]", (e) => [e.selectionStart, e.selectionEnd, e.value.length]);
    check("4 a second Ctrl+A selects the query text", picked[0] === 0 && picked[1] === picked[2] && picked[2] > 0, picked);
    await page.locator("[data-testid=row]").nth(2).click();
    await page.keyboard.press("Control+a");
    await page.keyboard.press("Control+c");
    await waitToast(page, /^Copied \d+ files|would not let/);
    const t4 = await toastNow(page);
    const cb4 = clip();
    check("4 Ctrl+C posts op copy with every selected path", posts.length === 1 && posts[0].op === "copy" && posts[0].paths.length === all.length, posts.map((p) => [p.op, p.paths.length]));
    if (t4 !== BUSY) check("4 the toast is the server's message", t4 === `Copied ${all.length} files — paste in File Explorer`, t4);
    clipCheck("4 READBACK Ctrl+C: CF_HDROP holds the selected files, DropEffect 1", t4, cb4,
      cb4.effect === 1 && JSON.stringify(lower(cb4.files)) === JSON.stringify(lower(all)), { effect: cb4.effect, n: (cb4.files || []).length });

    await page.locator("[data-testid=row]").nth(0).click();
    await page.locator("[data-testid=row]").nth(3).click({ modifiers: ["Control"] });
    await page.keyboard.press("Control+x");
    await waitToast(page, /^Cut 2 files|would not let/);
    const t5 = await toastNow(page);
    const cb5 = clip();
    check("5 Ctrl+X posts op cut with the 2 Ctrl+clicked paths", posts.length === 2 && posts[1].op === "cut" && JSON.stringify(posts[1].paths) === JSON.stringify([all[0], all[3]]), posts[1]);
    clipCheck("5 READBACK Ctrl+X: CF_HDROP holds both files, DropEffect 2", t5, cb5,
      cb5.effect === 2 && JSON.stringify(lower(cb5.files)) === JSON.stringify(lower([all[0], all[3]])), { effect: cb5.effect, files: (cb5.files || []).map((f) => f.split("\\").pop()) });

    await page.keyboard.press("Control+Shift+C");
    await waitToast(page, /paths/);
    const text = await page.evaluate(() => navigator.clipboard.readText());
    check("6 Ctrl+Shift+C copies the 2 paths as text and posts nothing", posts.length === 2 && text.split("\r").join("") === [all[0], all[3]].join("\n"), text.split("\n").map((p) => p.split("\\").pop()));

    await page.locator("[data-testid=row]").nth(3).click({ button: "right" });
    const labels = await page.$$eval("[data-testid=context-menu] .menuitem", (els) => els.map((e) => e.innerText.replace(/\s+/g, " ").trim()));
    check("7 menu: Copy 2 files / Cut 2 files / Copy path with their chords, after Reveal", labels.slice(2, 6).join("|") === "Reveal in Explorer Ctrl+Enter|Copy 2 files Ctrl+C|Cut 2 files Ctrl+X|Copy path Ctrl+Shift+C", labels);
    await page.click("[data-verb=cut_files]");
    await waitToast(page, /^Cut 2 files|would not let/);
    const t7 = await toastNow(page);
    const cb7 = clip();
    check("7 menu Cut posts op cut with the 2 paths", posts.length === 3 && posts[2].op === "cut" && posts[2].paths.length === 2, posts[2]);
    clipCheck("7 READBACK menu Cut: DropEffect 2, 2 files", t7, cb7, cb7.effect === 2 && (cb7.files || []).length === 2, cb7.effect);
    await page.locator("[data-testid=row]").nth(1).click();
    await page.locator("[data-testid=row]").nth(1).click({ button: "right" });
    const single = await page.$$eval("[data-testid=context-menu] .menuitem", (els) => els.map((e) => e.innerText.replace(/\s+/g, " ").trim()));
    check("7 one row: the menu says plain Copy / Cut", single.includes("Copy Ctrl+C") && single.includes("Cut Ctrl+X"), single.slice(0, 6));
    await page.click("[data-verb=copy_files]");
    await waitToast(page, /^Copied 1 file|would not let/);
    const t8 = await toastNow(page);
    const cb8 = clip();
    check("7 menu Copy on one row posts that row only", posts.length === 4 && posts[3].op === "copy" && JSON.stringify(posts[3].paths) === JSON.stringify([all[1]]), posts[3]);
    clipCheck("7 READBACK menu Copy: DropEffect 1, that file", t8, cb8, cb8.effect === 1 && JSON.stringify(lower(cb8.files)) === JSON.stringify(lower([all[1]])), cb8);
    await ctx.close();
  }
  // 8: error mapping: 403 all refused, 200 partial with the skipped reason, 422 shape
  {
    const { page, ctx } = await newPage({ handoff: { query: "clipdemo 01", chips: [], modified: "", sort: "name", select_path: file(10) } });
    await page.waitForSelector("[data-testid=row].active", { timeout: 20000 });
    await page.route("**/v1/files/clipboard", (r) => {
      const body = JSON.parse(r.request().postData());
      body.paths = [ROOT + "\\docs\\.env"];
      r.continue({ postData: JSON.stringify(body) });
    });
    await page.keyboard.press("Control+c");
    await waitToast(page, /Nothing was/);
    const t = await toastNow(page);
    check("8 403 all refused -> error toast with the reason", /^Nothing was copied \(refused: protected file/.test(t) && (await page.getAttribute("[data-testid=toast]", "class")).includes("error"), t);
    await page.unroute("**/v1/files/clipboard");
    await page.route("**/v1/files/clipboard", (r) => {
      const body = JSON.parse(r.request().postData());
      body.paths = [...body.paths, ROOT + "\\docs\\.env"];
      r.continue({ postData: JSON.stringify(body) });
    });
    await page.keyboard.press("Control+x");
    await waitToast(page, /^Cut 1 file|would not let/);
    const t2 = await toastNow(page);
    if (t2 === BUSY) blocked("8 200 partial message", "the server could not open the clipboard (500)");
    else check("8 200 partial -> message with the skipped count and reason", /; 1 skipped \(refused: protected file \(secrets deny-list\)\)$/.test(t2), t2);
    await page.unroute("**/v1/files/clipboard");
    await page.route("**/v1/files/clipboard", (r) => r.continue({ postData: JSON.stringify({ paths: [], op: "copy" }) }));
    await page.keyboard.press("Control+c");
    await waitToast(page, /could not be copied/);
    check("8 422 -> readable error toast", /paths must be a list/.test(await toastNow(page)), await toastNow(page));
    await ctx.close();
  }
  // 9: "" unbinds the four new actions: no POST, no toast, clipboard unchanged; menu shows no chord
  {
    const settings = { theme: "dark", shortcuts: { copy_files: "", cut_files: "", copy_path: "", select_all: "", rename: "" } };
    const { page, ctx, posts } = await newPage({ settings, handoff: { query: "clipdemo 00", chips: [], modified: "", sort: "name", select_path: file(2) } });
    await page.waitForSelector("[data-testid=row].active", { timeout: 20000 });
    await page.waitForTimeout(500);
    const before = clip();
    await page.locator("[data-testid=row]").nth(1).click();
    for (const k of ["Control+c", "Control+x", "Control+Shift+C", "Control+a"]) await page.keyboard.press(k);
    await page.waitForTimeout(800);
    const after = clip();
    check("9 unbound: no POST, no toast, one row selected", posts.length === 0 && (await toastNow(page)) === "" && (await selectedPaths(page)).length === 1, posts.length);
    if (before.error || after.error) blocked("9 READBACK unbound: clipboard unchanged", "this session cannot open the clipboard: " + (before.error || after.error));
    else check("9 READBACK unbound: clipboard unchanged", JSON.stringify(after) === JSON.stringify(before));
    await page.locator("[data-testid=row]").nth(1).click({ button: "right" });
    const kbds = Object.fromEntries(await page.$$eval("[data-testid=context-menu] .menuitem", (els) => els.map((e) => [e.dataset.verb, e.querySelector("kbd")?.innerText || ""])));
    check("9 unbound: menu shows no chord for Copy/Cut/Copy path; rename '' falls back to F2", kbds.copy_files === "" && kbds.cut_files === "" && kbds.copy_path === "" && kbds.rename === "F2", kbds);
    await ctx.close();
  }
  // 10: old settings (copy_path = Ctrl+C, no copy_files) served by a pre-migration route: Ctrl+C copies files, Ctrl+Shift+C path
  {
    const settings = { theme: "dark", shortcuts: { copy_path: "Ctrl+Shift+C" } };
    const { page, ctx, posts } = await newPage({ settings, handoff: { query: "clipdemo 00", chips: [], modified: "", sort: "name", select_path: file(3) } });
    await page.waitForSelector("[data-testid=row].active", { timeout: 20000 });
    await page.keyboard.press("Control+c");
    await waitToast(page, /^Copied 1 file|would not let/);
    check("10 Ctrl+C with default copy_files posts one copy of the active row", posts.length === 1 && posts[0].op === "copy" && posts[0].paths[0] === file(3), posts);
    await ctx.close();
  }
  await browser.close();
  const unexpected = errors.filter((e) => !/^Failed to load resource: the server responded with a status of (403|422|500)/.test(e));
  check("no console errors besides the provoked 403/422/500 responses", unexpected.length === 0, { unexpected });
  console.log(`TOTAL ${results.filter(Boolean).length}/${results.length}`);
})().catch((e) => { console.log("ERROR " + (e && e.stack ? e.stack : e)); process.exit(1); });
