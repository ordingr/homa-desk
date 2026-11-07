const { chromium } = require("playwright-core");
const [BASE, TOKEN, ROOT] = process.argv.slice(2);
// Driven by tests/e2e/test_fullpage_m43.py: node m43_driver.cjs <base-url> <token> <root>. Prints PASS/FAIL lines, then
// "TOTAL n/m". Lines starting KNOWN- are checks for bugs already filed (the Python side xfails them).
const results = [];
const known = (name, ok, detail) => console.log((ok ? "KNOWN-PASS " : "KNOWN-FAIL ") + name + (detail !== undefined ? "  | " + JSON.stringify(detail) : ""));
const check = (name, ok, detail) => { results.push(ok); console.log((ok ? "PASS " : "FAIL ") + name + (detail !== undefined ? "  | " + JSON.stringify(detail) : "")); };

(async () => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const errors = [];

  async function newPage({ mocks = {}, scheme = "dark", clip = true } = {}) {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: scheme,
      permissions: clip ? ["clipboard-read", "clipboard-write"] : [] });
    const page = await ctx.newPage();
    const state = { verbs: [], saved: [], searches: [], dialogs: [] };
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push("pageerror " + e.message));
    await page.route("**/v1/session/exchange", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ token: TOKEN }) }));
    if (mocks.statusCode) await page.route("**/v1/actions/ask_claude/status", (r) => r.fulfill({ status: mocks.statusCode, contentType: "application/json", body: JSON.stringify({ error: { code: "x", message: "boom" } }) }));
    else if (mocks.status) await page.route("**/v1/actions/ask_claude/status", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mocks.status) }));
    if (mocks.init) await ctx.addInitScript(mocks.init);
    if (mocks.settings) await page.route("**/v1/settings", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mocks.settings) }));
    if (mocks.preview) await page.route("**/v1/files/preview**", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mocks.preview) }));
    await page.route("**/v1/actions/*", async (route) => {
      const req = route.request();
      if (req.method() !== "POST") return route.continue();
      const verb = new URL(req.url()).pathname.split("/").pop();
      const body = JSON.parse(req.postData() || "{}");
      state.verbs.push([verb, body.confirm === true ? "confirm" : ""]);
      if (verb === "ask_claude" && mocks.ask) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mocks.ask(body)) });
      // A confirm:true ask_claude never reaches the real server: with the M4-1 backend that would run `claude` and send the file.
      if (verb === "ask_claude" && body.confirm === true) return route.fulfill({ status: 501, contentType: "application/json", body: JSON.stringify({ error: { code: "not_implemented", message: "Ask Claude is not available in this build." } }) });
      if (verb === "ask_claude" || verb === "folder_listing") return route.continue();
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, message: verb + " ok" }) });
    });
    await page.route("**/v1/saved", async (route) => {
      if (route.request().method() === "POST") state.saved.push(JSON.parse(route.request().postData()));
      return route.continue();
    });
    await page.goto(BASE + "/app/?launch=qa");
    await page.waitForSelector("[data-testid=query]");
    return { page, state, ctx };
  }
  async function search(page, q) {
    await page.fill("[data-testid=query]", q);
    await page.press("[data-testid=query]", "Enter");
    await page.waitForSelector("[data-testid=row]", { timeout: 20000 });
  }
  async function pick(page, name) {
    await page.locator("[data-testid=row]", { hasText: name }).first().click();
    await page.waitForSelector("[data-testid=pane-body]");
  }
  const val = (page, sel) => page.inputValue(sel);

  // ---- item 1: Ask Claude, status route absent (real server 404) ----
  {
    const { page, ctx } = await newPage({ mocks: { statusCode: 404 } });
    await search(page, "alpha");
    await pick(page, "alpha one");
    await page.locator("[data-testid=row]", { hasText: "alpha one" }).first().click({ button: "right" });
    const item = page.locator("[data-verb=ask_claude]");
    check("1a ask item disabled when status route 404s", await item.isDisabled());
    check("1a reason shown", (await page.locator("[data-testid=ask-unavailable]").textContent()) === "Claude Code not found on this machine");
    check("1a marker kept (data-scope sends-to-claude)", (await item.getAttribute("data-scope")) === "sends-to-claude");
    await ctx.close();
  }
  {
    const { page, ctx } = await newPage({ mocks: { statusCode: 500 } });
    await search(page, "alpha");
    await pick(page, "alpha one");
    await page.locator("[data-testid=row]", { hasText: "alpha one" }).first().click({ button: "right" });
    check("1a ask item disabled when status route errors (500)", await page.locator("[data-verb=ask_claude]").isDisabled());
    await ctx.close();
  }
  {
    // the real status route: the menu item must agree with what the server says
    const res = await fetch(BASE + "/v1/actions/ask_claude/status", { headers: { Authorization: "Bearer " + TOKEN } });
    const real = res.status === 200 ? await res.json() : null;
    const { page, ctx } = await newPage();
    await search(page, "alpha");
    await pick(page, "alpha one");
    await page.locator("[data-testid=row]", { hasText: "alpha one" }).first().click({ button: "right" });
    const enabled = await page.locator("[data-verb=ask_claude]").isEnabled();
    check("1a real status route: menu item agrees with it", real === null ? !enabled : enabled === (real.available === true), { real, enabled });
    await ctx.close();
  }
  // ---- available: real prepare summary, real 501 on send ----
  {
    const { page, state, ctx } = await newPage({ mocks: { status: { available: true, version: "2.1.317", reason: null } } });
    await search(page, "alpha");
    await pick(page, "alpha one");
    await page.locator("[data-testid=row]", { hasText: "alpha one" }).first().click({ button: "right" });
    check("1b ask item enabled when status available", await page.locator("[data-verb=ask_claude]").isEnabled());
    await page.click("[data-verb=ask_claude]");
    await page.waitForSelector("[data-testid=send-details]");
    const dialog = page.locator("[data-testid=dialog]");
    const details = await page.locator("[data-testid=send-details]").innerText();
    check("1b confirmation carries data-scope", (await dialog.getAttribute("data-scope")) === "sends-to-claude");
    check("1b real summary shows file name, byte counts and what is sent", /alpha one\.txt/.test(details) && /\d+ of \d+ bytes/.test(details) && /What/.test(details), details);
    known("1c Form row agrees with the What row for a tiny file sent whole (App.jsx:38 reads summary.mode, shell.py:603 sets it from the request)",
      /Whole file/.test(details) === /whole file/i.test(details.split("What")[1] || ""), details);
    check("1b no send yet (only confirm:false posted)", state.verbs.filter((v) => v[0] === "ask_claude").every((v) => v[1] === ""));
    if (await page.locator("[data-testid=dialog-ok]").isEnabled()) {
      await page.click("[data-testid=dialog-ok]");
      await page.waitForSelector("[data-testid=ask-notice]", { timeout: 10000 });
      const notice = await page.locator("[data-testid=ask-notice]").innerText();
      check("1b 501 not_implemented shown as notice in the pane", /not available in this build/i.test(notice), notice);
      check("1b copy disabled on error", await page.locator("[data-testid=ask-copy]").isDisabled());
    } else {
      check("1b Send disabled with the reason when the real summary says Claude is unavailable", /not found|unavailable|sign/i.test(await page.locator("[data-testid=dialog-warning]").innerText()));
    }
    await ctx.close();
  }
  // ---- mocked full interface ----
  {
    const summary = { path: ROOT + "\\docs\\alpha one.txt", bytes_to_send: 6000, file_bytes: 90000, whole_file: false, what: "the first part of the file's text",
      available: true, reason: null, mode: "preview_text", office_text: true, account: "me@example.test" };
    const { page, state, ctx } = await newPage({ mocks: {
      status: { available: true, version: "2.1.317", reason: null },
      ask: (b) => b.confirm ? { ok: true, message: "Answer from Claude.", answer: "# Summary\n\nThis is **bold** and\n\n- item one\n- item two\n\n```js\nconst a = 1;\n```", logged: true }
        : { ok: false, message: "Ask Claude would send 6000 of 90000 bytes of alpha one.txt to Claude. Nothing is sent until you confirm.", needs_confirmation: true, summary },
    } });
    await search(page, "alpha");
    await pick(page, "alpha one");
    await page.locator("[data-testid=row]", { hasText: "alpha one" }).first().click({ button: "right" });
    await page.click("[data-verb=ask_claude]");
    await page.waitForSelector("[data-testid=send-details]");
    const details = await page.locator("[data-testid=send-details]").innerText();
    check("2a modal shows preview-text mode", /Preview text only/.test(details), details);
    check("2a modal shows Office text flag", /Office text\s+Yes/.test(details), details);
    check("2a modal shows account", /me@example\.test/.test(details), details);
    check("2a modal shows byte counts", /6,000 of 90,000 bytes/.test(details), details);
    await page.keyboard.press("Escape");
    check("2a Esc cancels modal without sending", (await page.locator("[data-testid=dialog]").count()) === 0 && !state.verbs.some((v) => v[0] === "ask_claude" && v[1] === "confirm"));
    await page.locator("[data-testid=row]", { hasText: "alpha one" }).first().click({ button: "right" });
    await page.click("[data-verb=ask_claude]");
    await page.waitForSelector("[data-testid=dialog-ok]");
    await page.click("[data-testid=dialog-ok]");
    await page.waitForSelector("[data-testid=ask-answer]");
    check("2b answer rendered as markdown (h1, strong, 2 li, code)",
      (await page.locator("[data-testid=ask-answer] h1").count()) === 1 && (await page.locator("[data-testid=ask-answer] strong").count()) === 1 &&
      (await page.locator("[data-testid=ask-answer] li").count()) === 2 && (await page.locator("[data-testid=ask-answer] pre code").count()) === 1);
    check("2b pane carries data-scope", (await page.locator("[data-testid=ask-pane]").getAttribute("data-scope")) === "sends-to-claude");
    await page.click("[data-testid=ask-copy]");
    await page.waitForSelector("[data-testid=toast]");
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    check("2b copy button puts the answer text on the clipboard", clip.startsWith("# Summary") && clip.includes("item two"), clip.slice(0, 30));
    await page.keyboard.press("Escape");
    check("2c Esc closes the answer pane first", (await page.locator("[data-testid=ask-pane]").count()) === 0);
    check("2c ...and the selection is still there", (await page.locator("[data-testid=row].selected").count()) === 1);
    await page.keyboard.press("Escape");
    check("2c second Esc clears the selection", (await page.locator("[data-testid=row].selected").count()) === 0);
    await ctx.close();
  }
  // ---- unavailable in summary + plain-text fallback ----
  {
    const summary = { path: ROOT + "\\docs\\alpha one.txt", bytes_to_send: 10, file_bytes: 10, whole_file: true, what: "the whole file's text",
      available: false, reason: "Claude Code not found on this machine", mode: "whole_file", office_text: false, account: null };
    const { page, ctx } = await newPage({ mocks: {
      status: { available: true, version: "x", reason: null },
      ask: () => ({ ok: false, message: "m", needs_confirmation: true, summary }) } });
    await search(page, "alpha");
    await pick(page, "alpha one");
    await page.locator("[data-testid=row]", { hasText: "alpha one" }).first().click({ button: "right" });
    await page.click("[data-verb=ask_claude]");
    await page.waitForSelector("[data-testid=dialog-warning]");
    check("2d Send disabled and reason shown when summary.available is false",
      (await page.locator("[data-testid=dialog-ok]").isDisabled()) && /not found on this machine/.test(await page.locator("[data-testid=dialog-warning]").innerText()));
    await ctx.close();
  }
  // ---- item 2: A23 ----
  {
    const { page, ctx } = await newPage({ mocks: { preview: { kind: "placeholder", reason: "ignored server text", size: 10, mtime: 1 } } });
    await search(page, "alpha");
    await pick(page, "alpha one");
    await page.waitForSelector("[data-testid=preview-placeholder]");
    const text = await page.locator("[data-testid=preview-placeholder]").innerText();
    check("3a no Download and preview button", (await page.locator("[data-testid=preview-hydrate]").count()) === 0 && !/Download and preview/.test(text));
    check("3a card says in the cloud, not downloaded, Open downloads it", /in the cloud/.test(text) && /not downloaded/.test(text) && /Open downloads it/.test(text), text.slice(0, 200));
    await ctx.close();
  }
  // ---- item 3: keyboard parity ----
  {
    const { page, state, ctx } = await newPage();
    await search(page, "alpha");
    await pick(page, "alpha one");
    await page.locator("[data-testid=query]").focus();
    await page.keyboard.press("Control+c");
    await page.waitForSelector("[data-testid=toast]");
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    check("4a Ctrl+C copies the path", /alpha one\.txt$/.test(clip), clip);
    await page.keyboard.press("Delete");
    await page.waitForSelector("[data-testid=dialog]");
    check("4b Delete opens the page's own Recycle confirm", /Recycle/.test(await page.locator("[data-testid=dialog] h2").innerText()) && !state.verbs.some((v) => v[0] === "recycle"));
    await page.keyboard.press("Escape");
    check("4b Esc cancels it, nothing recycled", (await page.locator("[data-testid=dialog]").count()) === 0 && !state.verbs.some((v) => v[0] === "recycle"));
    await page.locator("[data-testid=query]").focus();
    await page.keyboard.press("F2");
    await page.waitForSelector("[data-testid=dialog-input]");
    check("4c F2 opens Rename", (await val(page, "[data-testid=dialog-input]")) === "alpha one.txt");
    await page.keyboard.press("Escape");
    await page.keyboard.press("Alt+Enter");
    await page.waitForTimeout(400);
    check("4d Alt+Enter runs properties (not a new search)", state.verbs.some((v) => v[0] === "properties"));
    // typing guard: Delete with an edited (unsearched) query does not recycle
    await page.fill("[data-testid=query]", "alpha x");
    await page.locator("[data-testid=query]").focus();
    await page.keyboard.press("Delete");
    await page.waitForTimeout(300);
    check("4e Delete in an edited query box only edits text (no dialog)", (await page.locator("[data-testid=dialog]").count()) === 0);
    await page.fill("[data-testid=query]", "alpha");
    await page.keyboard.press("Enter");
    await page.waitForSelector("[data-testid=row]");
    await pick(page, "alpha one");
    // folder listing
    await page.keyboard.press("Control+f");
    await page.waitForSelector("[data-testid=listing]");
    const rowsText = await page.locator("[data-testid=listing-rows]").innerText();
    check("4f Ctrl+F opens the folder listing from folder_listing", /notes\.md/.test(rowsText) && /alpha two\.txt/.test(rowsText) && /sub/.test(rowsText), rowsText);
    check("4f listing request went to folder_listing", state.verbs.some((v) => v[0] === "folder_listing"));
    await page.locator("[data-testid=listing-row]", { hasText: "sub" }).dblclick();
    await page.waitForFunction(() => document.querySelector("[data-testid=listing-rows]")?.innerText.includes("inner alpha"));
    check("4f drill into a folder + Back button", (await page.locator("[data-testid=listing-back]").count()) === 1);
    await page.click("[data-testid=listing-back]");
    await page.waitForFunction(() => document.querySelector("[data-testid=listing-rows]")?.innerText.includes("notes.md"));
    await page.keyboard.press("Escape");
    check("4f Esc closes the listing only (selection kept)", (await page.locator("[data-testid=listing]").count()) === 0 && (await page.locator("[data-testid=row].selected").count()) === 1);
    // Ctrl+D
    const seen = [];
    for (let i = 0; i < 5; i++) { await page.keyboard.press("Control+d"); seen.push(await val(page, "[data-testid=modified]")); }
    check("4g Ctrl+D cycles any, today, 7d, 30d, any", JSON.stringify(seen) === JSON.stringify(["today", "7d", "30d", "", "today"]), seen);
    // sort / save
    const sortBefore = await page.locator(".segmented button.on").innerText();
    await page.fill("[data-testid=query]", "alpha");
    await page.keyboard.press("Control+Shift+S");
    await page.waitForTimeout(500);
    const sortAfterSave = await page.locator(".segmented button.on").innerText();
    check("4h Ctrl+Shift+S saves the search", state.saved.length === 1 && state.saved[0].query === "alpha" && state.saved[0].name === "alpha", state.saved);
    check("4h Ctrl+Shift+S does not change sort", sortAfterSave === sortBefore, [sortBefore, sortAfterSave]);
    await page.keyboard.press("Control+s");
    await page.waitForTimeout(300);
    const sortAfterCycle = await page.locator(".segmented button.on").innerText();
    check("4i Ctrl+S cycles sort", sortAfterCycle !== sortAfterSave && state.saved.length === 1, [sortAfterSave, sortAfterCycle]);
    // Esc order
    await page.fill("[data-testid=query]", "alpha");
    await page.keyboard.press("Enter");
    await page.waitForSelector("[data-testid=row]");
    await pick(page, "alpha two");
    await page.keyboard.press("Escape");
    const afterOne = [(await page.locator("[data-testid=row].selected").count()), await val(page, "[data-testid=query]")];
    await page.keyboard.press("Escape");
    const afterTwo = [await val(page, "[data-testid=query]"), await page.locator("[data-testid=row]").count()];
    check("4j Esc order: selection, then query", afterOne[0] === 0 && afterOne[1] === "alpha" && afterTwo[0] === "" && afterTwo[1] === 0, [afterOne, afterTwo]);
    await ctx.close();
  }
  // ---- item 4: theme ----
  {
    const t = async (opts, mocks) => { const r = await newPage({ scheme: opts.scheme, mocks }); await r.page.evaluate(() => localStorage.removeItem("assistant.theme")); return r; };
    let { page, ctx } = await newPage({ scheme: "light", mocks: { settings: { theme: "system" } } });
    await page.evaluate(() => localStorage.removeItem("assistant.theme"));
    // reload loses launch nonce; re-open
    await page.goto(BASE + "/app/?launch=qa"); await page.waitForSelector("[data-testid=query]");
    const th = () => page.evaluate(() => document.documentElement.dataset.theme);
    await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
    check("5a setting=system + OS light -> light", (await th()) === "light");
    await page.emulateMedia({ colorScheme: "dark" });
    await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
    check("5b live OS change to dark flips the page without reload", (await th()) === "dark");
    await page.emulateMedia({ colorScheme: "light" });
    await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
    check("5b ...and back to light", (await th()) === "light");
    await ctx.close();

    ({ page, ctx } = await newPage({ scheme: "dark", mocks: { settings: { theme: "light" } } }));
    await page.evaluate(() => localStorage.removeItem("assistant.theme"));
    await page.goto(BASE + "/app/?launch=qa"); await page.waitForSelector("[data-testid=query]");
    await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
    check("5c app setting light is the default with no stored preference (OS dark)", (await th()) === "light");
    const themePosts = [];
    page.on("request", (r) => { if (r.method() === "POST" && /\/v1\/settings\/theme$/.test(r.url())) themePosts.push(JSON.parse(r.postData() || "{}").theme); });
    const realTheme = async () => (await (await fetch(BASE + "/v1/settings", { headers: { Authorization: "Bearer " + TOKEN } })).json()).theme;
    const setRealTheme = (theme) => fetch(BASE + "/v1/settings/theme", { method: "POST", headers: { Authorization: "Bearer " + TOKEN, "Content-Type": "application/json" }, body: JSON.stringify({ theme }) });
    await page.click("[data-testid=theme-toggle]"); // light -> system
    const labelSystem = await page.getAttribute("[data-testid=theme-toggle]", "aria-label");
    await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
    check("5d toggle light -> system follows OS dark", (await th()) === "dark" && /Theme: system/.test(labelSystem), labelSystem);
    check("5d the toggle POSTs /v1/settings/theme with the next theme", themePosts.length === 1 && themePosts[0] === "system", themePosts);
    check("5d after a successful POST the page keeps no stored preference (the app setting is the source)", (await page.evaluate(() => localStorage.getItem("assistant.theme"))) === null);
    check("5d the app really saved it (GET /v1/settings)", (await realTheme()) === "system");
    await page.click("[data-testid=theme-toggle]"); // system -> dark
    await page.click("[data-testid=theme-toggle]"); // dark -> light
    await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
    check("5e toggle cycles back to light", (await th()) === "light");
    check("5e one POST per click, in cycle order", JSON.stringify(themePosts) === JSON.stringify(["system", "dark", "light"]), themePosts);
    await page.goto(BASE + "/app/?launch=qa"); await page.waitForSelector("[data-testid=query]");
    await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
    check("5f after a reload the saved app setting applies and no stored preference exists", (await th()) === "light" && (await page.evaluate(() => localStorage.getItem("assistant.theme"))) === null);
    check("5f the app setting is what the real route reports", (await realTheme()) === "light");
    await ctx.close();

    await setRealTheme("dark");
    ({ page, ctx } = await newPage({ scheme: "light" })); // the real settings route, theme dark, OS light
    await page.evaluate(() => localStorage.removeItem("assistant.theme"));
    await page.goto(BASE + "/app/?launch=qa"); await page.waitForSelector("[data-testid=query]");
    await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
    check("5g default theme from the real settings route (dark) beats an OS in light mode", (await th()) === "dark");
    await ctx.close();

    // the POST route is missing (older app): only then the page keeps its own preference in localStorage
    ({ page, ctx } = await newPage({ scheme: "dark", mocks: { settings: { theme: "dark" } } }));
    await page.evaluate(() => localStorage.removeItem("assistant.theme"));
    await page.route("**/v1/settings/theme", (r) => r.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "not_found", message: "no" } }) }));
    await page.goto(BASE + "/app/?launch=qa"); await page.waitForSelector("[data-testid=query]");
    await page.click("[data-testid=theme-toggle]"); // dark -> light
    await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
    check("5h POST 404: the toggle still works and the choice is kept in localStorage", (await th()) === "light" && (await page.evaluate(() => localStorage.getItem("assistant.theme"))) === "light");
    await page.goto(BASE + "/app/?launch=qa"); await page.waitForSelector("[data-testid=query]");
    await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
    check("5h POST 404: the stored preference survives a reload (it beats the app setting)", (await th()) === "light");
    await ctx.close();

    // any other failure is not the 404 case: the choice must not be silently kept as a page-only preference
    ({ page, ctx } = await newPage({ scheme: "dark", mocks: { settings: { theme: "dark" } } }));
    await page.evaluate(() => localStorage.removeItem("assistant.theme"));
    await page.route("**/v1/settings/theme", (r) => r.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { code: "boom", message: "no" } }) }));
    await page.goto(BASE + "/app/?launch=qa"); await page.waitForSelector("[data-testid=query]");
    await page.click("[data-testid=theme-toggle]");
    await page.waitForTimeout(500);
    await page.waitForTimeout(300);
    check("5i POST 500: the theme is restored (still dark)", (await th()) === "dark" && /Theme: dark/.test(await page.getAttribute("[data-testid=theme-toggle]", "aria-label")));
    check("5i POST 500: nothing is written to localStorage", (await page.evaluate(() => localStorage.getItem("assistant.theme"))) === null);
    check("5i POST 500: an error toast is shown", (await page.locator(".toast").count()) >= 1, await page.locator(".toast").allInnerTexts().catch(() => []));
    await ctx.close();
    ({ page, ctx } = await newPage({ scheme: "dark", mocks: { settings: { theme: "dark" } } }));
    await page.evaluate(() => localStorage.removeItem("assistant.theme"));
    await page.route("**/v1/settings/theme", (r) => r.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ error: { code: "unauthorized", message: "no" } }) }));
    await page.goto(BASE + "/app/?launch=qa"); await page.waitForSelector("[data-testid=query]");
    check("5j POST 401: before the click the theme is dark", (await th()) === "dark");
    await page.click("[data-testid=theme-toggle]");
    await page.waitForTimeout(500);
    check("5j POST 401: the theme is unchanged (dark)", (await th()) === "dark", [await th(), await page.locator("body").innerText()]);
    check("5j POST 401: nothing is written to localStorage", (await page.evaluate(() => localStorage.getItem("assistant.theme"))) === null);
    await ctx.close();
    await setRealTheme("dark");
  }
  // ---- live search, URL q, ghost Tab on the full page ----
  {
    const fresh = async (opts = {}) => {
      const made = await newPage(opts);
      const { page } = made;
      made.searches = [];
      page.on("request", (r) => { if (/\/v1\/search\?/.test(r.url())) made.searches.push(new URL(r.url()).searchParams.get("q")); });
      return made;
    };
    const typed = async (page, text, delay) => { await page.click("[data-testid=query]"); await page.keyboard.type(text, { delay }); };
    {
      const m = await fresh();
      await typed(m.page, "testing", 30);
      await m.page.waitForTimeout(900);
      check("7a typing 'testing' fast sends exactly one search request", m.searches.length === 1 && m.searches[0] === "testing", m.searches);
      check("7a the search ran without pressing Enter (the empty-result state is shown)", (await m.page.locator("[data-testid=row]").count()) === 0 && (await m.page.locator("[data-testid=query]").inputValue()) === "testing");
      await m.ctx.close();
    }
    {
      const m = await fresh();
      await typed(m.page, "alph", 10);
      await m.page.waitForSelector("[data-testid=row]", { timeout: 15000 });
      check("7b live search lists results without Enter", (await m.page.locator("[data-testid=row]").count()) >= 3);
      m.searches.length = 0;
      await m.page.keyboard.type("a", { delay: 10 });
      await m.page.waitForTimeout(800);
      check("7b one more request for the extended text", m.searches.length === 1 && m.searches[0] === "alpha", m.searches);
      await m.ctx.close();
    }
    {
      // the first request is slow, the second supersedes it: the first must be aborted and must not paint
      const { page, ctx } = await newPage();
      await page.addInitScript(() => {
        window.__searches = [];
        const real = window.fetch;
        window.fetch = function (input, init) {
          const url = typeof input === "string" ? input : input.url;
          if (/\/v1\/search\?/.test(url)) {
            const entry = { q: new URL(url, location.href).searchParams.get("q"), aborted: false };
            window.__searches.push(entry);
            if (init && init.signal) init.signal.addEventListener("abort", () => { entry.aborted = true; });
          }
          return real.apply(this, arguments);
        };
      });
      await page.route("**/v1/search?**", async (route) => { await new Promise((r) => setTimeout(r, 700)); await route.continue().catch(() => {}); });
      await page.goto(BASE + "/app/?launch=qa"); await page.waitForSelector("[data-testid=query]");
      await page.click("[data-testid=query]");
      await page.keyboard.type("inner", { delay: 5 });
      await page.waitForTimeout(400);
      await page.keyboard.type(" alpha", { delay: 5 });
      await page.waitForSelector("[data-testid=row]", { timeout: 15000 });
      await page.waitForTimeout(500);
      const log = await page.evaluate(() => window.__searches);
      check("7c typing past the pause sends a second request and aborts the first", log.length === 2 && log[0].q === "inner" && log[0].aborted === true && log[1].q === "inner alpha" && log[1].aborted === false, log);
      const names = await page.locator("[data-testid=row]").allInnerTexts();
      check("7c only the newest search's rows are shown", names.length >= 1 && names.every((n) => /inner/i.test(n)), names);
      await ctx.close();
    }
    {
      const m = await fresh();
      await typed(m.page, "alpha", 10);
      await m.page.waitForSelector("[data-testid=row]", { timeout: 15000 });
      await m.page.waitForTimeout(300);
      const q1 = await m.page.evaluate(() => new URL(location.href).searchParams.get("q"));
      const launchGone = await m.page.evaluate(() => !new URL(location.href).searchParams.has("launch"));
      const histLen = await m.page.evaluate(() => history.length);
      check("7d the URL carries q=alpha and no launch nonce", q1 === "alpha" && launchGone, q1);
      await m.page.keyboard.type(" one", { delay: 10 });
      await m.page.waitForTimeout(400);
      check("7d replaceState: typing adds no history entries", (await m.page.evaluate(() => history.length)) === histLen);
      check("7d the URL follows the box", (await m.page.evaluate(() => new URL(location.href).searchParams.get("q"))) === "alpha one");
      await m.page.fill("[data-testid=query]", "");
      await m.page.waitForTimeout(300);
      check("7d clearing the box removes q from the URL", (await m.page.evaluate(() => new URL(location.href).searchParams.has("q"))) === false);
      check("7d clearing the box returns to the empty state", (await m.page.locator("[data-testid=row]").count()) === 0);
      await m.ctx.close();
    }
    {
      const m = await fresh();
      await typed(m.page, "alp", 10);
      await m.page.waitForSelector("[data-testid=row]", { timeout: 15000 });
      await m.page.waitForSelector("[data-testid=query-ghost]", { timeout: 5000 }).catch(() => {});
      const hint = await m.page.locator(".queryghost-hint").innerText().catch(() => "");
      check("7e the ghost shows the rest of the best row's word", hint === "ha", hint);
      await m.page.keyboard.press("Tab");
      await m.page.waitForTimeout(100);
      check("7e Tab accepts the ghost into the box and keeps the focus there", (await m.page.locator("[data-testid=query]").inputValue()) === "alpha" && (await m.page.evaluate(() => document.activeElement && document.activeElement.getAttribute("data-testid"))) === "query");
      await m.page.fill("[data-testid=query]", "alp");
      await m.page.waitForSelector("[data-testid=query-ghost]", { timeout: 15000 });
      await m.page.keyboard.press("Escape");
      await m.page.waitForTimeout(100);
      check("7e Esc dismisses the ghost without changing the text", (await m.page.locator("[data-testid=query-ghost]").count()) === 0 && (await m.page.locator("[data-testid=query]").inputValue()) === "alp");
      await m.page.fill("[data-testid=query]", "zzzz");
      await m.page.waitForTimeout(700);
      await m.page.keyboard.press("Tab");
      check("7e Tab with no ghost moves focus on instead of editing the text", (await m.page.locator("[data-testid=query]").inputValue()) === "zzzz" && (await m.page.evaluate(() => document.activeElement && document.activeElement.getAttribute("data-testid"))) !== "query");
      await m.ctx.close();
    }
    {
      const m = await fresh();
      await typed(m.page, "alpha", 10);
      await m.page.waitForSelector("[data-testid=row]", { timeout: 15000 });
      m.searches.length = 0;
      await m.page.press("[data-testid=query]", "Enter");
      await m.page.waitForTimeout(500);
      check("7f Enter runs the search again at once", m.searches.length === 1 && m.searches[0] === "alpha", m.searches);
      await m.ctx.close();
    }
  }
  // ---- M4-3b parity (ui agent's harness, folded in): custom shortcuts, settings fallbacks, Continue in Claude Code, whole-file ----
  {
  const json = (r, status, body) => r.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  async function newPageB({ settings, settingsStatus, cont, ask } = {}) {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, permissions: ["clipboard-read", "clipboard-write"] });
    const page = await ctx.newPage();
    const st = { verbs: [], bodies: [], cont: [], saved: [] };
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push("pageerror " + e.message));
    await page.route("**/v1/session/exchange", (r) => json(r, 200, { token: TOKEN }));
    await page.route("**/v1/actions/ask_claude/status", (r) => json(r, 200, { available: true, version: "x", reason: null }));
    if (settingsStatus) await page.route("**/v1/settings", (r) => json(r, settingsStatus, { error: { code: "x", message: "no" } }));
    else if (settings) await page.route("**/v1/settings", (r) => json(r, 200, settings));
    await page.route("**/v1/saved", (r) => { if (r.request().method() === "POST") st.saved.push(1); return r.fallback(); });
    await page.route("**/v1/actions/ask_claude/continue", (r) => {
      st.cont.push(JSON.parse(r.request().postData() || "{}"));
      if (cont === "404") return r.fulfill({ status: 404, contentType: "text/plain", body: "Not Found" });
      if (cont === "500") return json(r, 500, { error: { code: "error", message: "claude blew up" } });
      return json(r, 200, { ok: true, message: "launched" });
    });
    await page.route("**/v1/actions/*", async (route) => {
      const req = route.request();
      if (req.method() !== "POST" || req.url().endsWith("/continue")) return route.fallback();
      const verb = new URL(req.url()).pathname.split("/").pop();
      const body = JSON.parse(req.postData() || "{}");
      st.verbs.push(verb); st.bodies.push([verb, body]);
      if (verb === "ask_claude" && ask) return json(route, 200, ask(body));
      // an un-mocked confirm:true send never reaches the real server (it would run `claude` on a fixture file)
      if (verb === "ask_claude" && body.confirm === true) return json(route, 501, { error: { code: "not_implemented", message: "Ask Claude is not available in this build." } });
      if (verb === "ask_claude") return route.continue();
      if (verb === "folder_listing") return route.continue();
      return json(route, 200, { ok: true, message: verb + " ok" });
    });
    await page.goto(BASE + "/app/?launch=qa");
    await page.waitForSelector("[data-testid=query]");
    await page.waitForTimeout(400);
    return { page, st, ctx };
  }
  const kbds = async (page) => {
    await page.locator("[data-testid=row]", { hasText: "alpha one" }).first().click({ button: "right" });
    const out = {};
    for (const verb of ["open", "reveal", "copy_path", "rename", "properties", "md_vault", "recycle"]) {
      const k = page.locator(`[data-verb=${verb}] kbd`);
      out[verb] = (await k.count()) ? await k.textContent() : null;
    }
    await page.keyboard.press("Escape");
    return out;
  };

  // 1: custom shortcuts
  {
    const settings = { theme: "dark", shortcuts: { rename: "f3", properties: "alt+p", recycle: "Hyper+X", md_vault: "Ctrl+", reveal: "ctrl+shift+enter",
      save_search: "Ctrl+Alt+S", cycle_sort: "Ctrl+Y", cycle_date: "ctrl+q", folder_listing: "Alt+L", copy_path: "Ctrl+Shift+C", close: "esc", full_page: 5 } };
    const { page, st, ctx } = await newPageB({ settings });
    await search(page, "alpha");
    await pick(page, "alpha one");
    await page.locator("[data-testid=query]").focus();
    await page.keyboard.press("F2");
    await page.waitForTimeout(300);
    check("1a default F2 no longer renames when rename=f3", (await page.locator("[data-testid=dialog]").count()) === 0);
    await page.keyboard.press("F3");
    await page.waitForSelector("[data-testid=dialog-input]", { timeout: 3000 }).catch(() => {});
    check("1b rename=f3 (lower-case) opens Rename", (await page.locator("[data-testid=dialog-input]").count()) === 1);
    await page.keyboard.press("Escape");
    await page.keyboard.press("Alt+p");
    await page.waitForTimeout(300);
    check("1c properties=alt+p runs properties", st.verbs.includes("properties"));
    const before = st.verbs.length;
    await page.keyboard.press("Alt+Enter");
    await page.waitForTimeout(300);
    check("1d old Alt+Enter no longer runs properties", st.verbs.length === before);
    await page.keyboard.press("Delete");
    await page.waitForSelector("[data-testid=dialog]", { timeout: 3000 }).catch(() => {});
    check("1e malformed recycle chord falls back to Delete", (await page.locator("[data-testid=dialog]").count()) === 1);
    await page.keyboard.press("Escape");
    await page.keyboard.press("Control+m");
    await page.waitForTimeout(300);
    check("1f malformed md_vault 'Ctrl+' falls back to Ctrl+M", st.verbs.includes("md_vault"));
    await page.keyboard.press("Control+Shift+Enter");
    await page.waitForTimeout(300);
    check("1g reveal=ctrl+shift+enter runs reveal", st.verbs.includes("reveal"));
    st.verbs.length = 0;
    await page.keyboard.press("Control+Enter");
    await page.waitForTimeout(300);
    check("1h old Ctrl+Enter no longer reveals", !st.verbs.includes("reveal"));
    await page.keyboard.press("Alt+l");
    await page.waitForSelector("[data-testid=listing]", { timeout: 4000 }).catch(() => {});
    check("1i folder_listing=Alt+L opens listing", (await page.locator("[data-testid=listing]").count()) === 1);
    await page.keyboard.press("Escape");
    const seen = [];
    await page.keyboard.press("Control+q"); seen.push(await page.inputValue("[data-testid=modified]"));
    await page.keyboard.press("Control+d"); seen.push(await page.inputValue("[data-testid=modified]"));
    check("1j cycle_date=ctrl+q cycles; Ctrl+D does not", seen[0] === "today" && seen[1] === "today", seen);
    const sortBefore = await page.locator(".segmented button.on").innerText();
    await page.keyboard.press("Control+s");
    await page.waitForTimeout(250);
    const same = (await page.locator(".segmented button.on").innerText()) === sortBefore;
    await page.keyboard.press("Control+y");
    await page.waitForTimeout(250);
    const changed = (await page.locator(".segmented button.on").innerText()) !== sortBefore;
    check("1k cycle_sort=Ctrl+Y cycles; Ctrl+S does not", same && changed);
    await page.fill("[data-testid=query]", "alpha");
    await page.keyboard.press("Control+Shift+S"); await page.waitForTimeout(300);
    const oldSave = st.saved.length;
    await page.keyboard.press("Control+Alt+s"); await page.waitForTimeout(400);
    check("1l save_search=Ctrl+Alt+S saves; Ctrl+Shift+S does not", oldSave === 0 && st.saved.length === 1, [oldSave, st.saved.length]);
    await page.keyboard.press("Enter"); await page.waitForSelector("[data-testid=row]");
    await pick(page, "alpha one");
    await page.locator("[data-testid=query]").focus();
    await page.keyboard.press("Control+c");
    await page.waitForTimeout(250);
    const toastText = async () => ((await page.locator("[data-testid=toast]").count()) ? await page.locator("[data-testid=toast]").textContent() : "");
    const noCopy = (await toastText()) !== "Path copied";
    await page.keyboard.press("Control+Shift+C");
    await page.waitForFunction(() => document.querySelector("[data-testid=toast]")?.textContent === "Path copied", null, { timeout: 3000 }).catch(() => {});
    check("1m copy_path=Ctrl+Shift+C copies; Ctrl+C does not", noCopy && (await toastText()) === "Path copied", await toastText());
    const labels = await kbds(page);
    check("1n menu labels follow settings", labels.rename === "F3" && labels.properties === "Alt+P" && labels.reveal === "Ctrl+Shift+Enter" && labels.copy_path === "Ctrl+Shift+C"
      && labels.md_vault === "Ctrl+M" && labels.recycle === "Delete" && labels.open === "Enter", labels);
    await ctx.close();
  }
  // 2: settings route 404 / 500 / no shortcuts -> defaults
  for (const [name, opts] of [["404", { settingsStatus: 404 }], ["500", { settingsStatus: 500 }], ["theme only", { settings: { theme: "dark" } }], ["junk shortcuts", { settings: { shortcuts: [1, 2] } }]]) {
    const { page, st, ctx } = await newPageB(opts);
    await search(page, "alpha");
    await pick(page, "alpha one");
    await page.locator("[data-testid=query]").focus();
    await page.keyboard.press("F2");
    await page.waitForSelector("[data-testid=dialog-input]", { timeout: 3000 }).catch(() => {});
    const ok = (await page.locator("[data-testid=dialog-input]").count()) === 1;
    await page.keyboard.press("Escape");
    await page.keyboard.press("Alt+Enter"); await page.waitForTimeout(250);
    const labels = await kbds(page);
    check(`2 settings ${name}: defaults (F2 rename, Alt+Enter properties, labels)`, ok && st.verbs.includes("properties") && labels.rename === "F2" && labels.reveal === "Ctrl+Enter" && labels.md_vault === "Ctrl+M" && labels.recycle === "Delete" && labels.properties === "Alt+Enter" && labels.copy_path === "Ctrl+C", labels);
    await ctx.close();
  }
  // 3: Continue in Claude Code
  const answerMock = (b) => b.confirm ? { ok: true, message: "ok", answer: "# Hi\n\ntext", logged: true }
    : { ok: false, message: "m", needs_confirmation: true, summary: { path: ROOT + "\\docs\\alpha one.txt", bytes_to_send: 11, file_bytes: 11, whole_file: true, what: "w", available: true, mode: "whole_file", office_text: false, account: null } };
  async function toAnswer(page) {
    await search(page, "alpha");
    await pick(page, "alpha one");
    await page.locator("[data-testid=row]", { hasText: "alpha one" }).first().click({ button: "right" });
    await page.click("[data-verb=ask_claude]");
    await page.waitForSelector("[data-testid=dialog-ok]");
    await page.click("[data-testid=dialog-ok]");
    await page.waitForSelector("[data-testid=ask-answer]");
  }
  {
    const { page, st, ctx } = await newPageB({ cont: "200", ask: answerMock });
    await toAnswer(page);
    const btn = page.locator("[data-testid=ask-continue]");
    check("3a button present, enabled, has sends-to-claude marker", (await btn.count()) === 1 && (await btn.isEnabled()) && (await btn.getAttribute("data-scope")) === "sends-to-claude" && /Continue in Claude Code/.test(await btn.innerText()));
    await btn.click();
    await page.waitForFunction(() => /Opened in Claude Code/.test(document.querySelector("[data-testid=toast]")?.textContent || ""), null, { timeout: 4000 }).catch(() => {});
    check("3b success toast Opened in Claude Code", (await page.locator("[data-testid=toast]").textContent()) === "Opened in Claude Code");
    check("3b posted only {path} to /continue", st.cont.length === 1 && /alpha one\.txt$/.test(st.cont[0].path) && Object.keys(st.cont[0]).join() === "path", st.cont);
    await ctx.close();
  }
  {
    const { page, ctx } = await newPageB({ cont: "404", ask: answerMock });
    await toAnswer(page);
    const btn = page.locator("[data-testid=ask-continue]");
    await btn.click();
    await page.waitForTimeout(500);
    check("3c 404 route -> button disabled, says not available, no success toast", (await btn.isDisabled()) && /not available/i.test(await btn.innerText()) && (await page.locator("[data-testid=toast]").count()) === 0, await btn.innerText());
    check("3c answer still shown", (await page.locator("[data-testid=ask-answer]").count()) === 1);
    await ctx.close();
  }
  {
    const { page, ctx } = await newPageB({ cont: "500", ask: answerMock });
    await toAnswer(page);
    const btn = page.locator("[data-testid=ask-continue]");
    await btn.click();
    await page.waitForSelector("[data-testid=toast].error", { timeout: 3000 }).catch(() => {});
    check("3d server error -> error toast with message, button stays enabled", /claude blew up/.test(await page.locator("[data-testid=toast]").textContent()) && (await btn.isEnabled()));
    await ctx.close();
  }
  {
    const { page, ctx } = await newPageB({ cont: "200", ask: answerMock });
    await search(page, "alpha");
    await pick(page, "alpha one");
    await page.locator("[data-testid=row]", { hasText: "alpha one" }).first().click({ button: "right" });
    await page.click("[data-verb=ask_claude]");
    await page.waitForSelector("[data-testid=dialog-ok]");
    check("3e no checkbox when the file is already sent whole", (await page.locator("[data-testid=dialog-option]").count()) === 0);
    await ctx.close();
  }
  // 4: whole_file
  const summary = (whole, capped) => ({ path: ROOT + "\\docs\\alpha one.txt", bytes_to_send: whole ? (capped ? 50000 : 90000) : 6000, file_bytes: 90000, whole_file: whole && !capped,
    what: whole && !capped ? "the whole file's text" : "the first part of the file's text", available: true, reason: null, mode: whole && !capped ? "whole_file" : "preview_text", office_text: false, account: null });
  for (const capped of [false, true]) {
    const tag = capped ? "-capped" : "";
    const { page, st, ctx } = await newPageB({ ask: (b) => b.confirm ? { ok: true, message: "ok", answer: "A" } : { ok: false, message: "would send", needs_confirmation: true, summary: summary(b.whole_file === true, capped) } });
    await search(page, "alpha");
    await pick(page, "alpha one");
    await page.locator("[data-testid=row]", { hasText: "alpha one" }).first().click({ button: "right" });
    await page.click("[data-verb=ask_claude]");
    await page.waitForSelector("[data-testid=send-details]");
    const opt = page.locator("[data-testid=dialog-option]");
    const label = (await page.locator(".sendoption").textContent()).trim();
    const d0 = await page.locator("[data-testid=send-details]").innerText();
    check(`4a${tag} checkbox unchecked, label Send the whole file (90,000 bytes), preview details`, label === "Send the whole file (90,000 bytes)" && !(await opt.isChecked()) && /6,000 of 90,000/.test(d0) && /Preview text only/.test(d0), [label, d0]);
    await opt.check();
    await page.waitForTimeout(500);
    const d1 = await page.locator("[data-testid=send-details]").innerText();
    const reqs = st.bodies.filter(([v]) => v === "ask_claude").map(([, b]) => b);
    check(`4b${tag} re-requested with {path, confirm:false, whole_file:true}`, reqs.length === 2 && reqs[1].confirm === false && reqs[1].whole_file === true && /alpha one\.txt$/.test(reqs[1].path), reqs);
    if (!capped) {
      check("4c honest count after checking: 90,000 of 90,000 + Whole file, still checked", /90,000 of 90,000/.test(d1) && /Whole file/.test(d1) && (await page.locator("[data-testid=dialog-note]").count()) === 0 && (await opt.isChecked()), d1);
    } else {
      check("4c-capped honest count 50,000 of 90,000, Preview form, cut-off note shown", /50,000 of 90,000/.test(d1) && /Preview text only/.test(d1) && /Only 50,000 bytes/.test(await page.locator("[data-testid=dialog-note]").textContent()), d1);
    }
    await opt.focus();
    await page.keyboard.press("Enter");
    await page.waitForTimeout(200);
    check(`4d${tag} Enter on the checkbox does not send`, st.bodies.filter(([v, b]) => v === "ask_claude" && b.confirm === true).length === 0);
    await page.click("[data-testid=dialog-ok]");
    await page.waitForSelector("[data-testid=ask-answer]");
    const sent = st.bodies.filter(([v, b]) => v === "ask_claude" && b.confirm === true).map(([, b]) => b);
    check(`4e${tag} Send posts whole_file:true`, sent.length === 1 && sent[0].whole_file === true, sent);
    await ctx.close();
  }
  {
    const { page, st, ctx } = await newPageB({ ask: (b) => b.confirm ? { ok: true, answer: "A" } : { ok: false, message: "m", needs_confirmation: true, summary: summary(b.whole_file === true, false) } });
    await search(page, "alpha");
    await pick(page, "alpha one");
    await page.locator("[data-testid=row]", { hasText: "alpha one" }).first().click({ button: "right" });
    await page.click("[data-verb=ask_claude]");
    await page.waitForSelector("[data-testid=dialog-option]");
    await page.locator("[data-testid=dialog-option]").check(); await page.waitForTimeout(400);
    await page.locator("[data-testid=dialog-option]").uncheck(); await page.waitForTimeout(400);
    const d = await page.locator("[data-testid=send-details]").innerText();
    await page.click("[data-testid=dialog-ok]");
    await page.waitForSelector("[data-testid=ask-answer]");
    const sent = st.bodies.filter(([v, b]) => v === "ask_claude" && b.confirm === true).map(([, b]) => b);
    check("4f uncheck restores preview details and Send omits whole_file", /6,000 of 90,000/.test(d) && sent.length === 1 && !("whole_file" in sent[0]), [d, sent]);
    await ctx.close();
  }
  }
  // ---- item 6: M4-3c reading comfort: split handle, expand mode, zoom (assistant.split / assistant.previewZoom) ----
  {
    const NOTES = "zoomfix notes", PDF = "zoomfix sample";
    const ls = (page, key) => page.evaluate((k) => localStorage.getItem(k), key);
    const paneW = async (page) => (await page.locator("[data-testid=pane]").boundingBox()).width;
    const listW = async (page) => (await page.locator(".listwrap").boundingBox()).width;
    const handleBox = (page) => page.locator("[data-testid=split-handle]").boundingBox();
    const valueNow = (page) => page.getAttribute("[data-testid=split-handle]", "aria-valuenow");
    const zoomOf = (page) => page.getAttribute("[data-testid=pane]", "data-zoom");
    const title = async (page) => ((await page.locator(".pane-title strong").count()) ? (await page.locator(".pane-title strong").first().textContent()).trim() : null);
    const open = async (opts, name = NOTES) => {
      const r = await newPage(opts);
      await search(r.page, "zoomfix");
      await pick(r.page, name);
      return r;
    };
    const reopen = async (page, name = NOTES) => {
      await page.goto(BASE + "/app/?launch=qa");
      await page.waitForSelector("[data-testid=query]");
      await search(page, "zoomfix");
      await pick(page, name);
    };
    const drag = async (page, dx) => {
      const b = await handleBox(page);
      const x = b.x + b.width / 2, y = b.y + b.height / 2;
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(x + dx / 2, y, { steps: 4 });
      await page.mouse.move(x + dx, y, { steps: 4 });
      await page.mouse.up();
      await page.waitForTimeout(150);
    };

    // 6a: the five new test ids, prose measure
    {
      const { page, ctx } = await open({});
      const ids = ["split-handle", "preview-expand", "preview-zoom-out", "preview-zoom-reset", "preview-zoom-in"];
      const counts = [];
      for (const id of ids) counts.push(await page.locator(`[data-testid=${id}]`).count());
      check("6a the five new test ids exist exactly once", counts.every((n) => n === 1), counts);
      check("6a no preview-back while not expanded", (await page.locator("[data-testid=preview-back]").count()) === 0);
      check("6a split handle is an accessible separator at 50%", (await page.getAttribute("[data-testid=split-handle]", "role")) === "separator" && (await valueNow(page)) === "50" && (await page.getAttribute("[data-testid=split-handle]", "tabindex")) === "0");
      check("6a default zoom is 100% with data-zoom=100", (await zoomOf(page)) === "100" && /^100%$/.test((await page.locator("[data-testid=preview-zoom-reset]").textContent()).trim()));
      await page.waitForSelector("[data-testid=preview-markdown]");
      const prose = await page.evaluate(() => {
        const el = document.querySelector("[data-testid=preview-markdown]");
        const cs = getComputedStyle(el);
        const probe = document.createElement("span");
        probe.style.cssText = "position:absolute;visibility:hidden;width:72ch;font:" + cs.font;
        el.appendChild(probe);
        const ch72 = probe.getBoundingClientRect().width;
        probe.remove();
        return { size: cs.fontSize, line: cs.lineHeight, width: el.getBoundingClientRect().width, ch72 };
      });
      check("6a prose is 16px with 1.6 line height", prose.size === "16px" && Math.abs(parseFloat(prose.line) - 25.6) < 0.2, prose);
      check("6a prose column is at most 72ch wide", prose.width <= prose.ch72 + 1 && prose.width > 300, prose);
      await ctx.close();
    }
    // 6b: drag, clamp, persistence, keyboard, reset
    {
      const { page, ctx } = await open({});
      const w0 = await paneW(page), total = (await page.locator("main.split").boundingBox()).width;
      await drag(page, -150);
      const w1 = await paneW(page);
      check("6b dragging the handle left widens the pane by about the distance", w1 > w0 + 120 && w1 < w0 + 180, [w0, w1]);
      const v1 = await valueNow(page), stored = await ls(page, "assistant.split");
      check("6b ratio changed, aria-valuenow follows and the choice is stored", Number(v1) > 50 && Math.abs(Number(stored) - w1 / total) < 0.03, [v1, stored, w1 / total]);
      await reopen(page);
      check("6b the ratio survives a reload", Math.abs((await paneW(page)) - w1) < 6, [await paneW(page), w1]);
      await drag(page, -2000);
      check("6b dragging far left keeps the list at least 320px", (await listW(page)) >= 318 && (await paneW(page)) > 400, [await listW(page), await paneW(page)]);
      await drag(page, 3000);
      check("6b dragging far right keeps the pane at least 320px", (await paneW(page)) >= 318 && (await listW(page)) > 400, [await listW(page), await paneW(page)]);
      await page.locator("[data-testid=split-handle]").dblclick();
      await page.waitForTimeout(150);
      check("6b double-click resets to 50% and stores it", (await valueNow(page)) === "50" && Number(await ls(page, "assistant.split")) === 0.5);
      await page.focus("[data-testid=split-handle]");
      await page.keyboard.press("ArrowLeft");
      check("6b ArrowLeft widens the pane by 2%", (await valueNow(page)) === "52", await valueNow(page));
      await page.keyboard.press("ArrowRight"); await page.keyboard.press("ArrowRight");
      check("6b ArrowRight narrows it", (await valueNow(page)) === "48", await valueNow(page));
      await page.keyboard.press("Enter");
      check("6b Enter on the handle resets", (await valueNow(page)) === "50");
      await page.keyboard.press("ArrowLeft"); await page.keyboard.press("Home");
      check("6b Home on the handle resets", (await valueNow(page)) === "50");
      check("6b handle keys do not run page shortcuts (no dialog, still one pane)", (await page.locator("[data-testid=dialog]").count()) === 0 && (await page.locator("[data-testid=pane]").count()) === 1);
      await ctx.close();
    }
    // 6c: bad stored values fall back
    {
      const { page, ctx } = await open({});
      await page.evaluate(() => { localStorage.setItem("assistant.split", "0.99"); localStorage.setItem("assistant.previewZoom", "133"); });
      await reopen(page);
      check("6c an out-of-range stored split falls back to 50% and a non-step zoom to 100%", (await valueNow(page)) === "50" && (await zoomOf(page)) === "100", [await valueNow(page), await zoomOf(page)]);
      await page.evaluate(() => { localStorage.setItem("assistant.split", "banana"); localStorage.setItem("assistant.previewZoom", "x"); });
      await reopen(page);
      check("6c junk stored values fall back too", (await valueNow(page)) === "50" && (await zoomOf(page)) === "100");
      await ctx.close();
    }
    // 6d: expand mode
    {
      const { page, ctx } = await open({});
      const first = await title(page);
      await page.click("[data-testid=preview-expand]");
      await page.waitForSelector("[data-testid=preview-back]");
      const listBox = await page.locator(".listwrap").boundingBox();
      check("6d expand hides the result list and the split handle", (!listBox || listBox.width < 2 || !(await page.locator(".listwrap").isVisible())) && (await page.locator("[data-testid=split-handle]").count()) === 0 && (await page.locator("main.split.expanded").count()) === 1, listBox);
      const pw = await paneW(page), mw = (await page.locator("main.split").boundingBox()).width;
      check("6d the pane fills the width", pw > mw * 0.95, [pw, mw]);
      check("6d expand button is pressed and the back button shows", (await page.getAttribute("[data-testid=preview-expand]", "aria-pressed")) === "true");
      await page.keyboard.press("ArrowDown");
      await page.waitForFunction((was) => { const s = document.querySelector(".pane-title strong"); return s && s.textContent.trim() !== was; }, first, { timeout: 8000 }).catch(() => {});
      const second = await title(page);
      check("6d Down in expanded mode changes the selection and stays expanded", second !== first && (await page.locator("main.split.expanded").count()) === 1, [first, second]);
      await page.keyboard.press("ArrowUp");
      await page.waitForFunction((want) => document.querySelector(".pane-title strong")?.textContent.trim() === want, first, { timeout: 8000 }).catch(() => {});
      check("6d Up goes back to the first row", (await title(page)) === first && (await page.locator("[data-testid=preview-back]").count()) === 1);
      await page.keyboard.press("Escape");
      await page.waitForTimeout(250);
      check("6d Esc returns to the list with the selection kept", (await page.locator("main.split.expanded").count()) === 0 && (await page.locator(".listwrap").isVisible()) && (await title(page)) === first && (await page.locator("[data-testid=pane]").count()) === 1);
      await page.keyboard.press("Control+Backslash");
      await page.waitForSelector("[data-testid=preview-back]");
      await page.keyboard.press("Control+Backslash");
      await page.waitForTimeout(250);
      check("6d Ctrl+\\ toggles expand on and off", (await page.locator("main.split.expanded").count()) === 0);
      await page.click("[data-testid=preview-expand]");
      await page.click("[data-testid=preview-back]");
      await page.waitForTimeout(250);
      check("6d the Back to results button restores the list", (await page.locator("main.split.expanded").count()) === 0 && (await page.locator(".listwrap").isVisible()));
      await page.click("[data-testid=preview-expand]");
      await page.click("[data-testid=preview-expand]");
      check("6d the expand button also collapses", (await page.locator("main.split.expanded").count()) === 0);
      await ctx.close();
    }
    // 6e: zoom
    {
      const { page, ctx } = await open({});
      const fs = () => page.evaluate(() => parseFloat(getComputedStyle(document.querySelector("[data-testid=preview-markdown]")).fontSize));
      await page.click("[data-testid=preview-zoom-in]");
      check("6e zoom in steps 100 -> 110", (await zoomOf(page)) === "110" && /110%/.test(await page.locator("[data-testid=preview-zoom-reset]").textContent()));
      check("6e --pv-zoom and the prose size follow (110% of 16px)", (await page.evaluate(() => getComputedStyle(document.querySelector("[data-testid=pane]")).getPropertyValue("--pv-zoom").trim())) === "1.1" && Math.abs((await fs()) - 17.6) < 0.1, await fs());
      await page.focus("[data-testid=pane]");
      await page.keyboard.press("Control+=");
      check("6e Ctrl+= in the pane steps up (125)", (await zoomOf(page)) === "125", await zoomOf(page));
      await page.keyboard.press("Control+-");
      await page.keyboard.press("Control+-");
      check("6e Ctrl+- steps down twice (100)", (await zoomOf(page)) === "100", await zoomOf(page));
      await page.keyboard.press("Control+=");
      await page.keyboard.press("Control+=");
      await page.keyboard.press("Control+0");
      check("6e Ctrl+0 resets to 100", (await zoomOf(page)) === "100");
      for (let i = 0; i < 9; i++) await page.click("[data-testid=preview-zoom-in]", { force: true, timeout: 1500 }).catch(() => {});
      check("6e zoom tops out at 200 and zoom-in disables", (await zoomOf(page)) === "200" && (await page.locator("[data-testid=preview-zoom-in]").isDisabled()), await zoomOf(page));
      check("6e at 200% the prose is 32px", Math.abs((await fs()) - 32) < 0.1, await fs());
      await page.click("[data-testid=preview-zoom-reset]");
      for (let i = 0; i < 9; i++) await page.click("[data-testid=preview-zoom-out]", { force: true, timeout: 1500 }).catch(() => {});
      check("6e zoom bottoms out at 80 and zoom-out disables", (await zoomOf(page)) === "80" && (await page.locator("[data-testid=preview-zoom-out]").isDisabled()), await zoomOf(page));
      for (let i = 0; i < 5; i++) await page.click("[data-testid=preview-zoom-in]");
      check("6e 80 -> 150 after five steps and it is stored", (await zoomOf(page)) === "150" && (await ls(page, "assistant.previewZoom")) === "150", [await zoomOf(page), await ls(page, "assistant.previewZoom")]);
      await reopen(page);
      check("6e the zoom survives a reload", (await zoomOf(page)) === "150" && Math.abs((await fs()) - 24) < 0.1);
      await page.focus("[data-testid=query]");
      await page.keyboard.press("Control+=");
      check("6e Ctrl+= with focus in the query box does not zoom the preview", (await zoomOf(page)) === "150", await zoomOf(page));
      await page.click("[data-testid=preview-zoom-reset]");
      check("6e clicking the percentage resets to 100", (await zoomOf(page)) === "100" && (await ls(page, "assistant.previewZoom")) === "100");
      await ctx.close();
    }
    // 6f: localStorage that throws must not break the page
    {
      const init = () => {
        const boom = () => { throw new DOMException("denied", "SecurityError"); };
        Storage.prototype.getItem = boom; Storage.prototype.setItem = boom; Storage.prototype.removeItem = boom;
      };
      const { page, ctx } = await newPage({ init });
      await search(page, "zoomfix");
      await pick(page, NOTES);
      await page.waitForSelector("[data-testid=preview-markdown]", { timeout: 15000 }).catch(() => {});
      check("6f storage throwing: the page loads, searches and previews", (await page.locator("[data-testid=preview-markdown]").count()) === 1);
      check("6f storage throwing: defaults apply (50%, 100%)", (await valueNow(page)) === "50" && (await zoomOf(page)) === "100");
      await page.click("[data-testid=preview-zoom-in]");
      check("6f storage throwing: zoom still works in memory", (await zoomOf(page)) === "110");
      await drag(page, -120);
      check("6f storage throwing: dragging still works", Number(await valueNow(page)) > 50);
      await page.click("[data-testid=preview-expand]");
      check("6f storage throwing: expand still works", (await page.locator("main.split.expanded").count()) === 1);
      await ctx.close();
    }
    // 6g: diagrams grow with zoom (markdown with a markmap and a mermaid fence)
    {
      const { page, ctx } = await open({});
      await page.waitForSelector("[data-testid=preview-markmap] svg g", { timeout: 20000 }).catch(() => {});
      await page.waitForSelector("[data-testid=preview-mermaid] svg", { timeout: 20000 }).catch(() => {});
      await page.waitForTimeout(800);
      const size = (sel) => page.evaluate((s) => { const r = document.querySelector(s)?.getBoundingClientRect(); return r ? [r.width, r.height] : null; }, sel);
      const mm = "[data-testid=preview-markmap] svg", me = "[data-testid=preview-mermaid] svg";
      const markmapNodes = await page.locator(`${mm} g`).count();
      const mermaidText = (await page.locator(me).textContent().catch(() => "")) || "";
      check("6g markmap SVG rendered with nodes", markmapNodes > 3 && /Root|Alpha|Beta/.test((await page.locator(mm).textContent()) || ""), markmapNodes);
      check("6g mermaid SVG rendered with its labels", /Start/.test(mermaidText) && /Finish/.test(mermaidText), mermaidText.slice(0, 60));
      const m0 = await size(me), k0 = await size(mm);
      for (let i = 0; i < 3; i++) await page.click("[data-testid=preview-zoom-in]");
      await page.waitForTimeout(1200);
      const m1 = await size(me), k1 = await size(mm);
      check("6g at 150% the mermaid SVG is bigger (CSS zoom)", m0 && m1 && m1[1] > m0[1] * 1.3, [m0, m1]);
      check("6g at 150% the markmap box is 1.5x taller (ResizeObserver path)", k0 && k1 && k1[1] > k0[1] * 1.4 && k1[1] < k0[1] * 1.6, [k0, k1]);
      const fit = await page.evaluate((s) => { const svg = document.querySelector(s); const g = svg.querySelector("g"); const a = svg.getBoundingClientRect(), b = g.getBoundingClientRect(); return { top: b.top - a.top, bottom: a.bottom - b.bottom, left: b.left - a.left, right: a.right - b.right }; }, mm);
      check("6g markmap is re-fitted inside its grown box", fit.top >= -2 && fit.bottom >= -2 && fit.left >= -2 && fit.right >= -2, fit);
      check("6g markmap still has its nodes after zooming", (await page.locator(`${mm} g`).count()) > 3);
      await ctx.close();
    }
    // 6h: pdf page canvas grows with zoom
    {
      const { page, ctx } = await open({}, PDF);
      await page.waitForSelector("[data-testid=preview-pdf] canvas", { timeout: 20000 });
      await page.waitForFunction(() => { const c = document.querySelector("[data-testid=preview-pdf] canvas"); return c && c.width > 50 && /Page 1 of/.test(document.querySelector("[data-testid=preview-pdf-page]")?.textContent || ""); }, null, { timeout: 20000 }).catch(() => {});
      await page.waitForTimeout(800);
      const dims = () => page.evaluate(() => { const c = document.querySelector("[data-testid=preview-pdf] canvas"); return [parseFloat(c.style.width), c.width]; });
      const d0 = await dims();
      for (let i = 0; i < 3; i++) await page.click("[data-testid=preview-zoom-in]");
      await page.waitForFunction((w) => parseFloat(document.querySelector("[data-testid=preview-pdf] canvas").style.width) > w * 1.3, d0[0], { timeout: 15000 }).catch(() => {});
      const d1 = await dims();
      check("6h the PDF canvas is 1.5x wider in CSS and in pixels at 150%", d0[0] > 50 && Math.abs(d1[0] / d0[0] - 1.5) < 0.1 && d1[1] > d0[1] * 1.3, [d0, d1]);
      await page.click("[data-testid=preview-zoom-reset]");
      await page.waitForFunction((w) => Math.abs(parseFloat(document.querySelector("[data-testid=preview-pdf] canvas").style.width) - w) < 3, d0[0], { timeout: 15000 }).catch(() => {});
      const d2 = await dims();
      check("6h reset brings the PDF canvas back to its 100% size", Math.abs(d2[0] - d0[0]) < 3, [d0, d2]);
      await ctx.close();
    }
  }
  check("console: no errors beyond expected 404/500/501 resource logs", errors.filter((e) => !/404|500|501|Failed to load resource/.test(e)).length === 0, errors);
  await browser.close();
  console.log(`TOTAL ${results.filter(Boolean).length}/${results.length}`);
})().catch((e) => { console.log("ERROR " + e.stack); process.exit(1); });
