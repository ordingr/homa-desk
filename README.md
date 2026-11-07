# Assistant

A small always-on-top alert overlay for Windows. It shows a round icon on your screen and stacks cards beside it, one per
thing that needs you: a Claude Code session waiting for permission, a finished task, a reminder you set, and anything else
that can send a card. Cards blink and play a sound until you hover over them, and they clear themselves when the
session moves on.

It is written in Python, installs with pip and needs no admin rights, so it runs on managed laptops that block unknown
executables. Nothing leaves your PC.

**Status:** M1 shipped 2026-11-06. M2 (meetings and MCP card tools) is next. See [Status](#status).

## Contents

- [What it is](#what-it-is)
- [Install (Windows, no admin)](#install-windows-no-admin)
- [Using it](#using-it)
- [Reminders](#reminders)
- [Command line](#command-line)
- [File drop](#file-drop)
- [HTTP API](#http-api)
- [MCP (coming in M2)](#mcp-coming-in-m2)
- [Settings and files](#settings-and-files)
- [Troubleshooting](#troubleshooting)
- [Uninstall](#uninstall)
- [Features](#features)
- [Security & privacy](#security--privacy)
- [Status](#status)
- [Documentation](#documentation)

## What it is

- A round icon at the bottom-right of your primary monitor. Drag it anywhere; it remembers where you left it.
- Cards stack beside the icon, newest at the bottom. The icon shows a red count of open cards.
- Card colours: amber for "Claude needs you", green for "Claude finished", blue for "meeting", and purple for "reminder"
  and "custom".
- Sources that can send a card: Claude Code hooks, a command-line sender, a local HTTP API, a file drop, and your own
  reminders.

Not in this version yet: search (M3), meetings (M2), MCP tools for Claude (M2), previews, plugins (M5) and "Ask Claude"
about a file (M4). The menu entries for Search and Settings are placeholders for now.

## Install (Windows, no admin)

You need:

- Windows 10 or 11.
- Python 3.10 or newer on `PATH`, 64-bit. The checks in this release ran on CPython 3.13.1. Get Python from
  python.org and tick "Add python.exe to PATH".
- No admin rights. Nothing in the install asks for elevation, and no service or driver is installed.

Get the code (a clone, or the zip downloaded and unzipped), then from the folder:

```
cd <repo>
powershell -ExecutionPolicy Bypass -File install.ps1
```

`install.ps1` does this, for your user only:

1. Finds Python (`py -3`, then `python`) and checks that it is 3.10 or newer.
2. Runs `pip install --user --only-binary :all: -r requirements.txt`. Binary wheels only, so no build step runs.
3. Runs `python -m assistant --init`, which creates `~/.assistant` and the access token.
4. Runs `python -m assistant --doctor` and prints the report.
5. If `claude` is on your `PATH`, asks whether to show the Claude Code hook entries. Nothing is written until you confirm.
6. Creates a Startup-folder shortcut, `Assistant.lnk`, that runs `pythonw -m assistant` at logon.
7. Starts the app. Look for the round icon at the bottom-right.

Options: `-Hooks` asks about the hook entries even when `claude` is not found. `-NoStart` installs without starting.

### Connect Claude Code (hooks)

The hooks are what make Claude Code cards appear. The installer shows the entries first, then asks before writing:

```
python -m assistant hooks install --dry-run   # shows the eight entries, token redacted
python -m assistant hooks install             # writes them to ~/.claude/settings.json
```

- A backup of your Claude Code settings is written to `~/.assistant/hooks-backup/` before the first change.
- Your existing hooks and every other setting are kept with their values. The file is rewritten as indented JSON, so
  whitespace and key order may change.
- `python -m assistant hooks remove` takes the entries out again. If the file has not changed since install, it restores
  the backup exactly.
- Installed events: `PermissionRequest`, `Notification`, `Stop`, `StopFailure`, `UserPromptSubmit`, `PostToolUse`,
  `SessionStart`, `SessionEnd`. Each handler continues Claude Code's work if Assistant is not running.
- The access token is written into `~/.claude/settings.json` as plain text. See [Security & privacy](#security--privacy).

### Try it without installing anything permanent

From `<repo>`:

```
python demo.py
```

This shows sample cards with sample data. It writes only to a temporary folder that it deletes at the end. It needs a
desktop session.

## Using it

| You do | What happens |
|---|---|
| Hover over a card | It is marked seen. Its blinking and repeated sound stop. |
| Click `Snooze 5m` or `Snooze 15m` on a card | It disappears and comes back as a new card after that time. |
| Click `Dismiss` | The card closes for good. |
| Click a link on a card | It opens in your browser. Only `http://` and `https://` links open. |
| Click an extra button on a card | The press is recorded. Its effect depends on the source. |
| Drag the icon | It moves, and the new position is saved. |
| Right-click the icon | The menu below. |

Right-click menu, in this order: **Search** (placeholder for now), **Remind me…**, **Mute** (until unmuted, 30 minutes,
1 hour; becomes **Unmute** while muted), **Sound off** / **Sound on**, **Test sound**, **Reset position**,
**Settings** (placeholder for now), **Quit**.

### Card kinds

| Kind | Colour | Label | Stays until | Source |
|---|---|---|---|---|
| `waiting` | amber | `!! CLAUDE NEEDS YOU` | You act on it, or the session moves on | Claude Code permission requests and questions |
| `done` | green | `CLAUDE FINISHED` | It fades 20 seconds after you see it | Claude Code finished a task |
| `meeting` | blue | `!! MEETING` | You act on it | Meetings (M2) |
| `reminder` | purple | `REMINDER` | It fades 20 seconds after you see it | Your reminders |
| `custom` | purple | `NOTIFICATION` | It fades 20 seconds after you see it | HTTP, the command line, file drop |

- A `waiting` card shows `waiting m:ss` from the moment Claude asked.
- A `done` card says `Task completed in 6m 12s` (or `Paused — background work running` if background work is still going),
  followed by the start of Claude's last message.
- A permission card reads like `Permission needed: Bash — python upload.py`.
- Muted: the icon turns grey with a crossed-out bell. Cards keep arriving and are counted, and they appear when you unmute.
- Sound off silences sounds but leaves the cards and blinking alone. Test sound plays the custom sound once.
- Cards appear on every monitor by default. Set `cards_every_monitor` to `false` in `settings.json` to show them only on the
  icon's monitor. You need to restart after editing the file.

## Reminders

Right-click the icon, choose **Remind me…**, type one line, and press Enter. A purple card appears when the time comes.
The dialog shows how it read your text before you submit it, and has a `sticky (stay until dismissed)` checkbox.

Four shapes are understood:

| Shape | Examples | Meaning |
|---|---|---|
| `<text> in <N>m` / `h` / `d` | `driver selftest in 10m`, `stretch in 2h`, `pay rent in 3d` | N minutes, hours or days from now |
| `<text> at <h[:mm]>[am\|pm]` | `call Sam at 3pm`, `standup at 9:30`, `dentist at 15:00` | Next time that clock reading comes round. If it is already past today, tomorrow. |
| `<text> tomorrow <h[:mm]>[am\|pm]` | `dentist tomorrow 9am` | Tomorrow at that time |
| `<text> <weekday> <h[:mm]>[am\|pm]` | `review pr friday 10:15am`, `bins mon 7pm` | Next occurrence of that weekday. Today if the time is still ahead. |

- Weekday names: full names, or `mon`, `tue`, `tues`, `wed`, `thu`, `thur`, `thurs`, `fri`, `sat`, `sun`.
- Without `am` or `pm`, the time is read as 24-hour.
- `in` accepts 1 to 366 days (or the same span in hours or minutes).
- Anything else is refused with: `didn't understand — try 'text in 10m' or 'text at 3pm'`. Nothing is guessed.
- Pending reminders survive restarts. One that came due while the app was closed fires at the next start, with
  `(was due <time>)` added to its title.

Reminders are also available through the HTTP API (`POST /v1/reminders`) with the same grammar.

## Command line

Run from `<repo>` (or use `run.cmd` in place of `python -m assistant`).

```
python -m assistant                              start the app (overlay and local API on 127.0.0.1)
python -m assistant send --title "..." [--detail "..."] [--kind custom|waiting|done|meeting|reminder]
                         [--priority 1-5] [--sticky] [--url <url>]
python -m assistant shutdown                     ask the running app to quit
python -m assistant demo                         sample cards in a temporary folder
python -m assistant hooks install [--dry-run]    add the Claude Code hook entries
python -m assistant hooks remove                 remove them (restores the backup if untouched)
python -m assistant --init                       create ~/.assistant and the token if missing
python -m assistant --doctor                     environment report; exits 1 if a dependency is missing or a wheel fails
```

- `send` prints the new card's id and exits 0. If the app is not running it says so and exits 1.
- `shutdown` exits 0 whether or not the app was running.

Example:

```
python -m assistant send --title "Nightly backup" --detail "Finished in 14 minutes" --kind done
```

## File drop

Any program can make a card by writing a JSON file. Put a file ending in `.json` into `~/.assistant/events/`:

```json
{"id": "backup-2026-11-06", "title": "Nightly backup", "kind": "done", "detail": "Finished in 14 minutes"}
```

- The file body uses the same fields as the HTTP API (see [docs/api.md](docs/api.md)). Giving the same `id` again updates the card.
- The file is read once it has been unchanged for a quarter of a second, then deleted.
- A file that is too large (over 64 KB), is not JSON or has a bad field is moved to `events/rejected/`, with a
  `<name>.error.txt` beside it that says why.

## HTTP API

The app serves a local JSON API on `http://127.0.0.1:17171` (the port is in `settings.json`). Every request needs
`Authorization: Bearer <token>`, where the token is the contents of `~/.assistant/token`. Routes cover cards (create,
update, list, snooze, dismiss, events), reminders, the Claude Code hook receiver, health and shutdown.

The full route list, request fields, error shapes, limits and the hook contract are in **[docs/api.md](docs/api.md)**.

## MCP (coming in M2)

M2 adds an `assistant` MCP server, so Claude Code and Claude Desktop can post cards and add reminders with one tool
call, plus a small stdio shim for Claude Desktop. Nothing in this version listens for MCP.

## Settings and files

Everything lives in `~/.assistant` (set the `ASSISTANT_HOME` environment variable to use another folder). Copying that folder
to another machine carries your settings, cards, reminders and token. The hook entries live in `~/.claude/settings.json`,
which is not in the folder, so run `python -m assistant hooks install` on the other machine too.

| Path | What it holds |
|---|---|
| `settings.json` | Settings. Created on first run. Keys in this version: `port` (default `17171`, 1024 to 65535) and `cards_every_monitor` (optional, default `true`). Edit by hand; the Settings window comes later. |
| `token` | The bearer token. Created on first run, readable by your user only. Keep it private. |
| `state.json` | Open and recently dismissed cards, their seen and snooze state, and mute state. |
| `state_ui.json` | Icon position and sound on/off. |
| `reminders.json` | Pending reminders. |
| `assistant.pid` | pid and port of the running instance. Removed on a clean exit. |
| `hooks-install.json` | What the hook installer last changed, for `hooks remove`. |
| `hooks-backup/` | Copies of `~/.claude/settings.json` made before the hook install. Kept until you delete this folder. |
| `events/`, `events/rejected/` | The file drop folder, and files it refused. |
| `logs/app.log` | The application log. Rotates at 2 MB, keeps 4 old files. |
| `index/`, `plugins/`, `plugins-data/` | Created empty. Used by later milestones. |

Settings in the menu (**Settings**) and search (**Search**) are placeholders in this version.

## Troubleshooting

**"Assistant could not start: 127.0.0.1:17171 is not available… It is held by process N (name)."**
Another program is using the port. Close it, or change `port` in `~/.assistant/settings.json` to a free port between 1024
and 65535, then restart Assistant and run `python -m assistant hooks install` again. The hook URLs contain the port, so
they must be rewritten. When Assistant starts at logon with no console, this message appears as a message box.

**"Assistant is already running (pid N). This second copy will exit."**
A copy is already running, so the new one stops. If you cannot see the icon and the port is free, the pid file is stale:
it was left behind by a crash, and its pid now belongs to some other Python process. Quit the other Python program, or
delete `~/.assistant/assistant.pid` while Assistant is not running, then start again. The app does not clear a stale pid
file by itself yet.

**Icon is not visible.** Look at the bottom-right of your primary monitor. Right-click the icon and choose **Reset
position**. If that does not work, close Assistant, delete `~/.assistant/state_ui.json` (this also resets the sound
on/off setting), and start it again.

**Claude Code cards do not appear.**
- Run `python -m assistant --doctor`. The `port` line should say the port is in use by a Python process (Assistant running).
  The `hooks installed` line should say `yes (all 8 events)`. The `Claude Code` line should say the version meets the minimum.
- Confirm the port in the hook URL in `~/.claude/settings.json` matches `port` in `settings.json`.
- Hooks fail silently by design. If Assistant is not running, Claude Code carries on and shows nothing.
- Some waits may not send a hook event. Questions asked through `AskUserQuestion`, and plan approval, are not yet confirmed
  to do so. Probe P6 covers this and is still to run.
- A waiting card clears on the next event from that session. If Claude asks nothing else, dismiss it yourself.

**`install.ps1` says pip failed.** The install uses binary wheels only. Use a 64-bit Python 3.10 or newer, and read the pip
output above the message. A package without a wheel for your Python version will stop the install.

**`send` says "Assistant is not running on 127.0.0.1:…".** Start Assistant. The `send` command refuses to send if another
process holds the port, and says so.

**A reminder was refused.** The message gives two examples of accepted text. The grammar above lists every accepted shape.

**Logs.** `~/.assistant/logs/app.log`. Before you paste a log anywhere, check it: it contains the folder path of your
Windows profile.

## Uninstall

```
powershell -ExecutionPolicy Bypass -File uninstall.ps1
```

This stops the app, removes Assistant's hook entries from `~/.claude/settings.json` (restoring the backup if the file is
unchanged), removes the Startup shortcut, and asks before deleting `~/.assistant`. Deleting that folder also deletes
`hooks-backup/`, which holds full copies of your Claude Code settings from before the install.

The uninstall does not remove the pip packages it installed, and it does not delete the `<repo>` folder. Delete the
folder yourself when you are done. If `python` is not on `PATH`, the uninstall cannot stop the app or remove the hook
entries. It still removes the shortcut and offers to delete `~/.assistant`. Run `python -m assistant hooks remove` later,
with Python on `PATH`, to clear the hook entries.

## Features

What is in this version (M1):

- **Overlay:** round draggable icon, always on top, no taskbar button, does not take keyboard focus. Position saved and
  restored. Reset position from the menu.
- **Badge:** red count of open cards that are not snoozed. Grey icon with a crossed-out bell while muted.
- **Mute:** until unmuted, 30 minutes or 1 hour. Cards are queued and shown when you unmute.
- **Sound:** a sound per card kind, repeated every 5 seconds until hovered. Sound on/off and Test sound in the menu.
- **Cards:** four colours and labels (section above); priority 1 to 5 with priority 5 nearest the icon; snooze 5 and 15
  minutes; dismiss; up to three extra action buttons from the source; clickable `http(s)` links; `waiting m:ss` timer;
  expiry time; fade 20 seconds after being seen unless sticky; persistence across restarts.
- **Cards on every monitor** by default, with the `cards_every_monitor` setting.
- **Claude Code hooks:** one waiting card and one done card per session. Titles use the session title or the project folder
  name. Permission requests, questions, stops, stop failures and prompt-submit events are handled. Passive: never changes
  Claude's permission outcome.
- **HTTP API:** create and update cards, list, read, patch, snooze, dismiss, card event log, reminders, health, shutdown.
- **Command line:** `send`, `shutdown`, `demo`, `hooks install` and `hooks remove` (with `--dry-run`), `--init`, `--doctor`.
- **File drop:** JSON files in `events/`, with rejection and a reason file.
- **Reminders:** the four grammar shapes, a live interpretation, a sticky option, persistence, and a late fire marked
  `(was due …)`.
- **Install and uninstall:** `install.ps1` (per-user pip install, Startup shortcut, optional hooks prompt) and
  `uninstall.ps1`.
- **Safety:** one running copy at a time; a clear message when the port is in use, naming the process holding it.
- **Placeholders:** the Search and Settings menu entries exist but do nothing yet.

## Security & privacy

Facts about this version, with the source for each. The security review is in
[docs/security/2026-11-06-m1-review.md](docs/security/2026-11-06-m1-review.md).

- **Nothing leaves the PC.** No telemetry, no cloud sync, no auto-update, no background network connections. The app
  listens only on `127.0.0.1`. The only outbound HTTP code in the package is the command-line sender, and it talks to
  `127.0.0.1` with proxies disabled, so an environment proxy never receives the token.
- **Open question:** whether Claude Code itself honours an `HTTP_PROXY` setting for its hook requests to `127.0.0.1`
  is not yet checked. The check is on the list for the first run on a managed laptop.
- **Loopback only, checked on every request.** The `Host` and `Origin` headers must name `127.0.0.1` or `localhost`.
  Every route, including the hook receiver and shutdown, requires the bearer token.
- **The token** is in `~/.assistant/token`, readable by your user only. It is created on first run and never printed. The
  app, the command line and `--doctor` do not show its value.
- **The token is also in `~/.claude/settings.json`,** as plain text in the hook headers. The settings file keeps the
  permissions it inherits from your profile, not the stricter permissions of the token file. Anyone who can read that
  file can call the local API while Assistant runs on this PC. `hooks remove` deletes the entries.
- **The hook backup is in `~/.assistant/hooks-backup/`.** It is a full copy of your Claude Code settings from before the
  install, and it may contain values you keep in that file. It is never pruned. Delete it when you no longer need it.
- **The hook receiver is passive.** It answers `{}`, never returns a permission decision, and never changes what Claude does.
- **Card text is shown on screen and saved.** Permission cards show a summary of the tool input, such as a command or a
  file path. That text is in `state.json`. It is not written to `app.log`. If a command you approve contains a secret, the
  secret appears on the card and in `state.json`.
- **Cards are plain text.** Card links open only on click, and only for `http://` and `https://`.
- **Strict input handling.** JSON is strict: `NaN` and infinite numbers are refused. Body size is capped. Fields are
  validated, with length limits.
- **No admin, no service.** Install and run are per user. The Startup shortcut runs `pythonw -m assistant` from the
  `<repo>` folder. Keep that folder out of shared or world-writable locations.
- **Secrets deny-list** (PRD SEC-3) does not apply yet, because this version has no file search or preview.
- **Security review:** the review found one blocking issue: the command-line `send` and `shutdown` commands could send the
  token and card text to an environment proxy. It was fixed and re-checked. The re-check reports no blocking findings.
  Open hardening items are listed in the review file.

**For IT:** Assistant is a Python script run by the approved Python interpreter. It listens on `127.0.0.1` only and makes
no outbound network connections in this version. It downloads packages only when you run `install.ps1`, which runs pip
for the packages pinned in `requirements.txt`, as binary wheels, into your user profile, without elevation. It does not
download or run other code. The plugin folder that later versions add is not part of this version.

### Dependencies

`requirements.txt` pins 29 packages. The code imports `starlette`, `uvicorn` and `pywin32` directly. `mcp` is pinned for
M2 and not yet imported. Packages with compiled code (binary extensions) in the development environment are `pywin32`,
`pydantic-core`, `rpds-py`, `cryptography` and `cffi`. The install uses their binary wheels only, so no compiler is needed.
Pillow is present in the development environment but is not a dependency of this version.

## Status

- **M1 shipped 2026-11-06:** the overlay, the card model and store, Claude Code hooks, the HTTP card and reminder API,
  the file drop, the command-line sender, reminders, and the install and uninstall scripts. Plan: `docs/plans/2026-11-06-assistant-v1-plan.md`
  (section 7.1). Requirements: `docs/prds/2026-11-06-assistant-v1.md`.
- **Not yet observed for M1:** the on-screen visual pass; `install.ps1` and `uninstall.ps1` on a clean Windows user profile;
  the interactive hook probe (P6); the probes on the managed laptop; the overlay end-to-end tests, which need an unlocked
  desktop.
- **M2 next:** meetings from ICS calendar links and classic Outlook; the `assistant` MCP server with card and reminder tools;
  the stdio shim for Claude Desktop; Settings pages for calendars, Claude Code and MCP.
- **Later:** M3 files search and Ask notes, M4 Ask Claude and polish, M5 plugins (PRD section 15).

## Documentation

- [docs/api.md](docs/api.md): the local HTTP API, the hook contract, the card model and the file drop format.
- [docs/prds/2026-11-06-assistant-v1.md](docs/prds/2026-11-06-assistant-v1.md): requirements and acceptance criteria.
- [docs/plans/2026-11-06-assistant-v1-plan.md](docs/plans/2026-11-06-assistant-v1-plan.md): the milestone plan.
- [docs/security/2026-11-06-m1-review.md](docs/security/2026-11-06-m1-review.md): the M1 security review.
- [docs/reference/2026-11-06-assistant-popup-readme.md](docs/reference/2026-11-06-assistant-popup-readme.md): the behaviour of the
  reference build this replaces.
- [CHANGELOG.md](CHANGELOG.md): what changed, and when.
