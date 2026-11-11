# Changelog

## 2026-11-06

- Project scaffold created.

## 2026-11-06
- Commits 9f7334c … e07693c on main (no pull requests yet; the repo has no remote): M1, the overlay that replaces the alert half of the reference popup
  (plan section 7.1, tasks M1-1 to M1-9). Rebuilt as a Python app that needs no admin rights: a round icon with a card stack
  (four kinds, snooze, dismiss, mute), Claude Code hooks giving one waiting and one done card per session, a local HTTP card
  and reminder API behind a bearer token, a `~/.assistant/events/` file drop, a `send`/`demo`/`hooks` command line, reminders
  with a four-shape grammar, and `install.ps1` / `uninstall.ps1`. Why: retire the reference popup's alert half on the author's
  laptops, and let other developers install it with Python and pip alone.
  - Dispatched by tech-lead. Drafted by dev (M1-1 to M1-6). Tests and driver by qa (M1-7). Security review by security
    (`docs/security/2026-11-06-m1-review.md`: Review 3 BLOCK on B1, the token and card body sent to an environment proxy;
    lifted on re-check, which reports zero blocking findings). qa PASS on M1-4, M1-5 and M1-9 as cited in that review.
    Docs by doc-keeper (M1-9: `README.md`, `docs/api.md`). Merged by git-keeper (pending).
  - Verified numbers, as supplied in the tech-lead's M1-9 brief: hook event to card visible in 0.4 s or less (PRD target: 2 s,
    section 12); file drop to card visible in 1.3 s or less (PRD target: 2 s, AC-CS-12); idle CPU 0.19 % (PRD: about 0 %
    averaged over 10 minutes); resident memory about 36 MB (PRD proposal: 80 MB, section 12).
    Source: the qa full-pass and dev reports relayed through the tech-lead; those reports live in the session transcripts,
    not in the repo, so the only in-repo artifacts for these numbers are the commit messages (dffeaad) and the security
    review's own measurements in `docs/security/2026-11-06-m1-review.md`.
  - Not yet observed for M1: the on-screen visual pass of the overlay; `install.ps1` and `uninstall.ps1` on a clean Windows
    user profile (AC-IN-8); the interactive hook probe P6 (AskUserQuestion and plan-approval coverage); the probes on the
    managed laptop (`docs/probes/` has only the personal-laptop file); whether Claude Code honours `HTTP_PROXY` for its hook
    requests (Review 3 B1 tail); the six overlay end-to-end tests, which need an unlocked desktop.
  - Open items carried from the review: R4-1 and R4-2 (a missing or stale `assistant.pid` can misdirect or refuse a start),
    R4-3 (asyncio reset noise in `app.log`), R4-5 (`--doctor` does not name the port owner), N3 (control characters kept in
    card text), N7 (`uninstall.ps1` does not check the process image before `Stop-Process`), N9 (`pillow` in the dev venv only).
  - Not in M1: search and previews, Ask notes, Ask Claude, MCP, meetings, plugins, the Settings window, the `update` command.
    The Search and Settings menu entries are placeholders.
  - Docs: `README.md` rewritten as the user-facing README (install, use, reminder grammar, CLI, troubleshooting, features,
    security and privacy facts, status). `docs/api.md` added: every `/v1` route as shipped, the hook contract, the card model
    with caps and kind defaults, the file-drop format, and a list of differences from PRD section 8.

## 2026-11-06
- M2 (meetings and MCP card tools): commits on main 2026-11-06 (M2). No pull request (the repo has no remote). Meetings
  arrive as blue cards before the start, from calendar addresses (ICS) and from classic Outlook on this PC, and Claude
  Code and Claude Desktop post cards and add reminders through one MCP server at `/mcp`. Why: the plan's M2 goal (plan
  section 7.2), so that Claude sessions and calendar events reach the overlay without a second app.
  - Pieces: `calendar/ics.py` (fetch, RRULE daily and weekly with COUNT, UNTIL, INTERVAL and BYDAY, EXDATE, RECURRENCE-ID
    overrides, TZID through tzdata, then the feed's VTIMEZONE, then UTC with one warning per zone name); `calendar/outlook.py`
    (classic Outlook COM, read only); `calendar/meetings.py` (join-link detection, de-duplication, lead time, polling,
    auto-dismiss at the event end, the stale-feed card after one hour); `server/mcp.py` (four tools: `send_card`,
    `update_card`, `dismiss_card`, `add_reminder`; shape-only `mcp.log`); `mcp_stdio.py` (stdio shim for Claude Desktop);
    `GET /v1/mcp/log`; `/v1/health` gains `uptime_s`, `index`, `plugins` and `calendars`; `settings.py` (schema, defaults,
    ranges, live apply for `calendars`, `mute_sources` and `cards_every_monitor`); `overlay/settings_ui.py` (six pages, token
    shown as `<token hidden>`, Copy command, Copy JSON, Regenerate token with a two-click confirm); `security/token.py`
    (`regenerate()`, which rewrites the hook entries when they are installed). M2-0 changed the error bodies to the object
    shape and added the control-character stripping and the `expired` event type (plan 16.3).
  - Dispatched by tech-lead. Drafted by dev (M2-0 to M2-4). Tests by qa (M2-5). Security review by security (Review 5,
    `docs/security/2026-11-06-m2-review.md`): PASS with zero BLOCK findings; five FIX-BEFORE-M3 items (R5-F1 to R5-F5);
    twelve NOTEs. Docs by doc-keeper (M2-7: `README.md`, `CHANGELOG.md`, `docs/api.md`). Merged by git-keeper: not confirmed
    in this record.
  - Verified numbers, as supplied in the tech-lead's M2 brief (not recorded in a file in this repo): meeting card shown
    0.1 s after the T-5 point; `send_card` over MCP answered in 0.17 s; idle CPU 0.14 % and 0.31 % (the second with Outlook
    polling); test suite 349 + 26 tests. The Review 5 run recorded `349 passed, 1 skipped` with `tests/e2e` excluded, and a
    live ICS event starting 6 min after the run began, with a 5-minute lead, gave its card after 55 s (the review's
    wording: "meeting card after 55 s"), within the plan's T-5 min ± 5 s target.
  - Not yet observed for M2: the on-screen visual pass of the Settings window and meeting cards; the Claude Desktop MCP
    panel screenshot on both laptops (user); the work-laptop probes (whether Claude Code honours `HTTP_PROXY` for its
    hooks, and P8 on the work laptop); the `tests/e2e` set (needs an unlocked desktop).
  - Open items carried into M3, from Review 5: R5-F1 (join-link check accepts an `@` after an allowed host), R5-F2 (one
    malformed event hides a whole calendar), R5-F3 (Regenerate fails while the token file is open elsewhere), R5-F4
    (`hooks remove` can restore a backup with our entries), R5-F5 (classic Outlook on by default; one invalid `calendars`
    value resets the section). NOTEs R5-N1 to R5-N12 are listed in the review file.
  - Docs: `README.md` gains Meetings, Connect Claude over MCP, Settings (keys table) and the M2 security facts, with
    dated correction notes where M1 text is now wrong. `docs/api.md` section 9 gives the MCP transport, auth, tools,
    `mcp.log`, `/v1/mcp/log`, the shim, the health fields, the calendar card fields and the `settings.json` schema.

## 2026-11-06
- M2-9 (security fixes for Review 5): the five FIX-BEFORE-M3 items from `docs/security/2026-11-06-m2-review.md` are fixed in
  code. R5-F1: a join link is used only if it is `https`, has no user name or password, uses port 443 or none, and its host
  is on the allow-list (so `https://zoom.us@other.example/...` is refused). R5-F2: a malformed calendar event is skipped alone
  and the rest of the feed is read; durations are capped at 366 days and event years are limited to 1970 to 2100. R5-F3: token
  Regenerate no longer fails under concurrent readers, and retries the replace for up to 5 seconds. R5-F4: `hooks remove`
  takes Assistant's entries out of a backup before it restores it. R5-F5: classic Outlook is off by default, and a bad value in
  one `calendars` key no longer resets the others. Also in the same change set: ICS redirects limited to http and https, at
  most 3 (R5-N3); `POST` only on `/mcp` with 405 for other methods (R5-N2); bearer text masked in Stop, Notification and
  StopFailure cards (R5-N5); `ics_urls` capped at 20 entries of at most 2048 characters (R5-N7); the Claude Desktop snippet
  carries the real `python.exe` path (R5-N11, snippet path only). Why: Review 5 marked R5-F1 to R5-F5 as fix-before-M3.
  - Verification: `tests/test_m2_9.py`, 47 tests, qa PASS by observation (per the tech-lead brief). Full suite reported as
    `396 passed` (349 from M2 plus 47), per the same brief. doc-keeper did not re-run either figure.
  - Not yet recorded: the security re-check of M2-9. Commit hash: 2814fe5.
  - Dispatched by tech-lead. Drafted by dev (M2-9). Verified by qa. Security closure: in progress.
    Merge: not yet done.
  - Docs: correction notes in `README.md` (Meetings, Features, Settings keys table, Connect Claude over MCP, Security and
    privacy, Troubleshooting 405), and `docs/api.md` section 10. Existing text is kept as written.
  - Known inconsistency: `zoom.com` is in the join-link allow-list (`JOIN_DOMAINS`) by tech-lead decision (it is a Zoom-owned domain) although it was not in the M2-9 brief's
    list. Open for security to confirm.
- M2 merge confirmation: the tech-lead brief for M2-9 reports git-keeper confirmation that M2 is on `main` as commits
  `50dfdaf`, `886df89`, `619c862` and `02726c4`. The local reflog (`.git/logs/HEAD`) lists those four commits in that order
  (messages: M2-0; M2-1 to M2-3; M2-4 wiring and the M2 security review; M2 tests), and `.git/HEAD` points to `refs/heads/main`. The M2 entry above says "not confirmed in this
  record"; this bullet is the confirmation, dated 2026-11-06.

## 2026-11-07
- M3a (Files search, Ask notes, MCP file tools): built on the working tree 2026-11-07, uncommitted when this entry was written.
  No pull request number is available to doc-keeper. Commit: 847b8a5 … e9367d4 on main (four commits: 847b8a5, 09f25d48, 6fb1f5b5, e9367d4; `a9bdbf8` is the M3-0 commit and is not part of M3a). Merge: pending git-keeper.
  Why: plan section 7.3, so Files mode and Ask notes run in the popup and Claude can search and read indexed files through MCP,
  without a second app.
  - Pieces: index store, excludes and deny-list (M3a-1: `assistant/index/{store,excludes}.py`, `assistant/security/{denylist,pathcheck}.py`);
    crawler, watcher and manager (M3a-2: `assistant/index/{crawler,watcher,manager}.py`); query grammar, ranking and executor
    (M3a-3: `assistant/index/{query,rank,search}.py`, grammar in `docs/search-grammar.md`); hotkey thread, search popup, modes and
    saved searches (M3a-4: `assistant/overlay/{hotkey,search_ui}.py`, `assistant/search/{modes,saved}.py`); `GET /v1/search`, `GET /v1/roots`,
    `/v1/saved` and the four read-only MCP file tools (M3a-5: `assistant/server/{routes_search,mcp}.py`); Ask notes BM25 over
    `.md` (M3a-6: `assistant/search/notes_bm25.py`); wiring, Roots & excludes and Search settings pages, `--doctor` lines (M3a-7:
    `assistant/app.py`, `assistant/overlay/settings_pages_search.py`, `assistant/doctor.py`).
  - Dispatched by tech-lead. Drafted by dev (M3a-1 to M3a-7). Tests by qa (M3a-8). Security review by security (M3a-9, Review 7,
    `docs/security/2026-11-07-m3a-review.md`: PASS, zero BLOCK; two FIX-BEFORE-M3B, R7-F1 and R7-F2; twelve NOTEs). Fixes for
    R7-F1 and R7-F2 by M3a-11, agent not named in the brief, **fixed 2026-11-07**. Docs by doc-keeper (M3a-10: `README.md`,
    `CHANGELOG.md`, `docs/api.md` section 11, `docs/search-grammar.md`).
  - What changed for users: a `hotkey` (default `ctrl+alt+space`) opens the search box beside the icon; Files mode matches names
    and paths under user-configured roots, with the grammar in `docs/search-grammar.md`; Ask notes searches `.md` under a
    notes folder and sends nothing; Claude can call `search_files`, `get_file_info`, `read_text_preview` and `list_indexed_roots`,
    all read-only. Nothing is indexed until a root is added. The home folder, the secrets deny-list and cloud placeholders are
    never read.
  - Verified numbers, as reported in the tech-lead's M3a brief (not in-repo artifacts): real roots about 395,000 entries; cold
    crawl 36 s and warm crawl 22 s, both under load; index load 0.04 s; hotkey press to box 36 ms; selective queries p95 29 ms;
    overall p95 144 ms before the prefilter. A prefilter is being added (M3a-12, in progress), so the overall p95 is to be updated.
    Measured in repo: Review 7 recorded full-scan queries at 2.5 to 7 s on a synthetic 2,000,000-entry index before the R7-F2
    fix (`docs/security/2026-11-07-m3a-review.md`).
  - Not yet observed: the quiet-machine re-measure (the numbers above were taken under load); the work-laptop probes (user-run,
    not run by doc-keeper); the popup on a live desktop with the hotkey registered, and the qa driver run of the popup (qa M3a-8
    result: full suite 711 passed (non-e2e), per the tech-lead's brief); a re-check of the R7-F1 and R7-F2 fixes by security (not yet in `docs/security/` when this entry was written; see the update below).
  - Known gaps, recorded not fixed: hidden and system files are indexed (PRD AC-FI-1 says excluded); a full scan runs at every
    start (AC-FI-4); result actions (Open, Explorer, MD Vault, folder) and the Full page button do nothing yet (M3b); the popup
    shows no placeholder marker (AC-FI-6); no custom date range in the popup (AC-FI-15); saved-search sort is stored but not
    applied (AC-FI-16). The MCP file tools return file text to Claude, which sends it to Anthropic as part of the conversation.
    This is the one exception to "nothing leaves your PC" in this build, and the PRD's AC-MC-3 and AC-MC-4 asked for it.
  - Review 7 notes carried open, per the brief: R7-N1, R7-N2, R7-N3, R7-N4, R7-N5, R7-N6, R7-N7, R7-N8, R7-N9, R7-N10. The code
    now contains what R7-N3 (deny-list entries), R7-N4 (`OSError` catch), R7-N6 (route caps) and R7-N9 (reserved combos) asked
    for. Security to confirm which are closed. R7-N11 and R7-N12 are recorded as read. Also carried: R2-10 (`ws_max_size`, M3b),
    R5-N7 (second half, per-source deadline), the B1 tail (work-laptop probe), R6-4.
  - Docs: `README.md` gains Files search, Ask notes, Roots, excludes and protected files, and Claude and your files, plus M3a
    security facts and troubleshooting, with dated correction notes where M1 and M2 text is now wrong. `docs/api.md` section 11
    gives the search, roots, saved-search and health routes, the MCP file tools with every refusal text, and the differences from
    the PRD. `docs/search-grammar.md` is new.
  - Update 2026-11-07, commit: the four M3a commits are on main in this order (from `.git/logs/HEAD`): 847b8a5 (`feat(m3a): file index, query grammar, search popup, hotkey, Ask notes`), 09f25d48 (`feat(m3a): search routes, four read-only MCP file tools, app wiring, Roots/Search settings pages`), 6fb1f5b5 (`test(m3a): ...`), e9367d4 (`docs(m3a): ...`). The reflog has no commit between e9367d4 and 56c3aa8, so the M3a-11 and M3a-12 changes (the R7-F1 and R7-F2 fixes and the bigram prefilter) are in 56c3aa8, the M3b commit. This is an inference from the reflog; it was not checked against the diff.
  - Update 2026-11-07, security re-check: `docs/security/2026-11-07-m3a-review.md`, section "Re-check after M3a-11/12 (2026-11-07)". Its verdict line reads: "### Verdict: PASS — zero BLOCK, zero FIX-BEFORE-M3B. R7-F1 and R7-F2 are CLOSED by observation in all the layers the fix claims; …" It carries eight NOTEs (R8-N1 to R8-N8), none a merge gate. The suite figure of 711 in that section is cited "per the dispatch" and was not re-run by security.

## 2026-11-07
- M3b (full page, previews, result actions): built on the working tree 2026-11-07, uncommitted when this entry was written.
  No pull request exists (the repo has no remote). Commit: 56c3aa8 `feat(m3b): full-page React app, previews, shell actions, index fixes`, on main, committed 2026-11-07 (200 files, +18806/−155, per the tech-lead's brief; the diffstat was not re-run by doc-keeper). Merge: pending git-keeper.
  Security review of M3b: **pending (M3b-7)**. Why: plan section 7.4, so the full page replaces the `fastsearch` full view, files
  can be previewed in the page, and results can be opened, revealed, renamed, moved to the Recycle Bin or listed without another app.
  - Pieces: M3b-0 leftovers (plan 18.3, items (a) to (p)): hidden and system entries excluded by default (`index.include_hidden`);
    the start scan runs only when the persisted index is older than `index.scan_if_older_hours` (default 12); cloud placeholder marker in
    HTTP rows and the popup; closed date range `YYYY-MM-DD..YYYY-MM-DD`; popup sort control and Ctrl+S; `index.known_exts` setting;
    `hotkey_skip_fullscreen` in the defaults with validation; MCP `filters.ext` normalised and `filters.modified_before`; the root's own
    row left out of results; **Restore defaults** for excludes (a saved list replaces the defaults); drive-relative default excludes;
    deny-list names added (`.claude/settings.local.json`, `claude_desktop_config.json`, `*.keystore`); Ask notes index skips denied
    paths; `\\?\` paths accepted; open-then-verify before a text read. Code read by doc-keeper; not run.
    M3b-1: the full-page web app (`web/`, React 19 and Vite; `scripts/build-web.ps1` builds it with Node at build time only).
    M3b-2: preview renderers (`web/src/preview/`) and the built output under `assistant/static/app/` (commit status not checked by doc-keeper).
    M3b-3: `assistant/server/routes_files.py` (preview route, ranges, caps, sniffing, deny-list, placeholder check, open-then-verify),
    `assistant/server/static.py` (`/app/`), the launch nonce exchange in `assistant/server/auth.py`, `assistant/preview/extract.py`
    (DOCX, XLSX, PPTX and CSV text, with caps), `assistant/overlay/fullpage.py` (Edge app mode with its own profile).
    M3b-4: `assistant/actions/{names,shell}.py` and `assistant/server/routes_actions.py` (open, open with, reveal, copy path, rename,
    properties, Recycle Bin only, MD Vault, folder listing, Ask Claude as a stub).
    M3b-5: `assistant/overlay/search_ui.py` (context menu, action keys, Full page button and F11, full Esc chain).
    M3b-9: wiring in `assistant/app.py`.
  - Dispatched by tech-lead. Planned roles (plan 7.4 and 18.3 to 18.4): ui (M3b-1, M3b-2), dev (M3b-0, M3b-3, M3b-4, M3b-5, M3b-9),
    qa (M3b-6). Agent per task, attributed by file ownership (per the tech-lead's brief): ui for M3b-1, M3b-2 and M3b-10 (`web/**`, `assistant/static/app/**`); dev for M3b-0, M3b-3, M3b-4, M3b-5, M3b-9 and M3b-11; qa for M3b-6; security for M3b-7; doc-keeper for M3b-8; commit by git-keeper. Verifiers: qa M3b-6,
    result: 886 non-e2e tests passed; e2e 70 passed, and 6 overlay tests not run because they need an unlocked desktop (per the brief); security M3b-7, verdicts in the update below.
  - Reported numbers (from the tech-lead's M3b brief; not repo artifacts, not re-measured by doc-keeper): M3b-1 browser checks 70 of 70
    passed; M3b-2 headless-Edge checks 40 of 40 passed, including hostile preview fixtures with no alert and no navigation; popup queries of
    one letter plus a path term 25 to 85 ms; heavy queries on a 2,000,000-entry store at most 445 ms. No work-laptop result.
    The bundle is reported at 7.3 MB, with Mermaid and pdf.js loaded on demand.
  - Found by doc-keeper while reading the code (recorded, not fixed; `docs/api.md` section 12.9):
    the popup's action results and Ctrl+F folder listing do not reach the popup (`app._on_action` returns no result), and the page's
    **Download and preview** button does not download (the route ignores its `hydrate` parameter; the file stays a placeholder).
  - Not yet observed: the M3b security review; unlocked-desktop checks (clipboard, the real Ctrl, Delete, F2 and Alt+Enter keys in the
    popup); the work-laptop P2 probe (Edge app mode), which gates previews on that laptop (plan 7.4); the qa full-suite result for M3b.
  - Docs: `README.md` gains Full page and previews, Actions on a file, the M3b settings keys, the M3b security facts, troubleshooting
    entries, the Status line and dated correction notes. `docs/api.md` section 12 gives the new routes, the preview kinds and refusals,
    the range rules, the nonce exchange, the action verbs and errors, the settings keys and the differences from the PRD, with correction
    notes in sections 3.6, 8, 11.7 and 11.9. `docs/search-grammar.md` section 11 gives the custom date range, the sort values and
    `index.known_exts`, with correction notes in sections 5 and 9.
  - Update 2026-11-07, security review: the M3b security review is `docs/security/2026-11-07-m3b-review.md`. Its Review 9 verdict line reads: "### Verdict: BLOCK — one BLOCK finding (R9-B1: a Mermaid block in a previewed Markdown file makes the page fetch an attacker-chosen URL through CSS `url()`; demonstrated, fix verified both ways). Four FIX-BEFORE-M4 (R9-F1..F4), twelve NOTEs (R9-N1..N12)." The section "Re-check after M3b-10/11 (2026-11-07)" then lifts the block. Its verdict line reads: "### Verdict: BLOCK LIFTED — PASS for merge. R9-B1 is closed by three observed layers (locked Mermaid config keys, style guard, CSP header); …" The re-check's open items are R10-F1 (FIX-BEFORE-M4), R10-N1 to R10-N4 (any time), and R10-N5 and R10-N6 (informational).
  - Update 2026-11-07, R10-F1 status (not settled by this entry): the re-check lists R10-F1 as open ("14 s for a 128 KB member and no per-member cap on docx/pptx"). The re-check records no re-test of a fix. The current source has the regex half: the three patterns use `[^<>]` (`assistant/preview/extract.py` lines 66, 114 and 209). The member-cap half is not in the source: `word/document.xml` and the slides are read with no limit (`extract.py` lines 72 and 222). Timing was not re-measured for this entry.
  - Update 2026-11-07, carried items (per the re-check): before M4, R10-F1. Any time, R10-N1 to R10-N4. Carried unchanged: R9-N5, R9-N7, R9-N8, R9-N9, R9-N10 (katex), R9-N12, R5-N7 second half, and the B1 tail.
  - Update 2026-11-07, qa: the "qa full-suite result for M3b" in the list above is now known. The brief reports 886 non-e2e tests passed; e2e 70 passed; 6 overlay tests need an unlocked desktop and were not run. The "not yet observed" bullet above is kept as written.

## 2026-11-07
- Commit 3d7bc25 on `main` (subject: `feat: M4 full page, path search, live search, theme, rounded UI, hooks safety, bounded shutdown`; reflog timestamp 2026-11-07, -0600; no pull request, because the repo has no remote). The full page gets live search, Tab completion, a `q=` search URL and a light and dark theme that the popup shares; a search word may match the name or the path, with name matches first; the default excludes now leave out Windows, Program Files, Program Files (x86), ProgramData and AppData, so a whole drive can be indexed for the user's documents; Ask Claude sends a file's text to Claude Code only after confirmation. Why: the full page is the larger search window, and the index is for the user's own documents.
  - Dispatched by, drafted by, verified by: not recorded in the repo. Merged by: not applicable (committed to `main` directly).
  - Pieces, as read in the code at 3d7bc25: `web/src/App.jsx` (theme button, 150 ms live search, Tab completion, `q=` address, the theme saved through `POST /v1/settings/theme`); `web/src/components/PreviewPane.jsx` and `web/src/preview/zoom.js` (zoom 80 to 200 percent, default 100); `assistant/app.py` (the full page closes the popup when it opens; `SHUTDOWN_STEPS_S = 3.0`, `HARD_EXIT_S = 10.0`); `assistant/overlay/search_ui.py` (120 ms live search in the popup); `assistant/index/search.py` (every word may match the name or the path); `assistant/index/excludes.py` (wider default excludes); `assistant/overlay/win.py` (Windows 11 rounded corners, with an outline fallback); `install.ps1` and `scripts/install-shortcut.ps1` (Desktop shortcut, `-NoShortcut`); `assistant/update.py` (`update --yes`); `assistant/server/routes_settings.py` (`GET /v1/settings`, `POST /v1/settings/theme`); `assistant/server/routes_actions.py`, `assistant/actions/shell.py` and `assistant/actions/claude.py` (Ask Claude status, continue, and send after confirmation); `assistant/server/routes_hooks.py` (`POST /v1/hooks/claude-code`).
  - Verification: none run by doc-keeper. This session had no shell or git tool, so `git show --stat 3d7bc25` was not run and the diff was not read. The file list above is from the code at HEAD, which is 3d7bc25 (`.git/refs/heads/main`). No test suite and no live app run. The figures below come from the code or from the brief, not from a measurement here.
  - Known issues:
    - 401 theme revert: a 401 from `POST /v1/settings/theme` reverts the page's theme to the previous value (`web/src/App.jsx`, `chooseTheme`). Fixed after 3d7bc25, pending commit: the 401 now restores `<html data-theme>`.
    - A caret in the home folder path breaks Continue (`POST /v1/actions/ask_claude/continue`). Fixed after 3d7bc25, pending commit: Continue in Claude Code now works with `^ & ! ( )` in the home path.
    - The performance and churn tests are flaky under load.
    - The remind-me end-to-end test is flaky on a busy desktop.
  - Found while writing this entry, not in the list above:
    - `uninstall.ps1` removes only the Startup shortcut. The Desktop `Assistant.lnk` that `install.ps1` creates stays.
    - The "about 3 s" shutdown bound covers the teardown steps only. The server join adds up to 4 s (`STOP_JOIN_S`), and the hard exit is at 10 s (`HARD_EXIT_S`).
    - The popup says `truncated — refine the query`; the full page badge says `partial: refine the query`.
    - `POST /v1/settings/theme` returns `422 invalid_parameter` for a bad theme value; `400` is only for malformed JSON.
    - The new-file figure of about 1 second in search results is a target. The watcher batch is 0.25 s (`assistant/index/manager.py`, `HINT_BATCH_S`). The end-to-end time was not measured.
    - Security review of 3d7bc25: `docs/security/2026-11-07-m4-review.md`. Review 11 returned BLOCK on R11-F1 (the Continue launcher's working folder). Review 12 returns PASS for commit, with R11-F1 closed by observation and low NOTEs R12-N2 and R12-N3 open (R12-N1 resolved in place).
  - Docs: `README.md` (a correction at the top, and "What 3d7bc25 adds" in Features, with correction notes in Install, Search files, Full page and previews, Actions on a file, Roots and excludes, and a "3d7bc25 additions" note in Security and privacy; Status). `docs/api.md` section 13. `docs/search-grammar.md` section 12.

## 2026-11-08
- PR: none (the repo has no remote, so there is no PR number). Rename of the app from Assistant to Homa, per
  `docs/plans/2026-11-08-rename-to-homa.md` (rollback tag `pre-homa-rename` at `bcebd08`, work on branch `rename-homa` in the
  worktree `C:\AI Projects\Assistant-homa`). Why: the plan's goal, that the app is called Homa everywhere a person can see it
  (plan section 1). What changed: the package is `homa` (`python -m homa`, distribution `homa-desk` 0.2.0), the data folder is
  `%USERPROFILE%\.homa` (`HOMA_HOME`), the MCP server is `homa`, the icon is the new Homa bird, and the first start moves an
  existing `%USERPROFILE%\.assistant` and `%LOCALAPPDATA%\Assistant\edge-profile`, rewrites the matching `Assistant.lnk` and
  shows an "Assistant is now Homa" card. `python -m assistant` and `python -m assistant.mcp_stdio` remain as a 0.2.x shim, and
  the `ASSISTANT_*` variables are read as a fallback, both removed in 0.3.0. Details: `README.md`, "Upgrading (2026-11-08)".
  - Dispatch chain per the plan's section 5: R0 git-keeper (tag, worktree), R1 dev (package move), R2 dev (migration),
    R3 ui (web rename and bundle), R4 dev (icon artwork), R7 doc-keeper (this entry and the docs). R5a qa (tests), R6 security
    (review of the migration), R8 git-keeper (`--ff-only` merge into `main`) and R9 (cutover on the personal laptop, needs the
    user's go-ahead) are not done as of this entry.
  - Verified in the worktree by reading the code on 2026-11-08 (not by running it): `homa/migrate.py` (decision table, shutdown
    through `/v1/shutdown`, rename, shortcut rewrite, marker `migrated-from.json`), `homa/paths.py` (`home()` fallback order,
    deprecation warning for `ASSISTANT_*`), `homa/__init__.py` (`0.2.0`), `pyproject.toml` (`homa-desk`), `homa/server/mcp.py`
    (`SERVER_NAME = "homa"`), `homa/overlay/fullpage.py` (`WINDOW_TITLE`, Edge profile), `homa/calendar/ics.py` (`Homa/1`),
    `homa/index/store.py` (format tag `assistant-index`, unchanged on purpose), `assistant/__main__.py` and
    `assistant/mcp_stdio.py` (stderr notice). Tag `pre-homa-rename` and branch `rename-homa` exist in the repository.
  - Verification status: the test suite is pending (qa, R5a and R5b, `tests/test_rename_complete.py` included); the security
    review of the migration is pending (R6). No test result is claimed here.
  - Open decisions, from the plan's section 8: the repository folder stays `C:\AI Projects\Assistant` (Q1); re-installing the
    Claude Code hook entries at cutover needs the user's approval (Q2); the shim's lifetime is one minor release (Q3); icon
    sign-off is on the R4 screenshots, which are not in this entry (Q4).
  - Found while reading the code, not changed here: the Claude Code hook entries on the personal laptop point at port 62480,
    while the app listens on 17171 (plan section 2, F5). The migration reports this with a card and a `--doctor` suffix; it does
    not rewrite the entries.

## 2026-11-09
- Commit 2eca46b on `main` (subject: `feat: popup double-click opens full page with preview; multi-select copy/cut of files`),
  fast-forwarded from b320269 (`.git/logs/refs/heads/main`: "merge popup-clipboard: Fast-forward"). PR: none (the repo has no
  remote). Branch `popup-clipboard`, built in the worktree `C:\AI Projects\Assistant-clip`. Why: the user asked on 2026-11-09
  that a double-click in the popup open the full page with the preview, and for multi-select Copy and Cut so files can be
  copied or moved to another File Explorer folder (plan section 1, request quoted verbatim there).
  - What changed: the popup marks several rows (Ctrl+click, Shift+click, Shift+Up/Down, Ctrl+A); Ctrl+C and Ctrl+X, and
    **Copy** and **Cut** in the right-click menu, put the marked files on the Windows clipboard as `CF_HDROP` with
    `Preferred DropEffect` 1 or 2, in the popup and in the full page, so Paste in File Explorer copies or moves them (Explorer
    does the move; Homa never moves or deletes). Copy path moved from Ctrl+C to Ctrl+Shift+C in both UIs, with a load-time
    migration of a saved `copy_path` of `Ctrl+C`; `copy_files`, `cut_files`, `copy_path` and `select_all` can be rebound or set
    to `""`. A double-click on a Files-search row in the popup (and F11 or **Full page**) opens the full page with the same
    query, chips, date filter and sort, that row selected and its preview open; the state travels with the launch nonce as a
    one-shot `handoff`, never in the URL. New route `POST /v1/files/clipboard` (256 KiB body, 1 to 1,000 paths); new fields on
    `POST /v1/session/exchange` (`handoff`) and `POST /v1/fullpage/open` (`chips`, `modified`, `sort`, `select_path`, `422` on bad
    types). Copy and Cut refuse protected (deny-listed) files, paths outside the roots or not indexed, Homa's own folder and
    device or long-path prefixes, and report each skip with its reason; Cut also refuses root folders and folders that contain
    Homa's own folder.
  - Plan: `docs/plans/2026-11-09-popup-to-fullpage-and-file-clipboard.md` (dispatches P0, D1 to D7). Roles per the plan's
    section 5: git-keeper (P0 branch and worktree), dev (D1 backend, D2 popup), ui (D3 full page and bundle rebuild), qa (D4),
    security (D5), git-keeper (D6 merge), doc-keeper (D7, this entry). The agent that did each step is taken from the plan, not
    confirmed from a record in the repo. The security review states that qa passed D4 before Review 15 ran.
  - Security: `docs/security/2026-11-09-clipboard-handoff-review.md`. Review 15 verdict: "PASS. No BLOCK. Six NOTEs (R15C-N1 to
    R15C-N6)". The re-verification section of the same file (after dev's fixes) reads: "PASS. All six NOTEs RESOLVED. No new
    BLOCK." Fixed before merge:
    - R15C-N1: an unhashable `op` (a list or object) on `/v1/files/clipboard` was a `500`; now `422 invalid_parameter`,
      `field: "op"`, in the route and in `shell._clipboard_files`.
    - R15C-N2: a path with a lone UTF-16 surrogate was a `500` after the clipboard had already been written; now refused as
      `refused: not a usable path` before `_vet`, and skipped paths are echoed with `backslashreplace`.
    - R15C-N3: mixed-separator UNC spellings (`\/host/share`, `/\host\share`) reached `os.path.realpath` (an SMB attempt) before
      the refusal; now caught by the UNC check in both `shell._vet` and `mcp._vet`.
    - R15C-N4: a link with a protected name could be cut by adding a trailing dot or space; `_win_trim` now drops trailing dots
      and spaces per component before the deny-list check.
    - R15C-N5: Cut could name a folder that contains Homa's home; now refused (`refused: that folder holds Homa's own data`).
    - R15C-N6: `tools/driver.py` followed redirects with the bearer; it now uses a `_NoRedirect` handler.
    Informational, left open by the review: Copy (not Cut) of a folder containing Homa's home or protected files is allowed, as
    in Explorer; `recycle` does not refuse a folder that contains Homa's home (pre-existing, out of scope).
  - Test evidence in the repo: the review's re-verification run, `pytest` over `tests/test_clipboard_route.py`,
    `tests/test_clipboard_files.py`, `tests/test_launch_handoff.py`, `tests/test_shortcuts_migration.py`,
    `tests/test_driver_guard.py`, `tests/test_actions_shell.py`, `tests/test_denylist.py`, `tests/test_mcp_files.py`,
    `tests/test_preview_routes.py` and `tests/test_rename_hardening.py`: `416 passed, 1 skipped in 104.01s`. qa's full-suite and
    e2e results for D4, run on 2eca46b with the Explorer- and Edge-window tests excluded:
    `2 failed, 1887 passed, 5 skipped, 3 deselected in 1396.14s`; both failures (overlay remind-me, the two-million-name
    perf threshold) also fail on b320269 and predate this change. doc-keeper ran no tests.
  - Not yet observed: Paste in File Explorer copying and moving real files (plan AC-C4, user-run on the personal laptop with
    throw-away files); the live instance runs the new code only after a restart, which agents do not do.
  - Docs: `README.md` (a top correction note; notes in Search files, Full page and previews, Actions on a file and Settings; the
    new section "Copy, cut and the full-page handoff (2026-11-09)"; "What 2eca46b adds" in Features; "2eca46b additions
    (2026-11-09)" in Security & privacy; a Status bullet; a Documentation link). `docs/api.md` section 15. The README's older
    sentence "The page has no Ctrl+C" is corrected by a dated note and kept as written.

## 2026-11-11
- Card timeouts and Close all. Commit: on branch `card-timeouts`, merged to main 2026-11-11 (see `git log`). PR: none (pushed straight to
  main on github.com/ordingr/homa-desk). Why: the user asked on 2026-11-11 that informational
  cards stay 10 to 15 seconds and then go, that cards waiting for the user stay 15 minutes, and for a way to close all cards
  at once.
  - What changed: a seen informational (not sticky) card is closed (`expired`) after 12 seconds instead of 20
    (`cards.info_seconds`, default 12, 5 to 120). A seen sticky card (`waiting`, `meeting`, any `sticky: true` card,
    reminders with "wait for me") is closed after 15 minutes instead of staying until dismissed (`cards.waiting_minutes`,
    default 15, 0 to 1440, `0` keeps the old behaviour). An earlier `expires_at` still wins; a snoozed card restarts the count
    when shown again. Both settings apply live and are on the Settings Cards page. A **Close all** strip appears on a monitor
    with two or more cards; it, `POST /v1/cards/dismiss_all` (`{"dismissed": n}`) and the MCP tool `dismiss_all_cards` dismiss
    every open card except snoozed ones (cards held while muted are included). The reminder checkbox reads `wait for me
    (stays up to N min)`, or `wait for me (stays until dismissed)` at `0`. Source files: `homa/cards/store.py`,
    `homa/settings.py`, `homa/server/routes_cards.py`, `homa/server/mcp.py`, `homa/overlay/cards_ui.py`,
    `homa/overlay/reminder_ui.py`, `homa/overlay/settings_ui.py`.
  - Roles: dispatched by tech-lead; docs by doc-keeper (this entry); code by dev; qa PASS; security PASS with four NOTEs, of which
    1 to 3 (snoozed-card tick loop, RecursionError on deeply nested settings.json, unchecked timing input) were fixed before
    merge; merged by tech-lead. Plan: none (small change, briefed directly). Security review:
    `docs/security/2026-11-11-card-timeouts-review.md`.
  - Test evidence in the repo: boundary tests in `tests/test_cards_store.py` (for example
    `test_info_card_boundary_open_at_11_999_expired_at_12` and `test_waiting_card_boundary_open_at_14_59_expired_at_15_00`).
    Card unit tests: `151 passed` (store and timing) and `1 failed, 361 passed, 1 skipped` on the wider card set; the one
    failure, a meetings lead-time test, passes alone and on main (load flake). Overlay e2e: the fade and both Close-all tests
    pass; three `/v1/_debug/ui` 503 failures also fail on main 2fb484a. Tests that type keys or drag the mouse were excluded.
  - Not yet observed by doc-keeper: the Close all strip and the Cards page fields on a live desktop.
  - Docs: `README.md` (new section "How long cards stay, and Close all (2026-11-11)" under Using it; dated correction notes in
    Reminders, Meetings and Connect Claude over MCP; "Card timing keys, 2026-11-11" in Settings; "What the card-timeouts change
    adds" in Features). `docs/api.md` section 16, which also corrects section 3.1's out-of-date "no separate `expired` type"
    line. The older 20-second and "stays until dismissed" statements are kept as written.
