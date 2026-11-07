# Plan: Assistant v1

- Date: 2026-11-06
- Author: planner (TeamAgents)
- Plans against: `docs/prds/2026-11-06-assistant-v1.md` (the PRD; authoritative). Research briefs in `docs/research/` and the reference README in `docs/reference/` are cited where the PRD points to them.
- Status: first version. Append-only (archaeology rule): changes go in dated "Revision" sections at the end. Every dispatch cites this file by path.
- Hand-off: tech-lead reviews, then dispatches M0.

How to read this file: section 4 holds the four design decisions the tech-lead asked for; section 5 the package layout; section 6 the M0 probe procedure; section 7 the work breakdown (one subsection per milestone, one row per dispatchable task); section 8 testing; section 9 security review points; section 10 dependencies and the install scripts; section 11 risks and rollback; section 12 the first three dispatches written out in full.

## 1. Goal and non-goals

**Goal.** Ship Assistant v1 on both laptops as a single pure-Python package that the user installs with `install.ps1`, so that a Claude Code wait becomes a card within 2 s, a file is found by name in under 50 ms at 2M entries without admin, meetings and reminders arrive as cards, Claude reaches the index through one `assistant` MCP server, and a two-file folder extends the app — all exactly as the PRD's acceptance criteria describe.

**Non-goals.** Everything in PRD section 3 "Non-goals" and "Never" (FILES button, sync, approving permissions from a card, content search, plugin catalog, out-of-process plugin host, toast fallback, Graph, cloud, telemetry, auto-update, run-as-admin). Additionally, this plan does not: migrate the old `fastsearch` index (user decision: fresh build), build the Everything/USN accelerators (deferred until P7 numbers exist), or deliver any UI design mockups (the reference README and photos are the design; `designer` is skipped — see Assumption A6).

## 2. Assumptions (user answers and planner defaults)

Answers relayed by the tech-lead from the user:

- A1. PRD decisions PM 1–14 are accepted as written. Open questions 8, 9 and 10 in the PRD are therefore closed with PM 13, PM 6 and PM 3.
- A2. Claude Desktop is used on both laptops: the stdio shim (`python -m assistant.mcp_stdio`) is day-one, in M2.
- A3. A fresh file index is acceptable; nothing is read from the old `fastsearch` index.
- A4. Milestone order M0 → M1 → M2 → M3a → M3b → M4 → M5 stands.

Planner defaults (the PRD left these open; each is reversible and flagged in the final report):

- A5. **Agents can only execute on the personal laptop.** Every work-laptop probe or verification is run by the user from copy-pasted commands and the output is pasted back; `doc-keeper` records it. The plan never claims a work-laptop result an agent did not observe.
- A6. **No `designer` pass.** The reference README plus photos fix the look of the icon, cards and popup (`[REF L10-21, L59-73, L75-98]`), and the full page copies the `fastsearch` browser UI (`[REF L160-179]`). `ui` implements against those; a designer pass would re-derive what the user already chose.
- A7. **Role split for UI code.** TeamAgents' `ui` role is React/Tailwind. In this project `ui` owns `web/**` (the React source of the full page) and the vendored build output under `assistant/static/app/**`. All tkinter code is Python and belongs to `dev`.
- A8. **Default port 17171**, overridable in settings (`[PM 11]`: takes effect after restart). The PRD fixes no number; `--doctor` reports whether it is free.
- A9. **Settings persist as `settings.json`**, not TOML. The standard library can read TOML (3.11+) but cannot write it; a settings window that writes within 1 s (AC-ST-3) needs a writer. Plugin manifests stay `plugin.toml` as the PRD requires (read-only, `tomllib` on 3.11+, `tomli` on 3.10).
- A10. **One process** (section 4a) means the PRD's two RAM targets (daemon ≤ 300 MB at 2M entries; overlay ≤ 80 MB) become one combined target of ≤ 380 MB private working set at 2M entries, with the index component reported separately by `--doctor` so the two can still be judged apart.
- A11. **`demo` moves from M0 to M1.** `python demo.py` shows sample cards (AC-IN-2); there are no cards to show until M1's overlay exists. M0 ships `--doctor` and `/v1/health` as the PRD lists.
- A12. **Browser token hand-off uses a single-use launch nonce**, not the token in the URL. SEC-2 says the full page "receives it once at launch and keeps it in memory only"; a token in a URL would land in Edge's history. The launcher opens `http://127.0.0.1:<port>/app/?launch=<nonce>`; the page POSTs the nonce to `/v1/session/exchange` (valid for 30 s, once) and receives the bearer token into memory.
- A13. **Password-type plugin settings are protected with Windows DPAPI** (`CryptProtectData` via `ctypes`, user scope) in `~/.assistant/plugins-secrets.bin`. No extra dependency; satisfies AC-PL-11 "stored by the host".
- A14. **Test-mode debug route.** When the environment variable `ASSISTANT_TEST_MODE=1` is set at start, the server exposes `GET /v1/_debug/ui` returning each overlay window's handle, rectangle, card id, colour, blinking and seen state. It never exists otherwise. QA drives the overlay with it (section 8.3); `security` checks it is absent in normal mode.
- A15. Where this plan names a Python API (function signatures, module names), it is the contract between parallel tasks. Executors may add, but must not rename, what is written here without a Revision.

## 3. Approaches considered — summary

Four areas have real design freedom; section 4 weighs each in a few lines and decides. Everything else is fixed by the PRD's Decisions table (`[DEC 1-14]`) and is not reopened here.

## 4. Chosen approaches and why

### 4a. Process layout

| Option | Blast radius | Cost | Testability | Reversible? |
|---|---|---|---|---|
| **(1) One process:** tkinter main loop on the main thread; uvicorn, indexer, watcher, scheduler and plugin workers on daemon threads; a single UI queue drained by `root.after` | A pathological thread (GIL-heavy loop) can stutter the overlay; a hard crash anywhere takes down everything | Lowest: one entry point, one state store in memory, no IPC | One `ASSISTANT_HOME`, one port, one `/v1/health` to assert on | Yes — the indexer package imports no tkinter and can be moved behind the HTTP API later |
| (2) Two processes as the file-search brief sketches (`[FS §11]`): daemon (index + server + MCP) and overlay (tkinter + hotkey) talking over loopback HTTP | Overlay restart keeps the index; crawl cannot stutter the UI | Card state, reminders and settings must live in exactly one of them, and the other polls; two autostart shortcuts; two sets of logs; the hook receiver must forward to the overlay | Two processes to start and stop in every test | Yes but converges toward (1) anyway for cards |
| (3) Three processes: as (2) plus a separate hook/MCP listener | Nothing stutters anything | Highest; three tokens or a shared one; triple the install/uninstall surface | Worst | — |

**Decision: (1), one process.** The reference build ran everything (cards, hook receiver, MCP, search UI) in one `assistant.py` process and the user lived with it (`[REF L117-127]`); the only part that genuinely competes for CPU is the crawl, and `os.scandir` releases the GIL around the Win32 calls (`[FS §10.2]`), with P7/P9 measuring the real stutter before M3a commits. The data-only indexer package is the escape hatch if the measurement is bad.

How the pieces coexist without blocking the UI loop (this is the contract every M1+ task follows):

- **Main thread** owns tkinter exclusively. `assistant/uibus.py` exposes `post(fn, *args)` (thread-safe `queue.SimpleQueue`) and `start(root)` which installs `root.after(20, drain)`; `drain` runs at most 50 callables per tick. No other thread may touch a tkinter object.
- **Hotkey thread** (`overlay/hotkey.py`): `win32gui.RegisterHotKey` is per-thread, so a dedicated thread registers the key and runs its own `GetMessage` loop; on `WM_HOTKEY` it calls `uibus.post(search_ui.toggle)`. `ERROR_HOTKEY_ALREADY_REGISTERED` is reported back through a `threading.Event` plus a reason string for Settings (AC-SH-1). Changing the hotkey posts `WM_QUIT` to that thread and starts a new one (after restart in v1 per PM 11, but the mechanism is there).
- **Server thread** (`server/app.py`): `uvicorn.Server(config).run()` inside a daemon thread with its own asyncio loop (`asyncio.new_event_loop()`); handlers that need UI call `uibus.post` and return immediately; handlers that need the index call it directly (the index is read-safe from any thread, see 4b). The hook receiver therefore answers in well under 1 s (AC-CS-3) regardless of what the overlay is doing.
- **Scheduler thread** (`scheduler.py`): one `heapq` of `(when, seq, fn)` guarded by a `threading.Condition`; fires reminders, meeting lead-times, snooze returns, 20-s fades, mute end, ICS polls, plugin `every()` requests. It runs `fn` on the scheduler thread when `fn` is pure (store mutation) and via `uibus.post` when it touches UI. `scheduler.at(ts, fn) -> handle`, `scheduler.every(seconds, fn) -> handle`, `handle.cancel()`.
- **Indexer threads** (`index/crawler.py`): one walker thread per root inside a `ThreadPoolExecutor(max_workers=min(4, roots))` plus one `ReadDirectoryChangesW` watcher thread per root. Walkers call `time.sleep(0)` every 2,000 entries and lower their priority with `SetThreadPriority(THREAD_MODE_BACKGROUND_BEGIN)` (AC-FI-4). They write into a staging store and swap it in atomically (4b), so readers never see a half-built index.
- **Plugin workers** (`plugins/host.py`): one single-thread executor per enabled plugin (4d).
- **Shutdown**: `app.shutdown()` sets a global `stop` event, calls `server.should_exit = True`, posts `WM_QUIT` to the hotkey thread, cancels the scheduler, and persists state; `root.destroy()` last. Invoked by the Quit menu item, by `uninstall.ps1` through `POST /v1/shutdown` (token-protected), and by `SIGTERM`-equivalent `WM_CLOSE` on the hidden root.

### 4b. Index data structure and persistence

| Option | RAM at 2M (estimate, must be measured in P7) | Query ≤ 50 ms? | Persistence | Incremental updates |
|---|---|---|---|---|
| **(1) Columnar arrays:** one `str` blob of all names (original case) joined with `\x00`, a parallel casefolded blob, `array('I')` name offsets, `array('I')` parent-directory index, `array('q')` size, `array('q')` mtime (FILETIME), `array('B')` flags (dir / hidden / system / placeholder / deny-listed); directories in their own smaller table of full paths | ≈ 2 × 60 MB blobs + 2M × 25 B arrays ≈ 170 MB (`[FS §10.2]` estimate) | Yes: `casefold_blob.find(term)` scans at C speed (tens of ms per 60 MB); hits map to entries via `bisect` on offsets; further AND terms verified per hit | One file, versioned header + `pickle` protocol 5 of the arrays, written to `.tmp` then `os.replace` | Delta layer: `dict[path -> entry]` of adds and a `set` of removed ids consulted at query time; folded into the arrays at the next rescan or when the delta exceeds 50k entries |
| (2) SQLite (stdlib) table `files(name, name_lc, parent, size, mtime, flags)` | Lowest in-process RAM (page cache) | `LIKE '%x%'` over 2M rows is a full scan in SQLite's byte-compare loop: 150–400 ms expected; FTS5 trigram tokenizer (SQLite ≥ 3.34) gets substring to ms but triples disk size and needs the bundled SQLite version checked on both laptops | Free | Free (row updates) |
| (3) `dict[path -> tuple]` of Python objects | 0.5–1 GB (`[FS §10.2]`) | Needs a per-entry Python loop: hundreds of ms | `pickle` | Trivial |

**Decision: (1), columnar arrays with pickle persistence, SQLite trigram as the named fallback if P7 shows RAM over 300 MB for the index.** The reference build's "0.3M entries, 42.3 ms" is what a joined-blob `str.find` delivers (`[FS §10.2]`), and it is the only pure-Python layout that fits both the 50 ms and the 300 MB target on paper. Option (2) stays documented because it is a one-module swap behind the same `IndexStore` interface.

Contract (`assistant/index/store.py`), fixed so M3a-2 and M3a-3 can run in parallel:

```python
class IndexStore:                     # immutable after build(); swapped atomically
    built_at: float; roots: list[str]; entry_count: int
    def find_substring(self, term_cf: str) -> array  # entry ids whose casefolded name contains term
    def name(self, i) -> str; def path(self, i) -> str; def parent_path(self, i) -> str
    def size(self, i) -> int; def mtime(self, i) -> float; def flags(self, i) -> int
    def iter_in_dir(self, dir_path) -> Iterable[int]
    def save(self, path): ...;  @classmethod load(cls, path) -> "IndexStore"
class IndexBuilder:                   # used by the crawler on a worker thread
    def add(self, parent_id, name, size, mtime, flags) -> int; def add_dir(self, path, parent_id) -> int
    def finish(self) -> IndexStore
class IndexDelta:                     # live adds/removes between rescans; thread-safe
    def add(self, path, size, mtime, flags); def remove(self, path); def apply_to(self, hits) -> hits
```

Flags are computed at crawl time: `FILE_ATTRIBUTE_DIRECTORY`, `HIDDEN`, `SYSTEM`, placeholder = any of `FILE_ATTRIBUTE_OFFLINE (0x1000)`, `RECALL_ON_OPEN (0x40000)`, `RECALL_ON_DATA_ACCESS (0x400000)` (AC-FI-6), deny-listed = `denylist.matches(path)` (SEC-3) so the query layer can drop them without re-matching.

### 4c. Full-page web UI: build and vendoring

| Option | Preview fidelity | Node at runtime? | Who builds | Repo footprint |
|---|---|---|---|---|
| **(1) React 19 + Vite, reusing MD-Vault's `MarkdownViewer`, `MermaidDiagram`, `MarkmapDiagram` components (`[FS Q8]`), plus pdf.js, docx-preview, SheetJS CE, papaparse, highlight.js, DOMPurify; source in `web/`, built output committed to `assistant/static/app/`** | Full: Markdown with Mermaid/Markmap, PDF, DOCX, XLSX, CSV, code | No: Python serves static files; Node only on the personal laptop at build time (`[FS §10.1]`) | `ui` on the personal laptop (`C:\AI Projects\MD-Vault\` has `node_modules` and a built `dist/` today — verified 2026-11-06) | ~5–8 MB of vendored JS committed; acceptable, versioned with the code |
| (2) No-build: hand-written ES modules + vendored library bundles (marked, DOMPurify, pdf.js, mermaid ESM) | Same libraries, but MD-Vault's components (callouts, task lists, markmap wiring) would be rewritten by hand without React | No | `ui`, slower | Similar |
| (3) Server-side rendering in Python (markdown-it-py, no PDF) | Cannot do PDF/Mermaid/Markmap at all | No | `dev` | Small |

**Decision: (1).** The user already owns the exact Markdown renderer he wants in MD-Vault and the brief lists the drop-in libraries with licences; rewriting them without a build step costs more than vendoring a build. Rules: `web/package-lock.json` is committed; `scripts/build-web.ps1` runs `npm ci && npm run build` and copies to `assistant/static/app/`; the committed output must be reproducible (QA re-runs the build and checks `git status` is clean); the repo's `.gitignore` currently ignores `dist/` and `build/` — the static output lives under `assistant/static/app/` precisely so it is not ignored; `mermaid.initialize({securityLevel: 'strict'})` (not MD-Vault's `'loose'`) and DOMPurify on every HTML path (SEC-5).

### 4d. Plugin host: in-process loading and worker model

In-process is fixed by `[DEC 9]`; the freedom is in loading and threading.

| Option | Contains a hung plugin? | Attribution for the audit log | Complexity |
|---|---|---|---|
| **(1) One single-thread `ThreadPoolExecutor` per enabled plugin, thread named `plugin:<id>`; every call is `future.result(timeout=T)`; a timeout counts as a strike; after three strikes the plugin is disabled and its executor abandoned (the thread leaks, logged)** | Yes for everything but a hard crash (`[PL Q4]`): a hung plugin blocks only its own executor | Cheap: the PEP 578 audit hook reads `threading.current_thread().name` and returns immediately for non-plugin threads — no stack walk on the hot `open` event | Low |
| (2) Shared pool for all plugins | One hung plugin eventually starves the others | Needs a stack walk per audit event | Low |
| (3) Thread per call | Unbounded threads under a slow plugin | Same as (1) | Low, but no backpressure |

**Decision: (1).** It is the only option where the three-strike rule actually frees the app from a hung plugin and where audit attribution is O(1). Loading: `importlib.util.spec_from_file_location(f"assistant_plugins.{id}.{n}", entry_path)` with a fresh `n` per (re)load so reload never hits `importlib.reload`'s caveats (`[PL Q4 hot reload]`); the plugin's `lib/` is appended (not prepended) to `sys.path` while importing so the app's own pins win on conflict (`[PL Q4]`, R13). `ctx.every(seconds, fn)` registers with the host scheduler, which submits `fn` to the plugin's executor — plugins never own threads (AC-PL-7).

## 5. Package and module layout

Package name: **`assistant`** (unchanged from the PRD; the PRD's `python -m assistant …`, `python -m assistant.mcp_stdio` and `from assistant.plugin_api import …` all resolve as written). Nothing is renamed.

### 5.1 Repository tree

```
Assistant/
  pyproject.toml, requirements.txt, requirements-dev.txt
  install.ps1, uninstall.ps1, run.cmd, demo.py          # demo.py = 2-line shim to assistant.demo
  README.md, CHANGELOG.md, PLUGIN_AUTHORING.md
  assistant/
    __init__.py            __version__
    __main__.py            CLI: (none)=run app | --doctor | send | demo | update | hooks install|remove
                                | plugin new|check | shutdown
    app.py                 composition root: builds store, scheduler, server, overlay; mainloop
    uibus.py               post()/start(): the only bridge from threads to tkinter
    paths.py               ASSISTANT_HOME resolution and the ~/.assistant layout (5.2)
    settings.py            settings.json schema, defaults, atomic save, change listeners
    scheduler.py           heap scheduler thread (4a)
    doctor.py              --doctor report
    send.py                `send` CLI: POST /v1/cards with the local token
    demo.py                sample cards + search box with sample data, ASSISTANT_HOME in a temp dir
    update.py              `update`: git pull or zip download, pip install, "Updated to" card
    hooks_installer.py     Claude Code settings.json merge / backup / exact removal
    mcp_stdio.py           stdio shim forwarding to http://127.0.0.1:<port>/mcp
    plugin_api.py          Card, CardAction, Result, Preview, CardSource, SearchMode, ... (PRD §9)
    security/
      token.py             create/read/regenerate token file with user-only ACL
      denylist.py          SEC-3 patterns + private-key content check (shared by index, preview, MCP, Ask Claude)
      pathcheck.py         resolve symlinks/junctions, in-roots check (SEC-4)
    cards/
      model.py             Card dataclass + validation (PRD §7)
      store.py             open cards, snooze/dismiss/mute/seen, persistence state.json, events cursor
      claude_sessions.py   per-session_id state machine fed by hook events
      filedrop.py          ~/.assistant/events/ watcher
    reminders/
      grammar.py           DEC 6 grammar -> (title, detail, at, interpretation) | DidntUnderstand
      store.py             reminders.json, due-on-start handling
    calendar/
      ics.py               ICS fetch + parse + recurrence expansion
      outlook.py           classic Outlook COM reader
      meetings.py          merge, dedupe, lead-time scheduling, auto-dismiss
    overlay/
      win.py               window styles (TOPMOST, TOOLWINDOW, NOACTIVATE, LAYERED), click-through, DPI
      monitors.py          enumerate monitors/work areas, DPI per monitor, hot-unplug handling
      gfx.py               rounded panels, fonts, colours per kind, theme
      sounds.py            generated per-kind sounds via winsound
      icon.py              round icon, drag, badge, menu, mute visual, position persistence
      cards_ui.py          card windows, slide-in, blink, hover=seen, buttons, timer, mirroring per monitor
      panel.py             the rounded search panel
      hotkey.py            RegisterHotKey thread (4a)
      search_ui.py         search box, modes/badges, results list, chips, saved searches, Esc chain
      fullpage.py          Edge --app launcher with launch nonce, F11/Esc return
      reminder_ui.py       Remind me... input with live interpretation
      ask_claude_ui.py     confirmation dialog + answer view
      settings_ui.py       Settings window shell (notebook) + General/Cards/Calendars/Claude Code/MCP/About pages
      settings_pages_search.py   Roots & excludes, Search pages
      settings_pages_plugins.py  Plugins page, consent dialog
      mdrender_tk.py       Markdown -> tkinter Text tags for Ask-notes passages
    server/
      app.py               Starlette app factory, uvicorn thread, loopback bind
      auth.py              bearer check, Host/Origin check, launch-nonce exchange
      routes_health.py     GET /v1/health, POST /v1/shutdown, GET /v1/_debug/ui (test mode only)
      routes_cards.py      PRD §8 card routes
      routes_reminders.py  PRD §8 reminder routes
      routes_hooks.py      POST /v1/hooks/claude-code
      routes_search.py     GET /v1/search, saved searches, roots
      routes_files.py      GET /v1/files/preview, /v1/files/info; byte streaming, sniffing, caps
      routes_actions.py    POST /v1/actions/{open,reveal,copy,rename,recycle,properties,open_with}
      static.py            StaticFiles mount at /app
      mcp.py               FastMCP server at /mcp: card + reminder tools
      mcp_files.py         search_files, get_file_info, read_text_preview, list_indexed_roots
    index/
      store.py             IndexStore / IndexBuilder / IndexDelta (4b)
      excludes.py          gitignore-style rules, defaults from [FS Q6], per-rule counters
      crawler.py           os.scandir walkers, background priority, battery pause, share timeout
      watcher.py           ReadDirectoryChangesW per root, overflow -> subtree rewalk
      manager.py           load persisted index, rescan schedule, freshness, swap-in
      query.py             DEC 5 grammar parser -> Query + interpretation string
      rank.py              AC-FI-13 ranking
      search.py            execute Query over IndexStore + IndexDelta, chips, date filter, sort
    search/
      modes.py             mode registry (Files, Ask notes, plugin modes), Tab cycle, keyword prefixes
      saved.py             saved_searches.json
      notes_bm25.py        Ask notes (M3a built-in; moved into bundled_plugins/ask-notes in M5)
    actions/
      shell.py             open / open with / reveal / properties / recycle bin / clipboard / exec confirm
      names.py             Windows file-name validation (AC-AC-4)
      claude.py            Ask Claude: detection, payload building, `claude -p` send, log
    preview/
      extract.py           pure-Python text extraction for docx/pptx/xlsx/pdf (Ask Claude + previews)
    plugins/
      manifest.py          plugin.toml parse/validate
      loader.py            discovery, import under fresh module name, lib/ path handling
      host.py              executors, timeouts, strikes, Context, scheduler bridge
      installer.py         GitHub URL install, pip --target, updates, uninstall
      audit.py             PEP 578 hook, per-plugin audit log, undeclared-network card
      secrets.py           DPAPI-protected password settings
      cli.py               `plugin new`, `plugin check`
    bundled_plugins/
      timer/  bookmarks/  send-to-claude/  csv-ipynb-preview/  ask-notes/
    static/app/            vendored web build (committed)
  web/                     React + Vite source (ui role); package-lock.json committed
  scripts/build-web.ps1
  tools/
    driver.py              overlay test driver (section 8.3)
    probes/                M0 probe scripts (section 6)
  tests/                   pytest; fixtures under tests/fixtures/
  docs/
    plans/ prds/ research/ reference/ probes/ design/ security/ api.md
  .claude/skills/run-assistant/SKILL.md   how an agent starts, drives and screenshots the app
```

### 5.2 `~/.assistant/` (overridable with `ASSISTANT_HOME`, AC-ST-1)

```
settings.json          all settings incl. roots, excludes (user additions), ICS URLs (never logged in full)
token                  bearer token, user-only ACL (AC-MC-9)
state.json             open cards, snooze timers, mute state (AC-OV-13)
reminders.json         pending reminders (AC-RM-6)
saved_searches.json    (AC-FI-16)
index/files.idx        persisted IndexStore; files.idx.tmp during save
index/notes/           Ask-notes BM25 index
events/                file-drop inbox; events/rejected/ with .error.txt (AC-CS-12)
plugins/<id>/          user plugins; plugins-data/<id>/ their data (AC-PL-10)
plugins.json           enabled flags, install source and version per plugin
plugins-secrets.bin    DPAPI blob of password settings (A13)
hooks-backup/          timestamped copies of Claude Code settings before each write (AC-CS-1)
logs/app.log  logs/mcp.log  logs/claude-sends.log  logs/plugin-audit.log  (rotating, 5 x 2 MB)
assistant.pid          pid + port of the running instance (single-instance check, uninstall)
```

### 5.3 CLI entry points (all via `python -m assistant …`; unchanged from the PRD)

| Command | Module | Milestone |
|---|---|---|
| `python -m assistant` | `app.py` | M0 skeleton, M1 real |
| `python -m assistant --doctor` | `doctor.py` | M0 (grows every milestone) |
| `python -m assistant send --title … --detail … --kind …` | `send.py` | M1 |
| `python -m assistant demo` / `python demo.py` | `demo.py` | M1 |
| `python -m assistant hooks install|remove [--dry-run]` | `hooks_installer.py` | M1 (used by install.ps1 / uninstall.ps1) |
| `python -m assistant shutdown` | `__main__.py` → `POST /v1/shutdown` | M1 |
| `python -m assistant.mcp_stdio` | `mcp_stdio.py` | M2 |
| `python -m assistant update` | `update.py` | M4 |
| `python -m assistant plugin new <id>` / `plugin check <folder>` | `plugins/cli.py` | M5 |

## 6. M0 probe procedure (P1–P9)

Rules: probes run on **both** laptops where the PRD says so; on the work laptop the user runs them and pastes the output back (A5). Raw output is recorded verbatim by `doc-keeper` in `docs/probes/2026-11-06-m0-probes-personal.md` and `docs/probes/2026-11-06-m0-probes-work.md`, one `### Pn` section each with the command, the output, and PASS/FAIL against the PRD's "pass" column. A FAIL triggers the PRD's "If it fails" column and a dated Revision in this plan — never a silent workaround. Scripts referenced below are produced by task M0-1 and live in `tools/probes/`; until M0-1 lands, P1–P5 and P8 can be run as written because they are one-liners.

All commands are PowerShell. Replace `<repo>` with the clone path.

| # | Command(s) | Pass | Gates |
|---|---|---|---|
| P1 | `python -c "import win32gui, PIL; print('ok')"` then, because `mcp` pulls in more compiled wheels than the reference build used: `python -m pip install --user --only-binary :all: pydantic cryptography jsonschema` and `python -c "import pydantic_core, cryptography, rpds; print('ok')"` | both print `ok`, exit 0 | **Everything in M1+.** FAIL on the first → `ctypes`-only redesign of hotkey/shell/COM (surface to tech-lead). FAIL on the second → MCP must be served without the `mcp` package's compiled deps (hand-rolled Streamable HTTP over Starlette); surface before M2. |
| P2 | `python -m http.server 17171 --bind 127.0.0.1 --directory $env:TEMP` in one window; in another: `Start-Process msedge.exe -ArgumentList "--app=http://127.0.0.1:17171/"` | a chrome-less window opens listing `$env:TEMP` | M3b (full page, previews). FAIL → probe pywebview: `python -m pip install --user --only-binary :all: pywebview` and `python -c "import webview; webview.create_window('t','http://127.0.0.1:17171/'); webview.start()"`; if that also fails, previews are personal-laptop-only in v1 (Revision). |
| P3 | `python -m pip install --user --no-deps https://github.com/pypa/sampleproject/archive/refs/heads/main.zip` then `python -m pip uninstall -y sampleproject` | `Successfully installed sampleproject-…` | M5-3 (URL installer on the work laptop). FAIL → folder-drop only there; README states it. |
| P4 | `git --version` | prints a version | M1-5 install path and M4-2 `update` (git vs zip route). |
| P5 | `claude --version` | prints `≥ 2.1.233` | M1-4 hook coverage on that laptop; M4-1 Ask Claude availability (AC-AK-1). |
| P6 | `python <repo>\tools\probes\hook_logger.py --port 17199 --out $env:TEMP\hooks.jsonl` (keeps running; appends one JSON line per POST). Then `python <repo>\tools\probes\hook_probe_settings.py --port 17199 --print` prints the eight-event hooks block (AC-CS-2 list, `type: http`, `onFailure: continue`); paste it into `~\.claude\settings.json` **after** `Copy-Item ~\.claude\settings.json ~\.claude\settings.json.bak`. Perform, in the CLI and then in the desktop app: (a) ask Claude to run a shell command that needs permission, wait 10 s, approve; (b) ask Claude to "ask me a question using AskUserQuestion before continuing", answer it; (c) enter plan mode, let Claude present a plan, approve it. Finally `python <repo>\tools\probes\hook_probe_report.py $env:TEMP\hooks.jsonl` prints the table `case × client → events seen (with hook_event_name, matcher/notification_type, session_id, cwd, session_title?)`; restore `settings.json.bak`. | a table with at least `PermissionRequest` for (a) in both clients; (b) and (c) rows filled in with whatever fired, including "nothing" | M1-4 state machine; the one-sentence coverage statement for Settings → Claude Code (AC-CS-9). |
| P7 | `python <repo>\tools\probes\crawl_probe.py --roots "C:\Users\<you>" "C:\<other roots>" --excludes default` (pure `os.scandir`, dir-level excludes from `[FS Q6]`, prints entries, dirs, seconds, peak working set via `GetProcessMemoryInfo`, and the top 20 directories by entry count). Run twice: cold (after a reboot or `--cold-hint` which just labels the run) and warm. | numbers recorded; compared with PRD §12 (0.3M ≤ 30 s, 2M ≤ 5 min cold) | M3a commitment. Miss by > 2x → enable `scandir-rs` on the personal laptop, tighten excludes on the work laptop, Revision of §12 with evidence. |
| P8 | `reg query HKCR\Outlook.Application\CLSID` and `Get-AppxPackage Microsoft.OutlookForWindows | Select Name, Version` and `python -c "import win32com.client as w; o=w.Dispatch('Outlook.Application'); print(o.Version)"` | the third prints a version (classic Outlook present) or raises (new Outlook only) | M2-1 Outlook source enabled/disabled on that laptop; if new-only, user checks whether "Publish calendar" is allowed by the tenant (records yes/no). |
| P9 | `python <repo>\tools\probes\tk_idle_probe.py --minutes 10` (blank topmost tkinter window with a 1 Hz `after` label update; samples private working set and process CPU time every 10 s via `ctypes`; prints average CPU % and max working set) | numbers recorded against PRD §12 (overlay ≤ 80 MB proposal, idle CPU ≈ 0 %) | M1-3a/3b targets; A10 combined RAM target. |

Order on the personal laptop: P1, P2, P4, P5, P8, P9 first (minutes); P7 cold needs a reboot — schedule it last. P6 needs about 20 minutes of interactive Claude Code use and is the probe most likely to change scope, so the tech-lead runs it (or asks the user to) before dispatching M1-4.

## 7. Work breakdown

Conventions for every row: **Role** is the TeamAgents agent; **Files owned** is the complete write set (nothing else may be touched; two tasks in the same milestone never share a file); **AC** lists the PRD acceptance-criteria ids the task satisfies (or partially satisfies, marked "part"); **Done** is what `qa` observes; **Hand-off** is the verifier. "Parallel group" letters: tasks with the same letter may run concurrently; a task listing "after X" must wait for X to pass qa. Every generator task also: imports nothing from tkinter outside `assistant/overlay/`, logs through `logging.getLogger("assistant.<module>")`, and writes no file outside `ASSISTANT_HOME` at runtime. `git-keeper` merges after the named verifier(s) pass; `doc-keeper` runs once per milestone after the last merge.

### 7.0 M0 — Probes and skeleton (ends with numbers in hand)

| Id | Role | Goal | Files owned | AC | Done (qa observes) | Hand-off | Parallel |
|---|---|---|---|---|---|---|---|
| M0-1 | dev | Write the probe scripts in section 6 so both laptops can run P6, P7 and P9 from stdlib-only Python. | `tools/probes/hook_logger.py`, `tools/probes/hook_probe_settings.py`, `tools/probes/hook_probe_report.py`, `tools/probes/crawl_probe.py`, `tools/probes/tk_idle_probe.py`, `tools/probes/README.md` | PRD §5 P6, P7, P9 | Each script runs on the personal laptop with the exact command in section 6 and prints the fields named there; `crawl_probe.py` over a `tests/fixtures`-sized tree reports the true count; scripts import only the standard library. | qa | A |
| M0-2 | dev | Create the package skeleton that starts, serves `/v1/health` with token auth on loopback, and prints `--doctor`. | `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `.gitignore` (so `assistant/static/` is never ignored), `assistant/__init__.py`, `assistant/__main__.py`, `assistant/app.py` (skeleton: hidden tk root + server thread + uibus), `assistant/uibus.py`, `assistant/paths.py`, `assistant/settings.py`, `assistant/scheduler.py` (shell with the API of 4a), `assistant/doctor.py`, `assistant/security/__init__.py`, `assistant/security/token.py`, `assistant/server/__init__.py`, `assistant/server/app.py`, `assistant/server/auth.py`, `assistant/server/routes_health.py`, `run.cmd` | AC-ST-1 (part: folder layout, `ASSISTANT_HOME`), AC-MC-9 (token file + ACL), SEC-1, SEC-2 (part), AC-IN-5 (part: Python, deps, wheels import, port, token present) | `set ASSISTANT_HOME=<tmp>` then `python -m assistant` starts; `curl -H "Authorization: Bearer <token>" http://127.0.0.1:17171/v1/health` returns 200 JSON with `version`; no token → 401; `Host: evil.example` → 403; `icacls <tmp>\token` shows only the current user; `python -m assistant --doctor` prints every AC-IN-5 line that exists yet ("n/a" for the rest) and never prints the token value. | qa, then security (token + auth) | A |
| M0-3 | qa | Stand up pytest with an isolated `ASSISTANT_HOME`, a running-app fixture, and a fixture-tree builder used by every later milestone. | `tests/conftest.py`, `tests/test_health_auth.py`, `tests/fixtures/__init__.py`, `tests/fixtures/tree.py` (builds a tree from a JSON spec, incl. deny-listed names, a junction, a file with `FILE_ATTRIBUTE_OFFLINE`), `tests/fixtures/tree_small.json` | test infra for AC-FI-*, SEC-3 | `pytest tests/test_health_auth.py` passes against M0-2; `tree.py` builds `tree_small.json` and the printed count equals the spec. | git-keeper | A (health tests run after M0-2) |
| M0-4 | user + doc-keeper | Run P1–P9 per section 6 on both laptops and record the raw output. | `docs/probes/2026-11-06-m0-probes-personal.md`, `docs/probes/2026-11-06-m0-probes-work.md` | PRD §5, §12 (measured values) | Both files exist, every probe has command + verbatim output + PASS/FAIL; the planner appends a Revision to this plan recording the measured §12 values and any scope change. | tech-lead | after M0-1 |

M0 leaves: a package that starts and answers `/v1/health`; `--doctor`; probe numbers. Rollback: nothing is installed yet (no shortcut); delete `~/.assistant/`.

### 7.1 M1 — Overlay that replaces the reference popup

Interfaces fixed here so the parallel tasks agree: `cards.store.CardStore` exposes `upsert(card: Card, source: str, renotify=False) -> Card`, `get(id)`, `list(open_only=True)`, `patch(id, **fields)`, `snooze(id, minutes)`, `dismiss(id)`, `mark_seen(id)`, `press(id, action_id)`, `events_since(id, cursor)`, `set_mute(until: float|None)`, `subscribe(callback)` (callbacks run on the caller's thread; the overlay wraps its callback in `uibus.post`). `reminders.grammar.parse(text, now) -> Parsed(title, detail, at, interpretation)` or raises `DidntUnderstand(help_sentence)`.

| Id | Role | Goal | Files owned | AC | Done (qa observes) | Hand-off | Parallel |
|---|---|---|---|---|---|---|---|
| M1-1 | dev | Implement the card model, validation and the card store with persistence, snooze/dismiss/seen/mute semantics and the events cursor — no UI. | `assistant/cards/__init__.py`, `assistant/cards/model.py`, `assistant/cards/store.py` | §7 (model, defaults by kind, update-in-place, unknown fields logged once), AC-OV-6 (timing logic), AC-OV-9, AC-OV-10 (count), AC-OV-12 (queue while muted), AC-OV-13, AC-CS-11 (validation → field errors) | `pytest tests/test_cards_store.py` (M1-7) passes: a `done` card auto-expires 20 s after `mark_seen` (injected clock), snooze returns at +5 min unless dismissed, an update during snooze re-shows, muted cards are queued and counted, reload of `state.json` reproduces open cards with seen/snooze state, a 501-char detail returns `field: detail`. | qa | B |
| M1-2 | dev | Implement the scheduler thread and the reminder grammar and store. | `assistant/scheduler.py` (replaces M0-2's shell), `assistant/reminders/__init__.py`, `assistant/reminders/grammar.py`, `assistant/reminders/store.py` | AC-RM-2, AC-RM-3 (interpretation text + help sentence), AC-RM-5, AC-RM-6 | `pytest tests/test_reminder_grammar.py tests/test_scheduler.py` passes: the four grammar shapes incl. `at` past-today → tomorrow, 24-h without am/pm, weekday = next occurrence; `foo bar` raises with the exact help sentence; scheduler fires a 2-s job within ±0.5 s; cancel works; `reminders.json` round-trips and an overdue reminder is marked "(was due <time>)". | qa | B |
| M1-3a | dev | Build the overlay foundation: window styles, monitors/DPI, graphics, sounds and the draggable icon with its menu, badge, mute visual, position persistence, and the Remind-me input. | `assistant/overlay/__init__.py`, `assistant/overlay/win.py`, `assistant/overlay/monitors.py`, `assistant/overlay/gfx.py`, `assistant/overlay/sounds.py`, `assistant/overlay/icon.py`, `assistant/overlay/reminder_ui.py` | AC-OV-1 (styles), AC-OV-2, AC-OV-8, AC-OV-10 (badge render), AC-OV-11 (menu order), AC-OV-12 (grey + crossed bell), AC-OV-15 (part: icon), AC-RM-1, AC-RM-3 (UI), AC-RM-4, AC-PS-2, AC-PS-3 | With the app in test mode, `tools/driver.py` (M1-7) shows: icon at bottom-right of the primary work area; `GetWindowLong(GWL_EXSTYLE)` on its handle includes `WS_EX_TOOLWINDOW`, `WS_EX_NOACTIVATE`, `WS_EX_TOPMOST`; no taskbar button; drag by 200 px then restart → within 2 px; Reset position returns it; menu items in AC-OV-11 order (read via `/v1/_debug/ui`); "Remind me…" shows the live interpretation and the didn't-understand line. | qa | B |
| M1-3b | dev | Build the card windows: slide-in, stacking newest-at-bottom, blink + sound until hover, buttons, live waiting timer, 20-s fade, per-monitor mirroring, priority-5 sort. | `assistant/overlay/cards_ui.py`, `assistant/overlay/panel.py` | AC-OV-3, AC-OV-4, AC-OV-5, AC-OV-6 (visual), AC-OV-7, AC-OV-9 (visual), AC-OV-14, AC-OV-15 (cards), AC-MM-1, §7 `actions` buttons and `url` click | Driver posts five cards of five kinds: `/v1/_debug/ui` reports five windows with the kind colours (hex values fixed in `gfx.py` and asserted), stacked above the icon newest at the bottom; a `waiting` card's timer advances `0:01 → 0:02`; hover sets `seen=true` and blinking stops; a `done` card disappears 20 ± 2 s later; with two monitors the same card id appears on both, with the setting off only on the icon's. | qa | after M1-3a |
| M1-4 | dev | Implement the HTTP card and reminder routes, the Claude Code hook receiver with the per-session state machine, and the file-drop source. | `assistant/server/routes_cards.py`, `assistant/server/routes_reminders.py`, `assistant/server/routes_hooks.py`, `assistant/cards/claude_sessions.py`, `assistant/cards/filedrop.py` | §8 (all card/reminder routes, error shape, status codes), AC-CS-2 (part: receiver), AC-CS-3, AC-CS-4, AC-CS-5, AC-CS-6, AC-CS-7, AC-CS-8, AC-CS-9, AC-CS-11, AC-CS-12, AC-RM-7 (HTTP), SEC-11 | `pytest tests/test_api_cards.py tests/test_hooks_statemachine.py` passes: `POST /v1/cards` returns 201 + id and the card is visible within 1 s; wrong token 401; a recorded `PermissionRequest` payload (P6 fixture) yields one `waiting` card titled with the `cwd` basename within 2 s; a following `PostToolUse` with the same `session_id` clears it within 2 s; a second `PermissionRequest` for the same session updates, never duplicates; `Stop` → green card with 160-char detail and "Task completed in …"; `StopFailure` → waiting card naming the error class; hook responses are `200 {}` in < 1 s and contain no decision key; a JSON file dropped in `events/` becomes a card within 2 s and is deleted; a malformed one lands in `events/rejected/` with `.error.txt`. | qa, then security | B (integration test after M1-1) |
| M1-5 | dev | Deliver the CLI sender, the Claude Code hooks installer/remover, `demo`, and the install/uninstall scripts. | `assistant/send.py`, `assistant/hooks_installer.py`, `assistant/demo.py`, `demo.py`, `install.ps1`, `uninstall.ps1`, `assistant/__main__.py` (adds `send`, `demo`, `hooks`, `shutdown`), `assistant/doctor.py` (adds hooks installed + Claude version) | AC-CS-1, AC-CS-2 (entries written), AC-CS-10, AC-CS-13, AC-IN-1, AC-IN-2, AC-IN-3, AC-IN-5 (part), AC-IN-8 | `python -m assistant hooks install --dry-run` prints the exact eight entries; `hooks install` against a fixture `settings.json` with unrelated hooks leaves them byte-identical, writes `hooks-backup/<ts>.json`, and `hooks remove` restores byte-identical content; `python -m assistant send --title t --kind custom` shows a card; `python demo.py` with no `~/.assistant` shows sample cards and writes only under `%TEMP%`; in a clean Windows user profile with Python only, `install.ps1` installs deps for the user, creates `shell:startup\Assistant.lnk` → `pythonw.exe -m assistant`, starts the app, no UAC prompt; `uninstall.ps1` stops it, removes shortcut and hook entries, asks before deleting `~/.assistant`. | qa, then security | B |
| M1-6 | dev | Wire the pieces into one running application. | `assistant/app.py`, `assistant/uibus.py` | AC-OV-13 (end-to-end), AC-RM-5/6 (end-to-end), §12 hook → card ≤ 2 s | From a fresh `ASSISTANT_HOME`: icon appears; `send` shows a card; `x in 1m` fires a purple card at +60 ± 5 s; kill + relaunch restores open cards; Quit leaves no `python` process; 10-minute idle CPU ≈ 0 % and RAM recorded with the P9 method. | qa | after M1-1 … M1-5 |
| M1-7 | qa | Write the M1 tests, the overlay driver and the agent skill for driving the app. | `tests/test_cards_store.py`, `tests/test_reminder_grammar.py`, `tests/test_scheduler.py`, `tests/test_api_cards.py`, `tests/test_hooks_statemachine.py`, `tests/test_hooks_installer.py`, `tests/e2e/test_overlay.py`, `tests/fixtures/hook_payloads/*.json` (from P6), `tools/driver.py`, `.claude/skills/run-assistant/SKILL.md` | verification side of all M1 ACs | Every test above green on the personal laptop; the driver can start/stop the app, post a card, hover/click coordinates from `/v1/_debug/ui`, and save a screenshot; the skill tells an agent exactly those commands. | git-keeper | B (written against the fixed interfaces; run as tasks land) |
| M1-8 | security | Review the M1 HTTP surface, hook receiver, token handling and hooks installer. | `docs/security/2026-11-xx-m1-review.md` | SEC-1, SEC-2, SEC-11, AC-CS-1 | Verdict with evidence: Host/Origin tests, token ACL, no decision in hook responses, backup before write, `/v1/_debug/ui` absent without `ASSISTANT_TEST_MODE`. | tech-lead | after M1-4, M1-5 pass qa |
| M1-9 | doc-keeper | Record M1. | `README.md`, `CHANGELOG.md`, `docs/api.md` (§8 routes as shipped) | AC-IN-7 (part) | Appended; nothing deleted. | git-keeper | after merge |

M1 leaves: the alert half of the reference popup replaced on both laptops. Rollback: `uninstall.ps1`; `state.json` is forward-compatible (unknown keys ignored).

### 7.2 M2 — Meetings and MCP card tools

Interfaces fixed: `calendar.meetings.MeetingSource` protocol with `fetch(window_start, window_end) -> list[Event(uid, subject, start, end, location, join_url, all_day, declined, source_label)]`; `server/mcp.py` builds `FastMCP("assistant")` whose `streamable_http_app()` is mounted at `/mcp` by `server/app.py` behind `auth.py`.

| Id | Role | Goal | Files owned | AC | Done (qa observes) | Hand-off | Parallel |
|---|---|---|---|---|---|---|---|
| M2-1 | dev | Meeting cards from ICS URLs and classic Outlook COM with dedupe, lead time and auto-dismiss. | `assistant/calendar/__init__.py`, `assistant/calendar/ics.py`, `assistant/calendar/outlook.py`, `assistant/calendar/meetings.py` | AC-MT-1 … AC-MT-7, SEC-10, SEC-12 | `pytest tests/test_ics.py tests/test_meetings.py`: fixture ICS with a weekly recurring event, an all-day event, a declined event and a join URL yields exactly the expected `Event`s; a local test HTTP server serving an event starting in 6 min produces a blue card at T-5 min ± 5 s titled "<subject> starts in 5 minutes" with `expires_at` = end; the same event in two sources → one card; a 404 feed sets `calendars[].last_error` in `/v1/health` and no card before 1 h; `logs/app.log` never contains the ICS URL's path. Outlook COM exercised on the personal laptop only if P8 found classic Outlook there. | qa, then security | C |
| M2-2 | dev | Serve the `assistant` MCP server at `/mcp` with card and reminder tools, and the stdio shim. | `assistant/server/mcp.py`, `assistant/mcp_stdio.py`, `assistant/security/token.py` (adds `regenerate()`), `assistant/server/routes_health.py` (adds `/v1/mcp/log`) | AC-MC-1, AC-MC-2 (shim), AC-MC-6, AC-MC-7, AC-MC-8, AC-MC-9 (regenerate), AC-RM-7 (MCP), SEC-1 | `pytest tests/test_mcp_cards.py` with the `mcp` client over Streamable HTTP: `tools/list` returns exactly `send_card, update_card, dismiss_card, add_reminder`; `send_card` shows a card and returns its id; no token → 401; `Origin: http://evil` → 403; `add_reminder("x in 1m")` returns the interpretation, `add_reminder("nonsense")` the didn't-understand error; `python -m assistant.mcp_stdio` answers `initialize` and forwards `tools/call`; `logs/mcp.log` has one line per call (tool, path-only args, result size); regenerating the token makes the old one 401 immediately. | qa, then security | C |
| M2-3 | dev | Settings window shell and the pages that exist by M2. | `assistant/overlay/settings_ui.py`, `assistant/settings.py` (adds change listeners + live apply) | AC-ST-2 (General, Cards, Calendars, Claude Code, MCP, About), AC-ST-3, AC-ST-4, AC-ST-5, AC-CS-9 (coverage sentence from P6), AC-MC-2 (snippets), AC-MT-1 (URL list UI), AC-OV-11 (Settings entry) | Settings opens from the icon menu; changing lead time writes `settings.json` within 1 s and the next meeting card honours it without restart; port/hotkey fields say "takes effect after restart"; MCP page shows `claude mcp add --transport http assistant http://127.0.0.1:17171/mcp --header "Authorization: Bearer <token>"` and a Claude Desktop JSON snippet; Claude Code page shows the coverage sentence; per-source mute toggles for `hooks, http, mcp, file`; no default roots/notes/ICS values anywhere. | qa | C |
| M2-4 | dev | Wire meetings, MCP and Settings into the app. | `assistant/app.py`, `assistant/doctor.py` (adds Outlook COM availability, MCP mount status) | AC-IN-5 (part) | Full app start: `/mcp` answers `tools/list` with the token; a configured test ICS feed produces a card on time; `--doctor` prints "Outlook COM: available/unavailable". | qa | after M2-1 … M2-3 |
| M2-5 | qa | M2 tests. | `tests/test_ics.py`, `tests/test_meetings.py`, `tests/test_mcp_cards.py`, `tests/test_mcp_stdio.py`, `tests/e2e/test_settings_live_apply.py`, `tests/fixtures/calendars/*.ics` | verification side of M2 | Green on the personal laptop; a Claude Desktop config pointing at the shim, used once by the user on each laptop, shows the four tools (user pastes the Claude Desktop MCP panel screenshot). | git-keeper | C |
| M2-6 | security | Review the MCP surface and credential handling. | `docs/security/2026-11-xx-m2-review.md` | SEC-1, SEC-2, SEC-12 | Verdict with evidence incl. ICS URL redaction in logs and `--doctor`. | tech-lead | after M2-1, M2-2 |
| M2-7 | doc-keeper | Record M2; `docs/api.md` gains the MCP tools. | `README.md`, `CHANGELOG.md`, `docs/api.md` | AC-MC-2 (config snippets documented), AC-MC-3/6 (tool list documented) | Appended. | git-keeper | after merge |

M2 leaves: meetings arrive; Claude sessions post cards through MCP from Claude Code and Claude Desktop. Rollback: disable calendar sources in Settings; the MCP mount is one line in `app.py`.

### 7.3 M3a — Files mode: index, query, popup, Ask notes, MCP file tools

Gate: P7 numbers recorded (M0-4). If P7 missed by > 2x, the Revision that applied the fallbacks must exist before M3a-2 is dispatched. Interfaces fixed: section 4b (`IndexStore`/`IndexBuilder`/`IndexDelta`); `index.query.parse(text, known_exts) -> Query(terms, path_terms, exts, path_parts, nots, phrases, interpretation: str)`; `index.search.run(query, store, delta, chips, date_filter, sort, limit, offset) -> SearchResult(rows, total, elapsed_ms, interpretation, built_at)`; `search.modes.Mode` protocol `keyword, label, local: bool, search(text) -> list[Row]`; `security.denylist.matches(path) -> bool`, `security.denylist.content_has_private_key(text) -> bool`; `security.pathcheck.resolve_in_roots(path, roots) -> str | None`.

| Id | Role | Goal | Files owned | AC | Done (qa observes) | Hand-off | Parallel |
|---|---|---|---|---|---|---|---|
| M3a-1 | dev | Implement the columnar index store with persistence, the exclude rules with per-rule counters, the secrets deny-list and path resolution. | `assistant/index/__init__.py`, `assistant/index/store.py`, `assistant/index/excludes.py`, `assistant/security/denylist.py`, `assistant/security/pathcheck.py` | AC-FI-1 (rules + defaults from `[FS Q6]`), AC-FI-2 (persist/load), AC-FI-7 (counters), SEC-3 (patterns, content check, "show protected by name" flag honoured by callers), SEC-4 | `pytest tests/test_index_store.py tests/test_excludes.py tests/test_denylist.py`: a builder fed 2,000,000 synthetic names (generated in-process) produces a store whose `find_substring("rep")` returns in < 50 ms (median of 50 runs, printed) and whose private working set delta is printed; `save` then `load` round-trips byte-identically; `excludes` matches `**/node_modules/`, `$RECYCLE.BIN/`, `*.vhdx`, a trailing-`/` directory rule, and reports per-rule counts; every SEC-3 default pattern matches its canonical example and `-----BEGIN RSA PRIVATE KEY-----` is detected; a junction into an excluded tree resolves and is refused. | qa, then security | D |
| M3a-2 | dev | Crawl roots with `os.scandir` at background priority, watch for changes, persist and swap indexes, and keep the freshness timestamp honest. | `assistant/index/crawler.py`, `assistant/index/watcher.py`, `assistant/index/manager.py` | AC-FI-2 (start from persisted, rescan in background), AC-FI-3, AC-FI-4, AC-FI-5, AC-FI-6, AC-SH-9 (built_at source), §12 crawl targets | `pytest tests/test_crawler.py tests/test_watcher.py` on the fixture tree from `tests/fixtures/tree_search.json`: entry set equals the spec exactly (excluded dirs never entered — verified by a probe file placed inside `node_modules`); the `FILE_ATTRIBUTE_OFFLINE` fixture is indexed with the placeholder flag and its content is never opened (`open` audit hook asserts); create/rename/delete under a root appears in results within 5 s; a `ERROR_NOTIFY_ENUM_DIR` overflow (simulated by 20,000 quick creates) ends with a correct index after the scheduled rescan and `built_at` reflects the last completed walk; a UNC root pointing at a non-responding host is skipped after the configured timeout while `/v1/health` keeps answering; crawl of the real personal-laptop roots re-measured and recorded against P7. | qa | D |
| M3a-3 | dev | Implement the DEC 5 query grammar, ranking, filters and the search executor. | `assistant/index/query.py`, `assistant/index/rank.py`, `assistant/index/search.py` | AC-FI-8 … AC-FI-15 (filters), AC-FI-18, DEC 5 | `pytest tests/test_query_grammar.py tests/test_rank.py tests/test_search.py`: the five `[REF L82]` examples (`design md`, `design doc`, `config -e yml`, `report -p Documents`, plain `design`) produce the interpretation strings in AC-FI-12 and the expected rows from the fixture tree; `"md"` quoted is a substring term; `-x foo` is a literal term; `!draft` excludes; `ext:md,py`, `path:docs`, `*` and `?` wildcards work; ranking order exact > prefix > word boundary > substring > path-only, ties by shorter name; chips OR together; date filters apply; `elapsed_ms` populated. | qa, then security (input parsing) | D |
| M3a-4 | dev | Build the hotkey thread, the search popup (box, modes, badges, results, chips, saved searches, keyboard, Esc chain) and the mode registry. | `assistant/overlay/hotkey.py`, `assistant/overlay/search_ui.py`, `assistant/search/__init__.py`, `assistant/search/modes.py`, `assistant/search/saved.py` | AC-SH-1 … AC-SH-6, AC-SH-8, AC-SH-9, AC-FI-14 (chips UI), AC-FI-15 (UI), AC-FI-16, AC-FI-17, AC-OV-14 (Esc closes popup first), AC-MM-2, §12 hotkey → box ≤ 300 ms | Driver: pressing Ctrl+Alt+Space shows the box beside the icon within 300 ms (timestamps from `/v1/_debug/ui`); typing `design md` + Enter grows the box into results showing rows with ext tag, highlighted name, path, date, a details line for the selected row, header with count, `index from …`, elapsed ms, badge `Files · local`, interpretation text; Tab switches to Ask notes; `?x` switches for that query; Up/Down/Enter/Esc behave; saved search created, listed before typing, runs; occupying the hotkey with another process first makes Settings show "already registered by another app" and icon click still opens the box; placement options honoured. | qa | D |
| M3a-5 | dev | Expose search over HTTP for the popup and web, and the four read-only MCP file tools. | `assistant/server/routes_search.py`, `assistant/server/mcp_files.py` | §8 `GET /v1/search` (fields: `q, chips, modified, sort, limit, offset` → `SearchResult`), `GET /v1/roots`, saved-search routes; AC-MC-3, AC-MC-4, AC-MC-5, AC-MC-7, AC-MC-8, SEC-3, SEC-4 | `pytest tests/test_routes_search.py tests/test_mcp_files.py`: `GET /v1/search?q=design+md` equals the in-process result; MCP `tools/list` now also has the four file tools annotated `readOnlyHint: true, openWorldHint: false`; `search_files` honours `limit ≤ 500`; `read_text_preview` on a `.exe` fixture refuses with a reason, on `.env` refuses, on the OFFLINE fixture refuses, on a text file returns content wrapped in the labelled untrusted delimiter with `max_bytes` honoured; a path outside roots, and a junction pointing outside, are refused; deny-listed fixture names appear in no MCP result even with the "show protected by name" setting on. | qa, then security | D |
| M3a-6 | dev | Ask notes: BM25 over `.md` under `ASSISTANT_NOTES_DIR`, persisted index, change refresh, placeholder skipping, formatted passages in the popup. | `assistant/search/notes_bm25.py`, `assistant/overlay/mdrender_tk.py` | AC-AN-1 … AC-AN-6, AC-SH-5 (local marker), §12 notes targets | `pytest tests/test_notes_bm25.py`: with the setting unset the mode reports "Set your notes folder in Settings"; a 2,000-note synthetic vault indexes in the background in < 10 s with "building index…" visible; subsequent queries < 200 ms (printed); results show file name and the passage with headings/bold/lists/code/table rendering tags and highlighted terms; footer text "search over your notes; nothing left this PC"; Enter opens, Ctrl+Enter reveals (via M3b actions when present; in M3a opens via `os.startfile`), an OFFLINE note is skipped and the skip count shows in Settings. | qa | D |
| M3a-7 | dev | Wire the index manager, search, popup and MCP file tools into the app; add the Roots & excludes and Search settings pages; grow `--doctor`. | `assistant/app.py`, `assistant/overlay/settings_pages_search.py`, `assistant/doctor.py` (adds index size/age, hotkey status) | AC-FI-7 (UI), AC-ST-2 (Roots & excludes, Search pages), AC-IN-5 (part) | Full app: configure a root in Settings → crawl starts without restart; per-rule counts shown; `--doctor` prints index entries/age and hotkey status; hotkey → search → results end-to-end with the real index on the personal laptop; p95 of 50 random 2–6 character queries printed by `tests/perf/test_query_latency.py` against the real index and recorded. | qa | after M3a-1 … M3a-6 |
| M3a-8 | qa | M3a tests and fixtures. | `tests/test_index_store.py`, `tests/test_excludes.py`, `tests/test_denylist.py`, `tests/test_crawler.py`, `tests/test_watcher.py`, `tests/test_query_grammar.py`, `tests/test_rank.py`, `tests/test_search.py`, `tests/test_routes_search.py`, `tests/test_mcp_files.py`, `tests/test_notes_bm25.py`, `tests/perf/test_query_latency.py`, `tests/e2e/test_search_popup.py`, `tests/fixtures/tree_search.json`, `tests/fixtures/notes/` | verification side of M3a | Green on the personal laptop; perf numbers printed and copied into the qa report. | git-keeper | D |
| M3a-9 | security | Review deny-list uniformity, path resolution, query parsing and the MCP file tools. | `docs/security/2026-11-xx-m3a-review.md` | SEC-3, SEC-4, AC-MC-4, AC-MC-5 | Verdict with evidence: a deny-listed fixture never appears in `GET /v1/search`, popup, or any MCP tool; junction escape refused. | tech-lead | after M3a-1, M3a-3, M3a-5 |
| M3a-10 | doc-keeper | Record M3a. | `README.md`, `CHANGELOG.md`, `docs/api.md`, `docs/search-grammar.md` (new) | AC-FI-12 (documented examples) | Appended. | git-keeper | after merge |

M3a leaves: Files mode and Ask notes in the popup; Claude can search files. The author can uninstall `fastsearch`'s popup use (full page follows in M3b). Rollback: remove roots in Settings (index idle); delete `~/.assistant/index/`.

### 7.4 M3b — Full page, previews, result actions

Gate: P2 passed on the work laptop (else previews are personal-laptop-only and the Revision says so). Interfaces fixed: `GET /v1/files/preview?path=&range=` returns `{kind: markdown|text|html|image|table|pdf|docx|xlsx|meta|protected|placeholder|too_large, …}` or streams bytes with `Content-Type` and `Accept-Ranges`; `POST /v1/actions/<verb>` with `{path, …}` returns `{ok, message}`; the full page is served at `/app/` from `assistant/static/app/` or, when the environment variable `ASSISTANT_STATIC_DIR` is set, from that folder (dev/qa only); it reads `window.location.search` for `launch=<nonce>` (A12).

| Id | Role | Goal | Files owned | AC | Done (qa observes) | Hand-off | Parallel |
|---|---|---|---|---|---|---|---|
| M3b-1 | ui | Build the full-page React app: header (count, freshness, elapsed, badge, interpretation, theme toggle, Settings/About links), query bar, chips, date filter, sort, saved searches, result rows, selection model and mouse gestures, context menu calling `/v1/actions`, keyboard parity with the popup — with a placeholder preview pane. | `web/**` except `web/src/preview/**`, `scripts/build-web.ps1` (build output is **not** committed by this task; qa serves a local build through `ASSISTANT_STATIC_DIR`) | AC-PV-1 (gestures, pane chrome), AC-FI-14, AC-FI-15, AC-FI-16, AC-FI-17, AC-FI-18, AC-SH-9, AC-AC-2, AC-AC-7 (web side), AC-PS-1 (web theme), DEC 10 markers | In Edge app mode on the personal laptop (ui verifies with Chrome DevTools MCP against Edge, screenshots attached): the page loads from `/app/?launch=<nonce>`, exchanges the nonce, and no token appears in the URL afterwards; typing `design md` shows the same rows as `GET /v1/search`; single-click fills the pane with the placeholder; double-click calls `/v1/actions/open`; Ctrl+click multi-selects; Ctrl+right-click copies path; context menu lists the AC-AC-2 items with "Ask Claude" carrying the sends-to-Claude marker (colour + icon) and local badge on the mode; `scripts/build-web.ps1 -OutDir $env:TEMPpp` builds from a clean checkout and the app serves it with `ASSISTANT_STATIC_DIR=$env:TEMPpp`. | qa | E |
| M3b-2 | ui | Implement the preview renderers in the pane. | `web/src/preview/**`, `assistant/static/app/**` (the first committed build of the full page) | AC-PV-2, AC-PV-3 (client side), AC-PV-4/5/6 (rendering the server's `protected`/`placeholder`/`too_large` answers), AC-PV-8 (render plugin `Preview` kinds), SEC-5 | Fixtures from M3b-6 rendered in Edge: Markdown with a Mermaid block, a Markmap block and a `<script>alert(1)</script>` line → diagrams render, no alert fires (DevTools console clean; `mermaid` initialised with `securityLevel: 'strict'` visible in the bundle); SVG with `onload` → image shown via `blob:`/`<img>`, no handler runs; PDF renders page 1 with the worker from the same origin; DOCX, XLSX (table, row cap shown), CSV, PNG/WebP/AVIF, code with highlighting and "show more" for a 3 MB log; HEIC → "not supported"; `.pem` → "protected file — not previewed"; OFFLINE fixture → "Download and preview" button; 60 MB file → size message; the committed `assistant/static/app/**` equals a fresh `scripts/build-web.ps1` run (`git status` clean after rebuild). | qa, then security | after M3b-1 |
| M3b-3 | dev | Serve previews safely: byte streaming with ranges, binary sniffing, caps, deny-list and placeholder checks, Office/PDF text extraction, static mount and the launch-nonce exchange. | `assistant/server/routes_files.py`, `assistant/server/static.py`, `assistant/server/auth.py` (adds nonce exchange), `assistant/preview/__init__.py`, `assistant/preview/extract.py`, `assistant/overlay/fullpage.py` | AC-PV-3 (server never inlines content as HTML), AC-PV-4, AC-PV-5, AC-PV-6, AC-SH-7 (Edge launch + F11/Esc return), SEC-2 (nonce), SEC-5 | `pytest tests/test_preview_routes.py`: `.pem` → `protected`; OFFLINE → `placeholder` and the file's content is never read (audit hook); 60 MB → `too_large`; `.exe` → `meta`; `Range: bytes=0-1048575` on a 3 MB log returns 206; `.docx`/`.xlsx`/`.pptx`/`.pdf` extraction returns text (pptx: slide text + media list); a nonce works once and expires after 30 s; `fullpage.open(query)` launches `msedge.exe --app=…` and `Esc`/F11 in the page calls `POST /v1/fullpage/close` which the launcher honours by closing the Edge window it opened. | qa, then security | E |
| M3b-4 | dev | Shell actions with Windows name validation and executable confirmation; Recycle-Bin-only deletion. | `assistant/actions/__init__.py`, `assistant/actions/shell.py`, `assistant/actions/names.py`, `assistant/server/routes_actions.py` | AC-AC-1 (reveal reuses window, open, MD Vault, Ctrl+F folder listing data), AC-AC-3, AC-AC-4, AC-AC-5, AC-AC-6, SEC-6, SEC-7 | `pytest tests/test_actions_names.py tests/test_actions_shell.py`: `names.validate("con.txt")`, `"a<b"`, `"trailing. "` each return the specific reason; `recycle(fixture)` → the file is listed by `SHQueryRecycleBin`/`shell:RecycleBinFolder` enumeration and gone from disk; `recycle` on a removable/UNC path returns refused with the explanation; `open("x.bat")` returns `needs_confirmation` first; `reveal` twice on files in the same folder opens one Explorer window (window count via `EnumWindows` on `CabinetWClass`); `copy_path` strips `\\?\`; `grep -r "os.remove\|unlink\|rmtree\|FOF_" assistant/` shows only the Recycle Bin call with `FOF_ALLOWUNDO`. | qa, then security | E |
| M3b-5 | dev | Popup side of actions and full page: context menu, keyboard actions, Full page button/F11, Ctrl+F folder listing. | `assistant/overlay/search_ui.py` | AC-AC-1, AC-AC-2, AC-AC-7 (popup side), AC-SH-6 (full Esc chain), AC-SH-7 (button), AC-PV-7 | Driver: with a result selected, Enter/Ctrl+Enter/Ctrl+M/Ctrl+F invoke the matching `/v1/actions` route (asserted via the action log in test mode); right-click shows the AC-AC-2 menu; F11 opens Edge app mode with the current query; Esc chain full page → results → closed; no preview inside the popup. | qa | E (its calls target M3b-4's routes; integration test after M3b-4) |
| M3b-6 | qa | M3b tests and the preview safety fixtures. | `tests/test_preview_routes.py`, `tests/test_actions_names.py`, `tests/test_actions_shell.py`, `tests/e2e/test_fullpage.py`, `tests/fixtures/preview/` (md with script+mermaid+markmap, svg with onload, pem, 60 MB sparse file generator, docx/xlsx/pptx/pdf/csv/ipynb samples, 3 MB log, png/webp/avif/heic) | verification side of M3b | Green; the browser observations in M3b-1/2 reproduced by qa via `tools/driver.py fullpage` (opens Edge, takes a screenshot) and attached. | git-keeper | E |
| M3b-7 | security | Review the preview pipeline, static serving, nonce exchange and shell actions. | `docs/security/2026-11-xx-m3b-review.md` | SEC-2, SEC-5, SEC-6, SEC-7 | Verdict with evidence: no content-as-HTML path without DOMPurify, strict Mermaid, no permanent delete call sites, `\\?\` handling, nonce single-use. | tech-lead | after M3b-2, M3b-3, M3b-4 |
| M3b-8 | doc-keeper | Record M3b. | `README.md`, `CHANGELOG.md`, `docs/api.md` | AC-PV-2 (supported preview types listed), AC-AC-2 (actions and shortcuts listed), AC-IN-7 (new dependencies flagged) | Appended. | git-keeper | after merge |

M3b leaves: `fastsearch` fully replaced (metric M2 clock starts). Rollback: the "Full page" button hides behind a setting if the Edge probe later fails on a laptop; actions routes can be disabled individually in settings.

### 7.5 M4 — Ask Claude, personalisation, multi-monitor, update

Gate: P5 result per laptop (AC-AK-1).

| Id | Role | Goal | Files owned | AC | Done (qa observes) | Hand-off | Parallel |
|---|---|---|---|---|---|---|---|
| M4-1 | dev | "Ask Claude about this file": detection, what-will-be-sent confirmation, deny-list refusal, send through the user's Claude Code sign-in, answer view, continue-in-Claude, local log. | `assistant/actions/claude.py`, `assistant/overlay/ask_claude_ui.py`, `assistant/server/routes_actions.py` (adds `/v1/actions/ask_claude/{prepare,send}`) | AC-AK-1 … AC-AK-7, SEC-9, DEC 10 | With `claude` on PATH: the menu item is enabled; choosing it shows path, mode (preview text / whole file with byte count), the Office extracted-text note, the sign-in account type when `claude` reports it, and the sends-to-Claude marker; nothing is sent before "Send" (asserted by a fake `claude` shim on PATH recording invocations); `.env` fixture → item disabled with reason; after Send, `logs/claude-sends.log` has time, path, bytes, route; "Continue in Claude Code" launches the shim with the same context. With `claude` absent: disabled with tooltip "Claude Code not found on this machine". | qa, then security | F |
| M4-2 | dev | Theme, remappable shortcuts in the settings file, monitor hot-unplug, and the `update` command. | `assistant/overlay/theme.py`, `assistant/overlay/monitors.py` (hot-unplug), `assistant/update.py`, `assistant/__main__.py` (adds `update`), `assistant/settings.py` (adds `shortcuts` and `theme`) | AC-PS-1 (overlay + popup), AC-PS-4, AC-MM-4, AC-IN-4, DEC 13 (no self-update) | Theme light/dark/system switches overlay and popup live; `settings.json` lists shortcuts and the Settings window shows them read-only; disconnecting the icon's monitor (simulated by `ChangeDisplaySettingsEx` detach on the personal laptop, or by `monitors.simulate_remove(idx)` in test mode) moves the icon to the primary work area within 2 s and reconnect leaves it; `python -m assistant update` in a git clone runs `git pull --ff-only` + `pip install -r requirements.txt --user --only-binary :all:` and on next start shows "Updated to <version>"; in a zip install it downloads the latest release zip; `grep -r "update" assistant/scheduler.py assistant/app.py` shows no scheduled update check. | qa | F |
| M4-3 | ui | Ask Claude in the full page (menu entry, confirmation dialog with the sends-to-Claude marker, answer pane) and theme follow-system. | `web/src/**`, `assistant/static/app/**` | AC-AK-2, AC-AK-3, AC-AK-6, AC-AC-7, AC-PS-1 (web) | In Edge: the confirmation shows the same fields as the popup's; Send calls `/v1/actions/ask_claude/send`; the answer renders; theme follows `prefers-color-scheme` and the app setting. | qa | F |
| M4-4 | qa | M4 tests. | `tests/test_ask_claude.py`, `tests/fixtures/bin/claude.cmd` (recording shim), `tests/test_update.py`, `tests/e2e/test_theme_monitors.py` | verification side of M4 | Green; the fake-`claude` log proves nothing is sent before confirmation. | git-keeper | F |
| M4-5 | security | Review the Ask Claude send path. | `docs/security/2026-11-xx-m4-review.md` | SEC-9, SEC-10 | Verdict with evidence incl. a `netstat`/proxy observation over one hour of use showing only ICS and user-confirmed sends. | tech-lead | after M4-1, M4-3 |
| M4-6 | doc-keeper | README feature list, IT-facing paragraph, dependency list with compiled-code flags; CHANGELOG. | `README.md`, `CHANGELOG.md`, `docs/api.md` | AC-IN-6, AC-IN-7 | README contains the four IT statements verbatim from AC-IN-6 and the dependency table from section 10 of this plan. | git-keeper | after merge |

M4 leaves: feature-complete for one user. Rollback: Ask Claude is one menu item gated by a setting; `update` is user-invoked only.

### 7.6 M5 — Plugins

Design may start during M3 because `plugin_api.py` depends only on §7 and §9 of the PRD. Interfaces fixed: the PRD §9.2 table and the `[PL Q3]` sketch; `plugins.host.PluginHost` exposes `discover()`, `enable(id)`, `disable(id, reason)`, `reload()`, `call(id, fn, *args, timeout) -> result`, `search_modes()`, `previewers()`, `card_actions_for(card)`, `result_actions_for(result)`, `press(card_id, action_id)`.

| Id | Role | Goal | Files owned | AC | Done (qa observes) | Hand-off | Parallel |
|---|---|---|---|---|---|---|---|
| M5-1 | dev | The public plugin API module, manifest parsing/validation, and the `plugin new` / `plugin check` commands. | `assistant/plugin_api.py`, `assistant/plugins/__init__.py`, `assistant/plugins/manifest.py`, `assistant/plugins/cli.py`, `assistant/__main__.py` (adds `plugin`) | AC-PL-1, AC-PL-13 (scaffold + check), §9.1, §9.2 types | `python -m assistant plugin new demo-x` creates `~/.assistant/plugins/demo-x/` with a valid manifest and entry; `plugin check <folder>` on the `[PL Q4]` hello world prints OK without starting the app; a manifest whose `id` differs from the folder name fails with that reason; missing `min_assistant_version` fails; an unknown `[[settings]] type` fails. | qa | G |
| M5-2 | dev | Loader and host: discovery, fresh-module import with `lib/` on the path, per-plugin executors with timeouts and three strikes, the Context, host-owned scheduling, audit hook, DPAPI secrets. | `assistant/plugins/loader.py`, `assistant/plugins/host.py`, `assistant/plugins/audit.py`, `assistant/plugins/secrets.py` | AC-PL-2, AC-PL-3, AC-PL-6, AC-PL-7, AC-PL-11, AC-PL-12, SEC-8 (part) | `pytest tests/test_plugin_host.py`: the hello world loads unchanged and emits its card; a plugin whose `search` sleeps 5 s returns a timeout at 2 s and the popup stays responsive; three consecutive raises disable it and one purple card names it; a plugin that calls `socket.connect` with `network = []` triggers the undeclared-network card and a `plugin-audit.log` line attributed to it; a plugin that raises in `start` is listed with its error and the app started anyway; password setting round-trips through DPAPI and never appears in the plugin folder; "Reload plugins" after editing `plugin.py` runs the new code (module name suffix increments). | qa, then security | after M5-1 |
| M5-3 | dev | Installer: GitHub URL → zip → validate → unpack disabled; pinned binary-only requirements into `lib/`; check for updates with version + permission diff; uninstall with optional data removal. | `assistant/plugins/installer.py` | AC-PL-4 (data for the consent dialog), AC-PL-5, AC-PL-8, AC-PL-9, AC-PL-10, SEC-8 (part) | `pytest tests/test_plugin_installer.py` with `urllib` opener monkeypatched to a local server: `https://github.com/o/r` tries `releases/latest/download/<id>.zip` then `archive/refs/heads/main.zip`, validates `plugin.toml`, unpacks to `plugins/<id>/`, records `source_url` + version in `plugins.json`, leaves it disabled; `https://api.github.com/...` and non-github hosts are rejected; a `[requirements]` entry installs with `pip install --target lib --only-binary :all: pkg==x.y` (command asserted) and a failing install leaves the plugin disabled with the pip output stored; update shows the diff and does nothing without confirmation; uninstall keeps `plugins-data/<id>/` unless asked. | qa, then security | G |
| M5-4 | dev | Integrate plugins into the popup, cards, previews and Settings: plugin modes in the Tab cycle with local/network markers, card and result actions, previewer dispatch through the host's deny-list/caps path, the Plugins page with consent dialog, reload, install-from-URL, updates, per-plugin settings. | `assistant/overlay/settings_pages_plugins.py`, `assistant/search/modes.py`, `assistant/overlay/cards_ui.py`, `assistant/overlay/search_ui.py`, `assistant/server/routes_files.py`, `assistant/app.py`, `assistant/doctor.py` (plugins loaded/disabled with reasons) | AC-PL-4 (dialog text incl. the exact sentence), AC-SH-4, AC-SH-5 (plugin markers), AC-PV-8, AC-AC-2 (plugin actions), AC-OV-11 (Reload plugins entry), AC-ST-2 (Plugins page), AC-IN-5 (part) | Driver: enabling a plugin shows name, version, author, homepage, file list, permissions and the sentence "Assistant cannot enforce these — the plugin runs with your access." and requires confirmation; the hello world's `hi` mode joins the Tab cycle and `hi foo` returns its row; a previewer plugin for `.ipynb` renders in the full page while `.ipynb` under a deny-listed name is still "protected"; a card-action plugin's button appears on `waiting` cards and its press reaches `on_action`; `--doctor` lists plugins. | qa | after M5-2, M5-3 |
| M5-5 | dev | Bundled reference plugins, and Ask notes moved into a plugin with identical behaviour. | `assistant/bundled_plugins/timer/**`, `assistant/bundled_plugins/bookmarks/**`, `assistant/bundled_plugins/send-to-claude/**`, `assistant/bundled_plugins/csv-ipynb-preview/**`, `assistant/bundled_plugins/ask-notes/**`, `assistant/search/notes_bm25.py` (removed; code moves into `ask-notes/`) | AC-PL-15, AC-AN-7, AC-PL-3 (bundled disabled except Ask notes), PM 9 | `timer 25m` in the popup → card at T-0 (test with `timer 1m`); `bm <word>` finds a fixture Edge `Bookmarks` JSON entry and declares `filesystem` permission; `send-to-claude` appears as a result action and calls M4-1's prepare route; `.csv`/`.ipynb` previews render; `tests/test_notes_bm25.py` from M3a passes unchanged against the plugin; Plugins page shows all five, only Ask notes enabled. | qa | after M5-2 |
| M5-6 | doc-keeper | `PLUGIN_AUTHORING.md`: manifest, six interfaces, hello world, `plugin new`/`check`, timeouts, permissions honesty, data-only rule. | `PLUGIN_AUTHORING.md` | AC-PL-13 (doc), SEC-8 (never says "sandboxed") | File exists; `grep -i sandbox PLUGIN_AUTHORING.md README.md` returns nothing; metric M6 trial: Claude Code given only this file produces a loading plugin in ≥ 4 of 5 attempts (qa runs the trial and records). | git-keeper | after M5-1 (interfaces final) |
| M5-7 | qa | M5 tests and fixtures. | `tests/test_plugin_manifest.py`, `tests/test_plugin_host.py`, `tests/test_plugin_installer.py`, `tests/test_bundled_plugins.py`, `tests/e2e/test_plugins_page.py`, `tests/fixtures/plugins/hello-cards/**` (verbatim `[PL Q4]`), `tests/fixtures/plugins/{slow,crashy,networky,previewer-ipynb,card-action}/**`, `tests/fixtures/bookmarks/Bookmarks` | verification side of M5 | Green; the hello world fixture is byte-identical to the brief's two files. | git-keeper | G |
| M5-8 | security | Review loader, installer, audit hook and secrets. | `docs/security/2026-11-xx-m5-review.md` | SEC-8 | Verdict with evidence: binary-only pip invocation, github.com-only URLs, no auto-update path, secrets never in plugin folders, consent sentence present, no "sandboxed" wording in UI strings (`grep -ri sandbox assistant/ web/src/`). | tech-lead | after M5-2, M5-3, M5-4 |
| M5-9 | doc-keeper | Record M5; README plugin list. | `README.md`, `CHANGELOG.md`, `docs/api.md` | AC-PL-15 (reference plugins listed), AC-PL-8 (install-from-URL documented), AC-IN-6 (IT paragraph mentions the plugin folder) | Appended. | git-keeper | after merge |

M5 leaves: others can extend; metric M6 starts. Rollback: plugins are off by default; `plugins.json` → all disabled restores M4 behaviour; Ask notes as a bundled plugin ships enabled so the user loses nothing.

Task counts: M0 4 · M1 10 · M2 7 · M3a 10 · M3b 8 · M4 6 · M5 9 — 54 dispatches.

## 8. Verification strategy

### 8.1 Principles

- "Done" is observed, never inferred (fable-parity §3). Every qa verdict quotes the command and its output; perf numbers are printed by the tests and pasted, not estimated.
- Everything that can be tested without a screen is a pytest with an isolated `ASSISTANT_HOME` (M0-3 fixture) and an injected clock where timing matters (card store, scheduler, grammar).
- Everything on the screen is driven through `tools/driver.py` and `GET /v1/_debug/ui` (A14), not by pixel-hunting; screenshots are evidence, not the oracle.
- The work laptop is verified by the user following the exact commands qa writes into its report; qa marks those lines "unverified on work laptop until the user's paste arrives".

### 8.2 What qa verifies by observation, per milestone

| Milestone | Observations (each maps to a Done cell in section 7) |
|---|---|
| M0 | `/v1/health` 200/401/403 matrix; token ACL via `icacls`; `--doctor` output; probe scripts run and print the named fields. |
| M1 | `POST /v1/cards` → card visible within 1 s (timestamps from `/v1/_debug/ui`); recorded hook payloads → one card per session, cleared within 2 s; timer ticks; hover = seen; 20-s fade; snooze return at +5 min with a shortened clock; mute queue; restart restores; `install.ps1`/`uninstall.ps1` in a fresh Windows user profile (qa creates a local test user on the personal laptop, `net user` needs no admin? — it does; if qa lacks it, the user runs this step and pastes output); the eight hook entries and byte-identical preservation of foreign entries. |
| M2 | ICS fixtures → exact `Event` lists; a card at T-5 min ± 5 s from a locally served feed; dedupe; feed failure status; MCP tool list and behaviour via the `mcp` client; stdio shim round-trip; token regeneration; live-apply of a setting within 1 s. |
| M3a | Fixture tree `tree_search.json` (≈ 3,000 entries incl. `node_modules`, `.git`, a `.venv`, `$RECYCLE.BIN`, deny-listed names `.env`, `id_rsa`, `secrets.pem`, a `.kdbx`, a junction, an OFFLINE-attributed file, names covering the five `[REF L82]` examples) crawls to the exact expected set; deny-listed fixtures never appear in `GET /v1/search`, the popup, or any MCP tool; watcher reflects create/rename/delete within 5 s; grammar/ranking tables; synthetic 2M-name store query < 50 ms; real-index p95 over 50 random queries; hotkey → box ≤ 300 ms; Ask notes index/query timings. |
| M3b | Preview fixtures (`<script>` Markdown, `onload` SVG, `.pem`, OFFLINE, 60 MB, each Office type, HEIC) rendered in Edge with a clean DevTools console; Range requests; nonce single-use; Recycle Bin contents after `recycle`; name validation reasons; one Explorer window on repeated reveal; executable confirmation; `grep` for permanent-delete call sites. |
| M4 | Fake `claude` shim on PATH records that nothing is invoked before Send; deny-listed file disables the action; `claude-sends.log` lines; theme switch; monitor removal moves the icon within 2 s; `update` performs only when invoked; one-hour `netstat` capture shows only ICS and confirmed sends. |
| M5 | Hello world byte-identical to the brief loads unchanged; timeout, three-strike and undeclared-network fixtures; consent dialog text; installer URL rules and pip command; bundled plugins; Ask-notes tests pass against the plugin. |

### 8.3 Overlay driver and agent skill (equivalent of the reference build's `driver.py` and skill)

`tools/driver.py` (owned by qa, M1-7) is a stdlib + pywin32 + Pillow command-line tool:

```
python tools/driver.py start --home <dir> --port 17171 [--test-mode]   # launches pythonw -m assistant, waits for /v1/health
python tools/driver.py stop                                             # POST /v1/shutdown
python tools/driver.py post --title T --kind waiting [--detail D] [--id X]
python tools/driver.py ui                                               # prints /v1/_debug/ui as a table (windows, rects, kinds, colours, seen, blinking)
python tools/driver.py hover <card-id> | click <card-id> <button> | hotkey | type "<text>" | key <name>
python tools/driver.py screenshot --out <png>                           # PIL.ImageGrab.grab(all_screens=True)
python tools/driver.py fullpage --query "<q>" --screenshot <png>        # launches Edge app mode and captures it
```

Pointer and keyboard input use `SendInput` via `ctypes` (not `pyautogui`). The skill `.claude/skills/run-assistant/SKILL.md` documents exactly these commands, the test-mode requirement, where screenshots go (session scratchpad, never the repo), and the rule that a driver observation is quoted verbatim in reports.

### 8.4 Fixtures to create (qa)

`tests/fixtures/tree_small.json` (M0), `tree_search.json` (M3a), `notes/` (2,000 generated markdown files with front matter, headings, tables, code; one OFFLINE-attributed), `hook_payloads/` (verbatim P6 captures, redacted of paths outside the fixture), `calendars/*.ics`, `preview/` (section 7.4), `plugins/` (section 7.6), `bookmarks/Bookmarks` (Edge format), `bin/claude.cmd` (recording shim).

### 8.5 Browser verification

`ui` verifies its own work in Edge app mode through the Chrome DevTools MCP (Edge speaks the same protocol: launch `msedge.exe --remote-debugging-port=9222 --app=…`) and attaches screenshots and a console transcript; qa re-runs the same scenario through `driver.py fullpage` so the evidence is independent of the author.

## 9. Security review points (SEC-13)

| Milestone | Tasks that trigger `security` | Surface |
|---|---|---|
| M0 | M0-2 | token creation and ACL; loopback bind; Host/Origin check |
| M1 | M1-4, M1-5 | hook receiver (passive, no decision), card routes (input parsing), file-drop parsing, hooks installer writing the user's Claude Code settings |
| M2 | M2-1, M2-2 | MCP endpoint auth and Origin handling; ICS URL as a credential (storage, redaction); Outlook COM |
| M3a | M3a-1, M3a-3, M3a-5 | deny-list uniformity; symlink/junction resolution; query grammar parsing; MCP file tools (read-only, size caps, untrusted-content delimiter) |
| M3b | M3b-2, M3b-3, M3b-4 | preview pipeline (no script execution, DOMPurify, strict Mermaid, caps, sniffing); static serving and nonce exchange; shell actions (executable confirmation, Recycle Bin only, name validation) |
| M4 | M4-1, M4-3 | Ask Claude send path (confirmation, deny-list, logging, no API key) |
| M5 | M5-2, M5-3, M5-4 | plugin loader (fresh module, `lib/` precedence), installer (URL allow-list, binary-only pip), audit hook, DPAPI secrets, consent wording |

`security` is read-only and appends its verdict to `docs/security/`. On these tasks merge requires both `qa` and `security` verdicts.

## 10. Dependencies and the install scripts

### 10.1 Runtime dependencies (`requirements.txt`)

Exact pins are fixed in M0-2 by installing on the personal laptop and recording `pip freeze` output for these packages (this plan does not invent version numbers it has not observed; the briefs give `mcp 2.3.0`, `send2trash 2.1.0`, `watchdog 7.1.0` as current on 2026-11-06). All installs use `--only-binary :all:` so no build step ever runs.

| Package | Why | Compiled code? (AC-IN-7) | Introduced |
|---|---|---|---|
| `pywin32` | hotkey, window styles, shell actions, `ReadDirectoryChangesW`, Outlook COM, clipboard | yes | M0 |
| `pillow` | icon/card rendering helpers, driver screenshots | yes | M1 |
| `mcp` (pulls `starlette`, `uvicorn`, `pydantic`/`pydantic-core`, `anyio`, `sse-starlette`, `jsonschema`/`rpds-py`, `pyjwt[crypto]`/`cryptography`, `python-multipart`) | HTTP server, MCP server | yes via `pydantic-core`, `rpds-py`, `cryptography` — covered by the extended P1 | M0 |
| `httpx` (already an `mcp` dependency) | stdio shim client, ICS fetch, installer downloads | no | M2 |
| `icalendar` + `python-dateutil` (+ `tzdata`) | ICS parsing and recurrence expansion | no | M2 |
| `send2trash` | Recycle Bin via `IFileOperation`/`SHFileOperation` (uses pywin32 when present) | no (uses pywin32) | M3b |
| `pypdf` | PDF text extraction for Ask Claude | no | M4 |
| `tomli` (only when `python_version < "3.11"`) | `plugin.toml` on Python 3.10 | no | M5 |
| Optional, off by default: `watchdog` (Python ≥ 3.11; upgrade path if `ReadDirectoryChangesW` loses events), `scandir-rs` (compiled accelerator, personal laptop only, enabled by setting after P7) | — | `scandir-rs` yes | — |

Deliberately not used: `keyboard`/`pynput` (low-level hooks, `[FS Q10]`), `rank-bm25` (pulls numpy; a stdlib BM25 is ~60 lines), `pywebview` (only if P2 fails), `psutil` (ctypes suffices for working-set readings).

Dev-only (`requirements-dev.txt`): `pytest`, `pytest-timeout`. The `web/` toolchain (Node, Vite, React, the preview libraries listed in 4c) runs only on the personal laptop at build time.

### 10.2 `install.ps1` behaviour (AC-IN-1, AC-IN-8)

1. Locate Python: `py -3` then `python`; require ≥ 3.10 else print the download hint and exit 1. Never request elevation.
2. `python -m pip install --user --only-binary :all: -r requirements.txt` (shows pip output; exits on failure).
3. `python -m assistant --doctor` (creates `~/.assistant/` and the token on first run; prints the report).
4. Optional `-Hooks` switch (also offered interactively when `claude` is on PATH): runs `python -m assistant hooks install --dry-run`, shows the entries, asks y/n, then `hooks install`.
5. Creates `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Assistant.lnk` via `WScript.Shell` targeting `<pythonw.exe> -m assistant` with the repo as working directory.
6. Starts the app via the shortcut and prints "Look for the round icon at the bottom-right".

### 10.3 `uninstall.ps1` behaviour (AC-IN-3)

1. `python -m assistant shutdown` (reads `~/.assistant/assistant.pid`, POSTs `/v1/shutdown` with the local token; falls back to `Stop-Process` on the recorded pid).
2. `python -m assistant hooks remove` (removes exactly the entries recorded at install; restores nothing else).
3. Deletes the Startup shortcut.
4. Asks "Delete ~/.assistant (settings, index, plugins, logs)? [y/N]"; deletes only on `y`.
5. Prints the `pip uninstall` line for the dependencies but does not run it (other tools may share them).

## 11. Risks specific to this plan, and rollback per milestone

| # | Risk | Mitigation / rollback |
|---|---|---|
| PR1 | The single process stutters the overlay during a crawl (GIL contention from Python-level exclude matching). | P7 + M3a-2 measure UI tick latency during a crawl (`uibus` records drain gaps > 100 ms in test mode). If gaps exceed 100 ms, M3a-2 moves exclude matching to directory level only (already the design) and lowers batch size; if still failing, the Revision moves `index/*` behind the HTTP API into a second process — the package boundary is already clean. |
| PR2 | `mcp`'s compiled dependencies (`pydantic-core`, `cryptography`, `rpds-py`) are blocked on the work laptop even though `pywin32` works. | Extended P1 checks this before M0-2 pins anything. Fallback: hand-written Streamable HTTP MCP over Starlette (JSON-RPC, ~300 lines) without `pydantic`; decided at the P1 Revision, before M2. |
| PR3 | tkinter per-monitor DPI (AC-OV-15) is poor by default. | `win.py` calls `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` before the tk root exists and scales per-window from `GetDpiForMonitor`; M1-3a's Done includes a two-monitor check. If tkinter cannot render crisply, the Revision documents the limitation and keeps text sizes physical via per-monitor font scaling. |
| PR4 | P6 shows `AskUserQuestion`/plan approval fire nothing. | AC-CS-9 already allows it; M2-3 states the gap in one sentence; no scope change. |
| PR5 | Hook payload shape drifts with Claude Code versions (R3). | `tests/test_hooks_statemachine.py` runs against recorded payloads (contract test); `--doctor` reports the installed version against 2.1.233; a payload with unknown fields is logged once and still handled. |
| PR6 | The 2M-entry synthetic store meets 50 ms but the real index does not (path-term matching, delta layer). | M3a-7 measures p95 on the real personal-laptop index; if > 50 ms, M3a-3 adds a first-term prefilter on a 2-gram position table (RAM +~30 MB) before any Revision of the target. |
| PR7 | Edge app-mode window cannot be closed programmatically on Esc (AC-SH-7). | `fullpage.py` records the launched process id and the window found by `EnumWindows` on that pid; `WM_CLOSE` to it. If Edge reuses an existing browser process (common), fall back to `--user-data-dir=%LOCALAPPDATA%\Assistant\edge-profile` so the app-mode window owns its process. Decided in M3b-3. |
| PR8 | Vendored web build drifts from `web/` source. | M3b-2 and M4-3 Done criteria require a clean rebuild; `git-keeper` refuses a PR that touches `web/src` without touching `assistant/static/app`. |
| PR9 | Audit hooks (PEP 578) slow every `open()` in the process. | `audit.py`'s hook returns after one string comparison for non-plugin threads; M5-2 Done includes a before/after measurement of 10,000 `open()` calls (< 10 % overhead) — recorded. |
| PR10 | `install.ps1` in a clean profile cannot be tested by an agent (needs a second Windows user). | The user performs AC-IN-8 once per laptop; qa writes the exact steps and marks the criterion unverified until the paste arrives. |

Rollback story per milestone: every milestone merges to `main` behind an annotated tag `v0.<milestone>` (`v0.0` … `v0.5`); rollback is `git checkout v0.<n>` and `install.ps1` again. State files are forward-compatible (unknown keys ignored, versioned `files.idx` header — an older build rebuilds the index rather than failing), so stepping back a milestone leaves the app usable with the previous feature set. `uninstall.ps1` is the rollback of last resort at any point.

## 12. First dispatches after M0

M0's four tasks are dispatched first (M0-1, M0-2, M0-3 in parallel; M0-4 when M0-1 has landed). Once the probe files exist and the Revision with measured numbers is appended, the tech-lead dispatches these three M1 tasks in parallel — they share no files and depend only on interfaces fixed in section 7.1.

### Dispatch 1 — M1-1 (dev)

```
Goal: implement Assistant's card model and card store — validation per PRD §7, update-in-place by id,
snooze/dismiss/seen/mute semantics, auto-expiry timing, the per-card events cursor, and persistence
to state.json — with no UI code.
Scope: assistant/cards/__init__.py, assistant/cards/model.py, assistant/cards/store.py only.
Public API must match docs/plans/2026-11-06-assistant-v1-plan.md §7.1 (CardStore methods) exactly.
Inject the clock (a callable returning time.time()) so tests can advance it.
Done criteria: pytest tests/test_cards_store.py (qa, M1-7) passes: `done` card expires 20 s after
mark_seen; snooze(5) returns at +300 s unless dismissed; an upsert during snooze re-shows it;
set_mute queues cards and list() counts them; writing then reloading state.json reproduces open cards
with seen/snooze state; a 501-char detail raises a validation error with field="detail"; unknown
fields are ignored and logged once. Paste the pytest output in your report.
Hand-off: qa.
Plan: C:\AI Projects\Assistant\docs\plans\2026-11-06-assistant-v1-plan.md (§4a threading rules, §7.1 M1-1).
```

### Dispatch 2 — M1-2 (dev)

```
Goal: implement the scheduler thread and the reminder grammar and store so that
"driver selftest in 10m" becomes a timed job and anything unparseable raises DidntUnderstand.
Scope: assistant/scheduler.py (replace M0-2's shell, keep its API: at(ts, fn)->handle,
every(seconds, fn)->handle, handle.cancel(), stop()), assistant/reminders/__init__.py,
assistant/reminders/grammar.py, assistant/reminders/store.py only.
Grammar is PRD DEC 6 / AC-RM-2 exactly: "<text> in <N>[m|h|d]", "<text> at <h[:mm]>[am|pm]",
"<text> tomorrow <h[:mm]>[am|pm]", "<text> <weekday> <h[:mm]>[am|pm]"; past "at" means tomorrow;
24-hour accepted without am/pm; interpretation string like "Reminder 'driver selftest' at 14:32 today";
failure help sentence is the one in AC-RM-3, verbatim.
Done criteria: pytest tests/test_reminder_grammar.py tests/test_scheduler.py (qa, M1-7) passes;
scheduler fires a 2-s job within ±0.5 s and cancel works; reminders.json round-trips and an overdue
reminder loaded at start is flagged "(was due <time>)". Paste the pytest output.
Hand-off: qa.
Plan: C:\AI Projects\Assistant\docs\plans\2026-11-06-assistant-v1-plan.md (§4a scheduler contract, §7.1 M1-2).
```

### Dispatch 3 — M1-3a (dev)

```
Goal: build the overlay foundation — window styles, monitor/DPI helpers, drawing helpers, generated
sounds, and the round draggable icon with its right-click menu, badge, mute visual, remembered
position and the Remind-me input — so the icon behaves exactly like the reference build's.
Scope: assistant/overlay/__init__.py, win.py, monitors.py, gfx.py, sounds.py, icon.py, reminder_ui.py only.
Only the main thread touches tkinter; anything arriving from other threads goes through
assistant.uibus.post (exists from M0-2). Use win32gui/ctypes for WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE |
WS_EX_TOPMOST | WS_EX_LAYERED and for SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2) before the
root is created. Menu order is AC-OV-11 verbatim. Colours per kind are defined once in gfx.py as
named hex constants (amber/green/blue/purple) — qa asserts on them. For the Remind-me input call
assistant.reminders.grammar.parse (M1-2's contract) for the live interpretation line; if M1-2 has not
merged yet, code against the signature in the plan and mark that integration "unverified".
Done criteria: with ASSISTANT_TEST_MODE=1 and tools/driver.py (qa, M1-7): icon at bottom-right of the
primary work area, not over the taskbar; GetWindowLong(GWL_EXSTYLE) shows the three styles; no taskbar
button; drag 200 px then restart → within 2 px; Reset position returns it; /v1/_debug/ui lists the menu
items in AC-OV-11 order; "Test sound" plays; "Sound off" silences; mute turns the icon grey with a
crossed bell; Remind-me shows the interpretation and the didn't-understand line. Paste driver output
and attach one screenshot.
Hand-off: qa.
Plan: C:\AI Projects\Assistant\docs\plans\2026-11-06-assistant-v1-plan.md (§4a, §7.1 M1-3a, §8.3).
```

In parallel with these three, qa receives M1-7 (write tests and `tools/driver.py` against the fixed interfaces) so tests exist when the generators report.

## 13. Open questions

None block M0. Items the plan resolves by default and flags for the tech-lead to confirm or overrule: A5 (agents do not touch the work laptop), A7 (`ui` owns `web/**` only), A8 (port 17171), A9 (`settings.json`), A10 (combined RAM target), A11 (`demo` in M1), A12 (launch nonce), A13 (DPAPI), A14 (test-mode debug route). Items decided by probes, not by anyone: P1-extended (compiled MCP deps), P2 (Edge app mode), P6 (hook coverage sentence), P7 (crawl targets), P8 (Outlook source).

## 14. Revision 2026-11-06 (after M0 probes, personal laptop)

- Date: 2026-11-06. Author: planner. Trigger: M0-4 (personal half) recorded in `docs/probes/2026-11-06-m0-probes-personal.md`; security verdict on M0-2 in `docs/security/2026-11-06-m0-2-token-auth-review.md` (PASS, one FIX-BEFORE-M1, eight NOTEs); one qa follow-up relayed by the tech-lead. Decisions below were taken by the tech-lead and are recorded here with the evidence lines they rest on.
- Nothing above this section is edited. Where this section supersedes a sentence above, 14.9 says which one, by section number. The work-laptop file (`docs/probes/2026-11-06-m0-probes-work.md`) does not exist yet; M0-4 stays open for that half and every work-laptop gate in sections 7.3–7.6 stands.

### 14.1 Measured values and verdicts (personal laptop, Windows 11, Python 3.13.1)

| Probe | Verdict | Evidence (from the probe record) | Consequence |
|---|---|---|---|
| P1 | PASS | both import one-liners printed `ok`; the `--only-binary :all:` install step was not run (packages already present) | M1+ unblocked on this laptop. Binary-only install behaviour is still unexercised here; it is exercised by `install.ps1` (M1-5) and matters on the work laptop. |
| P2 | PASS | Edge app window, no address bar, title `127.0.0.1_/`, server log `"GET / HTTP/1.1" 200`; probe used port 17198 and a one-line `index.html` instead of the plan's 17171 / `$env:TEMP` | M3b unblocked here. Work-laptop P2 still gates M3b per 7.4. |
| P3 | N/A | work-laptop probe | M5-3 gate unchanged. |
| P4 | PASS | `git version 2.45.2.windows.1` | M1-5 install path and M4-2 `update` take the git route here. |
| P5 | PASS | `2.1.317 (Claude Code)` ≥ 2.1.233 | M1-4 and M4-1 enabled here. |
| P6 | PARTIAL | CLI `-p` only; 13 POSTs over three sessions; details in 14.3 | M1-4 contract facts fixed; interactive and desktop-app cases stay unknown. |
| P7 | PARTIAL | warm only: 552,245 entries, 70,169 dirs, 74.50 s, 18.5 MB peak working set, 1 permission error, 26 reparse points not followed | 2x trigger fired on the 0.3M warm projection; decision in 14.2. |
| P8 | PASS | `reg query` CLSID `{0006F03A-0000-0000-C000-000000000046}`; COM `Outlook.Application` version `16.0.0.20527`; new Outlook `1.2026.713.100` is also installed | M2-1 Outlook COM is exercised on this laptop (7.2 M2-1 Done). |
| P9 | PASS | blank topmost tkinter window, 10 min, 60 samples: average CPU 0.078 % of one core, max 16.8 MB private bytes | Overlay target stands; metric-label note in 14.4. |

### 14.2 P7 decision: excludes first, re-measure, accelerator stays optional

Numbers (warm, no reboot, probe default excludes, roots `C:\Users\<user>` and `C:\AI Projects`): 74.50 s / 552,245 entries = 134.9 µs per entry. Linear projections: 0.3M warm ≈ 40.5 s against a 15 s target (≈ 2.7x over — the section 6 P7 trigger "miss by > 2x" fired); 2M warm ≈ 270 s against 150 s (≈ 1.8x); 2M cold ≈ 4.5 min against 5 min (within target, but a projection — the cold run was not done and needs a reboot). The PRD's P7 fallback (PRD section 5) is keyed to "the 2M crawl"; the plan's row (section 6) says "miss by > 2x" without naming a size. The 0.3M warm projection is what fired. The tech-lead's decision resolves the ambiguity as follows.

What the probe shows about the miss: 19 of the top-20 directories by entry count are under `AppData` (the twentieth is `.vscode\extensions`); the top 20 alone hold 125,386 entries, 22.7 % of the total. The largest: a Claude desktop `Code Cache\js` (30,084), two `OfficeFileCache` trees (15,355 + 15,354), Zoom `Emojis` (7,404), `VSApplicationInsights` (4,998), Outlook `EBWebView\...\Service Worker\CacheStorage` (3,876 + 3,170), OneDrive `ListSync\settings\cdn` (4 × ≈ 3,750), Notion `Service Worker\CacheStorage` and `Shared Dictionary\cache` (3,760 + 3,754 + 2,008), Cursor `Code Cache\js` (2,946), Claude `Cache\Cache_Data` (2,380). PRD AC-FI-1 already names AppData caches among the default excludes; `tools/probes/crawl_probe.py` `DEFAULT_EXCLUDES` (lines 10–42) only excludes Temp, WindowsApps, Packages, Edge, Chrome and Docker under AppData, so the run measured a narrower exclude set than the product will ship.

Action, in order:

1. **Product excludes (M3a-1, `assistant/index/excludes.py`, unchanged owner).** The default list gains application-cache rules scoped under `AppData/Local/` and `AppData/Roaming/` so that a project folder named `Cache` inside a root is never excluded: browser/Electron `Code Cache/`, `Cache/`, `Cache_Data/`, `GPUCache/`, `Service Worker/`, `Shared Dictionary/`; Office `Microsoft/Office/*/OfficeFileCache/`; package-manager caches (pip, npm, Yarn, uv, NuGet under AppData). These are the categories the tech-lead chose; the exact glob text is M3a-1's to write and `tests/test_excludes.py` (M3a-8) to pin, each rule with its AC-FI-7 counter so the user can see what was dropped. Deliberately not excluded by default (user-visible content, not cache): `AppData\Roaming\Python\...\site-packages` (6,951 under `torch\include`), Zoom `Emojis`, `Android\Sdk`, `.vscode\extensions`; the user can add them in Settings (AC-FI-1 user additions). The probe script's list is a probe list, not the product list — the product list is defined in `excludes.py`, not copied from `crawl_probe.py`.
2. **Re-measure before M3a is dispatched (new task M0-5, below).** Same probe, same roots, same `--excludes default`, after the probe's list has been extended with the same categories; warm first, then cold after a reboot (user-run, section 6 order). doc-keeper appends a "P7 re-measure" section to the personal probe file; planner appends Revision 2 with the verdict against section 12 of the PRD.
3. **`scandir-rs` stays an option, not a task.** Section 10.1's row is unchanged: compiled accelerator, personal laptop only, behind a setting, off by default. It is enabled (and the setting built, in M3a-2) only if the re-measure still misses the warm targets by more than 2x. The work laptop never gets it (PRD DEC 14).
4. **Targets.** PRD section 12 warm targets (0.3M ≤ 15 s, 2M ≤ 2.5 min) are recorded here as "to be re-measured after excludes"; no new numbers are invented. The cold targets are untouched. The 300 MB index-RAM target was not measured by P7 (18.5 MB is the crawl alone) and is still owned by M3a-1's 2M synthetic test.

| Id | Role | Goal | Files owned | AC | Done (qa observes) | Hand-off | Parallel |
|---|---|---|---|---|---|---|---|
| M0-5 | dev | Extend the crawl probe's exclude list with the AppData application-cache categories in 14.2 item 1, fix the P9 probe's metric label (14.4), then re-run the warm P7 command from section 6 on the personal laptop. | `tools/probes/crawl_probe.py`, `tools/probes/tk_idle_probe.py`, `tools/probes/README.md` | PRD §5 P7, §12 crawl targets | The re-run prints the same fields as the first run; its top-20 list no longer contains `Code Cache`, `Cache_Data`, `GPUCache`, `Service Worker`, `Shared Dictionary` or `OfficeFileCache` directories; entries, seconds and µs/entry are pasted next to the 2026-11-06 figures (552,245 / 74.50 s / 134.9 µs); `tk_idle_probe.py` prints the column it actually measures (14.4) and `--help` says so. The cold run is user-run after a reboot and pasted. | qa, then doc-keeper (appends to `docs/probes/2026-11-06-m0-probes-personal.md`) | any time before M3a is dispatched; shares no file with M1 |

### 14.3 P6: what the CLI confirmed, what stays unknown

Confirmed with Claude Code 2.1.317, `claude -p`, `--settings <scratch file>` (the user's `~/.claude/settings.json` was not modified):

- Hook entries with `type: http`, `url`, `onFailure: continue` are accepted. The eight-event block from `hook_probe_settings.py` is the shape M1-5's installer writes (AC-CS-2).
- Events that fired: `UserPromptSubmit`, `PostToolUse`, `PermissionRequest`, `Stop`, `SessionEnd`. `PermissionRequest` carries `tool_name`, `tool_input`, `permission_suggestions`; `Stop` carries `last_assistant_message`, `stop_hook_active`, `background_tasks`, `session_crons`. Every payload carries `session_id`, `transcript_path`, `cwd`, `scratchpad_dir`, `prompt_id`, `permission_mode`, `effort`, `hook_event_name`.
- Did not fire in `-p` mode: `SessionStart`, `Notification`. `StopFailure` was not logged either, but no failure occurred in the three sessions, so that is not evidence about `StopFailure`.
- No payload carries a project-name or `session_title` field. AC-CS-4's title is therefore the basename of `cwd` on the CLI; the `session_title` branch stays in the code only as a fallback for a client that sends it.
- A pre-permitted Bash call raised no `PermissionRequest` (case a, `permission_mode: auto`); the same tools under `--permission-mode default` raised one `PermissionRequest` each and were then denied because `-p` cannot answer (case a2). `AskUserQuestion` was not available in the `-p` session (ToolSearch found nothing), so case (b) is uninformative, not negative.

Still unobserved: interactive CLI case (a) with an approval, (b) `AskUserQuestion`, (c) plan approval; every desktop-app case. Consequences:

- **M1-4 state machine.** `PermissionRequest` → `waiting` card for that `session_id`; a later event from the same session (`PostToolUse`, `Stop`, `SessionEnd`) clears or converts it as the PRD says. Any hook event name the machine does not map, and any future signal for `AskUserQuestion` or plan approval, is treated as an **"unknown wait"**: one `waiting` card per session whose detail names the event, cleared by the next event from that session, never a second card and never a decision in the response. This is the AC-CS-9 position: coverage is stated per observed event, and the coverage sentence shown in Settings (M2-3) names only what P6 has observed on that laptop.
- **M1-7 fixtures.** `tests/fixtures/hook_payloads/*.json` are built from the 13 recorded POSTs (paths outside the fixture redacted, username replaced). Interactive and desktop-app payloads are added when the user's run arrives; the state-machine test must pass with the current set.
- **Interactive P6 is open, user-run, and not a gate for M1-0, M1-1, M1-2, M1-3a or M1-7.** It is wanted before M1-4 passes qa; if it has not arrived by then, M1-4 passes on the recorded set and the gap is written into the AC-CS-9 sentence.
- PR4 (section 11) has partially materialised: `AskUserQuestion` is unobserved rather than observed-silent. Mitigation unchanged.

### 14.4 P9: the overlay target stands

Blank topmost window, 1 Hz label update, 10 minutes: average CPU 0.078 % of one core (60 samples, total process CPU 0.80 s), maximum 16.8 MB. PRD section 12's overlay target (≤ 80 MB, proposal) and A10's combined 380 MB target stand; M1-3a/M1-3b/M1-6 keep their Done cells.

NOTE for dev, not a blocker: `tk_idle_probe.py` labels its column `private` and its summary `max private working set MB`, but it reads private bytes (`PrivateUsage`), not the private working set the section 6 P9 row names. The 16.8 MB is private bytes. M0-5 fixes the label (and may print both figures); M1-6's Done ("RAM recorded with the P9 method") records which metric it quotes. The PRD's idle-CPU target covers daemon plus overlay; this probe measured the overlay alone, and a one-card ticking overlay was not measured — M1-6 measures both.

### 14.5 Work-laptop results are pending; every gate keeps

P3 and all nine work-laptop probes are still pending the user's paste. Gates unchanged: P1 (everything in M1+ on the work laptop), P2 (7.4 M3b previews there), P3 (7.6 M5-3 URL installer there), P5 (M1-4 coverage and M4-1 there), P8 (M2-1 Outlook source there). Nothing in this Revision claims a work-laptop result.

### 14.6 Security follow-through: new task M1-0 (serial, first in M1)

The security verdict on M0-2 was PASS with F1 "fix before M1" and NOTEs N1–N8. The tech-lead chose to land F1 and the cheap NOTEs in one hardening task before any route consumer exists. M1-0 is added to section 7.1's table:

| Id | Role | Goal | Files owned | AC | Done (qa observes) | Hand-off | Parallel |
|---|---|---|---|---|---|---|---|
| M1-0 | dev | Harden the M0-2 auth and token path per the 2026-11-06 security review (F1, N1–N5) and add a server-wide request-body cap, before any card or hook route exists. | `assistant/server/auth.py`, `assistant/security/token.py`, `assistant/paths.py`, `assistant/server/app.py`, `pyproject.toml` (one change: `pythonpath = ["."]` under `[tool.pytest.ini_options]`); may add `assistant/security/acl.py` if the ACL helper is cleaner shared than imported function-locally | SEC-1, SEC-2 (part), AC-MC-9 (ACL), §8 error shape (413) | `pytest tests/test_health_auth.py` from the repo root with no `PYTHONPATH` set: all 14 existing tests pass and `from tests.fixtures.tree import ...` resolves (the qa follow-up); a request for `/v1/health%0aINJECTED%20LINE` without a token adds exactly one line to `logs/app.log`, path shown repr'd and capped at 200 characters (F1); `whoami`/`icacls` are invoked by absolute `%SystemRoot%\System32\` path or replaced by pywin32 `win32security` calls (N1), and the SID is accepted only if `re.fullmatch(r"S-1-[\d-]+", sid)` (N4); a simulated ACL failure (qa patches the subprocess or pywin32 call to raise) leaves no token file, logs one ERROR line "could not secure the token file; refusing to start", and exits 1 with no traceback (N3); `icacls <ASSISTANT_HOME>` after start shows exactly one ACE `<user>:(OI)(CI)(F)` with no `(I)` flag, and `settings.json` and `logs\app.log` show only inherited entries from that single ACE (N2); a `POST` to any path with `Content-Length` above the cap, or a chunked body that exceeds it, returns `413 {"error": "payload too large"}` before any route code runs, while the existing 401/403 matrix is unchanged and the Host → Origin → bearer → body-cap order is kept (N5). | qa, then security (appends a dated section to `docs/security/2026-11-06-m0-2-token-auth-review.md`) | serial, first in M1; blocks M1-4 and M1-5; does not block M1-1, M1-2, M1-3a, M1-7 |

Contract fixed for M1-4 (A16, planner default): `assistant/server/app.py` exposes `MAX_BODY_BYTES = 1_048_576` and the cap is a middleware that never reads the body itself. M1-4 may set lower per-route caps (card routes need far less than 1 MiB: `detail` is ≤ 500 characters) and may raise the hooks route's cap only with evidence from recorded payload sizes (`tool_input` of a large `Write` is the plausible outlier). Implementation notes dev must respect: `paths.py` cannot import `security.token` at module level (token imports paths) — use a function-local import or the optional `acl.py`; the home-folder ACL is applied idempotently on every start (one process call; `ASSISTANT_HOME` under `%TEMP%` in tests must still work); N3's exit path must not need `assistant/app.py`, which is M1-6's file.

Recorded, no task: **N6** (token never in a URL; A12 nonce exchange in M3b-3) unchanged. **N8** (hash-pinned `requirements.txt`) deferred; revisit when the dependency table is written for the README (M4-6) or when a pin changes. **N7**: `--doctor` is read-only in the shipped code (`doctor.report()` creates nothing; the test `test_corrupt_settings_falls_back_to_defaults_and_is_left_alone` depends on it) — the plan's section 10.2 step 3 ("creates `~/.assistant/` and the token on first run") is wrong against the code and is superseded: `install.ps1` runs `python -m assistant --init` (creates the home layout and the token via `paths.ensure_layout()` + `token.ensure()`, prints the home path and "token: present", never the token, exits 0, idempotent) and then `python -m assistant --doctor`. `--init` is added to M1-5's scope (`assistant/__main__.py` is already in its file set; `install.ps1` too). Also a plan-vs-code note: `token.regenerate()` already exists from M0-2, so M2-2's "adds `regenerate()`" reduces to exposing it (MCP/Settings) — no new function.

### 14.7 Ctrl+C shutdown of `python -m assistant`: PASS

Tested by the tech-lead on 2026-11-06 in a real PowerShell console against the M0-2 skeleton with a scratch `ASSISTANT_HOME` (the `KeyboardInterrupt` path in `App.run()` wraps `mainloop()`, and `shutdown()` runs in `finally`). Result: **PASS**. Observed log lines, in order: `INFO assistant.app: interrupted`, `INFO assistant.app: shutting down`, `INFO uvicorn.error: Shutting down`, `INFO uvicorn.error: Finished server process [<pid>]`; the shell returned to its prompt and no `-m assistant` process remained. No traceback. No task is needed; M1-6 keeps the shutdown contract of section 4a as written.

### 14.8 M1 file-set check and the first dispatch set (supersedes section 12's ordering)

Overlap check against section 7.1: M1-0 owns `assistant/server/auth.py`, `assistant/security/token.py`, `assistant/paths.py`, `assistant/server/app.py`, `pyproject.toml` (and optionally a new `assistant/security/acl.py`). M1-1 owns `assistant/cards/*`; M1-2 owns `assistant/scheduler.py` and `assistant/reminders/*`; M1-3a owns `assistant/overlay/*`; M1-7 owns `tests/*`, `tools/driver.py` and the run-assistant skill. **No overlap.** `pyproject.toml` has no M1 owner besides M1-0. Rule 1 therefore does not force serialisation; the tech-lead's choice to run M1-0 first is about getting the security re-review done before any consumer of the auth path exists, and about M1-7's `pytest` runs needing the `pythonpath` fix. Practical consequence: M1-1, M1-2, M1-3a and M1-7 may be dispatched as soon as M1-0 is dispatched, but their qa runs should happen after M1-0 has merged so plain `pytest` works.

Gap found while checking: `assistant/server/app.py` has no M1 owner in section 7.1, yet M1-4's `routes_cards.py`, `routes_reminders.py` and `routes_hooks.py` must be registered in `create_app()`. Resolution: `assistant/server/app.py` is added to **M1-4's** file set (route registration, per-route body caps under A16), after M1-0 has merged. M1-6 keeps `assistant/app.py` and `assistant/uibus.py` only and does not touch `assistant/server/app.py`. M1-8 (security) adds the 413 behaviour and the home-folder ACL to its checklist.

First dispatch set, restated:

0. **M1-0** (dev) — first, serial. Text below.
1. **M1-1**, **M1-2**, **M1-3a** (dev, three agents) — in parallel, exactly as written in section 12 Dispatches 1–3, with one addition to each prompt: "`assistant/server/app.py`, `auth.py`, `token.py`, `paths.py` and `pyproject.toml` belong to M1-0 this milestone; do not touch them."
2. **M1-7** (qa) — alongside, as section 12 says; its hook-payload fixtures come from the P6 JSONL (14.3).
3. **M1-4** and **M1-5** — after M1-0 has merged (M1-4 also after M1-1 for the integration test, as 7.1 says); M1-5's prompt includes `--init` (14.6).

### Dispatch 0 — M1-0 (dev)

```
Goal: close the 2026-11-06 security review's FIX-BEFORE-M1 item and the cheap NOTEs on the M0-2
auth/token path, and add a server-wide request-body cap, so that M1-4's routes land on a hardened base.
Scope: assistant/server/auth.py, assistant/security/token.py, assistant/paths.py, assistant/server/app.py,
pyproject.toml (one change only: pythonpath = ["."] under [tool.pytest.ini_options]). You may add
assistant/security/acl.py for a shared ACL helper. Nothing else — assistant/app.py is M1-6's.
Read first: docs/security/2026-11-06-m0-2-token-auth-review.md (F1, N1–N5), then the four files.
Changes: (F1) _reject logs the path with %r truncated to 200 chars; (N1) whoami/icacls by absolute
%SystemRoot%\System32\ path, or pywin32 win32security instead of both (preferred: survives AppLocker and
localised whoami); (N4) SID must re.fullmatch(r"S-1-[\d-]+"); (N3) an ACL failure logs one ERROR line
"could not secure the token file; refusing to start", leaves no token file, exits 1, no traceback;
(N2) ensure_layout() applies /inheritance:r /grant:r *<SID>:(OI)(CI)(F) to ASSISTANT_HOME idempotently
(function-local import or acl.py — paths.py must not import token at module level); (N5) MAX_BODY_BYTES =
1_048_576 in assistant/server/app.py and a middleware returning 413 {"error": "payload too large"} on
Content-Length or streamed bytes above it, after Host → Origin → bearer, without reading the body.
Done criteria: from the repo root with no PYTHONPATH, `pytest tests/test_health_auth.py` → 14 passed;
GET /v1/health%0aINJECTED%20LINE with no token → exactly one new app.log line, path repr'd; icacls on
ASSISTANT_HOME → one ACE <user>:(OI)(CI)(F), no (I); settings.json and logs\app.log show only inherited
entries from it; a 2 MB POST with a valid token → 413 before routing; the 401/403 matrix from M0-2's
Done cell unchanged. Paste every command and its output.
Hand-off: qa (writes the F1, N3-simulated-failure, ACL and 413 tests), then security (appends to the
2026-11-06 review file).
Plan: C:\AI Projects\Assistant\docs\plans\2026-11-06-assistant-v1-plan.md (§14.6, §14.8; §4a shutdown rules).
```

### 14.9 What this Revision supersedes (by section)

- **§6, P6 pass column** ("PermissionRequest for (a) in both clients"): not met and not meetable by agents; recorded as PARTIAL with the CLI facts in 14.3. The interactive/desktop half is user-run and open.
- **§6, P7 row, "Miss by > 2x → enable `scandir-rs` on the personal laptop"**: superseded by 14.2 — excludes first (M3a-1 product list, M0-5 probe re-measure), accelerator only if the re-measure still misses by > 2x.
- **§7.0, M0-4 Done**: the personal half is recorded; the Revision this cell asks for is this section. The work half stays open.
- **§7.1 table**: M1-0 added (14.6); M1-4's file set gains `assistant/server/app.py` and its Done gains the 413 check (14.8); M1-5's scope gains `--init` (14.6); M1-8's checklist gains the 413 and home-ACL checks.
- **§7.2, M2-2 "adds `regenerate()`"**: the function already exists (M0-2); M2-2 exposes it.
- **§7.3 gate** ("the Revision that applied the fallbacks must exist before M3a-2 is dispatched"): this is that Revision for the excludes fallback; the verdict on the targets waits for the M0-5 re-measure and Revision 2.
- **§10.2 step 3** (`--doctor` "creates `~/.assistant/` and the token"): wrong against the code; `--init` precedes `--doctor` (14.6).
- **§11, PR4**: partially materialised (AskUserQuestion unobserved); mitigation unchanged.
- **§12 opening paragraph and dispatch order**: superseded by 14.8 (M1-0 first; Dispatches 1–3 and M1-7 unchanged in content, each with the do-not-touch line).
- **§13**: P6 and P7 remain "decided by probes"; P7's decision is now "after the re-measure".
- **PRD §12 warm crawl targets**: read as "to be re-measured after excludes" (14.2 item 4). The PRD is not edited by this role; if product or doc-keeper append a note there, it should cite this section.

New planner default: **A16** — `MAX_BODY_BYTES = 1_048_576` server-wide, per-route caps may only lower it, the hooks route may raise it with recorded-payload evidence (14.6).

Open items created by this Revision: M0-5 (dispatch any time before M3a); interactive P6 (user-run); work-laptop P1–P9 (user-run); cold P7 (user-run after reboot); Revision 2 after the P7 re-measure.

## 15. Revision 2 2026-11-06 (after M0-5 re-measure)

- Date: 2026-11-06 (later). Author: planner. Trigger: M0-5 warm re-runs recorded in `docs/probes/2026-11-06-m0-probes-personal.md`, section "P7 re-measure after M0-5 excludes" (two dev runs, one independent qa run; qa PASS on the probe change). This is the Revision 2 that 14.2 item 2 promised. Nothing above is edited.

### 15.1 Measured (warm, no reboot, same command and roots as the original run, probe excludes extended per 14.2 item 1)

| Run | Entries | Dirs | Seconds | µs per entry | 0.3M warm projection |
|---|---|---|---|---|---|
| Original (Revision 1, 14.2) | 552,245 | 70,169 | 74.50 | 134.9 | ≈ 40.5 s |
| Dev run 1 | 393,459 | 38,216 | 15.47 | 39.3 | ≈ 11.8 s |
| Dev run 2 | 393,467 | 38,217 | 8.78 | 22.3 | ≈ 6.7 s |
| qa independent run | 393,477 | 38,217 | 9.75 | 24.8 | ≈ 7.4 s |

Peak working set 18.7–18.9 MB in all three; 1 permission error and 26 reparse points not followed, as before. The new rules removed 158,786 entries (28.8 %). The three post-change runs differ by 1.8x on a near-identical entry set, so disk-cache state explains part of the gain; only the entry reduction is attributable to the excludes with certainty.

### 15.2 Verdict against PRD section 12

- **0.3M warm (≤ 15 s): PASS on projection, 6.7–11.8 s.** The section 6 P7 trigger ("miss by > 2x") is **cleared**; the slowest run is 0.79x of the target, not 2.7x. Noted, not a fail: dev run 1 took 15.47 s unscaled at 393k entries — above 15 s at a size 31 % larger than the target's.
- **2M warm (≤ 2.5 min): not re-measured.** Linear projection from the slowest run is ≈ 79 s; M3a-2's Done cell ("crawl of the real personal-laptop roots re-measured and recorded against P7") is where the real number lands.
- **Cold run: outstanding.** User-run after a reboot, as in section 6; it does not gate M3a.

### 15.3 Decisions

1. **Excludes-first stands** (14.2 items 1–2 done for the probe; item 1's product list is still M3a-1's to write).
2. **`scandir-rs` is not scheduled.** It remains the section 10.1 optional accelerator — personal laptop only, behind a setting, off by default — and M3a-2 does not build the setting. The condition in 14.2 item 3 ("still misses by > 2x") was not met.
3. **M3a-1's `assistant/index/excludes.py` default list must include the same AppData cache categories** the probe now excludes (`Code Cache`, `Cache`, `Cache_Data`, `GPUCache`, `Service Worker`, `Shared Dictionary` under `AppData/Local|Roaming/*/`; `Microsoft/Office/*/OfficeFileCache`; `npm-cache`; NuGet `v3-cache`/`plugins-cache`), each with its AC-FI-7 counter, and must keep a `Cache` directory outside `AppData` (qa's `proj\Cache` case) un-excluded. The glob text is still M3a-1's; the categories are fixed.
4. **M3a-1 pins the "`*` crosses `/`" behaviour deliberately, with a test** in `tests/test_excludes.py` (M3a-8): `fnmatch`'s `*` matches across path separators, which is what lets `AppData/Local/*/Cache/` catch `Microsoft/Office/16.0/OfficeFileCache` at depth. Whether the product matcher keeps that semantics or restricts `*` to one segment and adds `**`, the choice is written in the module docstring and asserted by a test on exactly that Office path and on a one-level case.
5. **PRD section 12 warm targets are restored as written** (0.3M ≤ 15 s, 2M ≤ 2.5 min); they are no longer "to be re-measured after excludes". The cold targets and the 300 MB index-RAM target are unchanged (the latter still owned by M3a-1's 2M synthetic test).

### 15.4 What this Revision supersedes

- **14.2 item 2** (re-measure pending): done for the warm half; the cold half stays an open user-run item.
- **14.2 item 3 and 14.9 §6 P7 bullet** ("accelerator only if the re-measure still misses by > 2x"): condition evaluated, not met; no accelerator task.
- **14.2 item 4 and 14.9 PRD §12 bullet** ("to be re-measured after excludes"): superseded by 15.3 item 5.
- **14.9 §7.3 gate bullet** ("verdict on the targets waits for the M0-5 re-measure and Revision 2"): this is that verdict; the 7.3 gate for M3a-2 is satisfied on the personal laptop. Work-laptop gates (14.5) are untouched.
- **§13**: P7 is now decided (warm); only the cold run remains a probe-decided item.

Open items after this Revision: cold P7 (user-run after reboot); 2M warm number (M3a-2 Done cell); interactive P6 and work-laptop P1–P9 (unchanged from 14.9).
