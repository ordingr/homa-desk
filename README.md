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

> **Correction, 2026-11-07 (M3a):** M3a shipped 2026-11-07: Files search in the popup, Ask notes, and the read-only file tools for Claude over MCP. M3b (the full page and previews) is next. See [Status](#status).
>
> **Correction, 2026-11-07 (M3b):** M3b shipped 2026-11-07 with the full page (Microsoft Edge in app mode), previews and result actions. Its security review is still pending, and M4 (Ask Claude, which is a stub in this build) is next. See [Full page and previews](#full-page-and-previews), [Actions on a file](#actions-on-a-file) and [Status](#status).

> **Correction, 2026-11-07 (commit 3d7bc25):** the features listed under "What 3d7bc25 adds" in [Features](#features) are in the code at 3d7bc25 (commit subject: `feat: M4 full page, path search, live search, theme, rounded UI, hooks safety, bounded shutdown`). Several earlier sections now carry correction notes for them. The text above is kept as written.

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
- [Search files](#search-files)
- [Ask notes](#ask-notes)
- [Full page and previews](#full-page-and-previews)
- [Actions on a file](#actions-on-a-file)
- [Roots, excludes and protected files](#roots-excludes-and-protected-files)
- [Claude and your files](#claude-and-your-files)
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

> **Correction, 2026-11-07 (M3a):** Files search and Ask notes are in this version (see [Search files](#search-files) and [Ask notes](#ask-notes)). Previews, plugins and Ask Claude are still to come.
>
> **Correction, 2026-11-07 (M3b):** the full page, previews and result actions are in this version (see [Full page and previews](#full-page-and-previews) and [Actions on a file](#actions-on-a-file)). Plugins (M5) are still to come. Ask Claude (M4) is a confirmation stub in this build and sends nothing.

> **Correction, 2026-11-07 (M3):** Search is live. The search box opens beside the icon from the hotkey (default `ctrl+alt+space`) or from the menu, and the full page opens from the box's **Full page** button or F11. See [Search files](#search-files) and [Full page and previews](#full-page-and-previews). The sentence above is kept as written.

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

Options: `-Hooks` asks about the hook entries even when `claude` is not found. `-NoStart` installs without starting. `-NoShortcut` skips the Desktop shortcut.

> **Correction, 2026-11-07 (commit 3d7bc25):** `install.ps1` also creates a Desktop shortcut, `Assistant.lnk`, by running `scripts\install-shortcut.ps1`. The shortcut starts `pythonw -m assistant` with no console window, using `assistant\assets\assistant.ico`. It uses `.venv\Scripts\pythonw.exe` in the repo when that file exists, and otherwise the `pythonw.exe` beside the `python` found on `PATH`. Steps 6 and 7 above are otherwise unchanged. `uninstall.ps1` removes only the Startup shortcut, so delete the Desktop `Assistant.lnk` by hand.

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

> **Correction, 2026-11-07 (M3a):** **Search** opens the search box from M3a (see [Search files](#search-files)). The sentence above is kept as written.

> **Correction, 2026-11-07 (M3):** **Search** is live: the popup from the hotkey or from this menu entry, and the full page from the popup (**Full page** button or F11). See [Search files](#search-files) and [Full page and previews](#full-page-and-previews). The sentence above is kept as written.

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

## Search files

Press **Ctrl+Alt+Space** (the default hotkey) or choose **Search** from the icon's menu. A search box slides out beside the
icon. Type a query and press **Enter**; the box grows into a result list. Search runs on Enter, not on every keystroke.
The hotkey and the icon both toggle the box, so pressing either again closes it.

> **Correction, 2026-11-07 (commit 3d7bc25):** the popup now searches while you type. A query runs 120 ms after the last keystroke (`assistant/overlay/search_ui.py`, `LIVE_DELAY_MS`), so the sentence "Search runs on Enter, not on every keystroke" above is out of date. **Enter** opens the selected result.

**The box.** At the left is the mode badge: `Files · local` or `Ask notes · local`. **Tab** switches between the two. A
leading `?` runs one query in Ask notes. Below the text, the results header shows the count, `index from <date time>`,
the elapsed time in milliseconds, and the interpretation of your query. Then come the chips and the date filter:

- Chips: **Docs**, **Sheets**, **PDF**, **Images**, **Code**, **Folders**. Click to toggle. Several chips match any of
  their types.
- **Modified:** Any time, Today, 7 days, 30 days. Click to cycle, or press **Ctrl+D** with results showing.
- **★ Save** saves the current query as a saved search, named after the query.

Each result row shows a coloured extension tag (`?` for folders), the name with the matched letters highlighted, the
folder, and the modified date. The row you select shows a details line with the full path, the size in bytes and the
modified time.

The buttons under the results are **Open (Enter)**, **MD Vault (Ctrl+M)**, **Explorer (Ctrl+Enter)**, **Fastsearch folder
(Ctrl+F)** and **Full page (F11)**. In M3a only the popup itself is wired: **Open, MD Vault, Explorer, the folder key and Full
page do nothing yet**. They are connected in M3b. Pressing Enter on an Ask notes result also does nothing yet, for the same
reason.

> **Correction, 2026-11-07 (M3b):** the buttons are connected in M3b. **Open** runs the Enter action; **MD Vault** and **Explorer** run their actions; **Full page** opens the full page (F11 does the same from the box). **Fastsearch folder** asks for the folder listing, but the popup does not show that listing in this build (see [Actions on a file](#actions-on-a-file)). Enter on an Ask notes result is wired to **Open** as well (not observed by doc-keeper). The paragraph above is kept as written.

**The grammar.** Words are ANDed. The first word must appear in the file name, and later words may appear anywhere in the
full path. A word that is a known extension (`md`, `pdf`, `py`, `docx` and so on) filters by extension. Five examples,
with the interpretation the box shows:

| You type | It finds | Interpretation shown |
|---|---|---|
| `design md` | `.md` files with "design" in the name | `name contains "design" · ext: md` |
| `design doc` | files with "design" in the name and "doc" in the path, such as a `docs` folder | `name contains "design" · path contains "doc"` |
| `config -e yml` | `.yml` files with "config" in the name | `name contains "config" · ext: yml` |
| `report -p Documents` | files with "report" in the name and "Documents" in the path | `name contains "report" · path contains "Documents"` |
| `design` | files with "design" in the name | `name contains "design"` |

Other forms: `!draft` leaves out anything with "draft" in its path. `"quoted phrase"` matches the exact words. `ext:md,py`
and `path:docs` are the long forms of `-e` and `-p`. `*` and `?` are wildcards, but they are not anchored, so `rep*` is the
same as `rep`. `doc` is deliberately not an extension word: `design doc` looks for "doc" in the path, so use `design -e doc`
for Word files. The full grammar, the caps and every interpretation string are in
[docs/search-grammar.md](docs/search-grammar.md).

> **Correction, 2026-11-07 (commit 3d7bc25), which words must match where:** every plain word and every quoted phrase may match the file name **or** the full path. The first word no longer has to be in the name. Results where every word matches the name rank first, and path-only matches follow. When a word is too common to search by its letters, the search matches names only and the result is marked **truncated**: the popup says `truncated — refine the query`, and the full page shows `partial: refine the query`. The rules are in [docs/search-grammar.md](docs/search-grammar.md) section 12. The sentence "The first word must appear in the file name" above is superseded.

**Chips and date filter** combine with the query: a chip or date limits the results further. The popup has no sort control in
M3a; results are ranked exact name, then name start, then word start, then inside the name, then path only, with shorter
names first among equals.

> **Correction, 2026-11-07 (M3b):** the popup now has a **Sort** control in its results header (Best, Name, Ext, Date; Ctrl+S cycles it), and a saved search's sort is applied when you recall it. The popup's date control still offers presets only; a custom date range can be entered on the full page. The text above is kept as written.

**Saved searches** are stored in `~/.assistant/saved_searches.json`, per machine. When the box is open and empty they are
listed; click one or press Enter on the highlighted one to run it. Press **Delete** on a highlighted saved search to remove
it. Saving the same name replaces the old one. There is a limit of 200. Saved searches keep the query, the chips and the
date filter. They also keep a sort value, but the popup does not apply it yet.

**Esc** steps back: from results to the empty box (your text stays), then from the box to closed. The full-page step
arrives in M3b.

> **Correction, 2026-11-07 (M3b):** the full-page step is in the Edge window, not in the popup. In the page, Esc clears the selection, then the query and results, and closes the window when the page is empty. The popup's Esc goes from the action menu or folder listing, to results, to the box, to closed. Saved searches apply their stored sort when recalled. The text above is kept as written.

**Where the box appears.** Settings, Search, "Where the search box appears": beside the icon (the default), on the monitor
with the mouse, on the monitor with the focused window, on the primary monitor, or where it was last. Drag the box to
move it; "where it was last" remembers the place you dropped it.

**The hotkey.** Settings, Search, Hotkey. The default is `ctrl+alt+space`. A hotkey needs at least one of Ctrl, Alt, Shift or
Win, plus a key, for example `ctrl+alt+f10` or `win+shift+s`. Leave the field empty to turn the hotkey off. Changes apply at
once. The status line after the field says what happened:

- `registered`: the hotkey works.
- `already registered by another app`: another program holds that combination. Choose another one. The icon still opens search.
- `unsupported combo`: the code refuses it. Refused combinations are a bare key, a Win-only combination such as `win+s`,
  Alt+F4, Alt+Tab, Win+L, Ctrl+Alt+Delete and Ctrl+Esc.
- `disabled`: the field is empty.

The hotkey is a system-wide registration (`RegisterHotKey`). It is not a keyboard hook and does not see what you type. The
option "Ignore the hotkey while a full-screen app is in front" is off by default. When it is on, the hotkey does nothing while
a full-screen window is in front. The icon still works.

**What is local.** The index, the search, the saved searches, the popup and Ask notes never leave the PC. The only network
traffic in these features is reading a network folder that you added as a root. The file-reading tools for Claude are
described in [Claude and your files](#claude-and-your-files).

## Ask notes

Ask notes searches the `.md` files in a notes folder you choose, and shows the best-matching passages with the matched words
highlighted. Press **Tab** in the box to switch to it, or start the query with `?`.

- **Set the folder** in Settings, Search, **Notes folder** (use **Browse…** or type a full path such as
  `C:\Users\<user>\Notes`). An environment variable `ASSISTANT_NOTES_DIR`, if set, overrides the setting. With no folder set,
  the box says **Set your notes folder in Settings**. No default folder is assumed.
- **First build.** The box shows `building index…` while the notes are read. The index is stored in
  `~/.assistant/index/notes.json.gz`. The folder is checked again every 60 seconds, so a changed note appears within about a
  minute.
- **What is read.** Only `.md` files, in the folder and its sub-folders. Folders starting with a dot, `node_modules`,
  `$RECYCLE.BIN`, `System Volume Information`, and links and junctions are skipped. Files over 2 MB are skipped.
- **Cloud placeholders** (a note that a sync service has not downloaded) are never opened. They are not downloaded either.
  Settings, Search says how many placeholder notes were skipped.
- **Results.** Up to three passages from each note, ranked by a local BM25 score, with headings, bold text, lists, code and
  tables rendered. Each result shows the file name. The footer reads **search over your notes; nothing left this PC**.
- **Protected names.** Notes whose names match the secrets list are not returned (see
  [Roots, excludes and protected files](#roots-excludes-and-protected-files)). Their text is still stored in the notes index
  file in your Assistant folder. That file is user-only, but the note text is in it. This is an open item (R7-N5).

## Full page and previews

The full page is the larger search window. It opens the query from the popup in Microsoft Edge in app mode, a window with no
address bar or tabs. Files are previewed only there. The popup never shows a preview (PRD AC-PV-7).

**How to open it.** Press **F11** in the search box, or choose **Full page (F11)** under the results. Assistant starts Edge with
its own profile folder, `%LOCALAPPDATA%\Assistant\edge-profile`, so your own browser profile is not used. Opening the full page
again closes the window it opened before and opens a new one.

**How to close it.** Press **F11** or **Esc** in the page. Esc first clears the selection, then the query and the results, and
closes the window when the page is empty. Assistant closes only the window it opened.

**What it needs.** Microsoft Edge in the standard Program Files folder. If Edge is not found, nothing opens; see
[Troubleshooting](#troubleshooting). The work-laptop check of Edge app mode (probe P2) is still to run, so previews there are not
confirmed yet.

**What the page has.** A status line with the result count, the index freshness, the elapsed time in milliseconds, the mode badge
and the query's interpretation. A query bar with the chips, a modified-date filter (any time, today, 7 days, 30 days, or a custom
range from two dates), a sort control (Best, Name, Ext, Date) and saved searches. Result rows as in the popup. A preview pane beside
the list when a row is selected. A narrow layout applies below 800 pixels of width; it has not been checked on a phone. Light and
dark themes; the page remembers the choice. The Settings and About links in the header say that those settings are in the
Assistant app.

**Mouse.** Click a row to select it and show its preview. Double-click opens the file. Ctrl+double-click reveals it in Explorer.
Ctrl+click adds or removes a row from the selection, and Shift+click selects a range. Right-click opens the action menu;
Ctrl+right-click copies the path.

**Keys.** Up and Down move, and Shift with them extends the selection. Home and End jump to the ends. Enter opens. Ctrl+Enter
reveals. Ctrl+M opens in MD Vault. Ctrl+S, and Ctrl+Shift+S, change the sort. Shift+F10 or the Menu key opens the action menu. Esc
and F11 are described above. The page has no Ctrl+C, Delete, F2, Alt+Enter, Ctrl+F or Ctrl+D. Use the menu for Copy path, Move to
Recycle Bin, Rename and Properties. The folder listing is in the popup only.

> **Correction, 2026-11-07 (commit 3d7bc25):** the page's query bar also does the following, from `web/src/App.jsx` at 3d7bc25. A query runs 150 ms after your last change, so no Enter is needed. Tab accepts the grey completion when one is shown: it completes the last word you typed from the first result's name or from a saved search's name. The address bar keeps the query as `q=`, and the popup opens the page with its query. The full page hides the search popup when it opens (`assistant/app.py`, `_open_fullpage`). The header's theme button cycles through Dark, Light and System and saves the choice to Assistant's settings (`theme` in `settings.json`, through `POST /v1/settings/theme`), so the popup follows it; the page reads the saved theme when it loads. Previews, Markdown included, zoom from 80 to 200 percent (default 100): use the buttons in the pane, or Ctrl+=, Ctrl+- and Ctrl+0 while the pane has focus. The zoom is remembered in the page's browser profile.

**What each file type shows.**

| File | What the pane shows |
|---|---|
| Markdown (`.md`, `.markdown`) | Formatted text, with Mermaid diagrams and Markmap mind maps. Raw HTML in the Markdown is not rendered as HTML (no raw-HTML plugin is used). Images are shown as their alt text, not loaded. The first 256 KB, then **Show more** (1 MB at a time). |
| HTML (`.html`, `.htm`, `.xhtml`) | The HTML after sanitising (scripts removed), inside a sandboxed frame. The same windows as Markdown. |
| Text and code (`.txt`, `.log`, `.py`, `.json`, `.ipynb` as JSON, and other text files) | Syntax highlighting for the languages it knows (off above 1 MB). The first 256 KB, then **Show more**. |
| CSV and TSV | A table of up to 500 rows. The server reads up to 1,000 rows and says when there are more. |
| XLSX and XLSM | A table of the first sheet, up to 500 rows. The sheet names are listed. |
| DOCX | The text. |
| PPTX | The slide text and the names of the embedded images. Slides are not drawn. |
| PDF | Rendered by pdf.js, loaded from the same address (no CDN). |
| PNG, JPEG, GIF, WebP, AVIF, BMP, ICO | The image. |
| SVG | The image, with no scripts and no outside loads. Up to 8 MB. |
| Anything else (for example `.zip`, `.doc`, `.odt`, `.tif`) | A card with the name, path, size, dates, type and the reason. |

**Never previewed, and why.**

- **Protected files**, the names and folders listed under [Roots, excludes and protected files](#roots-excludes-and-protected-files).
  The pane says `Protected file - not previewed`. Their content is not sent to the page (SEC-3).
- **A file whose content has a private-key header** in the part being shown. The same message.
- **Cloud placeholders that are not downloaded.** Assistant never opens them. See **Download and preview** below.
- **Executables and scripts** (`.exe`, `.dll`, `.sys`, `.msi`, `.msp`, `.bat`, `.cmd`, `.ps1`, `.scr`, `.com`). The card gives the
  reason `executable file: not previewed`. They are not run and not rendered.
- **Other binary files**, and files whose content looks binary: a card. **HEIC and HEIF** give the reason `HEIC images are not
  supported`.
- **Folders**, and files inside Assistant's own folder.
- **Files over the cap** (below).

The rule behind this list is that a preview shows what a file contains and never runs it. Scripts in SVG, HTML and Markdown do not
run. Mermaid runs in its strict mode, and its output is cleaned before it is shown.

**The 50 MB cap.** A file larger than `preview.max_bytes` is not loaded. The pane says `File too large to preview` and gives the
cap. The default is 50,000,000 bytes (about 50 MB). You can set it from 1 MiB to 4 GiB by editing `settings.json` (there is no
Settings field); the change applies within about 5 seconds. SVG files have their own limit of 8 MiB. Office files are unpacked in
memory and stop at 50 MiB unpacked.

**Download and preview.** A cloud placeholder is a file that a sync service keeps online only (for example with OneDrive Files
On-Demand). The pane shows `Cloud placeholder - not downloaded` with a **Download and preview** button. In this build the button
does not download anything. Assistant never fetches a placeholder, so the card then says that a download was requested and that
Assistant did not download the file. Download the file with your sync app, then search again.

## Actions on a file

Actions run on the selected result, in the popup or in the full page. They run on this PC only. Assistant has no action that deletes
a file for good (see **Recycle Bin only** below).

| Action | Popup | Full page | Notes |
|---|---|---|---|
| Open | Enter | Enter, double-click | Programs and scripts ask first. |
| Open with… | menu | menu | The Windows "Open with" dialog. |
| Reveal in Explorer | Ctrl+Enter | Ctrl+Enter, Ctrl+double-click | Uses an Explorer window that already shows the folder; otherwise opens one. |
| Copy path | Ctrl+C | Ctrl+right-click | The plain path, without the `\\?\` prefix. |
| Copy path (quoted) | menu | menu | The path in double quotes. |
| Rename… | F2 | menu | The name rules are below. |
| Properties | Alt+Enter | menu | The Windows Properties window. |
| Move to Recycle Bin | Delete | menu | Asks first. Recycle Bin only. |
| Open in MD Vault | Ctrl+M | Ctrl+M | See **MD Vault** below. |
| Folder listing | Ctrl+F (button labelled "Fastsearch folder") | not in the page | Popup only. Lists the items in the selected file's folder, with protected names left out. See the note below. |
| Ask Claude about this file | menu | menu | Marked "sends to Claude". A stub in this build: nothing is sent. |

**Results.** The page shows each action's result as a short message. The popup does not show results in this build. An action's
outcome goes to `~/.assistant/logs/app.log`, and only failures are written there. The folder listing from Ctrl+F does not appear
in the popup yet, for the same reason. Both are recorded in `docs/api.md` section 12.9.

**Confirmations.** These ask first and name the file:

- Open on a program or script. The list is `.lnk .url .exe .com .scr .bat .cmd .ps1 .vbs .vbe .js .jse .wsf .hta .msi .msp .reg .jar .cpl .pif`.
  Opening runs it.
- Move to Recycle Bin.
- MD Vault on a program or script, when no MD Vault address is set.
- Ask Claude. It shows how many bytes would be sent.

**Recycle Bin only.** Move to Recycle Bin uses the Windows shell, so the item stays in the Recycle Bin and can be restored there.
Assistant has no permanent delete and no code path that deletes a file for good. The move is refused, with the reason shown, on:

- network paths (a UNC path, or a mapped network drive), and removable, optical, RAM disk and unknown drive types;
- any drive whose file system is not NTFS or ReFS (FAT32 and exFAT, for example).

Indexed root folders cannot be renamed or moved to the Recycle Bin from here (`that is an indexed root folder`).

**Rename rules.** A new name is refused, with the reason shown, when it is empty or only spaces; longer than 255 characters; has a
control character; contains any of `< > : " / \ | ? *`; ends with a space or a period; or is a reserved device name (`CON`, `PRN`,
`AUX`, `NUL`, `CONIN$`, `CONOUT$`, `COM1` to `COM9` and `LPT1` to `LPT9`, with or without an extension). It is also refused when a
file of that name already exists in the folder.

**Protected files.** The code treats these files as your own, so Open, Open with, Reveal, Copy path, Rename, Properties, Move to
Recycle Bin, MD Vault and the folder listing work on them. Two things stop: Ask Claude refuses them, and folder listings leave them
out. Opening a protected file hands it to its default app, which is outside Assistant's previews. Whether actions should work on
protected files is a decision still to confirm.

**MD Vault.** Ctrl+M, and the menu item, use the MD Vault address in `settings.json`, if you set one:

```
"md_vault": {"url": "https://..."}
```

With an `http://` or `https://` address, the action opens that address in your default browser. MD Vault has no open-file link in
this build, so you pick the note inside MD Vault. Without an address, the note opens with its default app. There is no Settings
field for this key, and Assistant checks only that the address starts with `http://` or `https://`.

> **Correction, 2026-11-07 (commit 3d7bc25):** Ask Claude is no longer a stub. From the code at 3d7bc25 (`assistant/actions/shell.py`, `ask_claude`; `assistant/actions/claude.py`, `send`), Ask Claude first asks for confirmation and states the byte count and what it would send: the first 32 KB of the file's text, or the whole text when you choose that. It sends only after you confirm. It runs `claude -p` with no tools and no session saved, through your Claude Code sign-in. Office files send their extracted text, not the file. Binary, protected, cloud-placeholder and too-large files are refused. The HTTP route is in [docs/api.md](docs/api.md) section 13.4. The next paragraph describes the M3b stub and is kept as written.

**Ask Claude about this file.** A stub. The menu item and the confirmation exist. After you confirm, the page says `Sending to Claude
is not available in this build; nothing was sent.` The popup writes that message to the log instead. Nothing leaves the PC.

## Roots, excludes and protected files

Nothing is indexed until you add a folder. Settings, **Roots & excludes** lists what is indexed.

- **Add folder…** adds a folder. It must exist, and it cannot be added twice. Up to 20 folders. **Remove** takes one out.
  **Rescan now** starts a scan at once. Changes apply without a restart.
- **A folder on a network share** (a UNC path, or a mapped network drive) is read over the network. If it does not answer
  within 60 seconds (`index.root_timeout_s`), it is skipped and its state reads `timeout`. The rest of the app keeps working.
- **Persisted index.** The index is kept in `~/.assistant/index/files.idx`. After a restart, search works at once from the
  stored index while a background scan runs at low priority. Scans run at start, once a day (`index.rescan_hours`, default
  24), when you press **Rescan now**, and after a burst of changes. The daily scan waits while the PC is on battery
  (`index.pause_on_battery`, default on) and retries every 15 minutes. The start scan does not wait for battery.
- **Freshness.** The results header shows `index from <date time>`. That is the end time of the last complete scan. Files
  created, renamed or deleted under a root while the app runs are picked up by a file watcher between scans.
- **Excludes.** One rule per line, in the box under the root list. A rule with no `/` matches a name anywhere. A rule with a
  `/` matches the path below the root. A trailing `/` means folders only. `*` matches any characters, including `/`. A line
  that starts with `!` is ignored, because re-including is not supported. A drive-letter path such as `C:\Some\Folder\`
  excludes that folder and everything under it. Each rule shows how many entries it excluded in the last scan. The box holds
  up to 200 rules of up to 512 characters each.
- **Default excludes.** The Windows folder; the Recycle Bin; `System Volume Information`; the Windows search index data;
  `pagefile.sys`, `hiberfil.sys`, `swapfile.sys`; `*.vhdx` disk images; Temp, WindowsApps, Packages, and the Edge and Chrome
  caches under `AppData\Local`; Docker data; caches under `AppData` (`Cache`, `Code Cache`, `GPUCache`, `Service Worker`,
  `OfficeFileCache`, and npm and NuGet caches); and, inside any root, `node_modules`, `.git`, `.venv`, `venv`, `__pycache__`,
  `target`, `bin`, `obj`, `.gradle`, `.m2`, `.nuget\packages`, `.cache`, `.vs`, `dist`, `build`, `.next` and `.turbo`.
  Note that `bin`, `obj`, `build` and `dist` are excluded wherever they appear, including in your own project folders.
- **Hidden and system files are indexed** in this build. The PRD asks for them to be excluded by default with a toggle, and
  that is not built yet. Add `*` rules or folder rules if you want to leave some out.

> **Correction, 2026-11-07 (M3b):** hidden and system files are **left out** of results by default now (`index.include_hidden` is `false`). Turn on **Include hidden and system files in results** on the Roots & excludes page to see them; the change applies to results at once, with no rescan. The sentence above is kept as written.
>
> **Correction, 2026-11-07 (M3b), excludes:** a list you save on the Roots & excludes page **replaces** the default list; it does not add to it. **Restore defaults** on that page puts the built-in list back. The Windows entries in the built-in list are written for any drive (for example `*:\Windows\`), so they match on every drive letter. The "Default excludes" paragraph above describes the built-in list.

> **Correction, 2026-11-07 (commit 3d7bc25), default excludes:** the built-in list now also excludes `Program Files`, `Program Files (x86)`, `ProgramData` and every `AppData` folder, plus the Windows search data under `ProgramData`. The index is for your own documents and files, so these are left out. The exclude rules match a pattern with a `/` against the root-relative path or its tail (`assistant/index/excludes.py`, lines 8 to 9 and 36), so `AppData` is excluded at any depth under a root, not only the cache folders listed above. A whole drive such as `C:\` can be added as a root; the Windows, Program Files and ProgramData entries then apply to that drive. A new file under a root is picked up by the file watcher in about 1 second. That figure is the target, not a measurement by doc-keeper: the watcher batches events for 0.25 s (`assistant/index/manager.py`, `HINT_BATCH_S`).
- **Your Assistant folder is never indexed.** The folder `~/.assistant` (or the folder named by `ASSISTANT_HOME`) and any
  `.assistant` folder are left out, even when a root contains your whole user profile. Claude cannot read them either.
- **Protected files.** Files whose names or folders match the secrets list are flagged when the index is built. They are
  never previewed, never returned to Claude, and never sent to Ask Claude. The list:
  - names: `.env*`, `*.pem`, `*.key`, `*.pfx`, `*.p12`, `*.ppk`, `*.kdbx`, `id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `id_dsa*`,
    `*.asc`, `*.p8`, `*.jks`, `.pgpass`, `.htpasswd`, `.git-credentials`, `.netrc`, `.npmrc`, `.pypirc`, `kubeconfig`,
    `*.kubeconfig`, `.claude.json`
  - path endings: `.aws\credentials`, `.docker\config.json`, `.kube\config`, `.claude\settings.json`
  - folders: `.assistant`, `.ssh`, `.gnupg`, `.azure`, `.config\gh`, browser profile folders for Chrome, Edge, Brave, Vivaldi,
    Opera and Firefox, and password-manager data folders for KeePass, KeePassXC, Bitwarden, 1Password, LastPass, Dashlane,
    Enpass, NordPass and Keeper
  - content: a file that contains a PEM private-key header is refused when Claude asks to read it, even under a harmless name.
- **Show protected files by name.** Settings, Search, "Show protected files by name in local search". Off by default. When
  on, the popup and the search route list the names and paths of protected files, so you can find them. Their content is still
  never shown, and Claude's tools never show them. The settings page says this too.

## Claude and your files

Claude can find and read your indexed files through the `assistant` MCP server, with four read-only tools:

| Tool | What it does |
|---|---|
| `search_files` | Searches the index with the same grammar as the popup. Returns up to 500 rows per call (default 50): path, name, size, modified time, folder flag and cloud-placeholder flag. |
| `get_file_info` | Metadata for up to 20 paths. Never content. |
| `read_text_preview` | The start of a text file, at most 64 KB per call (16 KB by default), optionally cut to a number of lines. |
| `list_indexed_roots` | The folders that are indexed, with each folder's state and entry count, and when the index was built. |

Claude **can**: search the indexed roots by name, path, extension, chip and date; read the text of an ordinary text file that
is inside a root, not excluded, not protected and not a cloud placeholder that has not been downloaded.

Claude **cannot**: open, reveal, rename, move, delete or run anything; see files outside the roots; read files in the
Assistant folder; see protected files or their names in search; read binary files (Office documents, PDFs, images, archives,
executables); read a cloud placeholder that is not downloaded; or read a file whose first megabyte contains a private key.

**Text returned to Claude is wrapped.** Each `read_text_preview` result is placed between a header and a footer:

```
<<<UNTRUSTED FILE CONTENT path=<file path> bytes=<n>[ truncated=yes]>>>
...the file text...
<<<END UNTRUSTED FILE CONTENT>>>
```

The tool description tells Claude to treat that text as data and not to follow instructions found in it. The wrapper marks
the boundary. It does not filter the text, so a file can still contain text that tries to steer Claude. Read untrusted files
with that in mind.

**What leaves the PC.** Assistant itself sends nothing. But when Claude calls `read_text_preview`, the text it reads becomes
part of the Claude conversation, and that conversation is sent to Anthropic's service as Claude Code or Claude Desktop
normally does. So file text can leave your PC through a Claude session, when Claude is asked to read it. This is the one
exception to "nothing leaves your PC" in this version. The PRD asked for read-only file tools (AC-MC-3 and AC-MC-4), so the
exception is intended. Do not point Claude at folders you would not want it to read.

**What is logged.** Each MCP call is logged to `~/.assistant/logs/mcp.log` as a shape only: the tool name, the argument
names with their lengths, and the result size. Paths, queries and file text are never written.

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

> **Correction, 2026-11-07 (M3a):** the Settings window has eight pages from M3a: General, Cards, Calendars, Claude Code, MCP,
> About, **Roots & excludes** and **Search**. The Search page holds the hotkey field (enabled, see [Search files](#search-files)),
> the full-screen option, the search box placement, the notes folder and "Show protected files by name". The General page
> still shows the hotkey as disabled, which is the M1 wording kept above. The keys below are added to `settings.json` from M3a:
> `hotkey` (default `ctrl+alt+space`; text, at most 64 characters; empty turns it off), `search_placement` (default `icon`;
> `icon`, `mouse`, `focused`, `primary` or `last`), `hotkey_skip_fullscreen` (default off; read by the app, not listed in the
> defaults, see the open items in [docs/api.md](docs/api.md) section 11.9), `index` (`roots`, up to 20 absolute folders;
> `excludes`, up to 200 rules; `show_protected_by_name`, default `false`; `rescan_hours`, default 24, 0.01 to 720;
> `root_timeout_s`, default 60, 0.1 to 3600; `pause_on_battery`, default `true`) and `notes` (`dir`, empty or an absolute folder
> path). A bad `index` or `notes` value falls back for that key only, with a warning in the log.

**Keys added in M3b (2026-11-07).** These are in `settings.json`. The defaults and limits are read from `assistant/settings.py`, and a bad
value falls back for that key only, with a warning in the log.

| Key | Default | Allowed | Notes |
|---|---|---|---|
| `index.include_hidden` | `false` | `true` or `false` | Hidden and system files and folders are left out of results unless this is `true`. The Roots & excludes page has the toggle. |
| `index.scan_if_older_hours` | `12` | whole number, 1 to 168 | At start, a full scan runs only when the saved index is older than this. |
| `index.known_exts` | the built-in list | up to 200 entries, each letters and digits, at most 16 characters | The extensions a bare word such as `design md` turns into a filter. `doc` is not in the built-in list. The Search page edits it. |
| `hotkey_skip_fullscreen` | `false` | `true` or `false` | In the defaults from M3b. The Search page has the option. |
| `preview.max_bytes` | `50000000` | whole number, 1048576 to 4294967296 | The preview size cap. No Settings field; edit the file by hand. Applies within about 5 seconds. |
| `md_vault.url` | not set | an `http://` or `https://` address | Used by Ctrl+M (see [Actions on a file](#actions-on-a-file)). No Settings field. Not checked beyond the scheme. |

**Restore defaults.** On the Roots & excludes page, a saved excludes list replaces the built-in list. **Restore defaults** puts the
built-in list back.

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

> **Correction, 2026-11-07 (M3a):** **Search** is live from M3a. The index and the notes folder are stored under the same folder: `index/files.idx` (the file index), `index/notes.json.gz` (the Ask notes index) and `saved_searches.json` (saved searches). The `settings.json` keys added in M3a are listed under [Settings](#settings).

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

**Hotkey: "already registered by another app".** Another program holds the combination. Settings, Search, choose another
combination in the Hotkey field. The icon still opens the search box. To find the holder, run `python -m assistant --doctor`:
its `hotkey` line gives the status. If it says the running Assistant may be the holder, the combination is already in use by
the copy that is running. The log has a warning line that names the combination.

**Index not building, or search returns nothing.** Check that at least one folder is listed under Settings, Roots & excludes.
With no roots, nothing is indexed and the header says so. Then run `python -m assistant --doctor`: `index size and age` says
`no index file yet` until the first scan completes, and `index roots` lists the folders. In Settings, Roots & excludes, a
folder that reads `missing` does not exist or is not reachable, and one that reads `timeout` is a network folder that did not
answer within 60 seconds. A search that finds nothing in a folder can also mean the folder is excluded: each rule shows its
count, so a rule with a large count is the one to look at. Hidden and system files are indexed in this build.

> **Correction, 2026-11-07 (M3b):** hidden and system files are left out of results unless you turn on **Include hidden and system files in results** (Roots & excludes). The sentence above is kept as written.

**Search is slow on a very large root.** A single plain term examines at most 100,000 candidates. The scan also stops after
0.4 seconds. Either way the result carries `truncated`, and the popup's header count is then partial. Narrow the query: add a
second word for the path, an extension (`md`, or `-e md`), a chip, or a modified filter. The empty query shows only the newest
10,000 entries. If you have a very large folder under a root, consider leaving it out with an exclude rule, for example
`C:\Users\<user>\Archive\` as a drive-letter rule.

**Full page shows "Open this page from Assistant".** The page opened without a launch code. This happens when the page is reloaded
(a reload loses the session), when the `/app/` address was opened by hand, or when the code was used already. If the code is older
than 30 seconds, or was used, the page says `Could not start the session` with `The launch code is missing, used or expired.`
Press **F11** in the search box to open a new window.

**Full page does nothing, and the log says "msedge.exe not found".** Assistant looks for `msedge.exe` in the standard Program Files
folders (`Program Files (x86)\Microsoft\Edge` and `Program Files\Microsoft\Edge`). If neither exists, F11 and the button do nothing
visible, and `logs/app.log` says `msedge.exe not found; the full page cannot open`. Install or repair Microsoft Edge. The popup
shows no message for this. If Edge starts but its window is not found within 15 seconds, the log says so and Esc in the page does
not close it; close the window by hand.

**A preview says "protected", "too large" or "not downloaded".** `Protected file - not previewed` means the file's name or folder
is on the secrets list (for example `.env` files, `*.pem` or `*.key` keys, or password-manager folders). It is not a fault, and the
page never shows its content. `File too large to preview` means the file is over `preview.max_bytes` (default 50,000,000 bytes).
Raise it in `settings.json`, to a value from 1048576 to 4294967296; it applies within about 5 seconds. `Cloud placeholder - not
downloaded` is not an error: download the file with your sync app, then search again. The **Download and preview** button does not
download (see [Full page and previews](#full-page-and-previews)).

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

What M3a adds:

- **Files search:** a hotkey (default Ctrl+Alt+Space) and the icon open a search box beside the icon. Names and paths under your
  roots, with the grammar in [Search files](#search-files), chips, a modified-date filter, saved searches and a visible
  interpretation. Results rank by how well the name matches.
- **Index:** persisted under `~/.assistant/index/`, rescanned at start and daily, with a file watcher between scans. Excludes
  with per-rule counts, and network roots that time out without blocking the app.
- **Ask notes:** BM25 search over `.md` files in a notes folder, with rendered passages and a local footer.
- **Claude over MCP:** `search_files`, `get_file_info`, `read_text_preview` and `list_indexed_roots`, all read-only, with wrapped
  text and refusals that say why (see [Claude and your files](#claude-and-your-files)).
- **Settings:** Roots & excludes and Search pages.
- **`--doctor`:** hotkey status, index size and age, index roots, and the notes folder state.
- **Health:** `/v1/health` `index` is an object with roots, scan state and counters (see [docs/api.md](docs/api.md) section 11.4).

What M3b adds (2026-11-07):

- **Full page:** Edge in app mode with its own profile, opened with F11 or the **Full page** button and closed with F11 or Esc
  (see [Full page and previews](#full-page-and-previews)).
- **Previews:** Markdown with Mermaid and Markmap, text and code, CSV, TSV, XLSX, DOCX and PPTX text, PDF, and images (PNG, JPEG,
  GIF, WebP, AVIF, BMP, ICO and SVG). A card for everything else, and cards for protected, placeholder and too-large files.
- **Actions:** open, open with, reveal, copy path (plain and quoted), rename, properties, Move to Recycle Bin (Recycle Bin only),
  MD Vault, the folder listing (popup) and Ask Claude (a stub). See [Actions on a file](#actions-on-a-file).
- **Popup:** a sort control, a cloud marker on placeholder rows, the note text when a query is shortened, and the action shortcuts.
- **Roots & excludes:** a toggle for hidden and system files, and **Restore defaults** for the excludes list. **Search:** an editable
  list of known extensions. The new keys are in [Settings](#settings).

What 3d7bc25 adds (2026-11-07). These are read from the code at 3d7bc25. The commit diff was not read, and none of these has been observed on a live desktop:

- **Rounded cards and icon:** on Windows 11 the card and icon windows ask Windows for rounded corners. Where Windows does not offer that, the window keeps a square shape with a rounded outline (`assistant/overlay/win.py`).
- **Desktop shortcut:** `install.ps1` creates `Assistant.lnk` on the Desktop as well as in the Startup folder. Pass `-NoShortcut` to skip it. See [Install](#install-windows-no-admin).
- **Claude Code hooks:** `python -m assistant hooks install` adds the eight hook entries and `hooks remove` takes them out. When Claude is waiting for you, an amber card appears; when a task is done, a green card appears. See [Connect Claude Code (hooks)](#connect-claude-code-hooks).
- **Full page:** opens with the query from the popup, and the popup closes. It has live search (150 ms), Tab completion, the search URL (`q=`), a light and dark theme saved in `settings.json` and shared with the popup, and zoomable previews from 80 to 200 percent. See [Full page and previews](#full-page-and-previews).
- **Search words:** every word may match the name or the path, and name matches come first. A very common word gives a result marked truncated. See [Search files](#search-files).
- **Live search in the popup:** results update while you type (120 ms). Enter opens the selected result.
- **Whole-drive indexing:** a root can be a whole drive. The default excludes now also leave out Windows, Program Files, Program Files (x86), ProgramData and AppData. A new file reaches the results in about 1 second through the file watcher, which is a target and not a measurement (see [Roots, excludes and protected files](#roots-excludes-and-protected-files)).
- **Ask Claude:** sends a file's text only after your confirmation (see [Actions on a file](#actions-on-a-file)).
- **Shutdown:** the teardown steps share a 3-second deadline (`assistant/app.py`, `SHUTDOWN_STEPS_S`). The full bound, including the server stop, is in [docs/api.md](docs/api.md) section 13.6.
- **`update --yes`:** on a git clone, `update` runs `git pull --ff-only` and then pip on the requirements, with no flag needed. On a release zip (with `update.source` set), it downloads and unpacks the zip; pip runs only with `--yes`, and otherwise the command is printed (`assistant/update.py`).

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

> **Correction, 2026-11-07 (M3b):** M3b adds no pip package. The preview code uses the standard library (`zipfile`, `csv`, `re`, `html`). The Recycle Bin call uses `pywin32`, which is already pinned. `requirements.txt` as read on 2026-11-07 has 30 pinned lines, not 29; the count above is not corrected here. The full-page web app is built with npm at build time only (`scripts/build-web.ps1`). Its run-time packages, from `web/package.json`, are d3, dompurify, highlight.js, markmap-lib, markmap-view, mermaid, pdfjs-dist, react, react-dom, react-markdown, rehype-highlight and remark-gfm; vite and @vitejs/plugin-react are build-time only. The built output is the static files under `assistant/static/app/`, which the app serves. Node is not needed to run Assistant.

### M3a additions (2026-11-07)

Source: the M3a security review, `docs/security/2026-11-07-m3a-review.md` (Review 7: PASS, zero BLOCK, R7-F1 and R7-F2 as
FIX-BEFORE-M3B, twelve NOTEs), unless a point says otherwise.

> **Correction, 2026-11-07 (M3a):** the bullet "Secrets deny-list (PRD SEC-3) does not apply yet" above is superseded. The
> deny-list applies to search results, the file tools for Claude and Ask notes, from M3a. Previews (M3b) and Ask Claude (M4)
> are not built.
>
> **Correction, 2026-11-07 (M3a):** "Nothing leaves the PC" and the list of outbound paths above stay true for Assistant's own
> code. From M3a, file text that Claude reads through `read_text_preview` becomes part of a Claude conversation and so leaves
> the PC through Claude. See [Claude and your files](#claude-and-your-files).

- **Local search.** The index, the saved searches, the Ask notes index and the popup live in your Assistant folder. The
  index records names, paths, sizes and times. The crawler opens no file (observed in Review 7 with an open-audit hook).
  The Ask notes index does hold the text of notes, in a user-only file.
- **Deny-list.** Protected names and folders (listed under [Roots, excludes and protected files](#roots-excludes-and-protected-files))
  are refused by the file tools and never returned by search, except by name when "Show protected files by name" is on.
  The file tools also refuse a path inside the Assistant folder.
- **Fixed 2026-11-07 (M3a-11):** R7-F1, the Assistant's own folder was indexed and readable through the file tools (a root
  that contained the profile exposed the bearer token and the calendar addresses). The folder is now excluded from the index,
  the tools refuse it, and `.assistant`, `.claude/settings.json` and `.claude.json` are in the deny-list.
  R7-F2, full-scan queries took 2.5 to 7 seconds on a 2,000,000-entry index and the offset cap was bypassed. Limits now apply:
  `limit` clamps to 500, `offset` to 100,000, a single plain term examines at most 100,000 candidates, and the scan stops after
  0.4 seconds with `truncated` set. Security has not re-checked these fixes in `docs/security/` yet.
- **Open, carried from Review 7:** the notes index keeps passages of protected-name notes, filtered only at query time (R7-N5).
  The index loader does not check element types (R7-N1). The file tools have a check-then-open window that only a same-user
  process could use (R7-N2). Some validation messages echo the value sent (R7-N7). Security will confirm R7-N3, N4, N6 and
  N9, which the code appears to address.
- **Hotkey.** `RegisterHotKey` on its own thread. No keyboard hook. The reserved list refuses Alt+F4, Alt+Tab, Win+L,
  Ctrl+Alt+Delete and Ctrl+Esc, bare keys, and Win-only combinations.
- **Not yet observed for M3a:** the popup on a live desktop with the hotkey registered; the quiet-machine re-measure of the
  numbers (the numbers were taken under load); the work-laptop probes (run by you, not yet pasted).

> **Correction, 2026-11-07 (M3b):** the security re-check of the M3a fixes is recorded. `docs/security/2026-11-07-m3a-review.md`,
> section "Re-check after M3a-11/12 (2026-11-07)", reports PASS, with R7-F1 and R7-F2 closed by observation. The sentence above
> that says security has not re-checked the fixes is kept as written.

### M3b additions (2026-11-07)

Source: the code as read on 2026-11-07 (`assistant/server/auth.py`, `routes_files.py`, `static.py`, `assistant/preview/extract.py`,
`assistant/actions/shell.py`, `web/src/api.js`, `web/src/preview/`). The M3b security review (M3b-7) is **pending**. Until it is
recorded in `docs/security/`, the points below describe the design and the code, and are not a security verdict.

- **The page opens with a one-time launch code.** Assistant starts Edge with a launch code in the address. The code lasts 30
  seconds and works once. The page trades it for the bearer token through `/v1/session/exchange`. The token stays in the page's
  memory. It is not in any URL (the page removes the code from the address after reading it) and not in browser storage (the only
  stored value is the theme choice). The launch code is in the Edge command line for those 30 seconds, so a program running as the
  same Windows user could read it in that time.
- **Static files need no token.** The `/app/` files are served without the bearer for GET and HEAD. They hold no data. Every
  `/v1/` route still needs the token.
- **Previews never run content.** The server returns Markdown and HTML as JSON text for the page to render; it never sends HTML as
  `text/html`. HTML is cleaned with DOMPurify and shown in a frame with `sandbox`. SVG is served as an image with a
  Content-Security-Policy that blocks scripts and outside loads. Raw HTML in Markdown is not rendered. Mermaid runs with
  `securityLevel: 'strict'`, and its output is cleaned. Markmap labels are cleaned with a list of allowed tags that has no script.
- **PDF rendering stays on the same address.** The PDF.js worker is served from `/app/`. The page source has no CDN references.
- **Previews read only what they show.** A text preview reads the requested window (at most 1 MB per request), and the private-key
  scan covers that window. A cloud placeholder is checked from its file attributes before it is opened, so its content is never
  read. Each open file is checked against the roots and the deny-list before its bytes are read.
- **Actions never delete for good.** The only deletion in the actions code is the move to the Recycle Bin (`SHFileOperation` with
  allow-undo). Rename uses the file system's rename.
- **Ask Claude sends nothing** in this build. It asks for confirmation, shows how many bytes it would send, and then reports that
  sending is not available.
- **Outbound.** Previews and actions make no network request of their own. Two actions open a web address in your browser when you
  ask: an `https` address in MD Vault (`md_vault.url`), and a `.url` file.
- **Edge.** Assistant starts `msedge.exe` with its own profile folder under `%LOCALAPPDATA%\Assistant\edge-profile`, and closes only
  the window it opened.
- **Known issues, recorded.** The popup does not show action results or the folder listing (see
  [Actions on a file](#actions-on-a-file)). The page's **Download and preview** button does not download.

> **Correction, 2026-11-07 (M3b review):** the M3b security review is `docs/security/2026-11-07-m3b-review.md`, not pending. Review 9 (M3b-7) returned BLOCK on R9-B1. Its re-check section, "Re-check after M3b-10/11 (2026-11-07)", returns "BLOCK LIFTED — PASS for merge". R10-F1 (FIX-BEFORE-M4, the member-size cap on docx and pptx) and NOTEs R10-N1 to R10-N4 remain open. The "pending" wording above is kept as written.

### 3d7bc25 additions (2026-11-07)

- **Ask Claude sends file text after confirmation only.** Nothing is sent unless the request has `confirm: true`. The text goes to Claude Code in print mode (`claude -p`, no tools, no session saved), which sends it to Anthropic under your Claude Code sign-in. This is a second outbound path, next to the MCP file tools described in the M3a note above. Source: `assistant/actions/shell.py` and `assistant/actions/claude.py`.
- **Settings route.** `GET /v1/settings` returns the theme, shortcuts, search placement, hotkey, Markdown vault URL and known extensions. It does not return calendar addresses, the token, roots, excludes, the notes folder or the update source (`assistant/server/routes_settings.py`).
- **Security review of 3d7bc25:** `docs/security/2026-11-07-m4-review.md`. Review 11 returned BLOCK on R11-F1 (the Continue launcher's working folder). Review 12, in the same file, returns "BLOCK LIFTED — PASS for commit" with R11-F1 closed by observation, and three low NOTEs: R12-N1 (resolved in place), R12-N2 (search budget overshoot on wildcard-only queries) and R12-N3 (typed query text persists in the Edge app profile's History). Review 11's scope was the working tree against HEAD 56c3aa8, which is the 3d7bc25 change set. The code was read by doc-keeper, and no test or live run was done.

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
  > **Correction, 2026-11-07 (M3a):** this M3 bullet is the wording from before M3a. M3a shipped (next bullet). M3b is next.
- **M3a shipped 2026-11-07:** Files search in the popup (hotkey, box, grammar, chips, date filter, saved searches, placements), the
  file index with excludes and persistence, Ask notes over `.md` files, `GET /v1/search`, `GET /v1/roots`, the `/v1/saved` routes,
  the four read-only MCP file tools, the Roots & excludes and Search settings pages, and the `--doctor` lines. Plan:
  `docs/plans/2026-11-06-assistant-v1-plan.md` (sections 7.3 and 17.4). Requirements: `docs/prds/2026-11-06-assistant-v1.md`
  (sections 6.3, 6.4, 6.5 and 6.11). Security review: `docs/security/2026-11-07-m3a-review.md` (Review 7, PASS, zero BLOCK;
  R7-F1 and R7-F2 fixed 2026-11-07 by M3a-11). Commit: 847b8a5 … e9367d4 on main (filled 2026-11-07).
  Not yet observed: the popup on a live desktop with the hotkey registered; the quiet-machine re-measure; the work-laptop probes.
  Not built in M3a: the full page and previews (M3b); result actions (M3b); sort buttons, custom date range and placeholder markers
  in the popup (PRD gaps listed in [docs/api.md](docs/api.md) section 11.9).
- **M3b next:** the full page (Edge in app mode), previews of Markdown, PDF, Office and images, and result actions (open, reveal,
  Recycle Bin only). Plan section 7.4.
  > **Correction, 2026-11-07 (M3b):** M3b shipped the same day. The next bullet says what it contains. The bullet above is kept as written.
- **M3b shipped 2026-11-07, pending the security review:** the full page in Edge app mode, previews, the result actions, the M3b-0
  leftovers and the settings keys in [Settings](#settings). Plan: `docs/plans/2026-11-06-assistant-v1-plan.md` (sections 7.4 and
  18). Requirements: `docs/prds/2026-11-06-assistant-v1.md` (sections 6.3, 6.6 and 6.7). The M3a review
  (`docs/security/2026-11-07-m3a-review.md`) and its re-check report PASS. The M3b security review is **pending (M3b-7)**. Not yet
  observed: the unlocked-desktop checks (clipboard, and the real Ctrl, Delete, F2 and Alt+Enter keys in the popup); the popup's
  action feedback, which does not reach the popup in this build; the work-laptop probe of Edge app mode (P2); and the qa full-suite
  result for M3b. Commit: 56c3aa8 on main (filled 2026-11-07).
  > **Correction, 2026-11-07 (M3b review):** the M3b security review is no longer pending. Review 9 returned BLOCK on R9-B1, and the re-check in `docs/security/2026-11-07-m3b-review.md` returns "BLOCK LIFTED — PASS for merge" with R10-F1 open (FIX-BEFORE-M4). The bullet above is kept as written.
- **M4 next:** Ask Claude, where the stub becomes a real send after the confirmation, and polish.
- **Later:** M3 files search and Ask notes, M4 Ask Claude and polish, M5 plugins (PRD section 15).
- **Commit 3d7bc25, 2026-11-07, on main (no pull request; the repo has no remote):** the full page with live search, Tab completion, the search URL and the shared light and dark theme; the path-matching search rules; rounded cards and icon; the Desktop shortcut in `install.ps1`; whole-drive indexing with the wider default excludes; Ask Claude sending after confirmation; `GET /v1/settings`, `POST /v1/settings/theme` and the Ask Claude status and continue routes; bounded shutdown; `update --yes`. Contents are as read from the code at 3d7bc25. Reference: [docs/api.md](docs/api.md) section 13, [docs/search-grammar.md](docs/search-grammar.md) section 12 and [CHANGELOG.md](CHANGELOG.md), 2026-11-07.
  - Not yet observed: the live desktop pass for the page, the popup's live search and the theme; the test run for this commit (not run by doc-keeper).
  - Security review: `docs/security/2026-11-07-m4-review.md` (Review 11 BLOCK on R11-F1; Review 12 PASS for commit, with low NOTEs R12-N2 and R12-N3 open).
  - Known issues: a 401 on the theme route reverts the page's theme (fixed after 3d7bc25, pending commit: the 401 now restores `<html data-theme>`); a caret in the home folder path breaks Continue (fixed after 3d7bc25, pending commit: Continue now works with `^ & ! ( )` in the home path); the performance and churn tests are flaky under load; the remind-me end-to-end test is flaky on a busy desktop.

## Documentation

- [docs/api.md](docs/api.md): the local HTTP API, the hook contract, the card model and the file drop format.
- [docs/prds/2026-11-06-assistant-v1.md](docs/prds/2026-11-06-assistant-v1.md): requirements and acceptance criteria.
- [docs/plans/2026-11-06-assistant-v1-plan.md](docs/plans/2026-11-06-assistant-v1-plan.md): the milestone plan.
- [docs/security/2026-11-06-m1-review.md](docs/security/2026-11-06-m1-review.md): the M1 security review.
- [docs/security/2026-11-06-m2-review.md](docs/security/2026-11-06-m2-review.md): the M2 security review (Review 5).
- [docs/security/2026-11-07-m3a-review.md](docs/security/2026-11-07-m3a-review.md): the M3a security review (Review 7).
- [docs/search-grammar.md](docs/search-grammar.md): the Files search grammar, caps and interpretation strings.
- [docs/reference/2026-11-06-assistant-popup-readme.md](docs/reference/2026-11-06-assistant-popup-readme.md): the behaviour of the
  reference build this replaces.
- [CHANGELOG.md](CHANGELOG.md): what changed, and when.
