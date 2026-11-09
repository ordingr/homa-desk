# Plan: rename Assistant to Homa

- Date: 2026-11-08
- Author: planner (TeamAgents)
- Supersedes nothing; the v1 plan (`docs/plans/2026-11-06-assistant-v1-plan.md`) stays the source of truth for everything except the product name, package name and runtime folder names decided here.
- Status: first version. Append-only (archaeology rule): changes go in dated "Revision" sections at the end. Every dispatch cites this file by path.
- Hand-off: tech-lead dispatches R0 → R9 in the order of section 5.

Orientation evidence this plan rests on (read on 2026-11-08, personal laptop): `assistant/paths.py`, `__main__.py`, `hooks_installer.py`, `security/token.py`, `app.py` (pid file, single instance, logging, message box), `send.py`, `mcp_stdio.py`, `update.py`, `doctor.py`, `overlay/fullpage.py`, `overlay/settings_ui.py` (MCP snippets, hook re-install), `overlay/win.py`, `overlay/monitors.py`, `security/denylist.py`, `index/manager.py`, `calendar/ics.py`, `server/mcp.py`, `server/static.py`, `scripts/*.ps1`, `install.ps1`, `uninstall.ps1`, `pyproject.toml`, `web/index.html`, `web/vite.config.js`, `web/package.json`, `web/src/*`, `tests/conftest.py`, `tests/test_hooks_installer.py`, `tools/driver.py`, `.claude/skills/run-assistant/SKILL.md`, `docs/design/2026-11-08-homa-icon-spec.md`, and the user's live state (section 2).

## 1. Goal and non-goals

**Goal.** The app the user runs from this repo is called **Homa** everywhere a person can see it (icon, windows, messages, shortcuts, CLI, docs), is imported and launched as `homa` (`python -m homa`), keeps its state in `%USERPROFILE%\.homa`, and the user's existing instance moves over on its first start with nothing lost: token, `settings.json`, the 226k-entry file index, reminders and card state, hook backups, Claude Code hook entries, shortcuts.

**Non-goals.** No feature work, no behaviour change beyond names and the one-time migration. No publishing to PyPI or npm. No redesign of the overlay or the full page (the new icon is a drop-in from `docs/design/`). No change to the HTTP routes (`/v1/...`, `/mcp`) or hook payloads. No rename of the repository folder on disk in this change (section 8, Q1). Not touching the work laptop: the user runs the same cutover there and pastes the output.

## 2. Assumptions and live-state facts

Facts observed on the personal laptop on 2026-11-08 (the plan is built around them; the dispatches must not re-derive them from guesses):

- F1. The real instance is alive: `%USERPROFILE%\.assistant\assistant.pid` holds `74808 17171`; the process is `<repo>\.venv\Scripts\pythonw.exe -m assistant` started 2026-11-07 17:04 with working directory `<repo>`; `logs/app.log` shows uvicorn on `127.0.0.1:17171` and a daily index walk of 226,552 entries (`index/files.idx`, 18 MB).
- F2. The home folder holds: `token`, `settings.json`, `state.json`, `state_ui.json`, `assistant.pid`, `hooks-install.json`, `events/`, `hooks-backup/`, `index/{files.idx,notes/}`, `logs/app.log`, `plugins/`, `plugins-data/`. None of `settings.json`, `state.json`, `state_ui.json` contains the string `.assistant` (0 matches), so nothing inside the home refers to its own path.
- F3. `%LOCALAPPDATA%\Assistant\edge-profile` exists (the Edge app-window profile). `%LOCALAPPDATA%\Homa` and `%USERPROFILE%\.homa` do not exist.
- F4. Shortcuts: the only one is `Assistant.lnk` on the **OneDrive-redirected Desktop** (`[Environment]::GetFolderPath('Desktop')` returns the OneDrive path). Target `<repo>\.venv\Scripts\pythonw.exe`, arguments `-m assistant`, working directory `<repo>`, icon `<repo>\assistant\assets\assistant.ico,0`. There is **no** Startup-folder shortcut (`install.ps1` was evidently not used on this machine; `scripts/install-shortcut.ps1` creates the Desktop one, not a Start-menu one as the brief assumed).
- F5. Claude Code hook entries in `%USERPROFILE%\.claude\settings.json`: eight `type: http` handlers, all `http://127.0.0.1:62480/v1/hooks/claude-code` with an `Authorization` header and `onFailure: continue`. **Port 62480 is not the running instance's port (17171).** The entries are recognised by `hooks_installer._is_ours` (type http + `127.0.0.1:` prefix + route substring) regardless of port, so `--doctor` currently reports "hooks installed: yes (all 8 events)" while hooks cannot reach the app. This is pre-existing, unrelated to the rename, and is handled at cutover (R9) with the user's consent.
- F6. The hook entries contain **no product name**: only the route `/v1/hooks/claude-code`, the loopback URL and the bearer. The rename therefore does not require touching `~/.claude/settings.json` at all; port, route and token are unchanged.
- F7. `~/.claude.json` has no MCP server entry for this app, and `%APPDATA%\Claude\claude_desktop_config.json` does not exist. MCP re-pointing is moot on this machine; the plan still covers it generically for the README and `--doctor`.
- F8. The repo venv has `assistant 0.1.0` installed **editable** (`pip show assistant`: "Editable project location: <repo>"); `assistant.egg-info/` sits in the tree (ignored by git).
- F9. Git: local `main` at `bcebd08`, no remote, one worktree (the checkout itself). Agents work in this folder today.

Planner defaults (reversible; flagged in the final report):

- A1. **Import name `homa`, distribution name `homa-desk`** (section 4.1).
- A2. **`python -m assistant` stays as a thin shim for one minor release** (0.2.x), removed in 0.3.0; same for `python -m assistant.mcp_stdio` and the `ASSISTANT_*` environment fallbacks (section 4.3).
- A3. **Version bumps to 0.2.0** so the shim's lifetime and the "Updated to" card have an anchor.
- A4. **Work happens in a git worktree**, not the live checkout, so the running instance's lazy imports (`_build_ui`, actions, settings window) never see a half-renamed tree (section 4.4).
- A5. Hook entries are **not rewritten by the migration**; the migration verifies them and reports (F6). Rewriting `~/.claude/settings.json` stays a user-confirmed action, as `install.ps1` already treats it.
- A6. The MCP server's self-reported name (`server/mcp.py` `SERVER_NAME`) becomes `homa`, the Settings snippets register it as `homa`, and an existing client entry registered as `assistant` keeps working because URL, route and token are unchanged.
- A7. Web `localStorage` keys (`assistant.split`, `assistant.previewZoom`) are renamed to `homa.*`; the user loses a saved split ratio and preview zoom once. Accepted.
- A8. Logger names become `homa.*`; the log file stays `logs/app.log` (name-neutral already).
- A9. The two batch-local variables `ASSIST_CTX` / `ASSIST_ADD_DIR` in `actions/claude.py` are internal to the generated launcher and are renamed to `HOMA_CTX` / `HOMA_ADD_DIR` in the same edit.

## 3. Approaches considered

| | Approach | Blast radius | Migration cost | Testability | Reversible |
|---|---|---|---|---|---|
| (a) | **Full rename**: package `homa`, home `~/.homa`, `HOMA_*` env, display "Homa", with a one-release `assistant` shim and `ASSISTANT_*` fallbacks, plus an automatic one-time data migration | Every file (mechanical), one new module (`homa/migrate.py`) | One folder rename per machine, done by the app | Migration is unit-testable on a fake home; stale-name grep is a test | Yes: tag before merge; rename the two folders back; shortcuts re-created by script |
| (b) | **Display-only rename**: "Homa" in UI, icon, docs, shortcuts; package stays `assistant`, home stays `~/.assistant`, env stays `ASSISTANT_*` | Small (strings, assets, docs) | None | Trivial | Trivial |
| (c) | **Full rename without compatibility**: as (a) but no shim, no env fallbacks, no migration; user re-runs `--init`, re-installs hooks, re-creates settings | Every file | Highest for the user: token changes, index rebuilt, hooks rewritten, settings retyped | Simplest code | Yes, but the user pays twice |

**Chosen: (a).** (b) leaves `python -m assistant`, `~/.assistant`, `ASSISTANT_HOME` and the `assistant` MCP server name in every doc and error message, which is exactly the surface the user reads; the brief asks for the name "everywhere the user sees it". (c) throws away a working 18 MB index and the user's settings for no gain, when a same-volume `os.rename` of the home folder is instantaneous and preserves the ACL. The cost of (a) over (c) is one ~150-line module and a shim of three tiny files, both covered by tests.

## 4. Chosen approach in detail

### 4.1 Names

| Surface | Old | New | Notes |
|---|---|---|---|
| Display name | Assistant | **Homa** | Everywhere a person reads it |
| Python package / import | `assistant` | **`homa`** | `git mv assistant homa` |
| Distribution name (`pyproject` `[project].name`) | `assistant` | **`homa-desk`** | PyPI `homa` is taken by an unrelated ML library; we never publish, but the distribution name must not collide if we ever do |
| Repository / GitHub name | (folder `Assistant`) | `homa-desk` | Folder rename deferred (Q1) |
| CLI | `python -m assistant` | **`python -m homa`** | Shim keeps the old form working for 0.2.x |
| Home folder | `%USERPROFILE%\.assistant` | **`%USERPROFILE%\.homa`** | Moved by migration |
| Home env var | `ASSISTANT_HOME` | **`HOMA_HOME`** | `ASSISTANT_HOME` honoured as fallback with one logged deprecation line |
| Other runtime env vars | `ASSISTANT_TEST_MODE`, `ASSISTANT_STATIC_DIR`, `ASSISTANT_NOTES_DIR` | `HOMA_TEST_MODE`, `HOMA_STATIC_DIR`, `HOMA_NOTES_DIR` | Same fallback via one helper in `paths.py` |
| Build/QA-only env vars | `ASSISTANT_WEB_OUT`, `ASSISTANT_DEV_TARGET`, `ASSISTANT_QA_WEB_BUILD`, `ASSISTANT_QA_PLAYWRIGHT_DIR`, `ASSISTANT_PERF_ROOT` | `HOMA_*` | Hard rename, no fallback |
| Edge app profile | `%LOCALAPPDATA%\Assistant\edge-profile` | `%LOCALAPPDATA%\Homa\edge-profile` | Moved by migration, best effort |
| Pid file | `assistant.pid` | `homa.pid` | Old one deleted by migration (stale by construction) |
| Icon file | `assets/assistant.ico` | `assets/homa.ico` | Plus new artwork (R4) |
| Shortcuts | `Assistant.lnk` (Desktop, Startup) | `Homa.lnk` | Rewritten by migration when they point at this install |
| MCP server name | `assistant` | `homa` | `SERVER_NAME`, `claude mcp add ... homa ...`, `mcpServers.homa` |
| Stdio shim module | `assistant.mcp_stdio` | `homa.mcp_stdio` | Old path forwards for 0.2.x |
| Window titles | `Assistant settings`, `Assistant - file search` | `Homa settings`, `Homa - file search` | The full-page title is a **cross-file contract**: `overlay/fullpage.py WINDOW_TITLE` (dev) must equal `web/index.html <title>` (ui) |
| Win32 class | `AssistantDisplayWatch<id>` | `HomaDisplayWatch<id>` | Internal |
| User-Agent | `Assistant/1`, `assistant-update/<v>` | `Homa/1`, `homa-update/<v>` | |
| Loggers | `assistant.*` | `homa.*` | |
| Message box title | `Assistant` | `Homa` | `app._notify_user` |
| Web localStorage keys | `assistant.split`, `assistant.previewZoom` | `homa.split`, `homa.previewZoom` | A7 |
| Vite plugin names, `package.json` name | `assistant-*`, `assistant-fullpage` | `homa-*`, `homa-fullpage` | |
| Driver scratch | `%TEMP%\assistant-driver` | `%TEMP%\homa-driver` | `tools/driver.py` |
| Agent skill | `.claude/skills/run-assistant/` | `.claude/skills/run-homa/` | Not under `.claude/agents` or `.claude/hooks`, so the guard hook allows it |
| Version | 0.1.0 | 0.2.0 | A3 |

Module-name trade-off, recorded for the user:

- `homa` (recommended): the command people type and the import match the product; PyPI collision is irrelevant while the app installs from a checkout (`-m` puts the working directory first on `sys.path`, and both shortcuts set it to the repo). If the user ever pip-installs the unrelated ML `homa` into the same interpreter, a bare `import homa` outside the repo would pick the wrong one; `python -m homa` from the repo would not. Accepted.
- `homa_desk`: no collision anywhere, but `python -m homa_desk` and `~/.homa_desk`-style names leak an awkward suffix into every user-facing command. Fallback if the collision above ever bites.
- keep `assistant`: zero risk, but defeats the goal.

### 4.2 Home-folder resolution (`homa/paths.py`)

```
def home() -> Path:
    HOMA_HOME if set
    else ASSISTANT_HOME if set   (log one deprecation line, once per process)
    else ~/.homa if it exists
    else ~/.assistant if it exists        # pre-migration: every CLI keeps working, nothing fresh is created
    else ~/.homa
```

`ensure_layout()` creates `~/.homa` only when neither folder exists. Consequence: `send`, `shutdown`, `hooks`, `update`, `--doctor` and the stdio shim keep working against the old folder until the app's first start moves it; they never create a competing fresh `~/.homa`. `_KNOWN_FILES` gains `homa.pid` and `migrated-from.json` and keeps `assistant.pid`, so `secure_home()` still recognises a migrated folder as ours and re-applies the ACL.

A tiny helper `paths.env(name) -> Optional[str]` reads `HOMA_<name>` then `ASSISTANT_<name>`; `app.py` (`TEST_MODE_VAR`), `server/static.py` (`ENV_VAR`), `search/notes_bm25.py` (`ENV_NOTES_DIR`) and `overlay/settings_ui.py` (`desktop_snippet` env passthrough) call it, so the old names appear in exactly one source file.

### 4.3 Compatibility shim (`assistant/`, three files, 0.2.x only)

- `assistant/__init__.py`: `from homa import __version__`.
- `assistant/__main__.py`: prints `python -m assistant is now python -m homa (this alias is removed in 0.3.0)` to **stderr** when `sys.stderr` is not None, then `sys.exit(homa.__main__.main())`.
- `assistant/mcp_stdio.py`: same notice on stderr (never stdout: stdout is the JSON-RPC channel), then `homa.mcp_stdio.main()`.

Nothing else is aliased: `from assistant.x import y` is **not** supported (aliasing `__path__` would create duplicate module objects and duplicate module state). Tests import `homa`.

### 4.4 Working in a worktree

The user's instance runs from this checkout and imports lazily (`_build_ui`, actions, settings window, search popup). `git mv assistant homa` in the live tree would break the next lazy import of the running app. R0 therefore creates branch `rename-homa` in a worktree at `C:\AI Projects\Assistant-homa` with its own `.venv` (`python -m venv .venv`, `pip install -r requirements.txt -r requirements-dev.txt`, `pip install -e .`). All R1–R7 work and tests run there. R8 merges `--ff-only` into `main` in the live checkout; R9 performs the cutover.

### 4.5 One-time migration (`homa/migrate.py`, new; wired in `homa/__main__.py` and `homa/app.py`)

Trigger: `python -m homa` (app start) and `python -m homa --init`, **only** when neither `HOMA_HOME` nor `ASSISTANT_HOME` is set (an explicit home is never migrated; this also makes every test immune, since `conftest` always sets the home). `--doctor` never migrates; it reports the pending state.

Inputs: `old = Path.home()/".assistant"`, `new = Path.home()/".homa"`, `marker = new/"migrated-from.json"`.

Decision table:

| `old` | `new` | marker | Action |
|---|---|---|---|
| absent | any | any | nothing |
| present | absent | — | **migrate** (steps 1–6) |
| present | present | present | nothing; `--doctor` line: `old data folder  %USERPROFILE%\.assistant still present (N MB); Homa does not read it; delete it when you are sure` |
| present | present | absent | nothing is moved; `new` wins (it is what `home()` resolves to). One-time card and `--doctor` line: `both %USERPROFILE%\.homa and %USERPROFILE%\.assistant exist and were not merged; Homa uses .homa. If settings or the index are missing, stop Homa, rename .homa away, start again.` Deterministic, nothing deleted. |

Steps when migrating:

1. **Stop the old instance if it is running.** Read `old/assistant.pid` → `(pid, port)`. If `netinfo.listener_pid(port) == pid` and `netinfo.process_image(pid)` is a Python image: POST `/v1/shutdown` to `http://127.0.0.1:<port>` with `Authorization: Bearer <old/token>` through an opener built with `urllib.request.build_opener(ProxyHandler({}))` (never the default opener), then wait up to 15 s for the listener to disappear and the process to exit. If it does not: stop, nothing moved, message (console + message box when windowed): `Homa could not stop the running Assistant (pid N). Close it from its icon menu and start Homa again.` Exit 1.
2. **Rename** `old` → `new` with `os.rename` (same volume, atomic, keeps ACL, index and everything). If it raises (`PermissionError`: an Explorer window or editor inside the folder): stop, nothing moved, message names the folder and says to close whatever has it open and start again. Exit 1. No copy fallback (a copy would leave two tokens and two indexes to reconcile).
3. **Fix-ups inside `new`:** delete `assistant.pid` (stale by construction after step 1). Nothing else changes: `hooks-install.json` records the path and hash of `~/.claude/settings.json`, which are unchanged; `settings.json`, `state*.json`, `reminders.json`, `index/`, `events/`, `hooks-backup/`, `logs/` carry no self-path (F2).
4. **Edge profile:** if `%LOCALAPPDATA%\Assistant` exists and `%LOCALAPPDATA%\Homa` does not, `os.rename`; on failure (full page open, Edge holding locks) record `skipped: <ExceptionName>` and continue — `fullpage.profile_dir()` creates a fresh profile on next use. Never delete the old folder.
5. **Shortcuts:** for each of the Desktop and Startup folders obtained through `win32com.shell.shell.SHGetFolderPath` (`CSIDL_DESKTOPDIRECTORY`, `CSIDL_STARTUP`; this follows the OneDrive redirection seen in F4), if `Assistant.lnk` exists, read it with `WScript.Shell`; when `Arguments` contains `-m assistant` **and** `WorkingDirectory` equals `update.install_root()` (case-insensitive, normalised), write `Homa.lnk` beside it with the same `TargetPath` and `WorkingDirectory`, `Arguments` `-m homa`, `IconLocation` `<repo>\homa\assets\homa.ico,0`, `Description` `Homa`; then delete `Assistant.lnk`. A shortcut pointing at a different install is left alone. The folder list comes from `migrate.shortcut_dirs()` so tests can point it at temp folders.
6. **Marker:** write `new/migrated-from.json`: `{"from": "<old>", "when": "<ISO>", "homa_version": "0.2.0", "edge_profile": "moved|skipped: ...|absent", "shortcuts": ["<path> -> <path>", ...]}`, atomic write like `hooks_installer._atomic_write`.
7. **Hooks check (read-only):** after the app's UI is up, if `hooks_installer.installed()` starts with `yes`/`partial` and either the installed URL port differs from `settings.port` or `not hooks_installer.carries_bearer(token)`, post one `custom` sticky card: `Claude Code hooks point at port <p>; Homa listens on <port>. Open Settings > Claude Code and re-install them.` This is what the user will see on the personal laptop (F5). `--doctor` gets the same fact as a suffix on the `hooks installed` line: `yes (all 8 events) but at port 62480; settings port is 17171`.
8. **One-time card** on the first start after migration (`custom`, sticky): `Assistant is now Homa` with detail `Your data moved to %USERPROFILE%\.homa. Shortcuts: <n> updated. Edge profile: <moved|kept at old location>.`

Idempotent: a second start finds `old` absent (or the marker present) and does nothing. Nothing is ever deleted except the stale pid file and the `Assistant.lnk` that pointed at this install.

### 4.6 Protection of the old names

Old names stay protected so a lingering old folder is never indexed, served or overwritten: `security/denylist.py DIR_PATTERNS` adds `.homa` and `appdata/local/homa` and keeps `.assistant` / `appdata/local/assistant`; `index/manager._own_data_rules` emits `**/.homa/` and keeps `**/.assistant/`; `update.py PROTECTED_DIR` becomes a tuple `(".homa", ".assistant")`, `_strip_top` / `_extract` look for `homa/__init__.py`.

### 4.7 Icon

`docs/design/2026-11-08-homa-icon.svg` and `...-icon-muted.svg` (designer, Revision 2, **not yet rendered by anyone**) are copied over `homa/assets/icon.svg` and `icon-muted.svg`; `scripts/render-icons.ps1` (already pointed at `homa/assets` and `homa.ico` by R1) regenerates the six PNG tile sizes, the badge PNGs and `homa.ico`. R4 looks at the output at 16/24/32 px and on screen before committing, and reports back if the bird misreads (the spec has a "first fix to try"); the rename does not wait on icon aesthetics.

## 5. Work breakdown (dispatches)

Order: R0 → R1 → {R2, R3, R4 in parallel} → {R5a, R7 in parallel} → R5b → R6 → R8 → R9. Two dispatches never touch the same file; where the same role edits the same file twice (R1 then R2 on `paths.py`, `__main__.py`, `app.py`, `doctor.py`) they are serialised. Every dispatch loads `fable-parity` first and cites this plan by path. Worktree root below is `C:\AI Projects\Assistant-homa` (R0 creates it); "`<repo>`" means that worktree until R8, the live checkout after.

### R0 — git-keeper: tag, branch, worktree

- Goal: an immutable rollback anchor and an isolated tree to work in.
- Scope: git metadata only. Commands: `git tag pre-homa-rename bcebd08`; `git worktree add "C:\AI Projects\Assistant-homa" -b rename-homa`.
- Done: `git tag -l pre-homa-rename` prints it; `git worktree list` shows both paths; `git -C "C:\AI Projects\Assistant-homa" status` clean on `rename-homa`.
- Hand-off: tech-lead (dispatch R1).

### R1 — dev: package move and mechanical rename (Python, scripts, tools)

- Goal: the tree imports and runs as `homa` with every user-visible and runtime name from section 4.1 changed, old-name protections kept (4.6), the shim in place (4.3), and the home resolution + env helper from 4.2 — **without** the migration module.
- Step 0 (worktree bootstrap, not committed): `python -m venv .venv`, `.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt`, `.venv\Scripts\python -m pip install -e .` (after the `pyproject.toml` edit).
- Scope (exact): `git mv assistant homa` (includes `homa/static/app/**` and `homa/assets/**` moving unchanged; `git mv homa/assets/assistant.ico homa/assets/homa.ico`); new `assistant/{__init__,__main__,mcp_stdio}.py`; `pyproject.toml` (`name = "homa-desk"`, `version = { attr = "homa.__version__" }`, `include = ["homa*"]`, `package-data homa`); `homa/__init__.py` (0.2.0); `homa/paths.py`; `homa/__main__.py` (prog name only); `homa/app.py` (pid file name, log/print strings, message-box title, `TEST_MODE_VAR` via helper); `homa/send.py`, `homa/mcp_stdio.py`, `homa/update.py`, `homa/doctor.py` (report key `homa`, module path `homa.server.mcp`, env var names), `homa/hooks_installer.py` (docstring/messages), `homa/security/token.py` (docstring), `homa/security/denylist.py`, `homa/index/manager.py`, `homa/calendar/ics.py` (UA), `homa/server/mcp.py` (`SERVER_NAME`, messages), `homa/server/routes_files.py` and `homa/server/static.py` (docstrings, env via helper), `homa/search/notes_bm25.py` (env via helper), `homa/actions/{claude,shell}.py` (strings, A9), `homa/overlay/{fullpage,settings_ui,monitors,win,settings_pages_search}.py`, every `getLogger("assistant.…")` → `homa.…`, `homa/demo.py`, root `demo.py`, `run.cmd`, `install.ps1`, `uninstall.ps1` (handles `Homa.lnk` and legacy `Assistant.lnk`, `homa.pid` and legacy `assistant.pid`, asks about `~/.homa` and mentions a legacy `~/.assistant` if present), `scripts/build-web.ps1` (`HOMA_WEB_OUT`, default out dir `homa\static\app`), `scripts/render-icons.ps1` (`homa\assets`, `homa.ico`), `scripts/install-shortcut.ps1` (`Homa.lnk`, `-m homa`, `homa.ico`, description), `tools/driver.py`, `git mv .claude/skills/run-assistant .claude/skills/run-homa` + text, `.gitignore` (`!homa/static/`). Not in scope: `web/**`, `homa/static/app/**` contents, `homa/assets/*.svg|png` contents, `tests/**`, `docs/**`, `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `homa/migrate.py`.
- Contract strings fixed here for R3: `fullpage.WINDOW_TITLE = "Homa - file search"`; `settings_ui.mcp_add_command` registers `homa`; `desktop_snippet` uses `["-m", "homa.mcp_stdio"]` and key `homa`.
- Done criteria (paste outputs): `.venv\Scripts\python -m homa --doctor` exits 0 and its first line is `homa  0.2.0` with `home` under a scratch `HOMA_HOME`; `tools/driver.py start --home <scratch> --port <free> --test-mode` then `ui` then `stop` succeed (graceful shutdown line `True`); `HOMA_HOME=<scratch> python -m assistant --doctor` prints the deprecation line on stderr and the same report on stdout; `echo {"jsonrpc":"2.0","id":1,"method":"initialize",...} | python -m assistant.mcp_stdio` (app running on scratch home) returns one JSON line whose `serverInfo.name` is `homa` and nothing else on stdout; `git grep -n -i "assistant" -- homa scripts tools install.ps1 uninstall.ps1 run.cmd demo.py pyproject.toml .claude .gitignore` lists only: `homa/paths.py` (fallbacks), `homa/security/denylist.py`, `homa/index/manager.py`, `homa/update.py` (old protected dir), `uninstall.ps1` (legacy cleanup lines), `install.ps1`/`doctor.py` if they mention the old folder as "old". Report the full list. State plainly that `tests/` are red until R5 and that no test was edited.
- Hand-off: tech-lead (dispatch R2, R3, R4); qa verifies in R5.

### R2 — dev: migration module and wiring (after R1)

- Goal: section 4.5 implemented and demonstrated on a fake user profile.
- Scope (exact): new `homa/migrate.py`; `homa/__main__.py` (call `migrate.run_once()` before app start and `--init`); `homa/app.py` (one-time card and hooks-check card after `_build_ui`); `homa/doctor.py` (`migration`/`old data folder` line, `hooks installed` port/bearer suffix); `homa/paths.py` (`_KNOWN_FILES` additions only).
- Constraints: no default `urllib` opener anywhere; never touch a folder when `HOMA_HOME` or `ASSISTANT_HOME` is set; shortcut folders only through `migrate.shortcut_dirs()`; nothing deleted except the stale pid file and an `Assistant.lnk` whose working directory is this install.
- Done criteria (paste outputs): a scratchpad probe that builds `<tmp>\.assistant` (token, settings.json with a free port, `assistant.pid` with a dead pid, index/files.idx dummy, hooks-install.json), `<tmp>\AppData\Local\Assistant\edge-profile\x`, and a real `Assistant.lnk` in a temp "Desktop" with `-m assistant` and working directory `<repo>`, then runs `python -m homa --init` in a subprocess with `USERPROFILE/HOME/HOMEDRIVE/HOMEPATH/LOCALAPPDATA` pointing at `<tmp>` and `HOMA_HOME`/`ASSISTANT_HOME` **unset**, and `migrate.shortcut_dirs` redirected (env hook or module constant the subprocess reads) — shows: `<tmp>\.homa` exists with the same files minus `assistant.pid` plus `migrated-from.json`; `<tmp>\.assistant` absent; `...\Local\Homa\edge-profile\x` present; `Homa.lnk` present with `-m homa` and `homa.ico`, `Assistant.lnk` gone; a second `--init` changes nothing (directory listing byte-identical). Plus `python -m homa --doctor` against that profile shows the `migration` line. Never run against the real `%USERPROFILE%`.
- Hand-off: tech-lead; qa covers it with `tests/test_migrate.py` in R5.

### R3 — ui: web rename and bundle rebuild (after R1; parallel with R2/R4)

- Goal: the full page says Homa and the committed bundle under `homa/static/app/` is rebuilt from it.
- Scope (exact): `web/index.html` (`<title>Homa - file search</title>`, must equal `fullpage.WINDOW_TITLE`), `web/src/App.jsx` (4 strings), `web/src/components/Header.jsx` (brand), `web/src/api.js` (2 messages), `web/src/components/SplitHandle.jsx` and `web/src/preview/zoom.js` (storage keys), `web/vite.config.js` (`HOMA_DEV_TARGET`, `HOMA_WEB_OUT`, plugin names), `web/package.json` + `package-lock.json` name `homa-fullpage`; then `powershell -ExecutionPolicy Bypass -File scripts\build-web.ps1` → `homa/static/app/**` (replace, commit).
- Done criteria: `grep -rni assistant web/src web/index.html web/vite.config.js web/package.json` prints nothing; `grep -c Assistant homa/static/app/assets/*.js homa/static/app/index.html` is 0 for every file; with the app started on a scratch home (`tools/driver.py start … --test-mode`), `tools/driver.py fullpage --query "x" --screenshot <scratch>\full.png` opens the window and `fullpage` finds it by the new title (driver output pasted; screenshot shows the "Homa" brand in the header).
- Hand-off: qa (R5) for the e2e full-page tests.

### R4 — dev: new icon artwork (after R1; parallel with R2/R3)

- Goal: the gold-on-navy Homa bird is the app icon in every size, rendered through the existing pipeline.
- Scope (exact): `homa/assets/icon.svg`, `homa/assets/icon-muted.svg` (copied from `docs/design/2026-11-08-homa-icon.svg` and `...-icon-muted.svg`), regenerated `homa/assets/icon_*.png`, `icon-muted_*.png`, `badge_*.png`, `homa/assets/homa.ico`. Nothing else; do not edit the SVGs (design is the designer's).
- Steps: copy; `powershell -ExecutionPolicy Bypass -File scripts\render-icons.ps1` (needs node, Edge and `playwright-core` — figure out the module location from the script's `PLAYWRIGHT_MODULES` logic); view `icon_60.png`, `icon-muted_60.png` and the 16/24/32 frames of `homa.ico` (extract with a scratch script, or screenshot the Desktop shortcut after R9); start the app on a scratch home and `tools/driver.py screenshot` the overlay icon normal, muted (`driver` menu) and with a badge (`post` three cards).
- Done criteria: the PNG set and `homa.ico` are regenerated (file mtimes/sizes differ from the committed ones; `pack_ico.py` frame count 6); the three screenshots are pasted; a one-paragraph read-out of how the bird reads at 16 and 32 px, and whether the "first fix to try" in the spec is needed (if so: report, do not implement).
- Hand-off: tech-lead (designer revision if it misreads); qa (R5) checks the files load (`gfx.load_png`, `win.set_app_icon`) via the overlay e2e tests.

### R5a — qa: test-suite rename and new tests (after R1–R4; parallel with R7)

- Goal: the whole suite targets `homa`, and the rename, shim, fallbacks and migration are covered.
- Scope: `tests/**` only (fixtures included).
- Mechanical: every `from assistant` / `import assistant` → `homa` (23 files); `ASSISTANT_HOME` → `HOMA_HOME` and the other env names (conftest and the 28 files that mention them); `[sys.executable, "-m", "assistant", …]` → `"homa"`; `"-m", "assistant.mcp_stdio"` → `homa.mcp_stdio`; `pytest.importorskip("assistant.…")`; window titles (`tests/test_m4_0.py`, `tests/e2e/test_fullpage*.py`, `tests/e2e/m43_driver.cjs`); `desktop_snippet()["mcpServers"]["homa"]`; `mcp_add_command` assertion in `tests/e2e/test_settings_live_apply.py`; `test_update.py` expectations for `homa/__init__.py` and the protected dirs. `conftest.isolated_home_env` additionally sets `LOCALAPPDATA` to a temp dir so no test can reach the real Edge profile.
- New tests:
  - `tests/test_migrate.py`: in-process with `monkeypatch` (fake `Path.home()` via `USERPROFILE`/`HOME`, fake `LOCALAPPDATA`, `migrate.shortcut_dirs` → tmp dirs, `HOMA_HOME`/`ASSISTANT_HOME` deleted from the env): happy path (every file moved, pid file dropped, marker written, Edge folder moved, `Homa.lnk` written with `-m homa`, `Assistant.lnk` removed); idempotence (second run is a no-op, marker unchanged); both-exist-no-marker (nothing moved, report says so); old-instance-running path with a stub for the shutdown call (succeeds → proceeds; times out → nothing moved, exit 1 message); rename failure injected (`os.rename` raising `PermissionError`) → nothing moved, no marker; shortcut with a different working directory left untouched; explicit `HOMA_HOME` set → never migrates. Shortcut tests create a real `.lnk` through `WScript.Shell` in a temp folder (no window is shown).
  - `tests/test_legacy_shim.py`: `python -m assistant --doctor` (subprocess, fake home) exits 0, stdout equals `python -m homa --doctor` stdout, stderr contains the deprecation line; `python -m assistant.mcp_stdio` against a running fixture app answers `initialize` with `serverInfo.name == "homa"` and writes nothing else to stdout.
  - `tests/test_paths_fallback.py`: `HOMA_HOME` wins over `ASSISTANT_HOME`; `ASSISTANT_HOME` alone is used and logs one warning; with neither set and a fake `USERPROFILE`: `~/.homa` if it exists, else `~/.assistant` if it exists, else `~/.homa`; `ensure_layout` on a profile with only `~/.assistant` does not create `~/.homa`.
  - `tests/test_rename_complete.py`: the stale-name scan of section 6.2 with its allowlist.
  - `--doctor` assertions: `hooks installed` suffix when the installed port differs from `settings.port` (fixture settings file with a different port), and the `old data folder` line when both folders exist.
- Done criteria: `.venv\Scripts\python -m pytest -q -rs` from the worktree root pasted in full (pass/fail/skip counts and every skip reason); e2e files run on the unlocked desktop (not skipped) and are green; `git status` shows only `tests/**`.
- Hand-off: tech-lead (R5b after R7 lands).

### R5b — qa: final run (after R5a and R7)

- Goal: a green suite on the finished branch, including the README section of the stale-name scan.
- Scope: `tests/**` only (fixes to tests if R7 exposed allowlist mistakes; never product code or docs).
- Done: full `pytest -q -rs` output pasted; `tests/test_rename_complete.py` passes; verdict PASS/FAIL with evidence.
- Hand-off: security (R6).

### R6 — security: review (after R5b)

- Goal: a verdict on the security-relevant classes this change touches.
- Review points: the migration reads the old token to POST `/v1/shutdown` (loopback only, no default opener, bounded wait), moves user data (ACL preserved by `os.rename`? confirm with `icacls` on a probe folder), deletes exactly one `.lnk` under a strict match, writes nothing to `~/.claude/settings.json`; `paths.home()` fallback order cannot be steered by another user (same-user folders only; note that a pre-created `~/.homa` by the same account simply wins — not a boundary); the deny-list and own-data exclusions cover both old and new folder names; `send.py` still builds its own opener; the shim writes only to stderr; tests cannot reach the real `~/.claude/settings.json`, `~/.assistant`, `~/.homa` or `%LOCALAPPDATA%` (read `conftest.isolated_home_env` and `test_migrate.py`); no employer or personal identifier in any committed file (`git grep -i` for the user name and the employer, output pasted as "0 matches" without echoing the terms).
- Writes: `docs/security/2026-11-08-rename-review.md` (new file, append-only afterwards).
- Hand-off: git-keeper (R8) on PASS; tech-lead on BLOCK.

### R7 — doc-keeper: README, CHANGELOG, docs, CLAUDE.md (after R1–R4; parallel with R5a)

- Goal: current usage reads "Homa"; history stays intact; the upgrade and rollback are written down.
- Scope (exact): `README.md` — title and every current-usage section updated **in place** (install, connect Claude Code, command line, MCP snippets with `homa`, full page path `%LOCALAPPDATA%\Homa\edge-profile`, settings and files `~/.homa`, uninstall, features list, security section); the dated historical sections (`### M2 additions (2026-11-06)` and the like) are left as they are; a new section `## Upgrading from Assistant (2026-11-08)` describes what the first start moves, the both-folders rule, the shim and its 0.3.0 removal, the env-var fallbacks, the hooks-port card, and the rollback steps of section 7. `CHANGELOG.md` — new dated entry. `docs/api.md` — dated note at the end (server name `homa`, stdio module `homa.mcp_stdio`, nothing else changed). `docs/prds/2026-11-06-assistant-v1.md` — dated amendment section at the end recording the name decision and its effect on DEC 7 (`homa` server name), DEC 11 (`HOMA_NOTES_DIR`), AC-FI-16 and other `~/.assistant` mentions (no edits to the original text). `docs/plans/2026-11-06-assistant-v1-plan.md` — `## 20. Revision 7 2026-11-08 (rename)` pointing at this plan. `docs/design/2026-11-08-homa-icon-spec.md` — append the R4 render outcome under a dated heading. `CLAUDE.md` (project) — Layout and Source-of-truth sections updated to `homa/`, `HOMA_HOME`, this plan added to the list. `docs/search-grammar.md` — dated note only if its 6 mentions are about current usage.
- Rules: archaeology (no deletions in `docs/`), placeholders for paths, no employer or personal identifiers. Doc-keeper has no shell: it asks qa (R5b) to run `tests/test_rename_complete.py`, whose README rule is: `Assistant`/`-m assistant`/`.assistant`/`ASSISTANT_` may appear only inside sections whose heading contains a date in parentheses `(20YY-MM-DD)` or the words `Upgrading from Assistant`.
- Done: `Grep` evidence that the README outside those sections has no stale name (paste the grep with line numbers and show every remaining hit sits under an allowed heading); the new sections quoted in the report.
- Hand-off: qa (R5b).

### R8 — git-keeper: merge (after R6 PASS)

- Goal: `main` in the live checkout is the renamed tree; the rollback tag stays.
- Scope: git only. `git -C "C:\AI Projects\Assistant" merge --ff-only rename-homa`; `git worktree remove "C:\AI Projects\Assistant-homa"` only after R9 confirms (the worktree venv is the fallback test environment).
- Done: `git log --oneline -1` on main equals the branch head; `git tag -l pre-homa-rename` still prints.
- Hand-off: tech-lead (R9).

### R9 — cutover on the personal laptop (after R8; **needs the user's go-ahead in the conversation**, then run by dev with Bash, or by the user from pasted commands)

Why it needs consent: it stops the user's running overlay, renames their data folder, and (step 5) rewrites `~/.claude/settings.json`.

1. In the live checkout: `.venv\Scripts\python -m pip uninstall -y assistant` then `.venv\Scripts\python -m pip install -e .` (refreshes the editable install from F8; otherwise a stale `assistant` distribution lingers in the venv).
2. `.venv\Scripts\python -m homa` from a console (not `pythonw`, so the migration output is visible). Expected: the migration stops pid 74808 through `/v1/shutdown`, renames `%USERPROFILE%\.assistant` → `.homa`, moves the Edge profile, rewrites the Desktop `Assistant.lnk` → `Homa.lnk`, writes the marker, then the app starts on 17171 and shows the "Assistant is now Homa" card and (per F5) the hooks-port card.
3. Verify and paste: `.venv\Scripts\python -m homa --doctor` (home `…\.homa`, `migration` line, `port 17171 in use by pid <n> (pythonw.exe|python.exe)`, `index size and age` shows ~226k entries); `dir %USERPROFILE%\.homa` and `dir %USERPROFILE%\.assistant` (the latter must not exist); `dir %LOCALAPPDATA%\Homa\edge-profile`; the Desktop shows `Homa.lnk` with the new icon; the overlay icon is the Homa bird; hotkey opens the popup; F11 opens the full page titled `Homa - file search`.
4. Close the console instance (`python -m homa shutdown`) and start it the normal way via `Homa.lnk` (`pythonw`); confirm `--doctor` again.
5. **With the user's explicit OK:** `.venv\Scripts\python -m homa hooks install` (prints the backup path under `~/.homa/hooks-backup/`), then `--doctor` shows `hooks installed  yes (all 8 events)` with no port suffix; start a Claude Code session and confirm a card appears on `UserPromptSubmit`/`Stop`.
6. Work laptop: the user repeats steps 1–5 after bringing the new tree over the way the install was done there; results are pasted, never claimed.

## 6. Verification strategy

### 6.1 Per dispatch

| Dispatch | Proof |
|---|---|
| R1 | `--doctor` output; driver start/ui/stop output; shim stderr line; stdio `initialize` reply; the `git grep` allowlist listing |
| R2 | Probe transcript on a fake profile: before/after directory listings, marker contents, `.lnk` properties read back with `WScript.Shell`, second-run no-op |
| R3 | Empty greps on `web/` and the bundle; driver `fullpage` output and screenshot |
| R4 | Regenerated file list; screenshots at normal, muted and badged; 16/32 px read-out |
| R5 | Full `pytest -q -rs` output, e2e not skipped |
| R6 | Review file with findings and the identifier grep ("0 matches") |
| R7 | Grep with line numbers showing remaining hits sit under allowed headings; `test_rename_complete.py` green in R5b |
| R9 | Doctor output, directory listings, screenshots, a hook-triggered card observed |

### 6.2 Stale-name scan (`tests/test_rename_complete.py`, qa)

Files scanned: `homa/**/*.py`, `homa/static/app/index.html`, `homa/static/app/assets/*.js`, `web/index.html`, `web/src/**`, `web/vite.config.js`, `web/package.json`, `scripts/**`, `install.ps1`, `uninstall.ps1`, `run.cmd`, `demo.py`, `pyproject.toml`, `tools/driver.py`, `.claude/skills/**`, `README.md`.

Patterns (any hit fails unless allowlisted): `\bAssistant\b`, `-m assistant\b`, `assistant\.mcp_stdio`, `assistant\.pid`, `assistant\.ico`, `\bASSISTANT_[A-Z_]+`, `"assistant\.` (logger names), `\.assistant\b`, `AssistantDisplayWatch`, `"assistant"` (MCP key).

Allowlist (file → permitted patterns, each justified in a comment in the test):

- `homa/paths.py`: `ASSISTANT_[A-Z_]+`, `\.assistant` (fallbacks, 4.2)
- `homa/migrate.py`: everything (it is the migration)
- `homa/doctor.py`: lines containing `old data folder` or `migrat`
- `homa/security/denylist.py`: `\.assistant`, `appdata/local/assistant` (4.6)
- `homa/index/manager.py`: `\*\*/\.assistant/` (4.6)
- `homa/update.py`: `\.assistant` (4.6)
- `uninstall.ps1`: lines containing `legacy`
- `README.md`: hits inside sections whose heading matches `\(20\d\d-\d\d-\d\d\)` or `Upgrading from Assistant`
- Not scanned at all: `assistant/**` (the shim), `tests/**`, `docs/**`, `CHANGELOG.md`, `tools/probes/**` (historical M0 probe scripts), `web/node_modules`, `.venv`.

### 6.3 Constraints the tests enforce

- No test touches the real `%USERPROFILE%\.claude\settings.json`, `~/.assistant`, `~/.homa` or `%LOCALAPPDATA%`: `conftest.isolated_home_env` fakes `USERPROFILE/HOME/HOMEDRIVE/HOMEPATH`, `HOMA_HOME` and (new) `LOCALAPPDATA`; the migration never runs when `HOMA_HOME` is set; shortcut folders come from `migrate.shortcut_dirs()`.
- No visible Edge window from tests: the full-page e2e tests keep their existing driver/headless arrangement; `test_migrate.py` never launches Edge.
- `send.py` keeps its own opener; `migrate.py` builds its own; `test_rename_complete.py` or a one-line addition to the existing hardening test asserts `urllib.request.urlopen` is not referenced in `homa/send.py` or `homa/migrate.py`.

## 7. Risks and rollback

| Risk | Mitigation / rollback |
|---|---|
| The running instance holds files open, so renaming `~/.assistant` fails | Migration stops it first through its own token and waits; on any failure nothing is moved and the message says what to close. Retrying is just starting Homa again. |
| Half-renamed tree breaks the live instance's lazy imports during the work | Worktree (4.4); `main` changes only at R8. |
| The folder loses its user-only ACL | `os.rename` keeps it; `secure_home()` re-applies at start because `_KNOWN_FILES` includes the new names. R6 confirms with `icacls` on a probe. |
| Desktop is OneDrive-redirected (F4) | Shortcut folders via `SHGetFolderPath`, which returns the redirected path; R2's probe and `test_migrate.py` use injected folders. |
| Edge profile locked while the full page is open | Skipped with a recorded reason; fresh profile on next use; old folder left, reported by `--doctor`. |
| Hooks already point at the wrong port (F5) | Not caused by the rename; surfaced by the new card and doctor suffix; fixed at R9 step 5 with consent. |
| A fresh `~/.homa` exists before migration | Deterministic: `.homa` wins, nothing merged or deleted, card + doctor line tell the user how to recover (4.5 table). |
| Icon reads badly at 16 px (designer never rendered it) | R4 reports with screenshots; designer revises; rename ships regardless. |
| Stale editable `assistant` install in the live venv | R9 step 1. |
| Users or scripts dropping files into `~/.assistant/events/` | README "Upgrading" section names the new path; the old folder no longer exists so the failure is loud, not silent. |
| PyPI `homa` collision | Distribution is `homa-desk`; never published under `homa`; `homa_desk` is the documented fallback for the import name if a collision ever bites. |

**Rollback (any time after R9):** `python -m homa shutdown`; in the live checkout `git checkout pre-homa-rename` (or the user runs `git reset --hard pre-homa-rename` on `main`; agents may not); `ren %USERPROFILE%\.homa .assistant` and delete `.assistant\migrated-from.json`; `ren %LOCALAPPDATA%\Homa Assistant`; `.venv\Scripts\python -m pip install -e .`; `powershell -ExecutionPolicy Bypass -File scripts\install-shortcut.ps1` to recreate `Assistant.lnk` (delete `Homa.lnk` by hand); `python -m assistant`. Hook entries never changed in the rename, so they need nothing; if R9 step 5 re-installed them for 17171 they still fit the old app (same route and token). Before R8 the rollback is simply not merging.

**Shim removal:** 0.3.0 deletes `assistant/`, the `ASSISTANT_*` fallbacks in `paths.py`, the `.assistant` fallback in `home()`, and the migration's trigger (the module may stay for `--doctor` reporting). Recorded in the README "Upgrading" section.

## 8. Open questions (real decisions for the user)

- **Q1. Rename the repo folder `C:\AI Projects\Assistant` → `C:\AI Projects\homa-desk` now?** Default: **not in this change.** It would break the venv's editable path and the shortcut's working directory; if the user wants it, do it before R9 (then R9's shortcut rewrite points at the new folder) and run `pip install -e .` once more.
- **Q2. R9 step 5 rewrites `~/.claude/settings.json`** (re-installs the eight hook entries for port 17171, backup written first). Approve at cutover? The hooks are currently pointing at port 62480 and cannot reach the app either way.
- **Q3. Shim lifetime:** one minor release (removed in 0.3.0) is the default; say if `python -m assistant` should live longer.
- **Q4. Icon sign-off** happens on R4's screenshots; the rename does not block on it.

Assumptions A1–A9 in section 2 are the planner's defaults and stand unless the user objects.
