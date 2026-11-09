# PRD: Assistant v1

- Date: 2026-11-06
- Author: product (TeamAgents)
- Status: first version. Append-only from here (archaeology rule): revisions go in dated sections at the end.
- Hand-off: tech-lead → planner. Verifiers (`qa`, `security`) accept against the acceptance criteria in this file.

## 0. Sources and how to read the trace tags

Every requirement below cites where it comes from. Nothing here was invented without a tag.

| Tag | Source |
|---|---|
| `[REF Lnn]` | `docs/reference/2026-11-06-assistant-popup-readme.md`, line nn — the behavioural spec of the work-laptop app being rebuilt. OCR'd; `[unclear]` marks in that file are real uncertainty. |
| `[OV Qn]`, `[OV Rn]` | `docs/research/2026-11-06-desktop-alert-overlay.md`, finding Qn / "Recommendations for PRD" item n. Its WPF stack recommendation is overridden by `[DEC 1]`; its hook, card-state-machine, calendar and reminder-grammar findings stand. |
| `[FS §n]`, `[FS Qn]` | `docs/research/2026-11-06-fast-file-search.md`, section n / finding Qn. Sections 11 and 12 are authoritative; section 7 is the pre-constraint version kept for the record. |
| `[PL Qn]`, `[PL Rn]` | `docs/research/2026-11-06-feature-catalog-and-plugins.md`, finding Qn / "Recommendations for PRD" item n. |
| `[DEC n]` | Decision n in section 4 of this PRD — made by the user before this PRD; not open for debate. |
| `[PM n]` | Decision n in section 16 — made by product while writing this PRD to fill a gap; flagged for the tech-lead. |

Acceptance criteria are numbered `AC-<AREA>-<n>` so QA tests and verifier verdicts can reference them.

## 1. Problem and evidence

The user runs Claude Code sessions on two Windows 11 laptops — a personal one with admin rights and a corporate-managed one with no admin and an AppLocker policy that blocks unknown executables `[REF L43-46]`. Three things go wrong today:

1. **Claude waits and nobody notices.** Claude Code pauses for permission or finishes a task; the terminal is behind other windows. The Claude desktop app only emits an OS notification when a session finishes and the user is not viewing it — nothing for permission waits, no stacking, no timer, no snooze `[OV Q1]`. Every existing Windows notifier is a fire-and-forget toast or a one-line status strip; none stacks one card per waiting session, none retracts the alert when the user answers, none offers snooze `[OV Q2]`.
2. **Finding a file by name is slow on the locked-down laptop.** Everything-class tools need admin for NTFS indexing and are themselves unknown executables under AppLocker `[FS Q1, FS Q3, FS §10.1]`. Windows Search degrades past 400k items `[FS Q7]`.
3. **The user already solved both problems once, privately, in a pure-Python app** (`assistant-popup` plus a companion `fastsearch` indexer) on the work laptop `[REF L10-21, L160-179]`. That code is not available for reuse (photos and a README only), the two tools are coupled by a script path `[REF L105-108]`, and nobody else can install or extend it. Assistant rebuilds it from scratch as something other people can install, trust, and extend.

Evidence of demand beyond the user: third-party plugins are the top request of the one launcher that lacks them (ueli #96) and every launcher with a store leads with it `[PL Q1a cross-cutting 7]`; notification-hub users ask for priority, action buttons, update-in-place and grouping — exactly the card model here `[PL Q1a cross-cutting 4]`. No open-source Windows notification hub exists `[PL Q1a notification table]`.

## 2. Users and jobs-to-be-done

| User | Situation | Job |
|---|---|---|
| **The author** (primary; two laptops, one corporate-locked) | Several Claude Code sessions open across terminals and the desktop app; meetings in a corporate calendar and a personal Google calendar; a markdown notes vault; a few hundred thousand files worth finding by name. | "Tell me the moment Claude needs me, and which session; let me find any file by name in under a second without admin; keep everything on my machine." |
| **Other Claude Code developers on Windows** (secondary) | Same waiting-on-Claude problem; may or may not have admin; may be on a managed machine. | "Install in five minutes with Python and pip, no admin, and get Claude alerts that stack and clear." |
| **Plugin authors** (tertiary; includes "Claude writing a plugin for me") | Want a card from their CI, a search over their bookmarks, a previewer for their file type. | "Drop a folder with two files, flip a switch, done — no packaging, no build step." |
| **Claude itself** (as an MCP client) | Needs to locate the user's files by name and read a safe text preview. | "One read-only tool call to find a file; never see secrets." |

Explicitly not a user in v1: a corporate IT administrator deploying centrally; a team sharing cards across machines.

## 3. Goals and non-goals

### Goals (v1)

- G1. A Claude Code permission wait or finish on either laptop produces a visible, audible, snoozable card within seconds, labelled with the project, with a live wait timer, that clears itself when the user answers `[OV Q1, OV R2]`.
- G2. Reminders and upcoming meetings arrive as cards in the same stack `[REF L59-71]`.
- G3. A hotkey or icon click finds files by name/path across the configured roots in under 50 ms per query at 2M entries, with no admin rights `[FS §11]`.
- G4. Local-only search over markdown notes stays local and says so `[REF L83]`.
- G5. Claude can search the user's files through one `assistant` MCP server with read-only tools `[DEC 7]`.
- G6. A two-file folder drop extends the app in six defined ways, with honest security language `[PL R3, PL R4]`.
- G7. Installs on both laptops with Python 3.10+ and pip, no admin, no executable of our own `[DEC 1]`.

### Non-goals (v1) `[DEC 13]`

- FILES button in the overlay (file drop for Claude).
- Cross-machine sync of anything (cards, settings, saved searches, index).
- Approving Claude permissions from a card.
- Content (inside-file) search.
- Plugin catalog or store page.
- Out-of-process plugin host.
- Windows toast fallback channel.
- Microsoft Graph calendar access.

### Never

Cloud sync, telemetry, background auto-update, run-as-admin, built-in AI chat `[DEC 13, PL R1 "Never"]`.

## 4. Decisions already made (do not reopen)

| # | Decision | Reason |
|---|---|---|
| DEC 1 | Pure Python 3.10+, pip-only dependencies, no compiled executable of our own, no admin rights. Must run on both laptops. | The work laptop's AppLocker blocks unknown executables; an approved `python.exe` running a script is allowed `[REF L43-46, FS §10.1]`. This overrides the overlay brief's WPF recommendation `[OV Q3]`. |
| DEC 2 | Files mode matches names and paths only in v1; content search is later, opt-in per folder. | Content indexing is the expensive, RAM-hungry part `[FS Q7]`; names-only is what the reference build did `[REF L82]`. |
| DEC 3 | Search UI = hotkey popup + icon click; a full-page browser UI (Edge in app mode) for previews. Previews only in the full page in v1. | tkinter cannot render Markdown/PDF; Edge is an already-approved executable `[FS §10.7]`. |
| DEC 4 | v1 extras: filter chips (Docs/Sheets/PDF/Images/Code/Folders) + modified-date filter; saved searches; right-click actions (open with, rename, properties, Recycle Bin only — never permanent delete); "Ask Claude about this file" with a what-will-be-sent confirmation. | User scope decisions relayed to the researcher `[FS §1 items 4]`; Recycle-Bin-only honours "never permanent" `[FS Q11]`. |
| DEC 5 | Query grammar keeps the reference build's habits: space = AND; bare known extension = extension filter (`design md`); extra tokens match the path (`design doc`); `-e <ext>` / `-p <pathpart>` flags; NOT is `!word`; `ext:` / `path:` as long forms. The parsed interpretation is shown next to the match-mode badge. | Preserves the user's muscle memory and avoids the `-term` NOT collision with the `-e`/`-p` flags `[REF L82, FS §12.2 items 1-3]`. |
| DEC 6 | Reminders use a hand-written grammar: `<text> in <N>[m\|h\|d]`, `<text> at <h[:mm]>[am\|pm]`, `<text> tomorrow <h[:mm]>`, `<text> <weekday> <h[:mm]>`. Unparseable input shows an inline "didn't understand" — never a silent failure. | Four input shapes do not justify a parsing library; every library needs a regex pre-pass for bare `10m` anyway `[OV Q5, OV R3]`. |
| DEC 7 | One merged MCP server named `assistant` exposing the card/reminder tools and the read-only file-search tools (`search_files`, `get_file_info`, `read_text_preview`, `list_indexed_roots`). No open/delete tools exposed to Claude in v1. Localhost + bearer token in a user-only folder (`~/.assistant/`); a stdio shim for Claude Desktop. | One daemon, one port, one token is simpler for Claude Code/Desktop configuration `[FS §12.2 item 4, FS Q13, FS §10.4]`. |
| DEC 8 | Meetings: one or more ICS URLs polled (covers the Google secret address and published Outlook calendars) plus classic Outlook COM when present. Graph API is out of v1. | ICS is the one code path covering both providers with zero consent; Graph needs tenant consent that corporate policy may block `[OV Q4, OV R4]`. |
| DEC 9 | Plugins: folder drop into `~/.assistant/plugins/<id>/` with `plugin.toml` + `plugin.py`; six extension points (card sources, card actions, search modes, result actions, previewers, declarative settings); data-only contract; plugins off until enabled; declared-not-enforced permissions with the honest sentence; three failures → auto-disable with a card; install from a pasted GitHub URL; reference plugins: timer, bookmarks, send-to-claude, a csv/ipynb previewer, and Ask-notes as a plugin. | Every large Python desktop host ships this two-file model; in-process Python cannot be sandboxed and the PRD must not pretend otherwise `[PL Q3, Q4, Q5, Q6, R2-R4]`. |
| DEC 10 | Local-only modes (Files, Ask notes) and the one cloud-calling action (Ask Claude) must look visibly different in the UI. | "Nothing leaves your PC" is a product promise of the reference build `[REF L83, FS §12.2 item 7]`. |
| DEC 11 | Notes folder is a setting (`ASSISTANT_NOTES_DIR`); no default path is hard-coded for any employer. The user's employer is not mentioned anywhere in the product or docs. | Privacy and portability. |
| DEC 12 | Card colours and behaviour follow the reference build: amber "Claude waiting" (stays), green "Claude finished" (fades after 20 s unless sticky), blue "meeting" (stays), purple "reminder/custom" (fades after 20 s unless sticky). Esc steps back: full page → normal → closed. | `[REF L59-62, L97]`. |
| DEC 13 | Not in v1: overlay FILES button (file drop for Claude), cross-machine sync, approving Claude permissions from a card, content search, plugin catalog/store, out-of-process plugin host, Windows toast fallback. Never: cloud sync, telemetry, background auto-update, run-as-admin. | Scope control; privacy stance; corporate change control `[OV R7, PL R1]`. |
| DEC 14 | Two laptops are both v1 targets; the no-admin crawl path is the only engine on the work laptop and the baseline on the personal one. | `[FS §1 item 2, FS §11]`. |

## 5. Day-one probes that gate the plan

These are not features. The planner must schedule them before any milestone is committed, and record the raw output in the plan. A probe that fails changes scope (see the "If it fails" column), it does not get worked around silently.

| # | Probe | Where | What "pass" looks like | If it fails | Source |
|---|---|---|---|---|---|
| P1 | `python -c "import win32gui, PIL"` | work laptop | exits 0 | Compiled wheels are blocked; `pywin32`-dependent features (hotkey, shell actions, Outlook COM, change watching) need a `ctypes`-only redesign — surface to tech-lead | `[FS §11 "acceptance check, day one"]` |
| P2 | `msedge.exe --app=http://127.0.0.1:<port>/` opens a chrome-less window | work laptop | window opens showing the served page | Full-page previews need another host (pywebview via pythonnet, to be probed) or drop previews on that laptop for v1 | `[FS §10.7, §11]` |
| P3 | `pip install <GitHub archive zip URL>` | work laptop | package installs through the corporate proxy | URL plugin install is folder-drop only on that laptop; document it | `[PL Q7.10]` |
| P4 | `git.exe` present on PATH | work laptop | `git --version` prints | Install and update must work from a downloaded zip, not `git clone`/`git pull` | `[PL Q7.10]` |
| P5 | Claude Code present and runnable (`claude --version`) | work laptop | version ≥ 2.1.233 printed | "Ask Claude about this file" is personal-laptop-only in v1; hooks still work if Claude Code runs there at all | `[FS §10.10 last row, OV R1]` |
| P6 | Hook probe: which hook events fire for (a) a permission prompt, (b) `AskUserQuestion`, (c) plan approval, in both the CLI and the desktop app; payloads logged | either laptop | A table of event → payload for each case | Card coverage for (b)/(c) becomes an open gap documented in the UI ("Assistant cannot see this kind of wait") | `[OV Q1 known gaps, OV Q7.2-3, OV R8]` |
| P7 | Cold-crawl time and peak RAM of a pure-Python `os.scandir` walk over the real roots with the default excludes | both laptops | Numbers recorded against the targets in section 12 | If the 2M crawl misses by more than 2x: enable the compiled accelerator on the personal laptop, tighten default excludes on the work laptop, and revise section 12 | `[FS §10.2, §11 performance]` |
| P8 | Classic Outlook (`Outlook.Application` COM class registered) vs new Outlook | work laptop | which one is installed | New Outlook only → ICS URL is the only corporate calendar path; check whether "Publish calendar" is allowed by the tenant | `[OV Q4]` |
| P9 | Idle RAM and CPU of a blank tkinter always-on-top window with a 1 Hz timer, over 10 minutes | both laptops | private working set and average CPU recorded | Informs the overlay target in section 12 | `[OV Q7.7, OV R8]` |

## 6. Feature requirements

Each area has user stories (US) and acceptance criteria (AC). "Observable" means QA can verify it by running the app, sending an HTTP request, reading a file, or looking at the screen — no reading of source code required.

### 6.1 Overlay and cards `[REF L10-73, DEC 12]`

**US-OV-1.** As the author, when any source sends a card, I see it slide in beside the icon on every monitor, blinking and sounding until I hover it, so that I cannot miss it while working in another window.
**US-OV-2.** As the author, I can snooze a card for 5 or 15 minutes or dismiss it, so that a card I cannot act on now comes back later instead of nagging.
**US-OV-3.** As the author, I can drag the round icon anywhere and have cards stack beside it where I put it.
**US-OV-4.** As the author, I can mute all cards for a period when I am presenting, and see at a glance that I am muted.

Acceptance criteria:

- AC-OV-1. The overlay is always on top of other normal windows, has no taskbar button, does not take keyboard focus when a card appears, and is click-through everywhere except on the icon and the cards `[OV R5]`.
- AC-OV-2. A round draggable icon is visible on one monitor at start. Dragging it moves it; after restarting the app it reappears within 2 px of where it was left `[REF L55, L155]`. Right-click → "Reset position" returns it to the bottom-right of the primary monitor's work area (not over the taskbar) `[REF L57, OV Q3 cross-cutting]`.
- AC-OV-3. New cards appear with a slide-in animation and stack beside the icon, newest at the bottom `[REF L20, L12]`. With "cards on every monitor" on (default), the same card is visible on every connected monitor; with it off, only on the icon's monitor `[REF L11, PL Q1b multi-monitor]`.
- AC-OV-4. Each card shows: a kind label (e.g. "!! CLAUDE NEEDS YOU", "CLAUDE FINISHED", "!! MEETING", "REMINDER"), the creation time, a title, an optional detail line, and the buttons Snooze 5m, Snooze 15m, Dismiss `[REF L65-73]`. Cards of kind `waiting` additionally show a live "waiting m:ss" counter that increments once per second `[REF L66]`.
- AC-OV-5. Card colours: `waiting` amber, `done` green, `meeting` blue, `reminder` and `custom` purple `[REF L59-60, DEC 12]`.
- AC-OV-6. `waiting` and `meeting` cards remain until the user acts or the source clears them. `done`, `reminder` and `custom` cards disappear 20 s ± 2 s after they stop blinking (i.e. after being seen) unless `sticky` is true, in which case they remain `[REF L61-62, DEC 12]`.
- AC-OV-7. A new card blinks and replays its sound at a fixed interval until the pointer hovers over it; hovering marks it "seen" and stops both permanently for that card `[REF L52]`. A snoozed card that returns blinks and sounds again as if new `[REF L53]`.
- AC-OV-8. Each card kind has a distinct sound; "Sound off" in the icon menu silences all sounds without affecting blinking; "Test sound" plays the `custom` sound once `[REF L12, L57]`.
- AC-OV-9. Snooze 5m / 15m hides the card and re-shows it 5 / 15 minutes later ± 5 s, unless the source dismissed or updated it meanwhile (an update re-shows it immediately with the new content) `[REF L53, OV R2]`. Dismiss removes it permanently `[REF L54]`.
- AC-OV-10. The icon shows a red count equal to the number of open cards (visible, not snoozed, not dismissed), and no count when zero `[REF L21]`. `[PM 1]`
- AC-OV-11. Right-click menu contains, in this order: Search, Remind me…, Mute (submenu: until unmuted / 30 min / 1 hour) or Unmute, Sound on/off, Test sound, Reset position, Settings, Quit `[REF L57]` (Settings added — `[PM 2]`).
- AC-OV-12. While muted: the icon turns grey with a crossed-out bell `[REF L21]`; incoming cards are stored and counted on the icon but not shown and make no sound; on unmute, stored cards appear as new. A timed mute ends automatically at the chosen time `[PM 3]`.
- AC-OV-13. Card state (open cards, snooze timers, mute state and end time) survives an app restart: after kill and relaunch, the same cards are shown with the same seen/snoozed state `[REF L132 "state.json", PL Q1b card history]`.
- AC-OV-14. Escape, when the overlay has focus, closes the topmost popup (search bar) first; cards are never closed by Escape `[REF L97]`.
- AC-OV-15. Per-monitor DPI: on a two-monitor setup with different scale factors, card text and the icon render at the same physical size on both and are not blurred `[PL Q1b multi-monitor "quality bar"]`.

### 6.2 Card sources `[REF L17-18, OV Q1, PL R1]`

Five sources feed the same card model (section 7): Claude Code hooks, the MCP tool, the local HTTP API, a file drop, and plugins.

**US-CS-1.** As the author, when a Claude Code session asks for permission in any terminal or in the desktop app, an amber card appears naming the project and the tool, with a timer from the moment Claude asked; when I answer, the card goes away by itself.
**US-CS-2.** As the author, when a session finishes, a green card tells me which project and what Claude last said, and fades unless I pin it.
**US-CS-3.** As a script author, I can create or update a card with one HTTP request or by dropping a JSON file in a folder, with no SDK.
**US-CS-4.** As Claude in a session, I can send the user a card or a reminder with one tool call.

Acceptance criteria — Claude Code hooks:

- AC-CS-1. Assistant can add its hook entries to the user's Claude Code user-level settings on request from the installer or Settings → Claude Code, showing the exact entries to be added before writing, writing a backup copy of the settings file first, and leaving all pre-existing entries untouched. Uninstall removes exactly the entries Assistant added `[OV R1]`.
- AC-CS-2. The hook entries cover `PermissionRequest`, `Notification`, `Stop`, `StopFailure`, `UserPromptSubmit`, `PostToolUse`, `SessionStart`, `SessionEnd`, all delivered over HTTP to the local listener with the bearer token in a header, and configured so that Claude Code continues working if Assistant is not running (connection failure is non-blocking) `[OV Q1 transport, OV R1]`.
- AC-CS-3. Assistant's hook endpoint responds within 1 s and never returns a permission decision, so Claude Code's own prompt always proceeds `[OV Q1 "must answer promptly with no decision"]`.
- AC-CS-4. A `PermissionRequest` event produces (or updates) one `waiting` card per `session_id` within 2 s of the request arriving (time to be measured — section 12), titled with the project label and a detail line naming the tool and summarising the tool input (e.g. "Permission needed: run python upload.py?") `[REF L65-66, OV Q1]`. The project label is `session_title` when present, otherwise the last path segment of `cwd` `[OV Q1 "no explicit project-name field"]`.
- AC-CS-5. The waiting timer starts from the `PermissionRequest` (or `Stop`) event's arrival time, not from the later `Notification` `[OV Q1 timer semantics, OV Q7.8]`.
- AC-CS-6. Any later event from the same `session_id` (`PostToolUse`, `UserPromptSubmit`, `PreToolUse`, `Stop`, `SessionEnd`) clears that session's `waiting` card within 2 s `[OV Q1 clearing table, OV R2]`. There is one card per session, never one per event.
- AC-CS-7. A `Stop` event produces a green `done` card for that session, with the detail line showing the first 160 characters of `last_assistant_message`, and the elapsed time since that session's last `UserPromptSubmit` when known (e.g. "Task completed in 6m 12s") `[REF L67-68, OV Q1 Stop payload]`. If the `Stop` payload shows background tasks or session crons still running, the card says "paused — background work running" instead of "finished" `[OV Q1 Stop]`.
- AC-CS-8. A `StopFailure` event produces a `waiting` card whose detail names the error class (rate limit, overloaded, authentication, billing); it clears on the next `UserPromptSubmit` or on a quota-resume notification `[OV Q1]`.
- AC-CS-9. `Notification` events with `permission_prompt`, `idle_prompt`, `elicitation_dialog`, `agent_needs_input` matchers update the existing session card's detail (they never create a second card for the same session) `[OV Q1, OV R2]`. Coverage of `AskUserQuestion` and plan approval is whatever probe P6 shows; the Settings → Claude Code page states in one sentence which waits Assistant can and cannot see.
- AC-CS-10. Minimum supported Claude Code version is 2.1.233; `--doctor` (section 6.14) reports the installed version and whether it meets the minimum `[OV R1, OV Q7.1]`.

Acceptance criteria — HTTP API and file drop:

- AC-CS-11. `POST /v1/cards` with a valid bearer token and a JSON body per section 8 shows a card within 1 s and returns its id. The same request with no token or a wrong token returns 401 and shows nothing `[REF L17, PL Q7.6]`.
- AC-CS-12. Writing a JSON file that matches the card model into `~/.assistant/events/` shows a card within 2 s; the file is deleted after it is consumed; a malformed file is moved to `~/.assistant/events/rejected/` with a `.error.txt` beside it naming the problem `[REF L18, L132 "events/"]`.
- AC-CS-13. A command-line sender (`python -m assistant send --title ... --detail ... --kind ...`) exists so a hook or script can post a card without writing HTTP code `[REF L126 notify.py]`.

Acceptance criteria — MCP as a source: see section 6.11 (AC-MCP-6, AC-MCP-7).

Acceptance criteria — plugins as a source: see section 6.12 (AC-PL-7).

### 6.3 Search shell: hotkey, popup, modes `[REF L75-98, DEC 3, FS Q10, FS §10.7]`

**US-SH-1.** As the author, I press one hotkey (or click the icon) from any app and a small search box appears beside the icon within a fraction of a second.
**US-SH-2.** As the author, I type a query and press Enter, and the box grows into a result list; Tab switches between Files and Ask notes; Escape steps back.

Acceptance criteria:

- AC-SH-1. A configurable global hotkey (default Ctrl+Alt+Space `[PM 4]`) opens the search box; clicking the icon does the same `[REF L56, L77]`. The hotkey is registered system-wide; if another application already owns it, Settings shows "already registered by another app" and the icon-click path still works `[FS Q10, FS §11 hotkey]`.
- AC-SH-2. The box appears beside the icon by default; Settings offers: beside the icon / on the monitor with the mouse / on the monitor with the focused window / on the primary monitor / where it was last `[PL Q1b multi-monitor]`.
- AC-SH-3. The search box is a rounded text field that slides out to the left of the icon; pressing Enter runs the query and grows the same box into the results window `[REF L77-78]`. Search runs on Enter, not per keystroke `[REF L78, PM 5]`.
- AC-SH-4. Two built-in modes: Files (default) and Ask notes. Tab cycles modes; a leading `?` switches to Ask notes for that query `[REF L83, PM 6]`. Plugin search modes join the Tab cycle and are selectable by their keyword prefix `[PL Q3 search mode]`.
- AC-SH-5. The current mode is shown as a labelled badge. Local-only modes (Files, Ask notes, and any plugin mode that declares no network permission) carry a "local" marker; the Ask Claude action carries a visibly different "sends to Claude" marker (different colour and icon, not just text) `[DEC 10]`.
- AC-SH-6. Up/Down move the selection; Enter opens; Escape steps back: full page → normal results → closed `[REF L94-97, DEC 12]`.
- AC-SH-7. "Full page" (button or F11) opens the full browser UI for the current query in a chrome-less Edge app-mode window; pressing F11 or Escape there returns to the popup `[REF L95-97, DEC 3]`.
- AC-SH-8. An "ignore hotkey while a full-screen application is in the foreground" option exists and defaults to off `[FS Q10 PowerToys pattern]`.
- AC-SH-9. The results header shows the result count and the index freshness ("index from <date time>" or "<n>h ago") `[REF L166, L177, FS §12.3]`.

### 6.4 Files mode `[REF L82, L160-179, DEC 2, DEC 4, DEC 5, FS §11, FS §12]`

**US-FI-1.** As the author on the work laptop, with no admin, I can find any file under my configured roots by typing part of its name, and the results come back faster than I can read them.
**US-FI-2.** As the author, `design md` gives me markdown files with "design" in the name and `design doc` narrows by folder, exactly as my old tool did.
**US-FI-3.** As the author, I can narrow with chips (Docs, Sheets, PDF, Images, Code, Folders) and a modified-date filter, and save a search I run every day.

Acceptance criteria — index:

- AC-FI-1. The index covers a user-configured list of roots per machine and excludes paths matching a gitignore-style exclude list that ships with the defaults in `[FS Q6]` (system folders, Recycle Bin, AppData caches, `node_modules`, `.git`, virtual environments, build outputs, VHDX files). Hidden and system files are excluded by default with a toggle `[FS Q6, FS §7 include/exclude]`.
- AC-FI-2. The index is built with user privileges only (no elevation prompt ever appears) and persists on disk under `~/.assistant/`; after an app restart, search works immediately from the persisted index while an incremental rescan runs `[FS Q1 "must the process stay running", FS §11]`.
- AC-FI-3. Files created, renamed or deleted under a root while Assistant runs are reflected in results within 5 s under normal load; after a bulk change (e.g. a 10,000-file checkout) results are correct within one scheduled rescan, and the freshness indicator never claims an index newer than its last completed walk `[FS Q1 lossy notifications, FS §5.2]`.
- AC-FI-4. A full rescan runs once a day by default (configurable) and on start if the last one is older than a configurable threshold; indexing runs at background CPU priority and pauses on battery by default `[FS §7 engine]`.
- AC-FI-5. Network shares and removable drives are not indexed unless added explicitly; a share that does not respond within a per-share timeout is skipped without blocking the UI `[FS Q4]`.
- AC-FI-6. Cloud-placeholder files (OneDrive Files On-Demand and equivalents) appear in results by name with a "not downloaded" marker; indexing never triggers their download `[FS Q4]`.
- AC-FI-7. Settings shows, per exclude rule, how many entries it currently excludes, so the user can tune the list `[FS §7 include/exclude]`.

Acceptance criteria — query grammar `[DEC 5]`:

- AC-FI-8. Terms separated by spaces are ANDed. Each term matches case-insensitively as a substring of the file name; when a query has two or more terms, a term that does not match the name may match the full path instead (`design doc` finds `…\docs\design-notes.md`) `[REF L82, FS §12.2 item 3]`.
- AC-FI-9. A bare token equal to a known extension (configurable list seeded with common document, code, image and data extensions), when it is not the only token, acts as an extension filter: `design md` returns only `.md` files `[REF L82, FS §12.2 item 2]`. Quoting the token (`"md"`) makes it an ordinary substring term.
- AC-FI-10. Flags: `-e <ext>` filters by extension; `-p <pathpart>` requires the path to contain the part; `ext:<a,b>` and `path:<part>` are equivalent long forms; `!word` excludes names/paths containing the word `[REF L82, DEC 5]`. `-` followed by anything other than `e`/`p` plus a value is treated as a literal substring term, never as NOT.
- AC-FI-11. `"quoted phrase"` matches contiguously including spaces; `*` and `?` are wildcards `[FS Q5 synthesis]`.
- AC-FI-12. Next to the match-mode badge ("substring") the results header shows the parsed interpretation of the query in plain words, e.g. `name contains "design" · ext: md` or `name contains "config" · path contains "Documents"` `[DEC 5, FS §12.3]`. QA verifies the five examples in `[REF L82]` parse as described there.
- AC-FI-13. Ranking: exact name match > name starts with the query > match at a word boundary in the name > substring in the name > match only in the path; ties broken by shorter name. Sort buttons Name / Ext / Date override ranking `[FS Q5 synthesis, REF L168]`.

Acceptance criteria — filters, saved searches, display `[DEC 4]`:

- AC-FI-14. Chips: Docs, Sheets, PDF, Images, Code, Folders — each a toggle that restricts results to its extension set (Folders restricts to directories); multiple chips OR together; the active chips appear in the parsed interpretation `[FS §7 filters]`.
- AC-FI-15. Modified-date filter: Today / Last 7 days / Last 30 days / custom range; applies with chips and query `[FS §7 filters]`.
- AC-FI-16. A saved search stores name, query string, chips, date filter and sort; saved searches are listed in the popup (before typing) and in the full page; selecting one runs it; they are stored per machine under `~/.assistant/` `[FS §7 filters, FS §8 Q7, DEC 13 no sync]`.
- AC-FI-17. Result rows show: a coloured extension tag (`?` for folders), the name with matched characters highlighted, the full path, and the modified date; the selected row shows a details line with full path, byte size and modified time `[REF L170-172, L177-178]`.
- AC-FI-18. Query time is shown in the header in milliseconds `[REF L167]`.

### 6.5 Ask notes `[REF L83-86, L101-104, DEC 10, DEC 11, FS §10.9]`

**US-AN-1.** As the author, I type a question and get the best-matching passages from my markdown notes, rendered as formatted markdown with my words highlighted — knowing nothing left my machine.

Acceptance criteria:

- AC-AN-1. The notes folder is read from the setting `ASSISTANT_NOTES_DIR` (environment variable or Settings field). If unset, Ask notes shows "Set your notes folder in Settings" and no default path is assumed `[REF L101, DEC 11]`.
- AC-AN-2. Results are passages ranked by a local lexical relevance method (BM25 or equivalent) over `.md` files in the folder (recursive); each result shows the file name, the passage rendered as formatted markdown (headings, bold, lists, code, tables, links) with query terms highlighted `[REF L83-86]`.
- AC-AN-3. The first query after setting the folder builds the index without freezing the UI; the UI shows "building index…" meanwhile. Subsequent queries over a folder of up to 2,000 notes return in under 200 ms (to be measured — section 12). The index is stored under `~/.assistant/` and refreshes itself when notes change `[REF L102-104]`.
- AC-AN-4. The mode badge reads "Ask notes · local" and the results footer states "search over your notes; nothing left this PC" `[REF L83, DEC 10]`.
- AC-AN-5. With a result selected: Enter opens the file; Ctrl+M opens it in MD Vault when MD Vault is configured; Ctrl+Enter reveals it in Explorer `[REF L89-92]`.
- AC-AN-6. Notes on cloud-placeholder storage are indexed only when already downloaded; the indexer never triggers downloads, and the Settings page says how many notes were skipped for that reason `[FS §12.2 OneDrive note, FS Q4]`.
- AC-AN-7. Ask notes is delivered as a bundled plugin (section 6.12) with identical behaviour; the user cannot tell the difference except that it appears on the Plugins page `[DEC 9, PL R2]`.

### 6.6 Previews (full page only in v1) `[DEC 3, FS Q8, FS §10.6, REF L173-174]`

**US-PV-1.** As the author, in the full-page view I click a result and see it rendered — markdown with diagrams, a PDF, a spreadsheet, an image, code — without opening another app.

Acceptance criteria:

- AC-PV-1. In the full page, single-click on a result shows a preview in the right-hand pane with path and size and copy/close buttons; double-click opens the file; Ctrl+double-click opens its folder; Ctrl+click multi-selects; Ctrl+right-click copies the path `[REF L173-176]`.
- AC-PV-2. Preview types in v1: Markdown (formatted, including Mermaid diagrams and Markmap mind maps, with the renderer in its strict security mode), PDF, DOCX, XLSX and CSV (as a read-only table with a row cap), images (PNG/JPEG/GIF/WebP/SVG/AVIF/BMP/ICO; HEIC shows "not supported"), code and plain text (syntax-highlighted, first 1–2 MB only, with a "show more" control); everything else shows a metadata card (name, path, size, dates, type) `[FS Q8, FS §7 preview, FS §10.6]`. PPTX shows extracted text and embedded images only `[FS §7 preview]`.
- AC-PV-3. A preview never executes content from the file: scripts in SVG/HTML/Markdown do not run (QA uses a Markdown file containing `<script>` and an SVG with an `onload` handler) `[FS §5.5]`.
- AC-PV-4. Files matching the secrets deny-list (section 11) are never previewed; the pane shows "protected file — not previewed" `[FS Q13, FS §7 include/exclude]`.
- AC-PV-5. Cloud-placeholder files are not downloaded for preview; the pane offers a "Download and preview" button instead `[FS Q4]`.
- AC-PV-6. Preview size caps: a file above a configurable limit (default 50 MB) is not loaded; the pane says so `[FS §10.6]`.
- AC-PV-7. The popup (normal size) shows no preview in v1; "Full page" is the way to one `[DEC 3]`.
- AC-PV-8. Previewer plugins (section 6.12) can add types; the host applies AC-PV-3 to AC-PV-6 to them as well `[PL Q3 previewer, PL R4]`.

### 6.7 Result actions and right-click menu `[REF L88-93, DEC 4, FS Q9, FS Q11, FS §11 actions]`

**US-AC-1.** As the author, with a result selected I can open it, reveal it in Explorer, copy its path, open it with another app, rename it, see its properties, or move it to the Recycle Bin — and nothing I can do from here deletes a file permanently.

Acceptance criteria:

- AC-AC-1. Keyboard actions on the selected result: Enter = Open; Ctrl+Enter = reveal in Explorer with the file selected (reusing an existing Explorer window where the shell allows, never spawning a new one per invocation); Ctrl+M = Open in MD Vault (when configured); Ctrl+F = list that folder's contents in the results `[REF L89-92, FS Q9, PL cross-cutting 1]`.
- AC-AC-2. Right-click (or a context-menu key) on a result opens a menu with: Open, Open with…, Reveal in Explorer, Copy path, Copy path (quoted), Rename, Properties, Move to Recycle Bin, Ask Claude about this file, plus any plugin result actions that apply `[DEC 4, FS §7 actions]`.
- AC-AC-3. Open on an executable-class file (`.lnk .url .exe .com .scr .bat .cmd .ps1 .vbs .js .hta .msi .reg`) asks for confirmation naming the file before running it `[FS Q9]`.
- AC-AC-4. Rename validates the new name against Windows rules (forbidden characters, reserved device names including with extensions, trailing space or period) and shows the reason inline on failure `[FS Q11]`.
- AC-AC-5. Move to Recycle Bin asks for confirmation, sends the item to the Recycle Bin (it is visible there afterwards), and is refused with an explanation on network and removable volumes where no Recycle Bin exists. No menu, shortcut, API or MCP tool anywhere in the product performs a permanent delete `[DEC 4, FS Q11, FS §5.11]`.
- AC-AC-6. Copy path provides the plain path and a quoted variant; long-path prefixes are stripped before copying `[FS Q9]`.
- AC-AC-7. Every action is available in both the popup and the full page with the same shortcuts `[REF L88-96, L173-179]`.

### 6.8 Ask Claude about this file `[DEC 4, DEC 10, FS Q12, FS §11 Ask Claude]`

**US-AK-1.** As the author, I can hand the file I am looking at to Claude with one action — but only after seeing exactly what will leave my machine and confirming.

Acceptance criteria:

- AC-AK-1. The action is available only when Claude Code is installed and runnable on this machine (probe P5); otherwise the menu item is present but disabled with the tooltip "Claude Code not found on this machine" `[FS §10.10, FS §11]`.
- AC-AK-2. Choosing the action opens a confirmation listing: the file path, what will be sent (by default the preview text or the first N KB of text; optionally the whole file, with the byte count shown), the extracted-text note for Office files (the extracted text is sent, not the binary), and the account type Claude Code is signed in with when it can be determined. Nothing is sent until the user clicks "Send" `[DEC 4, FS §7 Ask Claude, FS §5.9]`.
- AC-AK-3. The confirmation dialog and the action's menu entry use the "sends to Claude" marker from AC-SH-5 `[DEC 10]`.
- AC-AK-4. Files matching the secrets deny-list cannot be sent; the action is disabled for them with the reason shown `[FS §5.9 "must not wire this to a deny-list bypass"]`.
- AC-AK-5. Assistant does not store or request an API key for this action in v1; it uses the user's existing Claude Code sign-in `[PM 7, PL Q1b "built-in AI chat: skip"]`.
- AC-AK-6. The answer is shown in Assistant; a "Continue in Claude Code" control hands the same context to an interactive session `[FS §7 Ask Claude]`.
- AC-AK-7. The send is logged locally (time, path, bytes sent, route) in `~/.assistant/logs/`; no other party receives any record `[FS §7 MCP "log every call" pattern applied here, DEC 13 no telemetry]`.

### 6.9 Reminders `[REF L57, L71, DEC 6, OV Q5, OV R3]`

**US-RM-1.** As the author, I type "driver selftest in 10m" into the Remind me box and a purple card appears in ten minutes; if I type something it does not understand, it tells me immediately.

Acceptance criteria:

- AC-RM-1. "Remind me…" in the icon menu (and the `remind` keyword in the search box) opens a one-line input `[REF L57]`.
- AC-RM-2. Grammar accepted, exactly: `<text> in <N>[m|h|d]`; `<text> at <h[:mm]>[am|pm]`; `<text> tomorrow <h[:mm]>[am|pm]`; `<text> <weekday> <h[:mm]>[am|pm]` (weekday = next occurrence; today if the time is still ahead). An `at` time already past today means tomorrow. 24-hour times are accepted when no am/pm is given `[DEC 6, OV R3]`.
- AC-RM-3. As the user types, an interpretation line shows the parsed result ("Reminder 'driver selftest' at 14:32 today"); unparseable input shows "didn't understand — try 'text in 10m' or 'text at 3pm'" inline and Enter does nothing. No silent failure, no silently-guessed time `[DEC 6]`.
- AC-RM-4. A "sticky (stay until dismissed)" checkbox exists, default off `[PL Q1b reminders "sticky until done"]`.
- AC-RM-5. The reminder fires as a purple `reminder` card at the scheduled minute (within 5 s of it); title = text, detail = optional detail line `[REF L71]`.
- AC-RM-6. Reminders persist across restarts; a reminder whose time passed while Assistant was not running fires immediately on start with "(was due <time>)" appended `[REF L132 reminders.json]`.
- AC-RM-7. Pending reminders are listed (and cancellable) in Settings and via `GET /v1/reminders`; the MCP tool and HTTP API accept the same grammar and return the same interpretation or the same "didn't understand" error (section 8) `[DEC 7]`.

### 6.10 Meetings `[REF L69-70, DEC 8, OV Q4, OV R4]`

**US-MT-1.** As the author, five minutes before a meeting from my work or personal calendar, a blue card tells me what, when and where (and the join link), without my calendar provider ever being asked for consent.

Acceptance criteria:

- AC-MT-1. Settings accepts zero or more ICS URLs (each with a label) and polls each every 5 minutes by default (configurable 1–60). A fetch failure is shown as a status line in Settings, not as a card, unless it persists for more than 1 hour, in which case one `custom` card appears `[DEC 8, OV R4]`.
- AC-MT-2. When classic Outlook is installed (probe P8), an "Outlook calendar (this PC)" source can be enabled; it reads the local default calendar for the next 24 hours, including recurring instances, and needs no sign-in `[OV Q4 Outlook COM]`.
- AC-MT-3. A `meeting` card appears N minutes before start (default 5, configurable 0–30) with title "<subject> starts in N minutes", detail "<start>–<end>, <location>" and, when the event has a join URL, that URL as the card's click action `[REF L69-70, OV R4]`.
- AC-MT-4. The meeting card stays until acted on; it auto-dismisses at the event's end time `[DEC 12, OV R4]`.
- AC-MT-5. An event that appears in both an ICS feed and Outlook (same UID or same subject+start) produces one card `[PM 8]`.
- AC-MT-6. All-day events and events the user declined produce no card `[PM 8]`.
- AC-MT-7. ICS URLs are stored only in the user-only settings file and never logged in full (secret addresses are credentials) `[OV Q4 "Reset invalidates"]`.

### 6.11 MCP server `[DEC 7, FS Q13, FS §10.4, FS §11 MCP, REF L127]`

**US-MC-1.** As Claude in a Claude Code or Claude Desktop session, I can find the user's files by name, read a safe text preview, and post a card or reminder — with one configured server.

Acceptance criteria:

- AC-MC-1. One MCP server named `assistant` is served by the running app on `127.0.0.1` at a configured port, using the current Streamable HTTP transport; it requires the same bearer token as the HTTP API and rejects requests whose Host or Origin header is not loopback with 403 `[DEC 7, FS Q13 transport and security]`.
- AC-MC-2. A stdio shim (`python -m assistant.mcp_stdio`) forwards to the HTTP server so Claude Desktop can use it from its local config; Settings → MCP shows the copy-pasteable config snippets for Claude Code (`claude mcp add …`) and Claude Desktop `[DEC 7, FS §10.4]`.
- AC-MC-3. Read-only file tools, each annotated read-only and not open-world: `search_files(query, limit ≤ 500 default 50, offset, sort, filters{ext[], modified_after, folders_only})` using the same grammar as Files mode and returning compact rows (path, name, size, modified); `get_file_info(paths[] ≤ 20)`; `read_text_preview(path, max_bytes ≤ 64 KB default 16 KB, max_lines)`; `list_indexed_roots()` `[DEC 7, FS §7 MCP]`.
- AC-MC-4. `read_text_preview` refuses binaries (content sniff), refuses deny-listed paths and OneDrive placeholders not yet downloaded, strips nothing silently (it says what it refused), and wraps the text in a clearly labelled "untrusted file content" delimiter `[FS Q13 safety, FS §7 MCP]`.
- AC-MC-5. Every tool resolves the path (symlinks, junctions) before checking it against the index include/exclude rules and the deny-list; a path outside the indexed roots is refused `[FS Q13 allow-list]`.
- AC-MC-6. Card tools: `send_card(title, detail?, kind?, priority?, sticky?, url?, actions?, id?)` returning the card id, `update_card(id, …)`, `dismiss_card(id)`; and `add_reminder(text)` returning the parsed interpretation or the "didn't understand" error `[REF L127, DEC 7]`.
- AC-MC-7. No tool opens, reveals, renames, deletes or executes anything in v1 `[DEC 7]`.
- AC-MC-8. Every MCP call is logged locally (time, tool, arguments with path only, result size) in `~/.assistant/logs/`; the log is viewable from Settings `[FS §7 MCP]`.
- AC-MC-9. The token file lives in `~/.assistant/` with an ACL readable only by the current user; it is created on first run and can be regenerated from Settings (which invalidates the old one) `[DEC 7, FS §12.2 item 5]`.

### 6.12 Plugins `[DEC 9, PL Q3-Q6, PL R2-R4]`

**US-PL-1.** As a non-developer, I paste a GitHub URL (or copy a folder) into Settings → Plugins, read what it says it needs, flip it on, and it works.
**US-PL-2.** As the author, I ask Claude Code to "write me a plugin that shows a card when my NAS watcher posts an alert", and the result loads without edits because the authoring guide and a check command exist.
**US-PL-3.** As the author, a broken plugin cannot take the app down or keep silently failing.

Acceptance criteria — lifecycle:

- AC-PL-1. Plugins live in `~/.assistant/plugins/<id>/` with `plugin.toml` and the entry module named in it; `id` must equal the folder name or the plugin is rejected with that reason `[DEC 9, PL Q3 manifest]`.
- AC-PL-2. Plugins are discovered at start and on "Reload plugins" (Settings and icon menu). A plugin that fails to load is listed with its error; app start is never blocked by a plugin `[PL Q4 recommendation 3]`.
- AC-PL-3. Every discovered third-party plugin is **disabled** until the user enables it on the Plugins page `[DEC 9, PL R4]`. Bundled reference plugins ship disabled too, except Ask notes, which ships enabled because it replaces a built-in feature of the reference build and declares no permissions `[PM 9]`.
- AC-PL-4. Enabling (and every update that changes permissions or dependencies) shows: name, version, author, homepage, the file list, and the declared permissions, followed by the sentence "Assistant cannot enforce these — the plugin runs with your access." The user must confirm `[DEC 9, PL R4]`.
- AC-PL-5. Declared `[requirements]` are installed into the plugin's own `lib/` folder, pinned to exact versions, binary wheels only (no build step ever runs); if installation fails the plugin stays disabled with the pip output shown `[PL Q4 dependency handling, PL R3]`.
- AC-PL-6. Every plugin call runs with a timeout (search 2 s, preview 5 s, card-source poll 30 s `[PM 10]`); an exception or timeout is logged with the plugin id; three consecutive failures disable the plugin and show one purple card "Plugin <name> disabled after 3 errors — see Settings → Plugins" `[DEC 9, PL Q4 recommendation 3]`.
- AC-PL-7. A card-source plugin can emit, update and dismiss cards and receives button presses on its own cards; the host owns all scheduling (plugins request periodic calls; they never own threads) `[PL Q3 card source, PL Q3 Context]`.
- AC-PL-8. Install from URL: pasting `https://github.com/<owner>/<repo>` downloads the latest release asset or the default-branch archive, validates `plugin.toml`, unpacks into the plugin folder, records the source URL and version, and leaves the plugin disabled. Only `github.com` release/archive URLs are accepted; the REST API is not used without a user-supplied token `[DEC 9, PL Q6, PL R4]`. On the work laptop this depends on probe P3.
- AC-PL-9. "Check for updates" compares recorded version with the latest release and offers the update showing the version and permission diff; nothing updates without a click `[PL R4 "no silent auto-update"]`.
- AC-PL-10. Uninstall removes the plugin folder; the plugin's data folder (`~/.assistant/plugins-data/<id>/`) is kept unless the user ticks "also delete its data" `[PL Q3 data_dir]`.
- AC-PL-11. Password-type settings are stored by the host (not in the plugin folder) and handed to the plugin at runtime `[PL R4]`.
- AC-PL-12. An audit log records, per plugin, network connections, subprocess launches and file opens outside its data folder; a plugin that uses the network without declaring it triggers one `custom` card "Plugin <name> used the network but declared none" `[PL Q5 defaults 3]`.

Acceptance criteria — authoring:

- AC-PL-13. `PLUGIN_AUTHORING.md` in the repo documents the manifest, the six interfaces (section 9), the hello-world plugin, and the check command; `python -m assistant plugin new <id>` scaffolds a plugin folder; `python -m assistant plugin check <folder>` validates manifest and interfaces without loading the app `[PL Q4 non-developer path, PL Q7.8]`.
- AC-PL-14. The two-file hello world in `[PL Q4]` (one card source, one search mode) loads and works with zero edits.
- AC-PL-15. Reference plugins shipped in the repo, one per extension point at least: `timer` (card source: "timer 25m" → card at T-0), `bookmarks` (search mode over browser bookmark files; declares filesystem permission), `send-to-claude` (result action), a CSV/IPYNB previewer, and Ask notes (search mode) `[DEC 9, PL R2]`.

### 6.13 Settings `[PL Q1b settings, FS §7, DEC 11]`

**US-ST-1.** As the author, every setting lives in human-readable files in one folder I can back up by copying, and there is a Settings window so I do not have to edit them by hand.

Acceptance criteria:

- AC-ST-1. All configuration and state live under `~/.assistant/` (configurable via `ASSISTANT_HOME`): settings, token, index, notes index, saved searches, reminders, card state, plugins, plugin data, logs, events drop folder. Copying that folder to another machine and starting Assistant there reproduces the configuration (index and token are rebuilt/regenerated) `[PL Q1b "file-driven config", REF L131-132]`.
- AC-ST-2. A Settings window has pages: General (autostart, theme, sounds, hotkey, popup placement, cards-on-every-monitor), Roots & excludes (with per-rule counts), Search (extension list for bare-token filters, saved searches, notes folder), Cards (snooze presets read-only in v1, mute, per-source mute), Calendars (ICS URLs, Outlook toggle, lead time), Claude Code (hook install/remove, version check, what-can-be-seen sentence), MCP (port, token regenerate, config snippets, call log), Plugins (list, enable/disable, install from URL, reload, check for updates, per-plugin declarative settings), About (version, update check, open config folder, open logs) `[PL R1 settings]`.
- AC-ST-3. Every setting changed in the window is written to the settings file within 1 s and applied without restart except the port and the hotkey, which say "takes effect after restart" `[PM 11]`.
- AC-ST-4. Per-source mute: any source id (hooks, http, mcp, file, `plugin:<id>`) can be muted individually `[PL Q1b "group/filter/mute by source"]`.
- AC-ST-5. The notes folder, roots, and ICS URLs have no defaults that name any organisation `[DEC 11]`.

### 6.14 Install, update, uninstall `[REF L26-46, DEC 1, PL Q1b install]`

**US-IN-1.** As a developer on a managed laptop, I can install Assistant with Python and pip only, start it, and remove it completely — without admin and without asking IT for anything.

Acceptance criteria:

- AC-IN-1. Requirements: Windows 10/11, Python 3.10+ on PATH. Install = clone or download-and-unzip, then `powershell -ExecutionPolicy Bypass -File install.ps1`, which installs pip dependencies for the current user, creates a Startup-folder shortcut so Assistant starts at login, and starts it. No step asks for elevation `[REF L28-38, DEC 1]`.
- AC-IN-2. `python demo.py` (or `python -m assistant demo`) shows sample cards and the search box with sample data without installing anything permanent or writing outside a temp folder `[REF L23, L40]`.
- AC-IN-3. `uninstall.ps1` removes the Startup shortcut, removes exactly Assistant's hook entries from the Claude Code settings (AC-CS-1), stops the app, and asks before deleting `~/.assistant/` `[REF L41]`.
- AC-IN-4. `python -m assistant update` is the only update path: it fetches the latest release (via git when present, otherwise a zip download — probe P4), installs dependency changes, and shows one `custom` card "Updated to <version>" on next start. Assistant never checks for or downloads updates on its own and never shows a modal update prompt `[PL Q1b install "update check without nags", DEC 13]`.
- AC-IN-5. `python -m assistant --doctor` prints: Python version, dependency versions, whether compiled wheels import, hotkey status, port free/in use, token file present, hooks installed and Claude Code version, Outlook COM availability, index size and age, plugins loaded/disabled with reasons `[PL Q1b "doctor", PL Q7.8]`.
- AC-IN-6. The README contains a one-paragraph IT-facing statement: Assistant is a Python script run by the approved interpreter, it listens only on loopback, it never downloads or runs code without an explicit user action, and the plugin folder can be made read-only or pointed at an allow-listed path `[PL Q5 defaults 9, PL Q7.3]`.
- AC-IN-7. The README lists the pip dependencies and flags which ones contain compiled code `[FS §10.1]`.
- AC-IN-8. Install and start work on a machine that has never had Node, Git, or any Assistant version before (QA uses a clean user profile).

### 6.15 Personalisation `[REF L55-57, PL Q1b personalisation]`

- AC-PS-1. Theme: light / dark / follow system, applied to the overlay, the popup and the full page `[PL Q1b, REF L165 theme toggle]`.
- AC-PS-2. Sounds: per-kind distinct sounds, global on/off, test sound `[REF L57]`.
- AC-PS-3. Icon position remembered and resettable (AC-OV-2).
- AC-PS-4. Keyboard shortcuts for in-app actions are listed in the settings file so power users can remap them; the Settings window shows them read-only in v1 `[PL Q1b "remappable shortcuts: nice"]`.

### 6.16 Multi-monitor `[REF L11, PL Q1b multi-monitor]`

- AC-MM-1. Cards on every monitor (default) or icon's monitor only (AC-OV-3).
- AC-MM-2. Popup placement options (AC-SH-2).
- AC-MM-3. Per-monitor DPI correctness for icon, cards, popup (AC-OV-15); the full page is a browser window and follows the browser.
- AC-MM-4. Disconnecting the monitor that holds the icon moves the icon to the primary monitor's work area within 2 s; reconnecting does not move it back `[REF L155 "check the other monitor"]`.

## 7. Card data model `[PL Q3 Card, PL R1, REF L59-73]`

One model for every source. Fields:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | string | no (host assigns) | Stable identifier. Sending a card with an existing `id` updates it in place (title, detail, timer, actions) without resetting its seen state unless `renotify` is true `[PL Q1b update-in-place]`. |
| `kind` | `waiting` \| `done` \| `meeting` \| `reminder` \| `custom` | no (default `custom`) | Determines colour, default stickiness, default priority and sound `[DEC 12]`. |
| `priority` | integer 1–5 | no | Default by kind: waiting 4, meeting 4, done 2, reminder 3, custom 3. Priority 5 also sorts to the bottom (nearest the icon) of the stack. `[PL Q3, PM 12]` |
| `sticky` | boolean | no | Overrides the kind default (waiting/meeting true; done/reminder/custom false) `[DEC 12]`. |
| `title` | string ≤ 120 chars | **yes** | First line. |
| `detail` | string ≤ 500 chars | no | Second line(s); plain text in v1. |
| `url` | string | no | Default click action: opened in the default browser or with the shell `[PL Q1b click URL]`. |
| `icon` | string | no | Path or well-known icon name; plugins use a path relative to their folder. |
| `actions` | list of `{id, label, shortcut?}` ≤ 3 | no | Extra buttons rendered after Snooze/Dismiss; a press is delivered to the source (plugins via callback; HTTP/MCP clients via `GET /v1/cards/{id}/events`) `[PL Q3 CardAction]`. |
| `source` | string | host-set | `hooks`, `http:<client-name>`, `mcp`, `file`, `plugin:<id>`; drives per-source mute and the small source label on the card. |
| `timer_started_at` | timestamp | no | When set and kind is `waiting`, the card shows a live elapsed counter from this time `[OV Q1 timer semantics]`. |
| `expires_at` | timestamp | no | Card auto-dismisses at this time (used for meetings' end time) `[OV R4]`. |
| `renotify` | boolean | no | On update, blink and sound again. |
| `created_at`, `seen_at`, `snoozed_until`, `dismissed_at` | timestamps | host-set | State; returned by list calls. |

Validation failures return a field-level error; unknown fields are ignored (forward compatibility) but logged once.

## 8. HTTP API contract, `/v1` `[REF L17, L131, PL Q7.6, FS Q13]`

General rules:

- Bound to `127.0.0.1` only. Every route requires `Authorization: Bearer <token>` (401 otherwise). Requests whose `Host`/`Origin` is not loopback get 403.
- Versioned under `/v1`. Breaking changes go to `/v2`; `/v1` keeps working for at least one major release after `/v2` ships.
- JSON in and out. Errors: `{ "error": { "code": "<snake_case>", "message": "<human sentence>", "field": "<optional>" } }`.
- Successful create: 201 with the full stored object. Update: 200. Not found: 404. Validation: 422.

| Route | Purpose | Body / parameters | Returns |
|---|---|---|---|
| `POST /v1/cards` | Create, or update in place if `id` matches an open card | Card (section 7) | the card, with `id` |
| `GET /v1/cards` | List open cards (visible and snoozed) | `?source=`, `?kind=` | list of cards |
| `GET /v1/cards/{id}` | One card | — | card |
| `PATCH /v1/cards/{id}` | Partial update | any card fields | card |
| `POST /v1/cards/{id}/snooze` | Snooze | `{ "minutes": 5 }` | card |
| `DELETE /v1/cards/{id}` | Dismiss | — | 204 |
| `GET /v1/cards/{id}/events` | Button presses and state changes for a card since a cursor | `?since=<cursor>` | list of `{at, type: pressed\|seen\|snoozed\|dismissed, action_id?}` |
| `POST /v1/reminders` | Add a reminder | `{ "text": "driver selftest in 10m", "sticky": false }` **or** `{ "title", "detail?", "at": <timestamp>, "sticky?" }` | `{ id, title, at, interpretation }` or 422 `code: "didnt_understand"` with the help sentence from AC-RM-3 |
| `GET /v1/reminders` | List pending | — | list |
| `DELETE /v1/reminders/{id}` | Cancel | — | 204 |
| `POST /v1/hooks/claude-code` | Claude Code hook receiver | raw hook JSON as sent by Claude Code | 200 `{}` within 1 s; never a decision object |
| `GET /v1/health` | Liveness and status | — | `{ version, uptime_s, index: { entries, built_at, rescanning }, plugins: { enabled, disabled } }` |
| `GET /v1/search`, `GET /v1/files/{…}/preview`, `POST /v1/actions/…` | Used by the popup and the full page; same semantics as the MCP file tools and section 6.7 | planner defines fields | — |

The file-drop format (AC-CS-12) is the `POST /v1/cards` body as a `.json` file.

## 9. Plugin manifest and the six interfaces `[DEC 9, PL Q3]`

### 9.1 Manifest `plugin.toml`

| Table / key | Required | Meaning |
|---|---|---|
| `[plugin] id` | yes | lowercase letters, digits, hyphens; equals the folder name; never changes |
| `[plugin] name`, `version` (semver), `author`, `description` | yes | shown on the Plugins page and the consent dialog |
| `[plugin] homepage` | no | shown on consent |
| `[plugin] min_assistant_version` | yes | plugin is not loaded on older hosts; shown as the reason |
| `[plugin] entry` | yes | the Python module file inside the folder |
| `[provides] card_sources`, `card_actions`, `search_modes`, `result_actions`, `previewers` | at least one list non-empty | class names in the entry module |
| `[permissions] network` (list of hosts or `["*"]`), `filesystem` (list of roots), `subprocess` (bool), `clipboard` (bool) | no (default none) | **declared, displayed, logged — not enforced** |
| `[requirements] packages` | no | exact-pinned pip packages installed to the plugin's `lib/` |
| `[[settings]]` with `key`, `type` (text / password / bool / int / choice / folder / file), `label`, `default`, `description`, `options` | no | rendered by the host; frozen type set — add, never remove `[PL Q7.9]` |

### 9.2 The six extension points — what a plugin gives, what it gets

All exchanges are plain data (strings, numbers, lists, dictionaries / dataclasses). Plugins never receive or create UI objects, threads or timers; the host renders, schedules and persists `[PL Q3 design rule]`.

| Extension point | Plugin gives | Plugin gets |
|---|---|---|
| **Card source** | `start(ctx)`, `stop()`, `on_action(card_id, action_id, ctx)` | a context with: `emit(card) → id`, `update(id, …)`, `dismiss(id)`, `every(seconds, fn)` (host scheduler), `log(msg)`, `settings` (values from `[[settings]]`), `data_dir` (survives updates), `send_to_claude(text, path?)` |
| **Card action** | `actions(card) → [CardAction]` for cards it wants a button on (filter by kind/source), `run(card, action_id, ctx)` | the card data and the same context |
| **Search mode** | `keyword`, `label`, `search(query, ctx) → [Result]`, `run(result, action_id, ctx)` where `action_id="open"` is Enter | the query string after the keyword; results carry `title`, `subtitle`, `icon`, `path?` (enables the built-in file actions and previewers), `url?`, `data` (opaque, returned to `run`), `score` 0–100, optional `preview` |
| **Result action** | `id`, `label`, `shortcut`, `applies(result) → bool`, `run(result, ctx)` | the selected result from any mode and the context |
| **Previewer** | `extensions` tuple, `render(path, ctx) → Preview{kind: markdown\|text\|html\|image\|table, content, truncated}` | the path; the host has already applied the deny-list, size caps and placeholder check before calling, and sanitises `html` before rendering |
| **Declarative settings** | the `[[settings]]` entries in the manifest; optional `on_settings_changed(ctx)` | current values via `ctx.settings`; password values decrypted at runtime, never written to the plugin folder |

The hello world is the two-file example in `[PL Q4]` and must load unchanged (AC-PL-14).

## 10. Success metrics (observable)

| # | Metric | How measured | Target |
|---|---|---|---|
| M1 | Permission-wait coverage | 20 trials across CLI and desktop app on each laptop: card appears and clears | ≥ 19/20 appear within 2 s; ≥ 19/20 clear within 2 s of the answer |
| M2 | Replacement of the reference build | the author uninstalls `assistant-popup` and `fastsearch` on the work laptop | within 14 days of milestone 3 and does not reinstall them |
| M3 | Search latency | 50 random 2–6 character queries over the real work-laptop index | p95 ≤ 50 ms query time as shown in the header |
| M4 | Cold crawl | probe P7 numbers recorded | within the section 12 targets or revised with evidence |
| M5 | MCP usefulness | a Claude Code session asked "find my file about X" | locates it with one `search_files` call in ≥ 8 of 10 trials |
| M6 | Plugin authoring | Claude Code, given `PLUGIN_AUTHORING.md`, is asked for a plugin that posts a card from a JSON file | loads and works with zero manual edits in ≥ 4 of 5 trials |
| M7 | Privacy | a loopback-only check (`netstat`/proxy log) over a working day | zero outbound connections except: configured ICS URLs, user-triggered GitHub plugin installs/updates, user-confirmed Ask Claude sends |
| M8 | Stability | 5 working days running on both laptops | zero crashes; idle CPU and RAM within section 12 |

## 11. Privacy and security requirements

- SEC-1. **Loopback only.** The HTTP API and the MCP server bind to `127.0.0.1`; `Host`/`Origin` checked; bearer token on every route including the hook receiver `[FS Q13, DEC 7]`.
- SEC-2. **Token handling.** Token in `~/.assistant/` with a user-only ACL; never in the repo folder, never in a URL that is logged, never printed by `--doctor` (which prints "present"/"missing"). The browser full page receives it once at launch and keeps it in memory only. Regeneration invalidates the old token immediately `[FS §12.2 item 5, AC-MC-9]`.
- SEC-3. **Secrets deny-list** applied uniformly to Files-mode results, previews, MCP results and previews, and Ask Claude: default patterns `.env*`, `*.pem`, `*.key`, `*.pfx`, `*.p12`, `*.ppk`, `*.kdbx`, `id_rsa*`, `id_ed25519*`, `.aws/credentials`, `.git-credentials`, `.netrc`, `.npmrc`, `.pypirc`, kubeconfig files, `.docker/config.json`, browser profile directories, password-manager data directories; plus a content check for `-----BEGIN … PRIVATE KEY-----` on any preview text `[FS Q13]`. Matching files are excluded from results by default; a setting "show protected files by name in local search" (default off) lists them in the local UI only, still never previewed, never returned over MCP, never sent to Claude `[PM 13]`. The list is user-editable (append only in Settings; removals require editing the file by hand).
- SEC-4. **Paths are resolved before checks.** Symlinks and junctions are resolved and the resolved path is checked against roots, excludes and the deny-list `[FS Q13]`.
- SEC-5. **Previews are inert.** No script execution from previewed content; HTML paths sanitised; diagram renderer in strict mode; Office/PDF parsing isolated from the app's own UI context; byte and line caps `[FS §5.5, AC-PV-3]`.
- SEC-6. **Executable confirmation.** Opening executable-class files asks first (AC-AC-3) `[FS Q9]`.
- SEC-7. **No permanent delete anywhere** (AC-AC-5, AC-MC-7) `[DEC 4]`.
- SEC-8. **Plugins:** off until enabled; declared-not-enforced permissions with the honest sentence; pinned binary-only dependencies; audit log of network/subprocess/file use with an undeclared-network card; password settings host-held; no silent updates; URL installs from github.com release/archive URLs only `[DEC 9, PL R4]`. The PRD, README and UI never use the word "sandboxed" for plugins `[PL Q7.2]`.
- SEC-9. **Ask Claude** is the only feature that sends file content off-machine; it requires per-send confirmation showing exactly what is sent; deny-listed files cannot be sent; sends are logged locally `[FS §5.9, AC-AK-2, AC-AK-4, AC-AK-7]`.
- SEC-10. **No telemetry, no background network.** The only unattended outbound traffic is ICS polling of user-configured URLs `[DEC 13, M7]`.
- SEC-11. **Hook receiver is passive.** It never returns a permission decision and never modifies Claude Code behaviour `[OV Q1, AC-CS-3]`.
- SEC-12. **Credential-like settings** (ICS secret URLs, plugin passwords) are stored in the user-only settings area and redacted in logs and `--doctor` `[AC-MT-7, AC-PL-11]`.
- SEC-13. **Security review** runs before the merge of every milestone that touches: the HTTP/MCP surface, the hook receiver, the plugin loader, the preview pipeline, the shell actions (project rule: `security` verifier on input parsing, secrets, new endpoints).

## 12. Performance targets — all "to be measured", none assumed

Numbers from `[FS §11]` for search; overlay numbers are product's proposal `[PM 14]` pending probes P7 and P9. The planner records measured values from **both** laptops in the plan; a miss by more than 2x triggers the fallbacks named in P7.

| Target | Value | Source |
|---|---|---|
| First cold crawl, 0.3M entries | ≤ 30 s | `[FS §11]` |
| First cold crawl, 2M entries | ≤ 5 min | `[FS §11]` |
| Warm full rescan, 0.3M / 2M | ≤ 15 s / ≤ 2.5 min | `[FS §11]` |
| Incremental rescan on start | ≤ 10 s typical | `[FS §11]` |
| Query, substring over 2M names | ≤ 50 ms | `[FS §11]` |
| Idle RAM of the daemon at 2M entries | ≤ 300 MB | `[FS §11]` |
| Idle CPU (daemon + overlay, no cards) | ≈ 0 % averaged over 10 min | `[FS §11, OV Q7.7]` |
| Overlay process idle RAM with one ticking `waiting` card | ≤ 80 MB (proposal) | `[PM 14, OV Q3 PySide row as upper reference]` |
| Hook event → card visible | ≤ 2 s | `[PM 14]` |
| Hotkey → search box visible | ≤ 300 ms | `[PM 14]` |
| Ask notes first index (≤ 2,000 notes) / later queries | ≤ 10 s in background / ≤ 200 ms | `[REF L102-103 "about 6 s", "milliseconds"]` |
| Reminder / meeting card timing accuracy | within 5 s of schedule | `[AC-RM-5, AC-MT-3]` |

## 13. Risks

| # | Risk | Effect | Mitigation in this PRD | Source |
|---|---|---|---|---|
| R1 | No "user answered" hook event; clearing is inferred from later events | A card may linger if the user answers and Claude produces no event for minutes | AC-CS-6 clears on *any* later event; probe P6 verifies; the card shows "waiting" honestly, with Dismiss always available | `[OV Q7.2]` |
| R2 | `AskUserQuestion` / plan approval may fire no hook | Those waits are invisible | P6 decides; Settings states the gap in one sentence (AC-CS-9) | `[OV Q7.3]` |
| R3 | Claude Code hook behaviour is version-gated and moves fast | Breakage on update | Minimum version pinned (AC-CS-10); planner budgets a hooks-contract test | `[OV Q7.1]` |
| R4 | Pure-Python crawl and RAM at 2M entries unmeasured | Targets missed on the real machines | P7 before commitment; fallbacks named | `[FS §10.2, §11]` |
| R5 | Compiled wheels or Edge app mode blocked on the work laptop | Hotkey/shell actions/previews need redesign | P1, P2 first; failure routes named | `[FS §10.1, §10.7]` |
| R6 | Change notifications are lossy | Stale results after bulk operations | Scheduled rescan + honest freshness indicator (AC-FI-3) | `[FS §5.2]` |
| R7 | Plugins are arbitrary code as the user | Exfiltration risk on the work laptop; policy risk | Off by default, consent with honest sentence, audit log, IT paragraph (SEC-8, AC-IN-6) | `[PL Q5, Q7.3]` |
| R8 | In-process plugins: hard crash or UI-thread block not contained | App crash from one plugin | Timeouts, three-strike disable; data-only contract keeps a v2 subprocess host possible | `[PL Q7.4]` |
| R9 | Ask Claude sends content off-machine | Conflicts with the privacy stance | Per-send confirmation, deny-list, logging (SEC-9) | `[FS §5.9]` |
| R10 | ICS feed latency unpublished; new Outlook has no COM | Late or missing meeting cards on the work laptop | P8; poll interval configurable; status line in Settings | `[OV Q7.9, OV Q4]` |
| R11 | Default hotkey collides with PowerToys, IMEs, Citrix/RDP full-screen | Hotkey silently swallowed | Remappable, "already registered" message, icon click always works, ignore-in-fullscreen option | `[FS §5.8, FS Q10]` |
| R12 | Reference build details are OCR'd photos | Rebuilding "wrong" habits | Each `[REF]` citation is behaviour, not code; `[unclear]` items are decided in section 16 and flagged | `[FS §5.13]` |
| R13 | Vendored dependency collisions between in-process plugins | One plugin breaks another | Loud log line; per-plugin `lib/`; accepted for v1 | `[PL Q4 in-process consequence]` |

## 14. Open questions

Questions product could not resolve; each is assigned to a probe or to the tech-lead. None is silently assumed.

1. Which hook events fire for `AskUserQuestion` and plan approval, in CLI and desktop? → P6 `[OV Q7.3]`.
2. Does Claude Code run on the work laptop at all? → P5. Decides whether Ask Claude and even hooks exist there `[FS §10.10]`.
3. Classic or new Outlook on the work laptop; is "Publish calendar" allowed by the tenant? → P8 `[OV Q4]`.
4. Does `pip install <github zip>` pass the corporate proxy; is `git.exe` present? → P3, P4 `[PL Q7.10]`.
5. Is Claude Desktop used on both laptops (determines whether the stdio shim is day-one or later)? → tech-lead to ask the user.
6. Does the existing `fastsearch` index need migrating? The brief says its format is unknown and a fresh build should be planned; confirm the user accepts a fresh build `[FS §12.3]`.
7. Should the Everything accelerator (personal laptop only) ever be built, given P7's numbers? Deferred to after P7 `[FS §8 Q2]`.
8. Does the user want the "show protected files by name in local search" toggle (PM 13) or strict exclusion only? → tech-lead to confirm with the user.
9. The `?` prefix for Ask notes is `[unclear]` in the reference; PM 6 adopts it — confirm with the user.
10. Mute semantics (PM 3: cards queued and shown on unmute) — confirm with the user; the alternative is "cards shown silently".

## 15. Phased delivery suggestion (for the planner to weigh)

Each milestone leaves a usable app on both laptops and tests the riskiest assumption still open. Order follows the tech-lead's steer: overlay + Claude hooks + reminders, then file search, then plugins.

| Milestone | Scope (AC groups) | Risk it retires | Usable outcome |
|---|---|---|---|
| **M0 — Probes and skeleton** | P1–P9; `~/.assistant/` layout; token; settings file; `/v1/health`; `--doctor`; `demo` | R4, R5, R2 | Numbers in hand; the plan can commit |
| **M1 — Overlay that replaces the reference popup** | 6.1 overlay; 6.2 hooks + HTTP + file drop + CLI sender; 6.9 reminders; 6.14 install/uninstall/autostart; section 7 and 8 contracts | R1, R3 | Author uninstalls the old popup's alert half; other developers can install |
| **M2 — Meetings and MCP card tools** | 6.10 meetings; 6.11 card/reminder MCP tools + stdio shim; 6.13 Settings pages for calendars, Claude Code, MCP | R10 | Claude sessions can post cards; meetings arrive |
| **M3 — Files mode and Ask notes** | 6.3 shell; 6.4 Files; 6.5 Ask notes (built-in first); 6.6 previews in the full page; 6.7 actions; 6.11 file tools; SEC-3 deny-list; 6.13 roots/excludes/search pages | R6, R11 | Author uninstalls `fastsearch`; M3 metric starts |
| **M4 — Ask Claude and polish** | 6.8 Ask Claude; 6.15 personalisation; 6.16 multi-monitor; update command; README feature list and IT paragraph | R9 | Feature-complete for a single user |
| **M5 — Plugins** | 6.12 host, six extension points, URL installer, consent, audit log, authoring guide and scaffold, reference plugins; Ask notes refactored into a plugin | R7, R8, R13 | Others can extend; M6 metric starts |

M3 is the largest; the planner may split it into index + popup (M3a) and full page + previews + actions (M3b). M5 may begin design work earlier because the card model (section 7) and the data-only contract are fixed from M1.

## 16. Decisions made by product in this PRD (flagged for the tech-lead)

| # | Decision | Why | Where |
|---|---|---|---|
| PM 1 | Icon badge counts open, non-snoozed, non-dismissed cards (seen or not) | Matches "shows a red count while cards are open" `[REF L21]`; answers `[OV Q7.10]` | AC-OV-10 |
| PM 2 | "Settings" added to the icon menu | A settings window exists (AC-ST-2) and needs an entry point | AC-OV-11 |
| PM 3 | Mute = cards queued and counted, shown on unmute; Sound off = silent but shown | The reference offers both mute and sound-off, so they must differ `[REF L57]` | AC-OV-12, open question 10 |
| PM 4 | Default hotkey Ctrl+Alt+Space | Researcher's recommendation avoiding Win+*, Alt+Space, F12, Ctrl+Space `[FS §11 hotkey]` | AC-SH-1 |
| PM 5 | Search on Enter, not as-you-type | Reference behaviour `[REF L78]`; keeps plugin latency budget comfortable `[PL Q7.5]` | AC-SH-3 |
| PM 6 | `?` prefix switches to Ask notes | `[REF L83]` marks it unclear; adopted as the most likely reading | AC-SH-4, open question 9 |
| PM 7 | Ask Claude uses the user's Claude Code sign-in; Assistant stores no API key in v1 | Avoids key handling, consistent with "no built-in AI chat" `[PL Q1b]`; the CLI route is documented `[FS Q12]` | AC-AK-5 |
| PM 8 | Meeting de-duplication across sources; no cards for all-day or declined events | Otherwise two sources produce two cards for one meeting | AC-MT-5, AC-MT-6 |
| PM 9 | Bundled plugins ship disabled except Ask notes | Reconciles "plugins off until enabled" `[DEC 9]` with "built-in modes stay on" `[PL Q5 defaults 1]` | AC-PL-3 |
| PM 10 | Plugin timeouts: search 2 s, preview 5 s, card poll 30 s | Researcher's proposed numbers "for the PRD to confirm" `[PL Q4 recommendation 3]` | AC-PL-6 |
| PM 11 | Port and hotkey changes take effect after restart; everything else live | Keeps v1 simple; both are rare changes | AC-ST-3 |
| PM 12 | Priority defaults by kind; priority 5 sorts nearest the icon | Gives `priority` a visible effect without changing the reference's colour/sticky rules | section 7 |
| PM 13 | Deny-listed files excluded from results by default, with an off-by-default toggle to show them by name locally only | Follows the tech-lead's "applied to results/previews/MCP" and the researcher's §7 while leaving `[FS §8 Q3]` answerable by the user | SEC-3, open question 8 |
| PM 14 | Overlay performance proposals (80 MB, 2 s, 300 ms) | No brief gives overlay numbers for Python; these are starting targets for P9 to confirm | section 12 |

## 17. Traceability check

Every AC above carries at least one of `[REF]`, `[OV]`, `[FS]`, `[PL]`, `[DEC]` or `[PM]`. ACs tagged only `[PM]` are the fourteen decisions in section 16 and nothing else. No requirement specifies a framework, schema or endpoint implementation beyond what `[DEC 1-14]` already pin (Python, pip, Edge app mode, `plugin.toml`, `/v1/cards`, the four MCP file tools). No organisation is named anywhere in this document.

## 18. Amendment 2026-11-08: product name Homa

Supersedes: nothing. Sections 0 to 17 are kept as written. Where they say `assistant`, `Assistant`, `~/.assistant/` or
`ASSISTANT_*`, read the amendment below.

- Decision: the product is called Homa. The decision, its alternatives and the migration are in
  `docs/plans/2026-11-08-rename-to-homa.md` (sections 3, 4.1 and 4.5). This amendment records the effect on the requirements.
  It changes no requirement, acceptance criterion or decision in sections 3 to 16.
- DEC 7: the MCP server is named `homa` (was `assistant`). The Settings snippets register `homa`, and the server reports
  `serverInfo.name` as `homa`. The transport, the one port, the one bearer token and the stdio shim are unchanged. The shim
  module is `homa.mcp_stdio`. `python -m assistant.mcp_stdio` remains as a 0.2.x alias and is removed in 0.3.0. The user-only
  folder named in DEC 7 is `~/.homa/` (was `~/.assistant/`).
- DEC 11: the notes-folder setting is `HOMA_NOTES_DIR` (was `ASSISTANT_NOTES_DIR`). `ASSISTANT_NOTES_DIR` is still read, with a
  deprecation warning, until 0.3.0. The rule that no default path is assumed and no organisation is named is unchanged.
- AC-MC-1 and AC-MC-2: the server is `homa`, and the stdio shim is `python -m homa.mcp_stdio`.
- AC-AN-1 and AC-ST-5 (notes folder): the variable is `HOMA_NOTES_DIR`, as in DEC 11.
- AC-FI-16, AC-MC-9 and the other `~/.assistant/` mentions (including the token and saved searches): the folder is `~/.homa/`,
  or the folder named by `HOMA_HOME`. An existing `~/.assistant/` is moved there by the first start of Homa. Rules are in the
  README section "Upgrading (2026-11-08)".
- Unchanged: the card kinds and their defaults, the routes, the token rule, the file tools and their limits, the index format
  (its format tag is still `assistant-index`, so an existing index is read as it was), and every privacy rule.
- Verification: pending. qa (R5a and R5b, `tests/test_rename_complete.py`) and security (R6) have not reported as of this
  amendment.
