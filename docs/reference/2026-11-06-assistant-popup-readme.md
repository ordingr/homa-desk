# Reference: assistant-popup README (work laptop build)

Transcribed 2026-11-06 from photos of the README of the user's existing
`assistant-popup` project on their work laptop. The text was OCR'd, so
this is a cleaned reconstruction. Garbled lines are rebuilt from context,
and anything uncertain is marked *[unclear]*. No code from that project
was copied. This file is the behavioural spec Assistant rebuilds against,
alongside the photos described at the end.

## What it is

A desktop alert popup for Windows. Cards appear on every monitor, slide
in, blink until you hover over them, and play a different sound for each
type. A round, draggable icon lives on your screen: drag it anywhere
(cards stack beside it), click it to search, right-click it for more.

Anything can send it a card: Claude Code hooks, a Claude MCP tool, a
local HTTP API, or a plain file drop.

Cards stack with the newest at the bottom. The round icon turns grey with
a crossed-out bell when muted, and shows a red count while cards are open.

Preview without installing: `python demo.py`, or open `mockup.html` in a
browser (sample data only).

## Install

Requires Windows 10/11 and Python 3.10+ on PATH. No admin rights needed.

```
git clone <this repo>
cd assistant-popup
powershell -ExecutionPolicy Bypass -File install.ps1
```

`install.ps1` installs `pillow` and `pywin32`, adds an autostart shortcut
in your Startup folder, and starts the assistant. Look for the round bell
icon at the bottom-right of your screen.

- Try without installing anything permanent: `python demo.py`
- Remove: `powershell -ExecutionPolicy Bypass -File uninstall.ps1`

### Why Python and not an exe

Many managed machines block unknown executables (AppLocker). A script run
by an already-approved Python is allowed.

## Using it

| You do | What happens |
|---|---|
| Hover over a blinking card | Marks it "seen": the blinking and repeat sound stop |
| Click Snooze 5m / Snooze 15m | The card comes back after that time |
| Click Dismiss | The card closes |
| Drag the icon | Moves it; the position is remembered |
| Click the icon | Opens the search bar (see Search below) |
| Right-click the icon | Search, Remind me…, mute / unmute (until unmuted, 30 min, 1 hour), sound on/off, test sound, reset position, quit |

**Card colours:** amber = Claude waiting, green = Claude finished, blue =
meeting, purple = reminder/custom. Amber and blue cards stay until you
act. Green and purple fade after 20 s unless you made them sticky.

Example cards from the running app:

- **!! CLAUDE NEEDS YOU**: "my-project is waiting for you",
  "Permission needed: run python upload.py?", with a live "waiting m:ss"
- **CLAUDE FINISHED**: "Nightly build finished", "Task completed in 6m
  12s *[unclear]*, 125 tests passing"
- **!! MEETING**: "Standup starts in 5 minutes", "10:00–10:15, Room 4B
  *[unclear]*, Zoom"
- **REMINDER**: title plus optional detail line

Every card has Snooze 5m, Snooze 15m and Dismiss, plus the creation time.

## Search: find files or ask your notes

Click the icon. A small rounded text box slides out to the left of it.
Type and press Enter, and the same box grows into the results window.

| Mode | What it does |
|---|---|
| **Files** (default) | Lists files from your fastsearch index by file name. Use several words: `design md` finds .md files with "design" in the name (a file type such as md, py, pdf or yml filters). `design doc` narrows by path. Flags work too: `config -e yml`, `report -p Documents`. It matches names, not text inside files; use Ask for that. |
| **Ask notes** (start with `?` *[unclear]*, or press Tab) | Finds the best-matching passages in your local markdown notes and shows them formatted, with the words you asked about highlighted. It is a search, not a written answer from an AI model, and nothing leaves your PC. |

Markdown files are shown formatted (headings, bold, lists, code, tables,
links), not as raw text.

**With a result selected:**

- Open (Enter)
- MD Vault (Ctrl+M): shows the note in the MD Vault viewer
- Explorer (Ctrl+Enter): selects the file in a folder window
- Fastsearch folder (Ctrl+F): lists that folder's contents
- Up/Down chooses, Tab switches mode, Esc closes
- Full page (button or F11, optional) makes the results window fill the
  screen; press again to go back
- Esc steps back: full page, then normal size, then closed

**Configuration:**

- Notes folder: `ASSISTANT_NOTES_DIR` (default is a folder under the
  user's OneDrive `Documents`). The first question builds a search index in a separate process (about
  6 s), saved in `~/.assistant-popup/`. Later questions answer in
  milliseconds and the index refreshes itself when notes change.
- Files mode uses the fastsearch tool at `ASSISTANT_FASTSEARCH` (default
  `~/ds/projects/fastsearch/fastsearch.py` *[unclear]*) and its index.
  Without it, Files mode searches file names in the notes folder only.
  The results are only as fresh as the fastsearch index *[sentence cut off]*.

More ways to send cards (HTTP API, Claude MCP tool, Claude Code hooks)
are in `docs/INTEGRATIONS.md`.

## Files

| File | Purpose |
|---|---|
| `assistant.py` | The long-running app: collects cards from every source and shows them |
| `toast_ui.py` | The cards: look, animation, blinking, stacking on every monitor |
| `icon_ui.py` | The draggable icon, its menu and the reminder dialog |
| `search_ui.py` | The search bar and results window opened by clicking the icon |
| `panel.py` | The smooth rounded panel (blue frame, white ring, shadow) the search lives in |
| `localmd.py` | Searches your markdown notes (BM25 ranking, saved index) |
| `mdrender.py` | Shows markdown formatted inside the preview |
| `gfx.py` | Shared drawing helpers: smooth layered windows, fonts |
| `sounds.py` | Generated alert sounds (no audio files) |
| `notify.py` | Command-line sender and Claude Code hook entry point |
| `mcp_server.py` | MCP server so a Claude session can send cards and reminders |
| `demo.py`, `mockup.html` | A preview of the cards, using sample data only |
| `install.ps1`, `uninstall.ps1`, `run.cmd` | Install, remove, start |

Runtime files created next to the app (not committed): `token.txt`,
`state.json`, `reminders.json`, `mute`, `nosound`, `events/`.

## For AI coding agents

- **Claude Code:** the skill `run-assistant-popup`
  (`.claude/skills/run-assistant-popup/`) starts the app, sends cards,
  takes screenshots and clicks the icon through `driver.py`.
- **GitHub Copilot (VS Code):** `.github/copilot-instructions.md` gives
  the project rules, and `.github/prompts/run-assistant-popup.prompt.md`
  runs the same checks.

## Documentation

- `docs/DESIGN.md`: design document with diagrams (context, components,
  flows, state machines, decisions, security)
- `docs/ARCHITECTURE.md`: how it works and why it is built this way
- `docs/INTEGRATIONS.md`: HTTP API, MCP tools, Claude Code hooks, calendar
- `CHANGELOG.md`: what changed in each version

## Troubleshooting

| Symptom | Fix |
|---|---|
| No icon after install | Check the other monitor, or delete `state.json` and restart to reset its position |
| *[second row unreadable]* | *[unclear]* |

---

## Companion: fastsearch (separate tool, from photos)

The work laptop also runs **fastsearch**, an Everything-like file finder
that the popup's Files mode calls:

- Browser UI at `localhost:5180`. The header shows the entry count
  ("0.3M entries"), index age ("18h ago"), Settings, About, a theme
  toggle and Filters.
- Query bar with result count, query time ("42.3 ms") and a match-mode
  badge ("substring"). Sort by Name / Ext / Date.
- Result rows: coloured extension tag (`.html`, `.md`, `.png`, `.txt`,
  `?` for folders), name with the matched term highlighted, full path,
  and date.
- Right-hand preview pane rendering Markdown formatted, with path and
  size, plus copy/close buttons.
- Mouse: Click = preview, Dbl-click = open file, Ctrl+dbl-click = open
  folder, Ctrl+click = select (multi), Ctrl+right-click = copy path.
- In the popup's Files mode: "80 file(s) | index from 2026-11-05", a
  compact result list (name + folder), a details line (full path, bytes,
  modified time) and the action buttons listed above.
