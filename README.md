# Assistant

A small always-on-top alert overlay for Windows. It shows a round icon on your screen and stacks cards beside it, one per
thing that needs you: a Claude Code session waiting for permission, a finished task, a reminder you set, and anything else
that can send a card. Cards blink and play a sound until you hover over them, and they clear themselves when the
session moves on.

It is written in Python, installs with pip and needs no admin rights, so it runs on managed laptops that block unknown
executables. Nothing leaves your PC.

> **Correction, 2026-11-06 (M2):** from M2 on, the one exception is a calendar address you add in Settings. The app fetches that address on a timer, through your system proxy. See [Meetings](#meetings) and [Security & privacy](#security--privacy).

**Status:** M1 shipped 2026-11-06. M2 (meetings and MCP card tools) is next. See [Status](#status).

> **Correction, 2026-11-06:** M2 shipped on 2026-11-06 and M3 is next. The status sentence above is the M1 wording, kept as written. See [Status](#status).

## Contents

- [What it is](#what-it-is)
- [Install (Windows, no admin)](#install-windows-no-admin)
- [Using it](#using-it)
- [Reminders](#reminders)
- [Command line](#command-line)
- [File drop](#file-drop)
- [HTTP API](#http-api)
- [Meetings](#meetings)
- [MCP (coming in M2)](#mcp-coming-in-m2)
- [Connect Claude over MCP](#connect-claude-over-mcp)
- [Settings](#settings)
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

> **Correction, 2026-11-06 (M2):** meetings and MCP tools are in this version (see [Meetings](#meetings) and [Connect Claude over MCP](#connect-claude-over-mcp)). The Settings menu entry opens a window from M2. Search (M3) is still a placeholder, and previews, plugins and Ask Claude are still to come.

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

> **Correction, 2026-11-06 (M2):** the **Settings** entry opens the Settings window from M2 (see [Settings](#settings)). **Search** is still a placeholder.

### Card kinds

| Kind | Colour | Label | Stays until | Source |
|---|---|---|---|---|
| `waiting` | amber | `!! CLAUDE NEEDS YOU` | You act on it, or the session moves on | Claude Code permission requests and questions |
| `done` | green | `CLAUDE FINISHED` | It fades 20 seconds after you see it | Claude Code finished a task |
| `meeting` | blue | `!! MEETING` | You act on it | Meetings (M2) |
| `reminder` | purple | `REMINDER` | It fades 20 seconds after you see it | Your reminders |
| `custom` | purple | `NOTIFICATION` | It fades 20 seconds after you see it | HTTP, the command line, file drop |

> **Correction, 2026-11-06 (M2):** the `meeting` row above is live from M2. See [Meetings](#meetings).

- A `waiting` card shows `waiting m:ss` from the moment Claude asked.
- A `done` card says `Task completed in 6m 12s` (or `Paused — background work running` if background work is still going),
  followed by the start of Claude's last message.
- A permission card reads like `Permission needed: Bash — python upload.py`.
- Muted: the icon turns grey with a crossed-out bell. Cards keep arriving and are counted, and they appear when you unmute.
- Sound off silences sounds but leaves the cards and blinking alone. Test sound plays the custom sound once.
- Cards appear on every monitor by default. Set `cards_every_monitor` to `false` in `settings.json` to show them only on the
  icon's monitor. You need to restart after editing the file.

> **Correction, 2026-11-06 (M2):** the every-monitor setting no longer needs a restart. Change it on the Cards page of [Settings](#settings), or edit `settings.json` by hand; the change applies within about 5 seconds.

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

## Meetings

Assistant can show a blue meeting card a few minutes before a calendar event starts. Two kinds of calendar source are
read in this version: calendar addresses (ICS) and classic Outlook on this PC. Both are set up on the **Calendars** page
of [Settings](#settings).

**Google Calendar: secret address.** In Google Calendar, open the calendar's settings and copy the **Secret address in
iCal format**. In Settings, Calendars, paste it into the box and press **Add**. The address works like a password: anyone
who has it can read that calendar. Settings then shows only the host and `(path hidden)`, and the full address is never
written to a log. Use the `https://` form of the address. `webcal://` is converted to `https://`.

**Outlook on the web: published calendar.** Publish the calendar in Outlook on the web and copy its ICS link. Add it the
same way as a Google address.

**Classic Outlook on this PC.** Tick **Read meetings from classic Outlook**. Assistant reads the default calendar of the
Outlook installed on this PC through Outlook's COM interface, and needs no sign-in. This needs classic Outlook. The new
Outlook has no COM interface, and the Calendars page then shows `classic Outlook detected: no`. The source is on by
default in this version; see [Security & privacy](#security--privacy), item R5-F5.

> **Correction, 2026-11-06 (M2-9):** classic Outlook is now **off** by default. A `settings.json` with no `calendars.outlook`
> key reads no Outlook calendar. Tick **Read meetings from classic Outlook** on the Calendars page to turn it on (saved as
> `calendars.outlook: true`). The sentence above that says the source is on by default describes the M2 code and is kept
> as written. Source: `assistant/settings.py` (default `false`), `tests/test_m2_9.py` (`test_f5_default_outlook_is_off_with_no_settings_file`), review item R5-F5.

**Timing.** Each source is read when Assistant starts and then every **Check every** minutes (default 5, range 1 to 60).
Each read covers the next 24 hours. A meeting card appears at the start time minus **Lead time** (default 5 minutes,
range 0 to 120). If a read happens after that moment but before the start, the card appears at once. Events that have
already started, all-day events, declined events and cancelled events get no card. Changing either number applies at once.

**What the card shows.** The title is `<subject> starts in 5 minutes`, or `starts now` when the lead time is 0. The
detail is the start and end time, the location (cut to 80 characters) and the name of the join link: `Zoom`, `Teams`,
`Meet`, `Webex` or `Join`. When the event has a join link to one of those services, clicking the card opens it in your
browser. The card stays until the event ends. Attendee names and the event description are not shown.

> **Correction, 2026-11-06 (M2-9):** a join link is used only when it is `https://`, has no user name or password, uses
> port 443 or no port, and its host is one of the services below or a subdomain of one. The allow-list is `zoom.us`,
> `zoom.com`, `zoomgov.com`, `teams.microsoft.com`, `teams.live.com`, `meet.google.com` and `webex.com`. A link that fails
> the check is not used, and the card has no link for it. `zoom.com` is in the code's list but not in the M2-9 brief's list,
> so it is open for the security review to confirm. Source: `assistant/calendar/meetings.py` (`JOIN_DOMAINS`, `_is_join_url`),
> `tests/test_m2_9.py` (R5-F1 tests). Review item R5-F1.

**Duplicates.** The same event in two sources gives one card when the titles match (ignoring case and extra spaces) and
the starts are within a minute of each other.

**How the feed is read.** Daily and weekly repeats are expanded, including the interval, the weekday list, a count, an
end date and skipped dates. Monthly and yearly repeats show only their first occurrence. Time zones are resolved from the
zone name in the feed, using the tzdata package, then from the time zone definition inside the feed. If neither works,
the times are read as UTC and one warning is logged for that zone name.

**If a calendar stops updating,** see [Troubleshooting](#troubleshooting), "Calendar not updating".

## MCP (coming in M2)

M2 adds an `assistant` MCP server, so Claude Code and Claude Desktop can post cards and add reminders with one tool
call, plus a small stdio shim for Claude Desktop. Nothing in this version listens for MCP.

> **Correction, 2026-11-06 (M2):** the MCP server is in this version. The paragraph above is the M1 wording and is kept as written. The M2 details are in [Connect Claude over MCP](#connect-claude-over-mcp).

## Connect Claude over MCP

Assistant runs an MCP server named `assistant` on its own HTTP port, at `/mcp`. It offers four tools: `send_card`,
`update_card`, `dismiss_card` and `add_reminder`. Every request needs the token. The exact arguments and errors are in
[docs/api.md](docs/api.md), section 9.

**Claude Code.** Run this once in a terminal, with the token in place of `<token>`. Settings, MCP, **Copy command** fills
in both values:

```
claude mcp add --transport http assistant http://127.0.0.1:<port>/mcp --header "Authorization: Bearer <token>"
```

`<port>` is the `port` value in `settings.json` (17171 by default). Both `POST /mcp` and `POST /mcp/` (with the trailing
slash) work.

**Claude Desktop.** Claude Desktop starts a local program, so it uses the stdio shim. The shim reads the token and the port
from your Assistant home folder. Add this to `claude_desktop_config.json`, with the path to your `python.exe`:

```json
{"mcpServers": {"assistant": {"command": "<path to python.exe>", "args": ["-m", "assistant.mcp_stdio"]}}}
```

Add `"env": {"ASSISTANT_HOME": "<your home folder>"}` next to `command` and `args` only when you use a home folder other
than `~/.assistant`. Two notes. First, `-m assistant.mcp_stdio` needs Python to import the `assistant` package from the
folder Claude Desktop starts the program in. That is not yet checked on both laptops (review item R5-N11 (c)). Second, the
MCP page in Settings writes `"command": "python"` instead of the full path. If that `python` is not the one with Assistant
installed, use the full path.

> **Correction, 2026-11-06 (M2-9):** the Settings MCP page now writes `"command"` as the full path of the Python that runs
> Assistant (`python.exe`, even when the app runs under `pythonw.exe`), not the bare word `python`. You no longer need to
> replace it by hand unless you use a different Python from the one Assistant runs under. The text above is kept as
> written. Source: `assistant/overlay/settings_ui.py` (`_python_command`, `desktop_snippet`), `tests/test_m2_9.py`
> (`test_n11_*`, review item R5-N11, snippet path). The import-folder caveat in the text above is not changed.

**Finding the token.** Either open Settings, MCP, and use **Copy command** or **Copy JSON** (Copy command puts the real
token on the clipboard, and the window shows `<token hidden>`), or read the file `token` in your home folder
(`~/.assistant/token` by default). The file is readable by your Windows user only. Do not paste the token into chats,
tickets or docs. [Security & privacy](#security--privacy) lists everywhere else it is kept.

**Regenerating the token.** Settings, MCP, **Regenerate token**. Click it twice within 4 seconds. The old token stops
working at once. If the Claude Code hooks are installed, they are rewritten with the new token, and the MCP page says so.
The Claude Code HTTP entry keeps the old token, so remove it (`claude mcp remove assistant`) and add it again with the new
command. The Claude Desktop shim reads the token file on each call, so it needs no change.

**What the tools do.**

- `send_card(title, detail?, kind?, priority?, sticky?, url?, actions?, id?)` shows a card and returns its id. `kind` is one
  of `waiting`, `done`, `meeting`, `reminder` or `custom`. Passing the id of an open card replaces that card.
- `update_card(id, ...)` changes only the fields you pass.
- `dismiss_card(id)` dismisses a card. Dismissing it again is not an error.
- `add_reminder(text, sticky?)` adds a reminder from the same text as [Reminders](#reminders), and returns the id and how
  the text was read.

The tools cannot open, delete or run anything, and they do not list cards or return card content. Each call is written to
`~/.assistant/logs/mcp.log` as a shape only: the tool name, the argument names with their lengths or types, and the result
size. Argument values are never written.

## Settings

Right-click the icon and choose **Settings**. The window has six pages: **General**, **Cards**, **Calendars**,
**Claude Code**, **MCP** and **About**. A checkbox is saved as soon as you change it. A typed value is saved about 0.4
seconds after you stop typing. A value that is not allowed is shown in red and is not saved.

- **General.** Port (saved at once; it takes effect after a restart, and the note shows the port that is running). Hotkey
  (disabled; coming in M3). Play sounds for new cards (see the note under the table below). Theme (dark only).
- **Cards.** Show cards on every monitor. Mute a source: Claude Code hooks, HTTP API, MCP, File drop or Reminders.
- **Calendars.** The list of calendar addresses (host only), with Add and Remove. Read meetings from classic Outlook.
  Lead time and Check every.
- **Claude Code.** Whether the hooks are installed, with **Install hooks** and **Remove hooks** (these run
  `python -m assistant hooks install` and `python -m assistant hooks remove`). The page also states what Assistant can
  and cannot see yet: "Assistant sees permission prompts and task completion; it cannot yet see AskUserQuestion or
  plan-approval waits (hook coverage probe pending)."
- **MCP.** The Claude Code command and the Claude Desktop JSON, with **Copy command**, **Copy JSON** and **Regenerate
  token**. See [Connect Claude over MCP](#connect-claude-over-mcp).
- **About.** The version, the home and log folders, and buttons that open the home folder, the logs, this README and the
  API reference.

**Editing `settings.json` by hand.** The file is checked every 5 seconds. A change made by hand applies within about 5
seconds, without a restart, except the port. A value that is not allowed is logged as a warning, and the default is used
for that key. An invalid value inside `calendars` or `mute_sources` resets the whole section to its defaults.

**After you change the port,** run **Install hooks** on the Claude Code page, because the hook addresses contain the port,
and update the MCP command.

The keys in `settings.json`:

| Key | Default | Allowed | Notes |
|---|---|---|---|
| `schema` | `1` | `1` | Written by the app. Not read for behaviour. |
| `port` | `17171` | whole number, 1024 to 65535 | Takes effect after a restart. |
| `hotkey` | `""` | text | Reserved for M3. The Settings field is disabled. |
| `theme` | `"dark"` | text | Stored. No effect in this version. |
| `sound_on` | `true` | `true` or `false` | Written by the General page. See the note below. |
| `cards_every_monitor` | `true` | `true` or `false` | Applies at once. |
| `mute_sources.hooks`, `.http`, `.mcp`, `.file`, `.reminders` | `false` each | `true` or `false` | Applies at once. Cards from a muted source are dropped. |
| `calendars.ics_urls` | `[]` | list of addresses starting `http://`, `https://` or `webcal://` | Each address is a credential. Once the list is not empty, the file is limited to your user. |
| `calendars.outlook` | `true` | `true` or `false` | Classic Outlook on this PC. |
| `calendars.lead_minutes` | `5` | whole number, 0 to 120 | Applies at once. The PRD (AC-MT-3) says 0 to 30; see [docs/api.md](docs/api.md), section 9.10. |
| `calendars.poll_minutes` | `5` | whole number, 1 to 60 | Applies at once. |
| `claude_code` | `{}` | object | Reserved. No key is read in this version. |
| `mcp` | `{}` | object | Reserved. No key is read in this version. |

> **Correction, 2026-11-06 (M2-9):** two rows of the table above now read differently.
>
> - `calendars.outlook`: the default is `false`, not `true` (R5-F5). The `true` in the row above describes the M2 code and is
>   kept as written.
> - `calendars.ics_urls`: at most **20** addresses, each at most **2048** characters. Settings refuses a 21st address, or one
>   longer than 2048 characters. An edited `settings.json` keeps the first 20 valid addresses, drops any longer than 2048
>   characters, and logs one warning with the count. The warning does not show any address (R5-N7).
>
> Also, a bad value in one `calendars` key (for example `lead_minutes`) now falls back only for that key. The other keys,
> including the `ics_urls` list, are kept (R5-F5). The paragraph above about invalid values resetting the whole section is
> the M2 wording.

Notes on the table:

- `sound_on`: the menu item **Sound off** / **Sound on** is stored in `state_ui.json`, not here. The General checkbox
  writes this key, and nothing in this version reads it, so the checkbox does not silence sounds. This is recorded in
  docs/api.md, section 9.10, and is not fixed.
- Calendar cards come from the source `calendar`, which is not in the mute list above, so they cannot be muted by source.
- Use `https://` addresses where your calendar offers them. The code also accepts `http://`.

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

> **Correction, 2026-11-06 (M2):** the `settings.json` row above lists only `port` and `cards_every_monitor`. The full key list, with defaults and ranges, is in [Settings](#settings). The Settings window exists from M2. Search is still a placeholder.

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

> **Correction, 2026-11-06:** a stale pid file is cleared at start since M1-10. If the file names a pid that is not the Assistant listening on the recorded port, Assistant removes the file and logs a warning. Observed in the M2 security review, item R4-2. The advice above still applies when another program holds the port.

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

**Calendar not updating.** Check the calendar's state first. With your token in the header,
`curl -H "Authorization: Bearer <token>" http://127.0.0.1:<port>/v1/health` returns a `calendars` entry for each source,
with `last_ok`, `last_error` and `events`. The `last_error` text is one of these:

- `HTTP 403` or `HTTP 404`: the address was reset or revoked. Create a new secret address in Google, or copy a new ICS link
  from Outlook. Remove the old entry in Settings, Calendars, and add the new one.
- `response is not an iCalendar feed`: the address opened a web page, usually a sign-in or proxy page. Open the address in a
  browser. It should show or download text that starts with `BEGIN:VCALENDAR`.
- `feed too large`: the feed is over 8 MB.
- A network error, such as a `gaierror` or `ConnectionRefusedError` text: this PC could not reach the address. Check the
  network, the DNS name and the proxy.
- `classic Outlook is not installed`: the Outlook source is on, but this PC has no classic Outlook. Untick **Read meetings
  from classic Outlook**.
- `Outlook COM failed (...)`: Outlook refused the read. Check that classic Outlook opens and that no dialog is waiting in it.

A failed source is tried again at the next poll, and a new address is read at once. After an hour without a successful read,
one purple `NOTIFICATION` card reads `Calendar not updating: <name>`, and it goes away when the calendar reads again. No
meeting cards come from a calendar that has not read successfully in the last hour. Events that already started, or that
start more than 24 hours after the read, get no card until they fall in the window.

**406 on `/mcp`.** The MCP endpoint answers `406` when a request has no `Accept` header, or lists only `text/event-stream`.
Send `Accept: application/json, text/event-stream`, which Claude Code, the Claude Desktop shim and the MCP client all send.
`application/json` alone, or `*/*`, also works. The 406 body is a JSON-RPC error. A `401` on `/mcp` after a Regenerate means
the Claude Code entry still has the old token. See [Connect Claude over MCP](#connect-claude-over-mcp).

> **Correction, 2026-11-06 (M2-9): 405 on `/mcp`.** The 406 entry above still applies to `POST /mcp` with a wrong `Accept`
> header. A request to `/mcp` or `/mcp/` with any method other than `POST` now answers `405 Method Not Allowed` with the
> header `Allow: POST`, and a plain-text body. Before M2-9, a `GET` with `Accept: text/event-stream` opened a stream that did
> not end. The M2-9 tests check that `POST` `initialize` still returns 200 and that a real MCP client still lists the tools
> (`tests/test_m2_9.py`, `test_n2_*`). The `401` note above is unchanged.

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

> **Correction, 2026-11-06 (M2):** the **Settings** entry is live from M2. **Search** is still a placeholder.

What M2 adds:

- **Meetings:** blue meeting cards from calendar addresses (a Google secret address, a published Outlook calendar) and
  from classic Outlook on this PC. Lead time, polling, de-duplication, and auto-dismiss at the end of the event. A purple
  "Calendar not updating" card appears after an hour without a successful read.
- **MCP:** an `assistant` MCP server at `/mcp` with four card and reminder tools, a stdio shim for Claude Desktop, and a
  call log at `logs/mcp.log` that holds shapes only.
- **Settings window:** six pages (General, Cards, Calendars, Claude Code, MCP, About). Changes are saved as you make
  them. Lead time, polling, source mute and the every-monitor setting apply without a restart.
- **Token regeneration:** Settings, MCP, **Regenerate token** (two clicks). It rewrites the Claude Code hook entries when
  they are installed.
- **Health:** `/v1/health` adds `uptime_s`, `plugins` (empty until M5), `index` (null until M3) and `calendars` (one entry
  per calendar source).

> **Correction, 2026-11-06 (M2-9):** the Features list above has three changes. Classic Outlook is off by default (see
> [Meetings](#meetings)). A join link is used only if it passes the allow-list (see Meetings, and item R5-F1 under
> [Security & privacy](#security--privacy)). Calendar addresses are capped at 20 (see [Settings](#settings)).

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

> **Correction, 2026-11-06 (M2):** from M2 the app makes outbound HTTP or HTTPS requests to the calendar addresses you add, on the poll schedule and through the system proxy (see [Security & privacy](#security--privacy)). It reads classic Outlook on this PC when that source is on. It still listens on `127.0.0.1` only, and it still runs no other code. The MCP server is on the same loopback port.

### M2 additions (2026-11-06)

Source: the M2 security review, `docs/security/2026-11-06-m2-review.md` (Review 5), unless a point says otherwise.

- **The calendar addresses are the one background network path.** Each address is fetched when Assistant starts, when the
  address list changes, and then once per poll (**Check every**, 1 to 60 minutes). There is no other fetch. The fetch uses your system proxy settings, so a corporate proxy applies to
  it. It also follows redirects, and the redirect target is not limited to the calendar's own host (R5-N3, to be fixed
  before M3). The command-line sender never uses a proxy and never follows redirects (Review 3 B1, closed).
- **Classic Outlook** is read through COM on each poll while the source is on. It is on by default in this version (R5-F5).
  The read does not save, send or delete anything. Reading through the COM interface may start Outlook in the background if
  Outlook is not running. This was inferred from the interface and not observed (R5-F5).
- **Cost.** Idle CPU was 0.14 % without Outlook polling and 0.31 % with it (figures from the M2 measurement brief). The
  review records the Outlook cost as not measured, so the 0.31 % figure is not in that file.
- **Calendar addresses are credentials.** They are stored only in `settings.json`. They do not appear in `app.log`,
  `mcp.log`, `state.json`, `/v1/health` or `--doctor`. Settings shows the host with `(path hidden)`. Observed in the review
  with planted paths and query strings (AC-MT-7, SEC-12).
- **Where the token is kept.** Besides `~/.assistant/token` (readable by your user only), the token is in:
  - `~/.claude/settings.json`, as plain text in each hook header. The file keeps the permissions of your profile.
  - `~/.assistant/hooks-backup/`. A backup taken during a reinstall can hold Assistant's hook entries, and so an older
    token (R5-F4).
  - the clipboard, after **Copy command**. Windows Clipboard History and cloud clipboard sync can keep it.
  - the PowerShell command history, if you paste the `claude mcp add` line.
  - `~/.claude.json`, where `claude mcp add` stores the header in plain text.
- **Settings file.** Once `calendars.ics_urls` is not empty, `settings.json` gets a single user-only permission entry
  (R2-11). Before that it has the permissions of the home folder.
- **Card text is saved.** Meeting titles, locations and join links, and MCP card text, are saved to `state.json`, as hook
  card text is (Review 3 N3 and N11 apply). The review's log checks covered the calendar addresses and the MCP call log.
  They did not check every line of `app.log`.
- **File drop threat note (Review 3 N10).** A hard link to a file elsewhere is read by the file drop, because the check
  only skips symbolic links and junctions. Creating a hard link needs write access to the target file.
- **Known limitations, fixes scheduled before M3.**
  - R5-F1: the join-link check accepts an `@` after an allowed host, so a link labelled "Zoom" can open another site. Until
    it is fixed, do not click a join link from an invitation you do not recognise.
  - R5-F2: one malformed event in a calendar stops every meeting from that calendar.
  - R5-F3: Regenerate fails if another program has the token file open, and then the old token stays valid.
  - R5-F4: after a reinstall, `hooks remove` can restore a backup that still holds Assistant's own entries.
  - R5-F5: classic Outlook is on by default, and one invalid `calendars` value resets the whole section.
  - Also open: R5-N1 (a token holder can write a forged line into `mcp.log` through a crafted tool name), R5-N2 (`GET /mcp`
    can open streams that never end), R5-N5 (the token is masked only in tool input, not in other hook text), and R5-N7
    (no limit on the number of calendar addresses).
  - Not yet checked: whether Claude Code honours `HTTP_PROXY` for its hook requests (review item B1, tail). The user runs
    that check on the work laptop.

### M2-9 fixes to the Review 5 items (2026-11-06)

> **Correction, 2026-11-06 (M2-9):** the R5-F1 to R5-F5 items in "Known limitations, fixes scheduled before M3" above are
> moved to **fixed 2026-11-06 (M2-9)**. The list above is kept as written. Evidence: `tests/test_m2_9.py` (47 tests, qa PASS
> by observation; the full suite reported 396 passed, see [CHANGELOG.md](CHANGELOG.md)). The security re-check of M2-9 is
> not yet recorded in `docs/security/`. Details are in [docs/api.md](docs/api.md), section 10.
>
> - **R5-F1, join links (fixed 2026-11-06, M2-9).** A join link opens only if it is `https://` with no user name or
>   password, on port 443 or no port, and its host is on the allow-list in [Meetings](#meetings) or a subdomain of one.
>   Lookalikes such as `https://zoom.us@other.example/...` and `https://zoom.us:443.other.example/...` are refused.
>   `zoom.com` is on the code's list and not on the M2-9 brief's list (open for security to confirm).
> - **R5-F2, one bad event (fixed 2026-11-06, M2-9).** A malformed event is skipped alone, and the rest of its calendar is
>   read. A duration is capped at 366 days. Event dates are accepted only in the years 1970 to 2100. The skip warning gives
>   the lengths of the UID and subject, not their text.
> - **R5-F3, Regenerate under readers (fixed 2026-11-06, M2-9).** The token file is read in a way that does not block the
>   replace. Regenerate retries the replace for up to 5 seconds. If it still fails, the old token file stays in place and
>   Regenerate reports the error.
> - **R5-F4, `hooks remove` after a reinstall (fixed 2026-11-06, M2-9).** When `hooks remove` restores a backup, it first
>   takes Assistant's own hook entries out of that backup. A restore therefore never puts back a bearer token.
> - **R5-F5, classic Outlook and settings (fixed 2026-11-06, M2-9).** Classic Outlook is off by default (see
>   [Meetings](#meetings)). A bad value in one `calendars` key no longer resets the other keys. The calendar addresses are
>   capped at 20, each at most 2048 characters.
> - **Also changed by M2-9 (not in the R5-F list):** ICS redirects are followed only to `http` or `https` targets, at most 3
>   redirects (R5-N3; this replaces the "not limited to the calendar's own host" sentence above). Any method other than `POST`
>   on `/mcp` or `/mcp/` answers `405` (R5-N2; see [Troubleshooting](#troubleshooting)). Bearer-style text is masked as `***`
>   in the text of Stop, Notification and StopFailure cards, not only in tool input (R5-N5). The Claude Desktop snippet
>   carries the full Python path (see [Connect Claude over MCP](#connect-claude-over-mcp)).
> - **Still as the list above says:** the classic Outlook sentence in "M2 additions" ("on by default") describes the M2
>   code. Items in the review file that this note does not name are unchanged by M2-9.

### Dependencies

`requirements.txt` pins 29 packages. The code imports `starlette`, `uvicorn` and `pywin32` directly. `mcp` is pinned for
M2 and not yet imported. Packages with compiled code (binary extensions) in the development environment are `pywin32`,
`pydantic-core`, `rpds-py`, `cryptography` and `cffi`. The install uses their binary wheels only, so no compiler is needed.
Pillow is present in the development environment but is not a dependency of this version.

> **Correction, 2026-11-06 (M2):** `mcp` is imported from M2 (the MCP server), and `tzdata` is used for calendar time zones. The count of 29 packages above is the M1 count, and `requirements.txt` was not recounted for M2.

## Status

- **M1 shipped 2026-11-06:** the overlay, the card model and store, Claude Code hooks, the HTTP card and reminder API,
  the file drop, the command-line sender, reminders, and the install and uninstall scripts. Plan: `docs/plans/2026-11-06-assistant-v1-plan.md`
  (section 7.1). Requirements: `docs/prds/2026-11-06-assistant-v1.md`.
- **Not yet observed for M1:** the on-screen visual pass; `install.ps1` and `uninstall.ps1` on a clean Windows user profile;
  the interactive hook probe (P6); the probes on the managed laptop; the overlay end-to-end tests, which need an unlocked
  desktop.
- **M2 next:** meetings from ICS calendar links and classic Outlook; the `assistant` MCP server with card and reminder tools;
  the stdio shim for Claude Desktop; Settings pages for calendars, Claude Code and MCP.
  > **Correction, 2026-11-06:** this M2 bullet is superseded by the "M2 shipped" bullet below. It is kept as the plan wording.
- **M2 shipped 2026-11-06:** meetings from calendar addresses and classic Outlook (lead time, polling, de-duplication, the
  stale-feed card); the `assistant` MCP server at `/mcp` with four card and reminder tools; the stdio shim for Claude
  Desktop; the Settings window with six pages; the `/v1/health` fields; token regeneration. Plan: `docs/plans/2026-11-06-assistant-v1-plan.md`
  (sections 7.2, 16). Security review: `docs/security/2026-11-06-m2-review.md` (Review 5, PASS with no blocking findings).
  Not yet observed: the on-screen pass of the Settings window and meeting cards; the Claude Desktop panel on both laptops;
  the work-laptop probes (whether Claude Code honours `HTTP_PROXY` for its hooks, and classic Outlook on the work laptop).
  Five fixes are scheduled before M3 (R5-F1 to R5-F5, listed under [Security & privacy](#security--privacy)).
  > **Correction, 2026-11-06 (M2-9):** those five fixes shipped the same day; see "M2-9 fixes" under Security & privacy.
- **M3 next:** files search (the index, query grammar and popup), Ask notes, and the file tools for MCP.
- **Later:** M3 files search and Ask notes, M4 Ask Claude and polish, M5 plugins (PRD section 15).

## Documentation

- [docs/api.md](docs/api.md): the local HTTP API, the hook contract, the card model and the file drop format.
- [docs/prds/2026-11-06-assistant-v1.md](docs/prds/2026-11-06-assistant-v1.md): requirements and acceptance criteria.
- [docs/plans/2026-11-06-assistant-v1-plan.md](docs/plans/2026-11-06-assistant-v1-plan.md): the milestone plan.
- [docs/security/2026-11-06-m1-review.md](docs/security/2026-11-06-m1-review.md): the M1 security review.
- [docs/security/2026-11-06-m2-review.md](docs/security/2026-11-06-m2-review.md): the M2 security review (Review 5).
- [docs/reference/2026-11-06-assistant-popup-readme.md](docs/reference/2026-11-06-assistant-popup-readme.md): the behaviour of the
  reference build this replaces.
- [CHANGELOG.md](CHANGELOG.md): what changed, and when.
