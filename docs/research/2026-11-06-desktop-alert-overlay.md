# Research brief: "Assistant" — Windows desktop alert overlay

Date: 2026-11-06
Author: researcher (TeamAgents)
Status: first brief; later findings go in dated appended sections (archaeology rule)
Hand-off: tech-lead → product (PRD)

## Question

Is it feasible, and is it worth building from scratch, a single-user Windows 11
always-on-top overlay (bottom-right: pill text input, bell with unread badge,
stacked cards with category / created time / live "waiting m:ss" timer / title
/ detail / Snooze 5m / Snooze 15m / Dismiss) fed by three sources:

1. Claude Code sessions waiting on the user (CLI and the Claude desktop app's Code tab);
2. reminders typed into the box ("driver selftest in 10m");
3. upcoming meetings ("Standup starts in 5 minutes 10:00-10:15").

Decomposed into the seven sub-questions the dispatch asked for: Q1 hook
detection, Q2 prior art, Q3 tech stack, Q4 calendar access, Q5 reminder
parsing, Q6 Windows toasts as an alternative, Q7 risks.

Market-demand validation is deliberately out of scope: this is a personal
tool for one user. The brief validates *feasibility* and *prior art*, not
demand.

## Method

- No `deep-research` skill exists in the TeamAgents project or user skills
  directory (checked `.claude/skills/` in both places), so the search was a
  structured fan-out: three read-only research subagents ran in parallel on
  (a) prior art, (b) tech stack footprint + Windows toasts, (c) calendar
  access + natural-language time parsing. The researcher fetched the Claude
  Code hooks reference and desktop-app docs directly, since Q1 is the
  load-bearing question.
- Every URL listed under "URLs fetched" at the end was actually fetched
  (by the researcher or a subagent) on 2026-11-06. Star counts and dates are
  as displayed on GitHub / package registries that day.
- Source classes are labelled on each load-bearing claim: **[official doc]**,
  **[repo page]** (README / GitHub API), **[community]** (blog, forum, Q&A),
  **[inference]** (our reasoning, not a sourced fact).
- Adversarial pass: searched for evidence that the idea is already solved
  (existing Windows Claude notifiers, the desktop app's own notifications,
  Windows toasts) and for dead prior attempts.

## Findings

### Q1. Detecting "Claude needs you" — confidence: HIGH (official docs, fetched today)

Source: Claude Code hooks reference, https://code.claude.com/docs/en/hooks
(the old `docs.claude.com/en/docs/claude-code/hooks` URL 301-redirects here).

**Events that matter for the overlay** [official doc]:

| Event | Fires when | Payload fields beyond the common set |
|---|---|---|
| `PermissionRequest` | Immediately, when "Claude Code is about to ask you for permission to use a tool" | `tool_name`, `tool_input` (e.g. `{"command": "rm -rf node_modules", "description": "..."}`), optional `mcp_server`, optional `permission_suggestions` |
| `Notification` matcher `permission_prompt` | "Claude needs you to approve a tool use ... and the prompt has waited about six seconds"; each keystroke defers it (CLI) | `message` (e.g. "Claude needs your permission"), optional `title` ("Permission needed"), `notification_type` |
| `Notification` matcher `idle_prompt` | "Claude finished responding about 60 seconds ago and you haven't typed since"; not sent while a background agent runs or while waiting for a usage-limit reset | same as above |
| `Notification` other matchers | `elicitation_dialog`, `elicitation_url_dialog`, `agent_needs_input`, `agent_completed`, `auth_*`, `quota_auto_resume_*`, `plugin_notification` | same |
| `Stop` | "When Claude finishes responding" (immediately, no 60 s wait) | `stop_hook_active`, `last_assistant_message` (final text, so no transcript parsing), `background_tasks[]`, `session_crons[]` — the two arrays "let hooks distinguish 'session is done' from 'session is paused waiting for background work'" |
| `StopFailure` | Turn ends on an API error (`rate_limit`, `overloaded`, `authentication_failed`, `billing_error`, ...) | matcher = error class |
| `UserPromptSubmit` | When the user submits a prompt | `prompt`, optional `session_title` |
| `PostToolUse` | After a tool call succeeds | `tool_name`, `tool_input`, `tool_use_id`, tool response |
| `SessionStart` / `SessionEnd` | Session begins/resumes (`source`: `startup`, `resume`, `clear`, `compact`, `fork`) / ends (`reason`: `clear`, `resume`, `logout`, `prompt_input_exit`, `other`) | `source` / `reason` |

**Common fields on every event** [official doc]: `session_id`, `prompt_id`,
`transcript_path`, `cwd`, `scratchpad_dir`, `permission_mode`, `effort`,
`hook_event_name`; subagent contexts add `agent_id`, `agent_type`. There is
**no explicit project-name field**; the project label on the card must be
derived from the basename of `cwd` or from `session_title` when present
[inference].

**Do hooks fire in the Claude desktop app?** Yes [official doc]: "Claude Code
fires the same hook events wherever it runs: sessions in the terminal, IDE
extensions, the Desktop app, and cloud sessions." Specifics for Desktop:

> "Claude Code times `permission_prompt` differently in sessions where it
> sends permission requests to the Agent SDK's `canUseTool` callback, which
> is how Claude Desktop and the VS Code extension host Claude Code: Expect
> `permission_prompt` about six seconds after Claude asks for permission.
> Claude Code doesn't defer it while you type. If you or a PermissionRequest
> hook answer sooner, Claude Code doesn't run `permission_prompt`. ... Before
> v2.1.233, `permission_prompt` didn't fire in these sessions."

So the desktop app requires Claude Code >= v2.1.233 for `permission_prompt`;
`PermissionRequest` "run[s] alongside your host, and whichever decides first
applies" — our hook must answer promptly with **no** decision object so the
normal UI prompt proceeds [official doc + inference]. Cloud sessions do not
read local `~/.claude/settings.json` [official doc] — out of scope anyway.
The desktop-app docs (https://code.claude.com/docs/en/desktop) only
describe a built-in OS notification "when a Code session finishes a task and
you aren't currently viewing that session" — nothing for permission waits,
and no stacking, timer, or snooze [official doc]. That is the gap.

**Transport** [official doc]: hook handler types are `command`, `http`,
`mcp_tool`, `prompt`, `agent`. The `http` type POSTs "the hook's JSON input
as the POST request body with `Content-Type: application/json`"; config
fields are `url` (required), `headers`, `allowedEnvVars`, `onFailure`
(`"continue"` default), `timeout` (600 s default). Connection failure and
non-2xx are **non-blocking** errors — Claude continues working if the
overlay is not running. The docs' own example uses
`http://localhost:8080/hooks/pre-tool-use`, so a loopback listener is the
documented pattern. An optional `allowedHttpHookUrls` setting can restrict
URLs. Command hooks support `"async": true` (background, cannot block);
HTTP hooks do not need it because a fast 2xx is enough [inference].
Consequence for the fan: one resident HTTP listener in the overlay, zero
PowerShell/node processes spawned per event [inference].

**How the overlay learns the user answered (card clearing)** [inference
from the documented events; no "user answered" event exists]:

| Waiting card created by | Clear on (same `session_id`) |
|---|---|
| `PermissionRequest` / `permission_prompt` | `PostToolUse` for the same `tool_name` (permission granted and tool ran); or any later `PreToolUse`, `UserPromptSubmit`, `Stop`, `SessionEnd` from that session (user denied or redirected) |
| `Stop` (finished, waiting for next instruction) / `idle_prompt` | `UserPromptSubmit` (user typed), `SessionEnd`, or a new `PreToolUse` (session was woken by a cron/background task) |
| `elicitation_dialog` | `Notification` `elicitation_complete` / `elicitation_response` |
| `StopFailure` | `UserPromptSubmit` or `quota_auto_resume_fired` |

Robust rule: **any subsequent event from a `session_id` clears that
session's waiting card**; a per-session "last event" state machine is the
design, not per-event toasts [inference]. `PermissionDenied` is *not* the
user-deny signal — it "fires when auto mode denies a tool call" [official
doc].

**Known gaps** [community, GitHub issues, not fetched — titles from search
results only]: anthropics/claude-code#13830 reports that `AskUserQuestion`
prompts produce no notification; #13024 asks for a hook when Claude pauses
for input because `idle_prompt` at 60 s is too slow; #36885 asks for a way
to know *which* of several sessions is blocked. A commenter on #13024
reports `PermissionRequest` matched on `AskUserQuestion` covers it; this
needs a probe. The current matcher list includes `agent_needs_input`, which
may be the newer answer — **unverified; open question for a 10-minute probe
during planning**.

**Timer semantics** [inference]: `PermissionRequest` fires at t=0 of the
wait; `permission_prompt` arrives ~6 s later; `Stop` fires at t=0 of
"finished"; `idle_prompt` at ~60 s. The "waiting m:ss" timer should start
from the `PermissionRequest` / `Stop` timestamp, not from the Notification.

### Q2. Prior art — confidence: MEDIUM-HIGH (repo pages fetched; stars/dates as of 2026-11-06)

**Claude Code notifiers** (all [repo page] unless noted):

| Tool | Platform | What it does | Hooks used | Stars / last push | Clears when answered? |
|---|---|---|---|---|---|
| [CCNotify (dazuiba)](https://github.com/dazuiba/CCNotify) | macOS | terminal-notifier toast, click opens VS Code, task duration | UserPromptSubmit, Stop, Notification | 214 / 2025-10-14 | not addressed |
| [AgentTray (sprklai)](https://github.com/sprklai/agenttray) | Linux primary; Win/mac "less tested" | Tauri 2 tray icon colour per state + session popup + beep; also Codex/Gemini; "Notifications disappear. The tray does not." | hooks (install-hooks.ps1) + process scan fallback | 4 / 2026-07-13 | hook-driven state, likely (unverified) |
| [cc-notification (kmio11)](https://github.com/kmio11/cc-notification) | Windows 10+ | pure PowerShell WinRT toast (no BurntToast), persists in Action Center, click-to-focus via protocol handler | Notification, Stop, PermissionRequest | 6 / 2026-09-23 | no |
| [notify (enescaakir)](https://github.com/enescaakir/notify) | Windows | Claude Code plugin; BurntToast + WPF + .wav; PreToolUse writes marker + 3 s timer, PostToolUse deletes marker so toast fires only if still pending | PreToolUse, PostToolUse, Stop | 9 / 2026-04-20 | cancels pending, does not retract shown |
| [claude-code-notify / CCN (yangshare)](https://github.com/yangshare/claude-code-notify) | Win11 + macOS | Rust, WinRT toast with own AUMID, status icons | Notification `permission_prompt` | 0 / 2026-01-04 | no |
| [ccnotifs (polyphilz)](https://github.com/polyphilz/ccnotifs) | macOS | `alerter`; APPROVE/REJECT from the notification; jumps to tmux pane | PreToolUse, Notification, Stop | 22 / 2026-03-29 | not stated |
| [ccnotify (foxytanuki)](https://github.com/foxytanuki/ccnotify) | any | generates Stop-hook configs for Discord / ntfy / macOS | Stop only | 2 / n.a. | no |
| [claude-status-bar (BankkRoll)](https://github.com/BankkRoll/claude-status-bar) | Win10/11, .NET 9 | borderless top-most strip next to the tray; hooks write `~/.claude/statusbar/state.json`, polled every 0.4 s; shows elapsed time of the current turn; yellow dot when awaiting permission | SessionStart/End + status writers | 2 / 2026-06-26 | yes via state file |
| [claude-watcher/gtk](https://github.com/claude-watcher/gtk) | Ubuntu/Debian | Conky-style persistent overlay listing every session Waiting/Working/Idle; no hooks, scans /proc + session files | none | 4 / 2026-10-28 | yes (re-reads state) |

BurntToast recipes: [Classmethod article 2026-05-21](https://dev.classmethod.jp/articles/notification-claudecode-in-windows/)
(WSL2 → powershell.exe, 30 s delayed toast, PostToolUse clears a marker) and
[a gist](https://gist.github.com/latenssi/e3a54bbdd84f6d595908c10931ac9b9f)
[community].

**General Windows overlay / reminder apps** [repo page / vendor page]:

| App | What it does | Gap vs our design |
|---|---|---|
| [WReminder](https://github.com/ezWhy/WReminder) | open-source, offline reminders; popup stays until interacted; snooze 10/20/30/60 | single popup, no stacking, no timer, not Claude/calendar-aware |
| [TaskCue](https://www.producthunt.com/p/taskcue-unignorable-desktop-reminders/taskcue-unignorable-desktop-reminders) | portable, front-centre modal reminder, snooze, recurring | centre modal, no calendar sync, closed source |
| [ntfy-desktop](https://github.com/Aetherinox/ntfy-desktop) | Electron tray client for self-hosted ntfy; sticky toasts; 229 stars | needs a server, no snooze/stack/timer |
| [gotify-tray](https://github.com/seird/gotify-tray) | Python tray client for Gotify; 135 stars | server-backed, OS toasts only |
| [Winify](https://grimore.org/windows/winify) | Gotify client with its own toast stack; "tearable" notifications pinned to desktop | needs Gotify, no snooze/timer, 2021 |
| [Zhorn Stickies](https://www.zhornsoftware.co.uk/stickies/) | sticky notes with alarms and "sleep" (de-facto snooze) | notes, not auto-created alert cards |

**What they do well / what our design fills** [inference over the above]:

- Solved and copyable: hook plumbing (Notification `permission_prompt`,
  PermissionRequest, Stop), the PreToolUse/PostToolUse marker trick to
  suppress false "waiting" alerts, click-to-focus, approve-from-notification
  (ccnotifs), toasts without BurntToast (cc-notification, CCN), and a
  top-most .NET strip beside the tray (claude-status-bar).
- Gap 1: every Windows Claude tool is fire-and-forget toast or a one-line
  status strip. None stacks one card per waiting session with a live
  "waiting m:ss" counter, created time and detail text.
- Gap 2: none retracts/updates the alert when the user answers; none
  offers Snooze/Dismiss on a Claude alert.
- Gap 3: no tool merges Claude sessions + typed reminders + calendar into
  one local, privacy-first overlay. Closest building blocks: AgentTray
  (multi-agent state model), Winify (tearable toast stack), WReminder
  (persistent snooze popup).
- Adoption signal: the two Windows overlay attempts have 2 and 4 stars.
  For a personal tool this is irrelevant to the build decision, but it
  means no mature project exists to fork instead of building.

### Q3. Tech stack — confidence: MEDIUM (capability claims from official docs; RAM numbers mostly non-Windows or anecdotal)

| Stack | Idle RAM (measured) | Always-on-top / transparent / click-through | Package size | Autostart | Notable risk |
|---|---|---|---|---|---|
| **WPF, .NET 8/9** | ~26–48 MB blank window [community: [MS Q&A](https://learn.microsoft.com/en-us/answers/questions/1285803/why-the-wpf-application-consumes-much-more-(nearly), [dotnet/wpf#8306](https://github.com/dotnet/wpf/issues/8306)]; ARM64 outlier ~100 MB | [`Topmost`](https://learn.microsoft.com/en-us/dotnet/api/system.windows.window.topmost), [`AllowsTransparency`](https://learn.microsoft.com/en-us/dotnet/api/system.windows.window.allowstransparency) (requires `WindowStyle=None`); per-region click-through via `WM_NCHITTEST`/`HTTRANSPARENT` or [`WS_EX_TRANSPARENT`+`WS_EX_LAYERED`, `WS_EX_NOACTIVATE`, `WS_EX_TOOLWINDOW`](https://learn.microsoft.com/en-us/windows/win32/winmsg/extended-window-styles) by P/Invoke [official doc] | framework-dependent: small, needs .NET Desktop Runtime; [self-contained](https://learn.microsoft.com/en-us/dotnet/core/deploying/): bundles runtime (tens of MB, no admin install) [official doc] | [HKCU Run key](https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys) or `shell:startup` [official doc] | Windows-only (fine); no Native AOT for WPF (unverified); ARM64 memory anomaly open |
| **Tauri 2** | 38–42 MB vs Electron 168–281 MB, but **macOS / unstated OS** [community: [gethopp](https://dev.to/gethopp/tauri-vs-electron-performance-bundle-size-and-the-real-trade-offs-1el4), [buildr](https://buildr-sh.pages.dev/posts/electron-is-a-tax.html), [tech-insider](https://tech-insider.org/tauri-vs-electron-2026/)]; no Windows measurement found | [`alwaysOnTop`, `transparent`, `decorations`, `skipTaskbar`](https://v2.tauri.app/reference/config/); [`setIgnoreCursorEvents`](https://v2.tauri.app/reference/javascript/api/namespacewindow/) is whole-window, so hover-toggle needed; each needs a capability permission [official doc] | ~3 MB bundle; [MSI/NSIS](https://v2.tauri.app/distribute/windows-installer/); WebView2 "distributed as part of the operating system" on Win11 [official doc] | [`tauri-plugin-autostart`](https://v2.tauri.app/plugin/autostart/) [official doc] | still runs a `msedgewebview2.exe` Chromium renderer process; Rust toolchain |
| **Electron** | 168–281 MB idle, 409 MB six windows [community, above] | [`setAlwaysOnTop`, `setIgnoreMouseEvents(true, {forward:true})`](https://www.electronjs.org/docs/latest/api/browser-window); transparent requires frameless; `forward` breaks while an elevated window is active [official doc] | 80–150 MB installed; [Forge/Squirrel](https://www.electronforge.io/config/makers/squirrel.windows) | [`app.setLoginItemSettings`](https://www.electronjs.org/docs/latest/api/app) | heaviest by every measurement; conflicts with the fan constraint |
| **WinUI 3** | no numbers; [#9154](https://www.github.com/microsoft/microsoft-ui-xaml/discussions/9154) reports GC overhead vs WPF | transparent window **not supported**: Microsoft, [#2992](https://github.com/microsoft/microsoft-ui-xaml/issues/2992) "it will not be supported in WinUI 3"; hacks on [MS Q&A](https://learn.microsoft.com/en-sg/answers/questions/1463839/how-to-make-winui-3-window-transparent) | MSIX typical | StartupTask | exclude |
| **AutoHotkey v2** | single-digit MB (unverified) | [`+AlwaysOnTop`, `-Caption`, `+ToolWindow`](https://www.autohotkey.com/docs/v2/lib/Gui.htm); [`WinSetTransColor`](https://www.autohotkey.com/docs/v2/lib/WinSetTransColor.htm) gives colour-key click-through for free [official doc] | ~1–2 MB compiled [Ahk2Exe](https://www.autohotkey.com/docs/v2/Scripts.htm) | [`shell:startup`](https://www.autohotkey.com/docs/v2/FAQ.htm) | GDI-only visuals, colour-key edges, AV false positives (community, unverified) |
| **Python + PySide6** | 40–80 MB (unverified) | [`WindowStaysOnTopHint`, `FramelessWindowHint`, `WindowTransparentForInput`, `WA_TranslucentBackground`](https://doc.qt.io/qt-6/qt.html) [official doc] | [PyInstaller 30–80 MB](https://www.pythonguis.com/faq/packaged-installer-file-sizes/); onefile extracts on every launch | Run key | ~1 s interpreter startup; second-heaviest |

Cross-cutting [official doc / community]: topmost windows do not draw over
exclusive-fullscreen games ([th.gl FAQ](https://www.th.gl/faq/overlay-not-visible-fullscreen),
[MS Q&A](https://learn.microsoft.com/en-us/answers/questions/3918271/keeping-python-application-always-on-top-(even-dur)) — irrelevant
for this user. Position the overlay inside the work area
(`SPI_GETWORKAREA`) rather than over the taskbar [inference]. WebView2
Evergreen is "included as part of the Windows 11 operating system"
([MS](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)).
Windows App SDK notifications: "Apps running with administrator privileges
(elevated) cannot send or receive app notifications" [official doc].

**Recommendation: WPF on .NET 8 (or 9), self-contained publish.** Reasons:
single process, no browser renderer, the smallest *measured-on-Windows*
footprint of the GPU-composited options, first-class `Topmost` +
transparency, per-region click-through in ~20 lines of P/Invoke,
self-contained publish avoids a runtime install on a locked-down corporate
machine, the same process can host the hook HTTP listener, Outlook COM
interop (trivial from .NET), ICS parsing (Ical.Net), and Windows App SDK
toasts as a fallback channel (see Q6). AutoHotkey is the lightest possible
v0 probe but cannot render the photographed card design well. Tauri is the
runner-up if the team prefers HTML/CSS for the cards and accepts one
WebView2 renderer process. Electron and WinUI 3 are excluded (fan; no
transparency). [inference over the sourced table]

### Q4. Meetings — confidence: HIGH on mechanics (official docs), MEDIUM on corporate-policy specifics

| Option | Cloud consent? | Admin-blockable? | Offline? | Latency | Source |
|---|---|---|---|---|---|
| **Google: "Secret address in iCal format"** (per-calendar private ICS URL; read-only; Reset invalidates) | No OAuth, no GCP project | Workspace admin can disable (unverified); personal Gmail: no | cacheable | Google serves a cached feed; third parties cite "up to 12 hours" for *external* feeds (unverified); our poll interval is under our control | [Google Help](https://support.google.com/calendar/answer/37648) [official doc] |
| Google Calendar API, OAuth Desktop-app client, scope `calendar.events.readonly` | Yes: GCP project + consent screen + user grant | Workspace admin can restrict scopes | No | seconds | [scopes](https://developers.google.com/workspace/calendar/api/auth), [quickstart](https://developers.google.com/workspace/calendar/api/quickstart/python) [official doc]. Trap: app in "Testing" status → refresh token expires after 7 days; "In production" with a sensitive scope shows an unverified-app warning until verification ([Google Cloud Help](https://support.google.com/cloud/answer/15549945), [OAuth2 doc](https://developers.google.com/identity/protocols/oauth2)) |
| Windows Mail & Calendar local store | — | — | — | — | **dead**: "Support for Windows Mail, Calendar, and People ended December 31, 2024" ([Microsoft](https://support.microsoft.com/office/windows-mail-calendar-and-people-are-becoming-outlook-773ecb94-5b16-4155-96e1-bc9afdc08e31)) |
| **Outlook classic COM** (`Outlook.Application` → `GetNamespace("MAPI")` → `GetDefaultFolder(olFolderCalendar).Items`; Sort by `[Start]`, `IncludeRecurrences=True`, then `Restrict` on a bounded date range) | No — reads the local OST | Only via Office object-model-guard / programmatic-access policy, or by removing classic Outlook | **Yes** | near real-time vs cached mailbox | [IncludeRecurrences](https://learn.microsoft.com/en-us/office/vba/api/outlook.items.includerecurrences), [automation](https://learn.microsoft.com/en-us/office/vba/outlook/concepts/getting-started/automating-outlook-from-a-visual-basic-application) [official doc]. CreateObject initialises Outlook (starts it if closed — inferred). |
| **New Outlook** (olk.exe) | — | — | — | — | **COM not supported**: "COM and VSTO add-ins aren't supported in the new Outlook on Windows" ([MS](https://learn.microsoft.com/en-us/office/dev/add-ins/outlook/outlook-add-ins-overview), [one-outlook](https://learn.microsoft.com/en-us/office/dev/add-ins/outlook/one-outlook)) [official doc]. That new Outlook also registers no automation server is inferred. |
| Microsoft Graph `GET /me/calendarView` (device-code or interactive flow; `Calendars.ReadBasic` least-privileged) | Yes: Entra app registration + user consent | **Yes**: tenant can disable user consent → "Approval required" prompt or hard block; app registration itself may be blocked | No | seconds | [calendarView](https://learn.microsoft.com/en-us/graph/api/calendar-list-calendarview?view=graph-rest-1.0), [user consent settings](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/configure-user-consent), [admin consent UX](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/user-admin-consent-overview), [device code](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code) [official doc] |
| Outlook on the web "Publish a calendar" ICS link | No app consent; user action in settings | **Yes**: option missing when "Anonymous" domain is absent from the Exchange sharing policy — common in corporate tenants | cacheable | poll-driven | [OWA sharing](https://support.microsoft.com/en-us/office/share-your-calendar-in-outlook-on-the-web-7ecef8ae-139c-40d9-bae2-a23977ee58d5), [option missing](https://learn.microsoft.com/en-us/troubleshoot/exchange/outlook-on-the-web-issues/publish-calendar-option-missing-from-calendar-settings), [Set-SharingPolicy](https://learn.microsoft.com/en-us/powershell/module/exchange/set-sharingpolicy?view=exchange-ps) [official doc] |
| Classic Outlook "Save Calendar" (.ics) via `CalendarSharing.SaveAsICal` | No | same as COM | Yes | manual / COM-triggered | [MS](https://learn.microsoft.com/en-us/office/client-developer/outlook/pia/how-to-save-a-calendar-to-disk) [official doc] |
| Windows 11 taskbar calendar / Widgets | — | — | — | — | no API; agenda view removed and still "delayed" per [pureinfotech](https://pureinfotech.com/?p=186086) [community] |

Sandbox note [official doc]: a UWP/AppContainer process cannot activate
third-party COM classes
([CoCreateInstanceFromApp](https://learn.microsoft.com/en-us/windows/win32/api/combaseapi/nf-combaseapi-cocreateinstancefromapp)),
so Outlook COM needs an unpackaged or full-trust desktop process — another
point for WPF over MSIX-only stacks.

Decision path [inference]: personal laptop → Google secret ICS URL polled
every 5–15 min (zero consent) — upgrade to the Calendar API only if
sub-minute freshness is needed. Corporate laptop → classic Outlook COM
first (offline, no consent) → published ICS (if "Anonymous" sharing is
allowed) → Graph `Calendars.ReadBasic` (if user consent is allowed) →
manual .ics export. The PRD should make ICS-URL ingestion the universal
baseline since it covers both providers with one code path.

### Q5. Reminder parsing — confidence: MEDIUM (repo pages / registries fetched; "in 10m" handling verified only for chrono-node by reading its source)

| Library | Language | Stars | Last release | Handles "in 10m"? | License | Source |
|---|---|---|---|---|---|---|
| [chrono-node](https://github.com/wanasit/chrono) | JS/TS | 5,303 | v2.10.3, 2026-10-12 | Yes in casual mode (`(within|in|for)` + unit dictionary with `m`, `min`, `h`, `hr`; code-read, not run) | MIT | repo + raw `ENTimeUnitWithinFormatParser.ts` |
| [Microsoft.Recognizers.Text](https://github.com/microsoft/Recognizers-Text) (DateTime) | .NET primary | 1,801 | NuGet 1.8.13, 2025-02-19 | relative durations expected; not shown on fetched pages (unverified); heavyweight (npm build 11 MB) | MIT | repo, NuGet |
| [nChronic](https://github.com/robertwilczynski/nChronic) / Chronic.Core | .NET | 66 | NuGet Chronic.Core 0.4.0; repo pushed 2026-07-09 | Ruby-Chronic grammar ("3 hours from now"); "10m" unlikely (unverified) | MIT | repo, NuGet |
| [interim](https://github.com/conradludgate/interim) (maintained fork of chrono-english) | Rust | 11 | 0.2.2, 2026-11-05 | "3h", "15m ago", "next friday 8pm"; "in 10m" undocumented → regex pre-pass | MIT | repo, crates.io |
| [human-date-parser](https://github.com/technologicalMayhem/human-date-parser) | Rust | 6 | 0.3.1, 2025-03-27 | "In 2 hours", "Today 18:30", "Next Tuesday 13:25"; "10m" shorthand undocumented | MIT | repo |
| [dateparser](https://github.com/scrapinghub/dateparser) | Python | 2,863 | 1.5.0, 2026-11-04 | relative phrases yes; "10m" unverified | BSD-3 | repo, PyPI |
| [parsedatetime](https://github.com/bear/parsedatetime) | Python | 710 | 2.6, 2020-05-31 (stale) | known for "in 10 minutes" (unverified) | Apache-2.0 | repo |
| date-fns, dateutil | JS / Python | — | — | **No** natural language | — | repos |
| AutoHotkey | — | — | — | none found | — | — |

Takeaway [inference]: for the WPF/.NET recommendation the only maintained
.NET option is Microsoft.Recognizers.Text, which is heavy for four input
shapes. The user's grammar is tiny ("in 10m", "at 3pm", "tomorrow 9",
"next monday 14:00", "<task> in <dur>") — a hand-written ~100-line parser
(duration shorthand, clock time with optional am/pm, `today`/`tomorrow`/
weekday names, split task text from time phrase) is the lower-risk, lower-
footprint choice; keep Recognizers.Text as an optional later upgrade. Every
library surveyed needs a regex pre-pass for bare "10m" anyway.

### Q6. Windows toasts as the alternative — confidence: HIGH (Microsoft docs fetched)

What toasts **can** do [official doc, Windows App SDK app-notifications docs:
[overview](https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/),
[content](https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/app-notifications-content),
[schema](https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/app-notifications-schema),
[progress/data binding](https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/app-notifications-progress-bar),
[manage](https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/manage-app-notifications),
[scenario enum](https://learn.microsoft.com/en-us/windows/windows-app-sdk/api/winrt/microsoft.windows.appnotifications.builder.appnotificationscenario),
[WPF how-to](https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/app-notifications-dotnet?pivots=wpf)]:

- Up to 5 buttons; built-in system Snooze/Dismiss with a selection box of
  snooze minutes (raw XML only, not the builder).
- `scenario=reminder` / `alarm` toasts "stay on screen until the user
  dismisses it or takes action" (must have at least one button).
- `scenario=urgent` can break through Focus Assist / Do Not Disturb, subject
  to a one-time per-app user permission.
- Update in place via Tag+Group with a sequence number — but only
  data-bound fields: progress-bar properties and "the Text property on the
  top-level text elements". Updates "silently update within Notification
  Center", never re-pop, and fail if the user dismissed the toast.
- Unpackaged desktop apps: `AppNotificationManager.Default.Register()`
  handles COM/AUMID registration automatically.
- Default and maximum expiration 3 days; `RemoveByTag/Group/All`.

Where toasts **fall short** for this design [official doc + inference]:

- **Live waiting timer**: a ticking m:ss would need a per-second `Update()`
  on a data-bound text field; the docs don't rate-limit it but it only
  shows inside Notification Center once the popup has gone — the popup
  itself does not keep re-rendering. Reminder-scenario toasts stay on
  screen, but a wall of persistent toasts is not the stacked-card UX.
- **Stacking**: Windows decides how many popups are visible and in what
  order; no bottom-right persistent stack, no custom layout beyond the
  generic template; legacy cap of 20 per app in Action Center (Windows
  Phone 8.1 doc; current desktop docs don't restate it — unverified).
- **Focus Assist**: in "Priority only" normal and reminder toasts are
  suppressed unless the app is on the user's priority list; "Alarms only"
  hides everything; DND auto-enables while presenting, gaming, or in
  full-screen apps ([Microsoft](https://support.microsoft.com/en-us/windows/make-it-easier-to-focus-on-tasks-0d259fd9-e9d0-702c-c027-007f0e78eaf2),
  [KB4578007](https://support.microsoft.com/en-us/kb/4578007)). Only
  `urgent` (with consent) or `alarm` reliably break through.
- Elevated processes cannot send toasts.

Implementation routes if toasts are used as a secondary channel: Windows
App SDK `AppNotificationManager` (C#, works unpackaged), Python
[windows-toasts 1.3.1](https://windows-toasts.readthedocs.io/en/latest/)
(needs a custom AUMID or toasts say "Command Prompt"), PowerShell
[BurntToast 1.1.0](https://www.powershellgallery.com/packages/BurntToast).

Verdict [inference]: toasts are a good *fallback channel* (reminder-scenario
toast with system Snooze/Dismiss when the overlay is hidden, or `urgent`
for a stale permission wait), not the primary UI.

### Q7. Risks and open questions for the product role

1. **Claude Code moves fast.** Hook behaviour is version-gated in several
   places on the docs page fetched today (`permission_prompt` in Desktop
   only from v2.1.233; `onFailure` from v2.1.295; `bypass_permissions_disabled`
   removed in v2.1.234). The PRD should pin a minimum Claude Code version
   and the planner should budget for a hooks-contract test. [official doc]
2. **No "user answered" event.** Card clearing is an inferred state machine
   over subsequent events (Q1 table). Edge cases: user denies permission and
   closes the terminal (SessionEnd fires on exit — fine), user answers in a
   way that produces no further event for minutes (Claude thinking), two
   sessions with the same `cwd`. Needs a probe before the PRD commits to
   "cards auto-clear". [inference]
3. **AskUserQuestion / plan-approval waits** may not fire any hook
   (GitHub #13830, #9878 — not fetched, titles only). Probe
   `PermissionRequest` matched on `AskUserQuestion` and the
   `agent_needs_input` matcher. [community, unverified]
4. **Project label**: no project-name field in payloads; `cwd` basename is
   the only cheap label. Desktop-app worktree sessions will show the
   worktree folder name. [official doc + inference]
5. **Multiple machines**: hooks run on the machine running Claude Code;
   the user has more than one laptop. v1 is one overlay per machine; a
   cross-machine view would need a sync layer the user's privacy stance
   constrains to LAN/NAS. [inference]
6. **Corporate machine**: self-contained .NET exe needs no admin, but
   SmartScreen / AppLocker / AV may block an unsigned binary; the hooks
   HTTP listener on loopback is normally fine. Classic vs new Outlook
   determines whether the COM path exists at all. [inference]
7. **RAM numbers for Tauri/Electron are not Windows-measured**, and WPF
   numbers are anecdotal. If the fan constraint is hard, the planner
   should prescribe a 1-hour spike: blank WPF overlay vs blank Tauri
   overlay, Task Manager private working set + CPU at idle with a 1 Hz
   timer. [inference]
8. **Timer start time**: `permission_prompt` lags the real wait by ~6 s;
   use `PermissionRequest`/`Stop` timestamps. If the PRD wants
   "waiting since the moment Claude asked", `PermissionRequest` is
   mandatory, and it must answer within the hook timeout with no decision.
   [official doc + inference]
9. **Google ICS feed latency** is unpublished; if meetings get added
   minutes before they start, ICS may miss them; the Calendar API path
   carries the 7-day-refresh-token trap unless the OAuth app is published.
   [official doc / unverified third party]
10. **FILES button** is out of scope for v1 (dispatch), but the pill input
    and bell are shared chrome; the PRD should say what the bell's unread
    badge counts (cards not yet seen? snoozed?). [design question]

## Competing solutions table

| Solution | Covers Claude waits | Persistent stack | Live timer | Snooze | Reminders | Calendar | Local-only | Windows | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| Claude desktop app built-in notification | finish only | no | no | no | no | no | yes | yes | insufficient |
| cc-notification / CCN / notify (toasts) | yes | Action Center only | no | no | no | no | yes | yes | alert only |
| claude-status-bar | yes (dot) | single strip | current turn only | no | no | no | yes | yes | closest, not cards |
| AgentTray | yes | tray popup | no | no | no | no | yes | partial | multi-agent model worth copying |
| WReminder / TaskCue / Stickies | no | single popup | no | yes | yes | no | yes | yes | reminders only |
| ntfy / Gotify clients | via hook → server | partial | no | no | no | no | self-hosted | yes | needs server |
| Windows toasts (DIY) | yes | Action Center | no (NC only) | system snooze | yes | yes | yes | yes | fallback channel |
| **Assistant (proposed)** | yes | yes | yes | yes | yes | yes | yes | yes | fills the gap |

## Risks / unknowns

See Q7. The three that most affect the PRD: (a) no "user answered" event
→ inferred clearing; (b) `AskUserQuestion` / plan-approval coverage
unverified; (c) stack footprint numbers need a 1-hour Windows spike if the
fan constraint is a hard requirement.

## Recommendation

**Build** — the hook contract exists and is documented for both CLI and
the desktop app, no existing Windows tool offers a stacked, snoozable,
live-timer overlay that also carries reminders and meetings, and the
privacy constraint rules out the hosted alternatives. Build it as a
single-process WPF (.NET 8/9) self-contained app with a loopback HTTP hook
listener, ICS-URL calendar ingestion as the universal baseline plus classic
Outlook COM where available, and a hand-written reminder grammar. Before
the PRD is final, run two cheap probes: a 10-minute hook probe to confirm
which event fires for `AskUserQuestion` and how a denied permission can be
detected, and a 1-hour WPF-vs-Tauri blank-overlay footprint spike on the
user's laptop.

## Recommendations for PRD

1. **Minimum Claude Code version** >= v2.1.233 (desktop `permission_prompt`),
   configured via `~/.claude/settings.json` user-level hooks: `PermissionRequest`
   (no matcher), `Notification` (no matcher), `Stop`, `StopFailure`,
   `UserPromptSubmit`, `PostToolUse`, `SessionStart`, `SessionEnd`, all
   `type: "http"` to `http://127.0.0.1:<port>/hook`, `onFailure: "continue"`.
   The overlay ships an installer step that merges these entries.
2. **Card model = per-session state machine**, not per-event toast: one
   card per `session_id`, state in {working, needs-permission, finished,
   failed, elicitation}, label from `cwd` basename, timer from the
   `PermissionRequest`/`Stop` timestamp, cleared by any later event from
   the same session. Snooze hides a session card for N minutes but
   re-shows it if the state changes.
3. **Reminder grammar** (v1): `<text> in <N>[m|h|d]`, `<text> at <h[:mm]>[am|pm]`,
   `<text> tomorrow <h[:mm]>`, `<text> <weekday> <h[:mm]>`; anything else
   → inline "didn't understand" state, never a silent failure.
4. **Calendar sources** (v1): one or more ICS URLs polled every 5 min
   (covers Google secret address and Outlook published calendar), plus
   classic Outlook COM when `Outlook.Application` is registered. Graph is
   v2 at most. Meeting card appears at T-N minutes (user setting, default
   5), auto-dismisses at end time.
5. **Overlay behaviour**: `Topmost`, frameless, transparent, no taskbar
   button, no focus stealing, click-through outside cards/pill/bell,
   positioned inside the work area bottom-right, autostart via HKCU Run.
6. **Fallback channel**: a `scenario=reminder` Windows toast with system
   Snooze/Dismiss only when the overlay is hidden or the card has waited
   longer than a threshold; never as the primary surface.
7. **Non-goals for v1**: FILES button, cross-machine sync, Graph OAuth,
   approving permissions from the card (ccnotifs shows it is possible via
   the `PermissionRequest` decision object — tempting, but a safety
   decision for the user to make explicitly later).
8. **Acceptance probes the PRD should require of the planner**: hook probe
   (which events fire for permission / AskUserQuestion / plan approval in
   both CLI and desktop app; payloads logged), footprint spike (idle RAM
   and CPU of the chosen stack with a 1 Hz timer), and a Focus Assist test
   that the overlay stays visible while toasts are suppressed.

## URLs fetched (2026-11-06)

Claude Code (researcher):
- https://code.claude.com/docs/en/hooks (and `/hooks.md`, three segments)
- https://code.claude.com/docs/en/desktop
- https://docs.claude.com/en/docs/claude-code/hooks (301 → above)

Prior art (subagent A):
- https://github.com/dazuiba/CCNotify
- https://github.com/foxytanuki/ccnotify
- https://github.com/sprklai/agenttray
- https://github.com/kmio11/cc-notification
- https://github.com/enescaakir/notify
- https://github.com/yangshare/claude-code-notify
- https://github.com/polyphilz/ccnotifs
- https://github.com/BankkRoll/claude-status-bar
- https://github.com/claude-watcher/gtk
- https://dev.classmethod.jp/articles/notification-claudecode-in-windows/
- https://gist.github.com/latenssi/e3a54bbdd84f6d595908c10931ac9b9f
- api.github.com/repos/{dazuiba/CCNotify, sprklai/agenttray, kmio11/cc-notification, enescaakir/notify, BankkRoll/claude-status-bar, claude-watcher/gtk, polyphilz/ccnotifs, yangshare/claude-code-notify}
- https://alternativeto.net/software/wreminder/about/
- https://www.producthunt.com/p/taskcue-unignorable-desktop-reminders/taskcue-unignorable-desktop-reminders
- https://github.com/Aetherinox/ntfy-desktop
- https://github.com/seird/gotify-tray
- https://grimore.org/windows/winify
- https://www.zhornsoftware.co.uk/stickies/

Tech stack and toasts (subagent B):
- https://dev.to/gethopp/tauri-vs-electron-performance-bundle-size-and-the-real-trade-offs-1el4
- https://moklabs-research.pages.dev/reports/technology/2026-03-20-tauri-vs-electron-desktop-framework-decision
- https://buildr-sh.pages.dev/posts/electron-is-a-tax.html
- https://tech-insider.org/tauri-vs-electron-2026/
- https://www.pkgpulse.com/guides/electron-vs-tauri-2026
- https://vilunov.me/blog/windows-ui/
- https://v2.tauri.app/reference/config/
- https://v2.tauri.app/reference/javascript/api/namespacewindow/
- https://v2.tauri.app/plugin/autostart/
- https://v2.tauri.app/distribute/windows-installer/
- https://www.electronjs.org/docs/latest/api/browser-window
- https://www.electronjs.org/docs/latest/api/app
- https://www.electronjs.org/docs/latest/api/structures/base-window-options
- https://www.electronjs.org/docs/latest/tutorial/window-customization
- https://www.electronjs.org/docs/latest/tutorial/custom-window-styles
- https://www.electronjs.org/docs/latest/tutorial/application-distribution
- https://www.electronforge.io/config/makers
- https://www.electronforge.io/config/makers/squirrel.windows
- https://learn.microsoft.com/en-us/dotnet/api/system.windows.window.topmost
- https://learn.microsoft.com/en-us/dotnet/api/system.windows.window.allowstransparency
- https://learn.microsoft.com/en-us/windows/win32/winmsg/extended-window-styles
- https://learn.microsoft.com/en-us/dotnet/core/deploying/
- https://github.com/dotnet/wpf/issues/8306
- https://learn.microsoft.com/en-us/answers/questions/1285803/why-the-wpf-application-consumes-much-more-(nearly
- https://www.github.com/microsoft/microsoft-ui-xaml/discussions/9154
- https://github.com/microsoft/microsoft-ui-xaml/issues/2992
- https://learn.microsoft.com/en-sg/answers/questions/1463839/how-to-make-winui-3-window-transparent
- https://www.autohotkey.com/docs/v2/lib/Gui.htm
- https://www.autohotkey.com/docs/v2/lib/WinSetTransparent.htm
- https://www.autohotkey.com/docs/v2/lib/WinSetTransColor.htm
- https://www.autohotkey.com/docs/v2/Scripts.htm
- https://www.autohotkey.com/docs/v2/FAQ.htm
- https://doc.qt.io/qt-6/qt.html
- https://www.pythonguis.com/faq/packaged-installer-file-sizes/
- https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution
- https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys
- https://learn.microsoft.com/en-us/answers/questions/3918271/keeping-python-application-always-on-top-(even-dur
- https://www.th.gl/faq/overlay-not-visible-fullscreen
- https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/
- https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/app-notifications-content
- https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/app-notifications-schema
- https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/app-notifications-progress-bar
- https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/app-notifications-ux-guidance
- https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/app-notifications-dotnet?pivots=wpf
- https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/manage-app-notifications
- https://learn.microsoft.com/en-us/windows/windows-app-sdk/api/winrt/microsoft.windows.appnotifications.builder.appnotificationscenario
- https://learn.microsoft.com/en-us/uwp/api/windows.ui.notifications.toastnotification.expirationtime
- https://learn.microsoft.com/en-us/previous-versions/windows/apps/dn631259(v=win.10)
- https://support.microsoft.com/en-us/windows/make-it-easier-to-focus-on-tasks-0d259fd9-e9d0-702c-c027-007f0e78eaf2
- https://support.microsoft.com/en-us/kb/4578007
- https://windows-toasts.readthedocs.io/en/latest/ (+ getting_started, interactable)
- https://www.powershellgallery.com/packages/BurntToast
- https://toastit.dev/

Calendar and parsing (subagent C):
- https://developers.google.com/workspace/calendar/api/auth
- https://developers.google.com/identity/protocols/oauth2
- https://support.google.com/calendar/answer/37648
- https://support.google.com/calendar/answer/37100
- https://developers.google.com/workspace/calendar/api/quickstart/python
- https://support.google.com/cloud/answer/10311615
- https://support.google.com/cloud/answer/15549945
- https://knowledge.cheqroom.com/helpcenter/how-often-does-google-update-its-calendar-feeds (third party)
- https://learn.microsoft.com/en-us/graph/api/calendar-list-calendarview?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/configure-user-consent
- https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/configure-admin-consent-workflow
- https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/user-admin-consent-overview
- https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code
- https://learn.microsoft.com/en-us/office/dev/add-ins/outlook/outlook-add-ins-overview
- https://learn.microsoft.com/en-us/office/dev/add-ins/outlook/one-outlook
- https://learn.microsoft.com/en-us/office/vba/api/outlook.items.includerecurrences
- https://learn.microsoft.com/en-us/office/vba/outlook/concepts/getting-started/automating-outlook-from-a-visual-basic-application
- https://learn.microsoft.com/en-us/office/client-developer/outlook/pia/how-to-save-a-calendar-to-disk
- https://learn.microsoft.com/en-us/windows/win32/api/combaseapi/nf-combaseapi-cocreateinstancefromapp
- https://learn.microsoft.com/en-us/powershell/module/exchange/set-sharingpolicy?view=exchange-ps
- https://learn.microsoft.com/en-us/troubleshoot/exchange/outlook-on-the-web-issues/publish-calendar-option-missing-from-calendar-settings
- https://support.microsoft.com/en-us/office/share-your-calendar-in-outlook-on-the-web-7ecef8ae-139c-40d9-bae2-a23977ee58d5
- https://support.microsoft.com/en-us/office/share-an-outlook-calendar-with-other-people-353ed2c1-3ec5-449d-8c73-6931a0adab88
- https://support.microsoft.com/office/windows-mail-calendar-and-people-are-becoming-outlook-773ecb94-5b16-4155-96e1-bc9afdc08e31
- https://pureinfotech.com/?p=186086
- https://github.com/wanasit/chrono (+ raw `src/locales/en/constants.ts`, `ENTimeUnitWithinFormatParser.ts`, `ENTimeUnitCasualRelativeFormatParser.ts`)
- https://github.com/conradludgate/interim
- https://github.com/technologicalMayhem/human-date-parser
- https://github.com/DevinVS/fuzzydate
- https://github.com/bear/parsedatetime
- https://github.com/robertwilczynski/nChronic
- https://github.com/microsoft/Recognizers-Text (+ `.NET/README.md`, `JavaScript/packages/recognizers-date-time/README.md`)
- https://github.com/stevedonovan/chrono-english, https://github.com/dfhoughton/two-timer, https://github.com/neilgupta/Sherlock (raw READMEs)
- api.github.com/repos/{wanasit/chrono, neilgupta/Sherlock, date-fns/date-fns, stevedonovan/chrono-english, conradludgate/interim, DevinVS/fuzzydate, technologicalMayhem/human-date-parser, dfhoughton/two-timer, microsoft/Recognizers-Text, robertwilczynski/nChronic, scrapinghub/dateparser, bear/parsedatetime, dateutil/dateutil}
- crates.io API for chrono-english, fuzzydate, human-date-parser, two_timer, interim; npm registry for chrono-node, @microsoft/recognizers-text-date-time, sherlockjs; PyPI for dateparser, parsedatetime, python-dateutil; NuGet for Microsoft.Recognizers.Text.DateTime, Chronic, nChronic
- https://dateparser.readthedocs.io/en/latest/

Searched but not fetched (titles/snippets only; marked unverified above):
anthropics/claude-code issues #13830, #13024, #9878, #36885, #10168;
BetaNews Electron memory article; ghisler.ch AHK memory thread.
