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
  No pull request number is available to doc-keeper. Commit: **[placeholder: git-keeper to fill in]**. Merge: pending git-keeper.
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
    result **[placeholder: not in brief]**); a re-check of the R7-F1 and R7-F2 fixes by security (not yet in `docs/security/`).
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
