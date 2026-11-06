# Research brief: Assistant feature catalog and plugin system

- Date: 2026-11-06
- Author: researcher (TeamAgents)
- Status: first version. Append-only from here (archaeology rule): new findings go in dated sections at the end.
- Hand-off: tech-lead → product (PRD: feature list + extensibility section)
- Companion briefs (do not duplicate): `2026-11-06-desktop-alert-overlay.md` (Claude hooks, calendar, toasts, overlay stack), `2026-11-06-fast-file-search.md` (index engine, query syntax, previews, shell actions, hotkey, MCP). Behavioural spec of the app being rebuilt: `docs/reference/2026-11-06-assistant-popup-readme.md`.

## Question

Assistant is a pure-Python Windows 11 desktop app (pip-only dependencies, no admin, AppLocker allows the approved `python.exe` but blocks unknown executables) made of (1) an always-on-top alert overlay fed by Claude Code hooks, an MCP tool, a local HTTP API and a file drop; (2) a hotkey/icon search bar with a Files mode and an Ask-notes mode; (3) an MCP server. The user wants other people to use it and extend it with their own plugins. Two questions:

1. **What do users expect from apps in this category?** Survey launchers / command palettes, notification and reminder hubs, and Everything-style finders; produce a consolidated feature table with a core / nice / skip verdict for Assistant.
2. **How should Assistant let non-developers add plugins easily?** Compare ten plugin ecosystems, propose Assistant's extension points with minimal Python interfaces and manifest fields, pick a v1 mechanism (folder drop vs pip entry points vs both; in-process vs subprocess), state the security model honestly, and compare a community catalog with "paste a GitHub URL".

Sub-questions written before searching: who praises what (reviews, READMEs, top-reacted issues); what is most requested and still missing; which ecosystems are Python-based and how they load code; what Python packaging offers natively (entry points, pluggy, stevedore, `pip --target`); whether in-process Python can be sandboxed at all; how catalog-in-git ecosystems actually work (Flow Launcher, Obsidian, HACS, Package Control); what a URL-paste installer costs (GitHub API limits).

## Method

- No `deep-research` skill exists in the TeamAgents or user skills directories (checked `C:\AI Projects\TeamAgents\.claude\skills\` and `C:\Users\<you>\.claude\skills\`), so this was a structured fan-out: four read-only research subagents ran in parallel on (A) launcher features, (B) notification hubs and finders, (C) plugin ecosystems, (D) Python plugin mechanics and security. The researcher then fetched the Flow Launcher Python client source directly to settle the one load-bearing mechanical question (per-request process vs long-lived).
- Every URL in "URLs fetched" at the end was fetched and read on 2026-11-06 by the researcher or a subagent. Items marked **[unverified]** come from search snippets or memory and were not confirmed on a fetched page. **[inference]** marks our own reasoning.
- Source classes: **[official doc]** (vendor/Microsoft/Python docs), **[repo]** (README, raw source, GitHub API counts), **[issue]** (GitHub issue with reaction count), **[community]** (forum, blog, Product Hunt), **[inference]**.
- Confidence scale: **High** = primary source read this session; **Medium** = README/forum/consistent secondaries; **Low** = snippet, anecdote, or extrapolation.
- Adversarial pass: searched for evidence that the plugin idea is over-engineering (do any Windows notification hubs have plugins? do users actually install plugins in launchers?), that in-process Python plugins are a security dead end (they are, see Q5), that "paste a GitHub URL" is a trap (rate limits, corporate proxies), and that curated catalogs are expensive (they are run by volunteers with multi-day lag).
- Known gaps: `alfredapp.com` refused connections three times (Alfred claims rest on alfred.app and Wikipedia); `albertlauncher.github.io/extending/*` and `/python/` are 404 and `albertlauncher/python` is archived (Albert Python API details are [unverified]); `flowlauncher.com/docs` is a JavaScript SPA, so Flow's docs were read from the raw markdown in `github.com/Flow-Launcher/docs`.

## Findings

### Q1. Feature catalog — confidence: High for features on fetched docs/READMEs; Medium for "most requested" (reaction counts are a proxy for demand, not usage data)

#### Q1a. What each tool is praised for and what its users ask for

**Launchers / command palettes** [repo, official doc, issue]:

| Tool | Platform, size (2026-11-06) | Praised for | Most-requested / missing |
|---|---|---|---|
| [PowerToys Run](https://learn.microsoft.com/en-us/windows/powertoys/run) | Windows; PowerToys repo 140,626 stars | Alt+Space; per-plugin activation keywords (`=` calc, `>` shell, `<` windows, `??` web); context buttons with shortcuts (run as admin, open containing folder Ctrl+Shift+E, copy path Ctrl+C); Window Walker; "Preferred monitor position" primary / mouse / focused; "Selected item weight" usage boost; History plugin | Dictionary plugin [#3309, 174 reactions](https://github.com/microsoft/PowerToys/issues/3309); switch to running app instead of new instance [#5962, 71](https://github.com/microsoft/PowerToys/issues/5962); skin/theme/transparency [#4794, 42](https://github.com/microsoft/PowerToys/issues/4794); timer plugin [#8064, 45](https://github.com/microsoft/PowerToys/issues/8064); ChatGPT integration [#23143, 58](https://github.com/microsoft/PowerToys/issues/23143); snippets [#15032, 26](https://github.com/microsoft/PowerToys/issues/15032); remember window position [#4576, 24](https://github.com/microsoft/PowerToys/issues/4576). Docs say Run "is getting an upgrade to v2" (Command Palette); parity tracking [#41696, 51](https://github.com/microsoft/PowerToys/issues/41696) |
| [PowerToys Command Palette](https://learn.microsoft.com/en-us/windows/powertoys/command-palette/overview) ([settings](https://learn.microsoft.com/en-us/windows/powertoys/command-palette/settings)) | Windows | Win+Alt+Space; full palette or compact search box with draggable vertical position; pinned + recent home page; built-in clipboard history, bookmarks with `{query}` placeholders, window switcher, WinGet search; Extension Gallery in settings ("Browse, install, update, and uninstall community extensions directly"); rich personalisation (acrylic/mica, opacity, background image); "Open on" monitor with mouse / primary / focused / don't move / last position; `x-cmdpal://` command links with per-link consent | Global results without typing a prefix [#38683, 40](https://github.com/microsoft/PowerToys/issues/38683); reorder top-level commands [#38595, 14](https://github.com/microsoft/PowerToys/issues/38595); direct file access without prefix [#39713, 11](https://github.com/microsoft/PowerToys/issues/39713); hide unwanted apps [#41497](https://github.com/microsoft/PowerToys/issues/41497); switch-to-running [#39125](https://github.com/microsoft/PowerToys/issues/39125) |
| [Flow Launcher](https://github.com/Flow-Launcher/Flow.Launcher) | Windows; 15,967 stars, pushed 2026-11-06 | Configurable Alt+Space; "Supports Everything and Windows Index" (no own index); F1 preview panel; Ctrl+O / Shift+Enter context menu, Ctrl+Enter containing folder; acronym matching (`acr` → Acrobat); plugins in .NET / F# / Python / Node; Plugin Store in settings plus `pm install/update/uninstall` from the search bar; custom themes; fully portable build; backup = copy the UserData folder ([usage tips](https://raw.githubusercontent.com/Flow-Launcher/docs/main/usage-tips.md)) | Export/import settings [#2643, 8](https://github.com/Flow-Launcher/Flow.Launcher/issues/2643); settings sync [#2684, 7](https://github.com/Flow-Launcher/Flow.Launcher/issues/2684); native ARM64 [#2318, 8](https://github.com/Flow-Launcher/Flow.Launcher/issues/2318); Win+Space hotkey blocked [#2709, 6](https://github.com/Flow-Launcher/Flow.Launcher/issues/2709); workflow chaining [#1128, 6](https://github.com/Flow-Launcher/Flow.Launcher/issues/1128); better search algorithm [#3459, 5](https://github.com/Flow-Launcher/Flow.Launcher/issues/3459); open folders in existing file-manager window [#1915, 7](https://github.com/Flow-Launcher/Flow.Launcher/issues/1915) |
| [Wox v2](https://github.com/Wox-launcher/Wox) ([site](https://www.woxlauncher.com/)) | Win/mac/Linux; 27,599 stars, v2.4.9 Oct 2026 | "native Raycast / Alfred alternative", GPU rendering, "~150 MB of memory"; built-in clipboard history, converter, bookmarks + open tabs, Quick Jump into file dialogs, Notes, Cloud Sync, Backup, Doctor; AI Chat and AI Commands; plugins in Node.js / Python / script with a plugin store and a theme store; SignPath code signing | Historic v1 asks (all closed): portable version [#629, 24](https://github.com/Wox-launcher/Wox/issues/629); "Everything Service is not running" false error [#1176, 17](https://github.com/Wox-launcher/Wox/issues/1176); mouse moves selection [#975, 12](https://github.com/Wox-launcher/Wox/issues/975); font size [#1987, 10](https://github.com/Wox-launcher/Wox/issues/1987); switch-to-running [#1922, 10](https://github.com/Wox-launcher/Wox/issues/1922); Alfred-like snippets [#1614, 9](https://github.com/Wox-launcher/Wox/issues/1614) |
| [Keypirinha](https://keypirinha.com/) ([first steps](https://keypirinha.com/first.html), [FAQ](https://keypirinha.com/faq.html)) | Windows; closed-source core, Python plugins | "Lightning fast", "Battery friendly", "File-driven configuration", freeware with no nag; learns keyword→item associations; Ctrl+Enter actions menu, Tab for arguments, drag-out of results; shipped Everything package; installed vs portable mode; persistent window geometry | No public tracker. Observed gaps: no clipboard history, no auto-update (manual procedure), no AI, no release date on site [community/inference] |
| [ueli](https://github.com/oliverschwendener/ueli) | Win/mac/Linux Electron; 4,615 stars | Alt+Space; Ctrl+K actions; favourites pinned first; 23 bundled extensions (bookmarks, calculator, file search, web search, VS Code, Terminal launcher) | Third-party plugin support [#96, 42 +1](https://github.com/oliverschwendener/ueli/issues/96) is the top ask; switch to open program [#86, 27](https://github.com/oliverschwendener/ueli/issues/86); remap shortcuts [#325, 7](https://github.com/oliverschwendener/ueli/issues/325); remember window size [#1104, 7](https://github.com/oliverschwendener/ueli/issues/1104); image preview thumbnails [#926, 4](https://github.com/oliverschwendener/ueli/issues/926) |
| [Raycast](https://www.raycast.com/) ([Windows](https://www.raycast.com/windows), [Windows 2.0 changelog](https://www.raycast.com/changelog/windows/2-0)) | macOS + Windows (2.0 out of beta Aug 2026); extensions repo 7,834 stars | Hotkeys/aliases per command; clipboard history, snippets, quicklinks, natural-language calculator, window management; store with "thousands" of open-source extensions, TypeScript API with hot reload; Raycast AI (Quick AI, chat, local models, BYOK — Pro); background auto-update; Enterprise extension allow-list | Cloud Sync on Windows "upcoming"; macOS feature parity; AI needs internet ("most core features work offline, but AI features do not"); Dictation and AI Chat moved behind Pro at 2.0 |
| Alfred [Wikipedia](https://en.wikipedia.org/wiki/Alfred_(software)) (official site unreachable) | macOS only | Usage learning ("uses repeated queries to prioritize search results"); file actions; Powerpack: clipboard history, snippets, workflows (node-based), themes; [Gallery](https://alfred.app/) for workflows | macOS only; Powerpack paywall; no built-in AI [unverified] |
| [Albert](https://github.com/albertlauncher/albert) ([basics](https://albertlauncher.github.io/basics/)) | Linux/macOS; 8,005 stars | Trigger handlers (prefix → one handler, inline hints), global handlers "sorted by match and usage score", fallback handlers; alt-actions on Ctrl+Return; Vim/Emacs bindings; curated, peer-reviewed plugin set loaded/unloaded at runtime | Switch to running app [#123, 57](https://github.com/albertlauncher/albert/issues/123); Wayland hotkeys [#309, 19](https://github.com/albertlauncher/albert/issues/309); clipboard history [#255, 17](https://github.com/albertlauncher/albert/issues/255) |

**Notification / reminder hubs** [repo, official doc, issue, community]:

| Tool | Size | Praised for | Most-requested / missing |
|---|---|---|---|
| [ntfy](https://github.com/binwiederhier/ntfy) ([publish docs](https://docs.ntfy.sh/publish/), [web app](https://docs.ntfy.sh/subscribe/web/)) | 35,343 stars; Go server + mobile + PWA; **no first-party Windows client** | 5 priority levels; tags/emoji; Markdown body; click URL; up to 3 action buttons (view / http / copy; `broadcast` Android-only); attachments; icons; scheduled delivery (`X-Delay`, `X-In`, natural language); topic mute; message templates | iOS critical alerts [#1235, 44](https://github.com/binwiederhier/ntfy/issues/1235); E2E encryption [#69, 43](https://github.com/binwiederhier/ntfy/issues/69); synchronize between devices [#159, 32](https://github.com/binwiederhier/ntfy/issues/159); group by thread #729; topic browser #1475. Nothing top-reacted about snooze, DND schedule or a Windows client |
| [ntfy-desktop](https://github.com/Aetherinox/ntfy-desktop) | 229 stars, Electron | Sticky ("require user interaction to clear") vs timed notifications; start minimised; hotkeys; topic filter; taskbar unread count; polling interval | Issues not fetched [unverified] |
| [Gotify](https://github.com/gotify/server) + [gotify-tray](https://github.com/seird/gotify-tray) (Python/Qt, 135 stars) | 16,181 stars | Per-app tokens and priority; message history; tray: "Disable notification banners for low priority messages", history of all messages, reconnect after sleep, dark theme | Auth plugin [#203, 22](https://github.com/gotify/server/issues/203); configurable retention [#376, 21](https://github.com/gotify/server/issues/376); update an existing message for ongoing tasks [#302, 13](https://github.com/gotify/server/issues/302); notification actions [#494, 9](https://github.com/gotify/server/issues/494); groups for apps [#800, 9](https://github.com/gotify/server/issues/800); sync read state between clients [#266, 7](https://github.com/gotify/server/issues/266) |
| [BurntToast](https://github.com/Windos/BurntToast) | 1,732 stars; **archived, read-only** | PowerShell toast sender: Urgent switch, buttons, hero image, progress bar, snooze-and-dismiss [unverified: docs site DNS failed] | n/a — sender library, no longer maintained |
| [SnoreToast](https://github.com/KDE/snoretoast) | 361 stars | CLI toasts with buttons, text box, sound, image, callback via named pipe, exit codes for Dismissed / TimedOut / ButtonPressed | n/a — sender only |
| [Zhorn Stickies](https://www.zhornsoftware.co.uk/stickies/) | closed source | Alarms, recurring wake (daily/weekly/monthly), sleep-until, attach note to app/window/site, send notes over TCP/IP, skins, SQLite store, "An API allows integration with other applications" | n/a (no public tracker) |
| [TaskCue](https://www.producthunt.com/products/taskcue-unignorable-desktop-reminders) | paid, portable, 1 upvote | Centre-screen "genuinely hard to ignore" popup, native alert sound, runs from USB | Snooze 5/10 min (several commenters); reminder that stays until marked done (Esc dismisses and user forgets); Google Calendar / Outlook sync |
| Windows "notification center replacement" | — | Search found no credible open-source Windows notification hub/history tool (only a Store "Notification Logger" [unverified] and Linux projects) [community] | This is a gap on Windows [inference] |

**Everything-style finders and preview companions** (user-facing features only; indexing internals are in the file-search brief) [official doc, repo, issue, community]:

| Tool | Praised for | Most-requested |
|---|---|---|
| [Everything](https://www.voidtools.com/) ([1.5 beta thread](https://www.voidtools.com/forum/viewtopic.php?t=9787)) | "Minimal resource usage", "Real-time updating"; 1.5: dark mode, filter bar, bookmarks rework, search history dropdown, context menu commands, custom open commands, copy as TSV, preview pane, content search, Windows-key hotkey override | Preview pane docking position / horizontal ([forum](https://voidtools.com/forum/viewtopic.php?p=29858), [various preview improvements](https://mail.voidtools.com/forum/viewtopic.php?p=36603)), multi-preview ([forum](https://www.voidtools.com/forum/viewtopic.php?p=58639)), details pane "on my TODO list" ([dev](https://voidtools.com/forum/viewtopic.php?p=79451)) — forum thread bodies [unverified], titles from search |
| [Listary](https://www.listary.com/) | Results ordered by usage habits; fuzzy app launch; Quick Switch into Open/Save dialogs; copy / copy path actions; type/date filters | not researched |
| [Fluent Search](https://fluentsearch.net/) | Native indexer "with low CPU"; search tags; community plugins; portable zip | not researched |
| [PowerToys Peek](https://learn.microsoft.com/en-us/windows/powertoys/peek) | Space to preview; images, Office, video, web, Markdown, text, SQLite (200 rows/table), source files, archives, folders (size scan); Left/Right between files; pin window; Delete key → Recycle Bin with confirmation; command-line launch | — |
| [QuickLook](https://github.com/QL-Win/QuickLook) | 25,669 stars; "100+ formats"; `.qlplugin` plugins (OfficeViewer, PdfViewer-Native, CADImport); Explorer and Open/Save dialog integration; portable | Disable update nags [#749](https://github.com/QL-Win/QuickLook/issues/749), [#1106](https://github.com/QL-Win/QuickLook/issues/1106); dark/light mode [#1343](https://github.com/QL-Win/QuickLook/issues/1343); font size [#271](https://github.com/QL-Win/QuickLook/issues/271); "why this app doesn't have settings?" [#676](https://github.com/QL-Win/QuickLook/issues/676); UI to manage plugins and settings [#1916](https://github.com/QL-Win/QuickLook/issues/1916); second-monitor preview opens on main monitor [#935](https://github.com/QL-Win/QuickLook/issues/935) (reaction counts not rendered; order is by reactions) |

**Cross-cutting patterns** [inference over the tables above]:

1. "Switch to the running app instead of opening a new one" is top-3 in four trackers (PowerToys Run, ueli, Wox, Albert). It is a launcher concern, not Assistant's — but it shows users punish tools that open duplicates; Assistant's "Open" actions should reuse existing windows where the shell allows (the file-search brief's `SHOpenFolderAndSelectItems` note covers Explorer).
2. Settings import/export and sync recur (Flow #2643/#2684, Raycast Windows sync, Wox Cloud Sync/Backup). File-driven config in one folder (Keypirinha, Flow "copy UserData") satisfies export for free.
3. Users of preview tools complain most about **missing settings UI, dark mode, font size, update nags and multi-monitor placement** (QuickLook issues). These are quality-bar items, not features.
4. Notification-hub users want **priority, action buttons, update-in-place for ongoing tasks, grouping by source, retention/history** — exactly the card model Assistant already has plus history and grouping.
5. Reminder users want **snooze presets and stay-until-done** — Assistant's existing Snooze 5m/15m and sticky cards match.
6. AI integration splits the field: Raycast and Wox ship chat; PowerToys Run users ask for it (58 reactions); Flow, Keypirinha, Albert, ueli have none. Assistant's differentiator is the reverse direction: Claude drives Assistant (hooks, MCP), and Assistant hands files to Claude. Wox's developer page says "In the era of AI agents, there is no need for a long plugin development guide" and scaffolds a plugin for the user to "paste the result into your agent" ([woxlauncher.com/development](https://www.woxlauncher.com/development/)) — directly relevant to "non-developers add plugins".
7. Third-party plugins are the single most requested feature of the one launcher that lacks them (ueli #96, 42 +1), and every launcher with a store (Flow, Wox, Raycast, Command Palette, Alfred) advertises it on the front page. Demand for extensibility in this category is real [Medium: proxy evidence].

#### Q1b. Consolidated feature table

Relevance: **core** = v1 of the rebuilt Assistant; **nice** = after v1 or as a bundled plugin; **skip** = out of scope or contradicts the privacy/local constraint. "Plugin" in the reason column means the feature should be a bundled or community plugin rather than core code.

**Alerts and cards**

| Feature | Tools that have it | Relevance | Reason |
|---|---|---|---|
| Stacked persistent cards beside a draggable icon, on every monitor | Assistant (ref README); Winify tearable toasts (overlay brief); none of the launchers | core | The gap the overlay brief identified: no Windows tool stacks snoozable cards |
| Priority levels on incoming alerts | ntfy (5), Gotify (int), BurntToast Urgent | core | Maps to card type (sticky amber/blue vs fading green/purple); expose `priority` on the HTTP API and plugin card model |
| Sticky-until-acted vs auto-expire per card | ntfy-desktop (both modes), ref README (20 s fade unless sticky), TaskCue users asked for stay-until-done | core | Already in the reference; make it a per-card field, not per-type only |
| Snooze presets | ref (5m/15m), WReminder (10/20/30/60), Windows toasts (system snooze), TaskCue requests (5/10) | core | Exists; consider a configurable preset list |
| Blink + repeating sound until hovered ("seen") | ref README; TaskCue "unignorable" | core | Exists; the differentiator vs toasts |
| Action buttons on a card | ntfy (3 buttons: view/http/copy), Gotify requested #494 (9), Windows toasts (5) | core | Plugin extension point "card actions"; ccnotifs-style approve-from-card stays v2 (overlay brief) |
| Click URL / default click action | ntfy `X-Click`, Gotify `click.url` [unverified] | core | One `url` or `on_click` per card |
| Update an existing card in place (progress, timer) | Gotify #302 (13), Windows toast Tag+Group, ref "waiting m:ss" | core | Needed for Claude session cards; `ctx.update(card_id, ...)` in the plugin API |
| Group / filter / mute by source | ntfy topics, Gotify apps (+ groups #800), ref mute | core | `source` = plugin id or API client; mute per source |
| Mute / do-not-disturb with duration | ref (until unmuted / 30 min / 1 h), Focus Assist | core | Exists |
| Unread count on icon / taskbar | ref red count, ntfy-desktop taskbar count | core | Exists |
| Card history / log | gotify-tray history, Gotify retention #376 (21); no Windows hub has it | nice | "Recent cards" list in the right-click menu; cheap once cards are persisted |
| Markdown or rich body | ntfy `X-Markdown`, Gotify extras [unverified] | nice | Reuse the existing markdown renderer for card detail |
| Icon / image per card | ntfy `X-Icon`, `X-Attach`, BurntToast hero image | nice | Icon yes (plugin branding); attachments skip |
| Scheduled delivery ("in 20m") on the API | ntfy `X-Delay`/`X-In` | nice | Reminders already cover it; add `deliver_at` to the API later |
| Windows toast as fallback channel | overlay brief Q6 | nice | Only when overlay hidden or card stale |
| Multi-device sync of cards / read state | ntfy #159 (32), Gotify #266 (7) | skip | Per-machine by design; privacy stance (memory: local over convenience) |
| End-to-end encryption, server auth plugins | ntfy #69 (43), Gotify #203 (22) | skip | No server in Assistant |

**Search and launch**

| Feature | Tools | Relevance | Reason |
|---|---|---|---|
| Global hotkey, configurable, with fullscreen/RDP suppression | all launchers; PowerToys "ignore in fullscreen" | core | Covered in the file-search brief Q10 |
| Open search from icon click | ref | core | Exists |
| Mode keywords / triggers (`?`, Tab to cycle) | Flow action keywords, PowerToys Run prefixes, Keypirinha, Albert trigger handlers | core | Plugins declare a `keyword`; Tab cycles modes |
| Merged results without a prefix | Command Palette #38683 (40), Flow/Alfred default behaviour | nice | v1 modes are explicit; a "global" mode that fans out to plugins can come later |
| Substring + word-boundary ranking (fuzzy/acronym optional) | Flow `acr`→Acrobat, PowerToys, fzf/VS Code (file-search brief Q5) | core | File-search brief already specifies the ranking |
| Frecency / usage boost | PowerToys "Selected item weight", Keypirinha learning, Albert usage score, Raycast frecency, Alfred | nice | File-search brief recommends a decayed bonus; v1.x |
| File search by name/path | all | core | Exists (Files mode) |
| Use Everything as engine if present | Flow, Keypirinha package, Wox v1 | nice | File-search brief: optional accelerator on the personal laptop |
| Query / input history | Wox Query History, PowerToys `!!`, Albert Shift+Up | nice | Small; also feeds frecency |
| App launcher | every launcher | skip (plugin) | Assistant is not a launcher; a bundled "apps" search mode is a 50-line plugin |
| Calculator, unit converter | every launcher | skip (plugin) | Classic first community plugin |
| Clipboard history | Command Palette, Wox, Raycast, Alfred (paid); Albert asked #255 | skip (plugin) | Good candidate for a bundled plugin; privacy-sensitive, must be opt-in |
| Snippets / text expansion | Raycast, Alfred; asked in PowerToys #15032 (26), Wox #1614 (9) | skip (plugin) | Search mode + "paste" result action |
| Browser bookmarks / open tabs | Command Palette, Wox, Flow plugin, Keypirinha | skip (plugin) | The dispatch's own example of a plugin search mode |
| Window switcher / switch-to-running | PowerToys Window Walker; top ask in 4 trackers | skip | Not Assistant's job; possible plugin |
| Shell / run command, web search | all | skip (plugin) | Trivial plugins; shell plugin must declare `subprocess` permission |

**Previews and actions**

| Feature | Tools | Relevance | Reason |
|---|---|---|---|
| Preview pane for selected result | Flow F1, Everything, Peek, QuickLook; ueli asked #926 | core | Exists for md/markmap/pdf/docx/xlsx |
| Rendered Markdown incl. mermaid/markmap | ref, MD-Vault (file-search brief Q8) | core | Exists |
| Previewer plugins for new file types | QuickLook `.qlplugin`, Peek handlers | core | Extension point "previewers" |
| Open / reveal in Explorer / copy path | PowerToys Ctrl+Shift+E & Ctrl+C, Flow Ctrl+Enter, Listary copy path, ref | core | Exists; implementation notes in file-search brief Q9 |
| Result context menu with shortcuts | Flow Ctrl+O, ueli/Raycast Ctrl+K, Keypirinha Ctrl+Enter, Albert alt-actions | core | Extension point "result actions" |
| "Send to Claude" / "Ask Claude about this file" | file-search brief Q12 | core | The flagship result action; routes documented there |
| Open with… | PowerToys, Peek | nice | `SHOpenWithDialog` (file-search brief) |
| Rename, properties, move to Recycle Bin (never permanent) | Peek Delete key, file-search brief Q11 | nice | User asked for these in the search module; keep as a bundled result-action plugin |
| Copy file / drag-out of results | Keypirinha drag-out | nice | Later |
| Run as administrator | PowerToys, Flow | skip | No admin on the work laptop |
| Preview pane position (side/bottom), multi-preview | Everything forum | nice | Most-threaded Everything ask; cheap toggle later |
| Full-page results (F11) and Esc stepping back | ref | core | Exists |

**Reminders and calendar**

| Feature | Tools | Relevance | Reason |
|---|---|---|---|
| Natural-language reminders ("x in 10m", "at 3pm") | ref, ntfy `X-In`, overlay brief Q5 grammar | core | Exists |
| Meeting cards from ICS URL / Outlook COM | overlay brief Q4 | core | Specified there |
| Sticky "until done" reminder option | TaskCue comments | core | One flag on the reminder dialog |
| Recurring reminders | Stickies daily/weekly/monthly, TaskCue | nice | v1.x |
| Timer / countdown ("timer 25m") | PowerToys #8064 (45) | nice (plugin) | A card source plugin that emits a card at T-0 |
| Attach note to app/window | Stickies | skip | Different product |

**Claude / AI integration**

| Feature | Tools | Relevance | Reason |
|---|---|---|---|
| Claude Code hooks → session cards (needs you / finished) | ref; CCNotify, cc-notification etc. (overlay brief Q2) | core | Exists; spec in overlay brief |
| MCP server: search files, send cards/reminders | ref; mcp-everything-search prior art (file-search brief Q13) | core | Exists |
| Ask Claude about this file / passage | file-search brief Q12 | core | Result action |
| Local Ask-notes (BM25, nothing leaves the PC) | ref | core | Exists; becomes the first built-in search mode plugin |
| "Ask Claude to write me a plugin": scaffold + authoring guide readable by an agent | Wox `wpm create` → "paste the result into your agent" ([dev page](https://www.woxlauncher.com/development/)) | core (for extensibility) | The cheapest way for a non-developer to get a plugin; ship `PLUGIN_AUTHORING.md` + `assistant plugin new <id>` |
| Built-in AI chat / AI commands | Raycast (Pro), Wox; PowerToys ask #23143 (58) | skip | Claude Code / Desktop is the chat; avoids API key handling in Assistant |
| Approve a permission from the card | ccnotifs (overlay brief) | skip v1 | Safety decision deferred (overlay brief non-goal 7) |

**Personalisation**

| Feature | Tools | Relevance | Reason |
|---|---|---|---|
| Light / dark / follow system | Command Palette, Flow, Wox, Everything 1.5; QuickLook asked #1343 | core | Expected baseline |
| Sound per card type, test sound, sound on/off | ref, ntfy priority sounds | core | Exists; add custom `.wav` per type as nice |
| Draggable icon, remembered position, reset | ref; PowerToys #4576 (24) remember position | core | Exists |
| Card colours per type configurable | ref fixed palette | nice | Plugins may add custom card kinds with a colour |
| Font size / UI scale | Wox #1987 (10), QuickLook #271 | nice | Frequently asked of preview tools |
| Opacity / backdrop / background image | Command Palette; PowerToys #4794 (42) | nice | Low priority for an overlay |
| Remappable in-app shortcuts | ueli #325 (7) | nice | Keep shortcuts in the TOML config so power users can edit |
| Theme store | Wox, Flow custom themes | skip | Not worth a marketplace; a theme is a TOML section |

**Multi-monitor**

| Feature | Tools | Relevance | Reason |
|---|---|---|---|
| Cards on every monitor (toggle) | ref | core | Exists; add "icon's monitor only" option |
| Where the search opens: follow the icon / mouse / focused / primary / last position | PowerToys Run (3 options), Command Palette (5) | core | Default "beside the icon"; offer the PowerToys set |
| Per-monitor DPI correctness | QuickLook #935 bug | core (quality bar) | Mixed-DPI laptops + docks; must be in QA acceptance |
| Dock / edge toolbar | Command Palette dock | skip | Different product |

**Settings, import/export**

| Feature | Tools | Relevance | Reason |
|---|---|---|---|
| Settings window incl. per-plugin pages | Flow, Command Palette, ueli; QuickLook asked #676, #1916 | core | Hosts the declarative plugin settings (Q3) |
| File-driven config in one folder (`~/.assistant/`) | Keypirinha, Flow UserData, Stickies single SQLite | core | Makes export = copy folder |
| Enable/disable per plugin, reload plugins | ueli toggles, Albert runtime load/unload, Flow `pm` | core | Part of the plugin manager |
| Import/export settings | Flow #2643 (8), Wox Backup | nice | "Open config folder" + documented copy covers v1 |
| Settings sync across machines | Flow #2684, Raycast cloud sync, Wox Cloud Sync | skip | Privacy; let the config dir live on OneDrive/NAS instead [inference] |
| Portable mode | Everything, Keypirinha, Fluent Search, Wox #629 (24) | nice | `git clone` + `python` already is portable if state stays in the app folder when asked |

**Privacy / offline**

| Feature | Tools | Relevance | Reason |
|---|---|---|---|
| No telemetry, everything local | ref ("nothing leaves your PC"), Keypirinha | core | Stated principle |
| HTTP API bound to 127.0.0.1 with a token | ref `token.txt`; Everything HTTP binds all interfaces unless configured (file-search brief) | core | Exists; keep |
| Plugin network use declared and visible | none of the surveyed hosts enforce it (Q5) | core (declared, not enforced) | See Q5 |
| Offline AI / local models | Raycast local models | skip | Out of scope |

**Install / update**

| Feature | Tools | Relevance | Reason |
|---|---|---|---|
| No-admin install, pip-only, Python already approved | ref `install.ps1` | core | The AppLocker argument in the reference README |
| Autostart shortcut, uninstall script, `demo.py` | ref | core | Exists |
| Update check without nags; user-triggered | Flow "Check for new update" command; QuickLook complaints #749/#1106; Alfred "Workflows must not update themselves" ([alfred.app/submit](https://alfred.app/submit/)) | core | `assistant update` = `git pull` + `pip install -r`; show one card, never a modal |
| Background auto-update | Raycast ("we think of this as a security feature") | skip | Conflicts with the user-control stance and corporate change control |
| Doctor / diagnostics command | Wox Doctor | nice | `python assistant.py --doctor`: Python version, deps, hooks installed, port free, plugins loaded |
| winget / Store / signed exe | Wox SignPath, Raycast Store | skip | AppLocker blocks exes; `git clone` is the channel |

### Q2. Plugin systems compared — confidence: High for Flow Launcher, Raycast, Obsidian, Home Assistant/HACS, Stream Deck, Keypirinha, Anki, Calibre, Sublime (primary docs read); Medium for Wox and Alfred; Low for Albert (docs 404)

| Ecosystem | Language; Python? | Manifest | Discovery and catalog | API surface | Sandbox / review | Versioning | Hot reload | Install from git URL | Settings UI |
|---|---|---|---|---|---|---|---|---|---|
| **Flow Launcher** | C#/F#/Python/JS/TS/exe; **Python yes, out-of-process**: host launches `python main.py '<json-rpc>'` **once per request**, plugin prints `{"result": [...], "debugMessage": ""}` to stdout and exits ([FlowLauncher.py source](https://raw.githubusercontent.com/Flow-Launcher/Flow.Launcher.JsonRPC.Python/master/flowlauncher/FlowLauncher.py): `self.rpc_request = loads(sys.argv[1])`); embedded Python "is isolated from the user's system" ([dev doc](https://raw.githubusercontent.com/Flow-Launcher/docs/main/develop-python-plugins.md)) | `plugin.json`: ID (UUID), ActionKeyword, Name, Description, Author, Version, Language, Website, IcoPath, ExecuteFileName ([schema doc](https://raw.githubusercontent.com/Flow-Launcher/docs/main/plugin.json.md)); no permissions field | Store = [PluginsManifest repo](https://github.com/Flow-Launcher/Flow.Launcher.PluginsManifest) `plugins.json` (ID, Name, Author, Version, Language, UrlDownload to a GitHub release zip, UrlSourceCode, IcoPath on jsDelivr, MinimumAppVersion); submit by PR adding `plugins/<name>-<uuid>.json`, approved by the Flow team, VirusTotal-scanned, CI every ~3 h refreshes from the release; CDN lag "up to a week"; `pm install` for manual installs | Results (Title, SubTitle, IconPath, score, JsonRPCAction), context menu; host calls ChangeQuery, ShellRun, ShowMsg, OpenSettingDialog, ReloadAllPluginData… ([json-rpc.md](https://raw.githubusercontent.com/Flow-Launcher/docs/main/json-rpc.md)) | None; PR review + VirusTotal + policy text | `Version` in plugin.json "important for plugin update checking"; catalog `MinimumAppVersion` hides from old hosts | `ReloadAllPluginData` API; Python plugins are re-spawned per request anyway | `pm install` referenced for manual install; URL scope [unverified] | **Declarative `SettingsTemplate.yaml`**: `body` list of {type: textBlock/input/inputWithFileBtn/inputWithFolderBtn/textarea/passwordBox/dropdown/checkbox, attributes: name/label/description/defaultValue/options}, host-rendered ([doc](https://raw.githubusercontent.com/Flow-Launcher/docs/main/json-rpc-settings.md)) |
| **Wox v2** | Node.js / Python / script via per-language host processes (`wox.plugin.host.python` dir in repo) ([repo](https://github.com/Wox-launcher/Wox)); **Python yes, out-of-process** | [unverified] (`store-plugin.json` in repo root) | [Plugin store](https://www.woxlauncher.com/store/plugins); `wpm create` scaffolds and tells you to paste into an AI agent ([dev page](https://www.woxlauncher.com/development/)) | [unverified] | [unverified] | [unverified] | [unverified] | [unverified] | Screenshot of plugin settings on home page; no doc |
| **Alfred workflows** | Any script language as subprocess; "Compiled binaries must be signed and notarised. Scripts are not in this category" ([alfred.app/submit](https://alfred.app/submit/)) | `info.plist` [unverified] | [Gallery](https://alfred.app/): invitation after sharing on the forum; rules: no post-install download-and-run, "Workflows must not update themselves", community "expects to be able to audit a workflow's contents" | Script Filter JSON, Run Script, Hotkey objects [unverified] | "automatic and manual security checks", workflows "notarised and signed"; binaries flagged with a banner; "There has never been a known case of a malicious workflow" ([security page](https://alfred.app/security-and-privacy/)) | Gallery-driven updates | n/a (scripts re-run) | No | User Configuration → env vars [unverified] |
| **Albert** | C++ and Python (embedded, in-process) [unverified: docs 404; `albertlauncher/python` archived 2025-10-15, plugins moved to separate repos ([repo](https://github.com/albertlauncher/python))] | module-level `md_*` vars [unverified] | Curated bundled set; no store ([home](https://albertlauncher.github.io/)) | Trigger/global/index/fallback handlers ([basics](https://albertlauncher.github.io/basics/)) | none [unverified] | — | plugins load/unload at runtime | No | configWidget [unverified] |
| **Keypirinha** | **Python 3 embedded, in-process**: "every keypirinha.Plugin subclass is instantiated"; "multi-threaded… plugins methods are called in an asynchronous fashion"; "Keypirinha will never force a particular plugin, or more generally a thread, to terminate" ([API overview](https://keypirinha.com/api/overview.html)) | none documented (package = zip of `.py` + `.ini`) | Drop `.keypirinha-package` into `%APPDATA%\Keypirinha\InstalledPackages`; community list on [contributions page](https://keypirinha.com/contributions.html); third-party PackageControl "must be installed manually", list "manually curated by @ueffel" | `on_start`, `on_catalog`, `on_suggest`, `on_execute`, `on_config_changed`, `should_terminate` | None, no review | PackageControl handles updates | **Yes**: watches Live Packages dir; `.ini`-only change → `on_config_changed()`, else "the concerned package will be fully reloaded" | No | INI files (machine/user split) |
| **ueli** | TS/Electron, bundled extensions only; **no third-party API** ([repo](https://github.com/oliverschwendener/ueli)) | — | PR to the repo | — | — | app updates | — | — | per-extension settings page |
| **Raycast** | TypeScript/React in "a single child Node.js process", each extension in its own V8 isolate ([security](https://developers.raycast.com/information/security)); **Python no** | `package.json`: name, title, icon, author, platforms, categories, commands[] (name, mode view/no-view/menu-bar, arguments), tools[], preferences[], dependencies ([manifest](https://developers.raycast.com/information/manifest)) | Store backed by [raycast/extensions](https://github.com/raycast/extensions) monorepo; `npm run publish` opens a PR, "we'll review your extension", merged = published ([publish](https://developers.raycast.com/basics/publish-an-extension)) | List/Grid/Detail/Form views, actions, menu-bar, AI tools | "not further sandboxed as far as policies for file I/O, networking"; human + CI review; encrypted password prefs; all open source | Auto-update from store | `ray develop` [unverified] | No | **Declarative `preferences`**: textfield/password/checkbox/dropdown/appPicker/file/directory, host-rendered |
| **Obsidian** | JS in-process (Electron renderer); **Python no** | `manifest.json`: id, name, version (semver), minAppVersion, description, author, authorUrl, fundingUrl, isDesktopOnly ([manifest](https://docs.obsidian.md/Reference/Manifest)); `versions.json` maps plugin version → min app version ([releases repo](https://github.com/obsidianmd/obsidian-releases)) | `community-plugins.json` (flat array of id/name/author/description/repo, ~3 MB); submission now via community.obsidian.md with automated review; app downloads `main.js`/`manifest.json`/`styles.css` from the GitHub release whose tag equals the manifest version ([submit](https://docs.obsidian.md/Plugins/Releasing/Submit+your+plugin)) | commands, ribbon, settings tab, views, editor extensions [unverified list] | "Restricted Mode" by default; "Obsidian cannot reliably restrict plugins to specific permissions or access levels"; "Community plugins can access files… connect to internet… install additional programs"; every version auto-scanned, manual review for popular/flagged ([plugin security](https://obsidian.md/help/plugin-security)) | GitHub release tag == manifest version; minAppVersion | Community Hot-Reload plugin [unverified] | Via [BRAT](https://github.com/TfTHacker/obsidian42-brat): "Simply add the GitHub repository path"; "Updates are downloaded and the plugin is reloaded" | Plugin renders its own SettingTab [unverified] |
| **Home Assistant + HACS** | **Python in-process**; `requirements` pinned pip packages, "Home Assistant installs them before it loads the integration. If installing fails, the integration does not load" ([manifest doc](https://contributors.home-assistant.io/core/integrations/manifest/)) | `manifest.json`: domain (= folder name, "can never change"), name, version (required for custom), requirements, dependencies, config_flow, iot_class, codeowners, documentation, issue_tracker…; plus `hacs.json` (`name`, `content_in_root`, `zip_release`, min versions) ([HACS publish](https://hacs.xyz/docs/publish/start/)) | Folder drop into `custom_components/<domain>/`, or HACS store fed by [hacs/default](https://github.com/hacs/default) category lists via PR; "Only public repositories on GitHub will work"; "you need to publish releases" | entities, services, config/options flows, events, panels | None (imported into core); HACS checks structure only; no warning text on the custom-repositories page ([faq](https://hacs.xyz/docs/faq/custom_repositories/)) | `version` vs GitHub releases; shows 5 latest + default branch ([integration rules](https://hacs.xyz/docs/publish/integration/)) | Restart required [general knowledge] | **Yes, natively**: HACS → Custom repositories → paste URL + category | Declarative config/options flow schemas, host-rendered |
| **Stream Deck** | Any executable via `CodePath`; Node first-class; **Python possible as a packaged exe** (not viable under AppLocker [inference]) | `manifest.json` in `<uuid>.sdPlugin/`: Name, Version, Author, UUID (reverse-DNS), CodePath, OS[].MinimumVersion, Software.MinimumVersion, Nodejs, Actions[] with PropertyInspectorPath ([manifest](https://docs.elgato.com/streamdeck/sdk/references/manifest/)) | Elgato Marketplace; UUID immutable once published ([getting started](https://docs.elgato.com/streamdeck/sdk/introduction/getting-started/)) | actions, states, dials, property inspector | None in manifest; Marketplace review [unverified] | Version + min versions | `streamdeck restart <uuid>` | No | HTML Property Inspector, plugin-rendered |
| **Anki add-ons** | **Python in-process** (`addons21/<package>/__init__.py`); vendored deps: "putting them in a subfolder, and adjusting sys.path" ([python modules](https://docs.ankiweb.net/addons/python-modules)) | `manifest.json` (package, name, conflicts, mod) only for non-AnkiWeb distribution; `config.json` + `config.md` ([config](https://docs.ankiweb.net/addons/addon-config), [sharing](https://docs.ankiweb.net/addons/sharing)) | AnkiWeb upload of `.ankiaddon` zip | hooks, menus, Qt UI | None | [unverified] | Restart | No | Host shows `config.json` editor; `setConfigAction` for custom UI; `user_files/` preserved on upgrade |
| **Calibre** | **Python in-process**; "it's just a ZIP file that contains some Python code" ([creating plugins](https://manual.calibre-ebook.com/creating_plugins.html)) | Class attributes: name, description, author, version tuple, supported_platforms, minimum_calibre_version | MobileRead forum index; `calibre-customize -b .` | FileTypePlugin, InterfaceAction, EditBook tools | None | `minimum_calibre_version` | Restart (`calibre-debug -s`) | No | `config_widget()` + JSONConfig |
| **Sublime Text / Package Control** | **Python 3.3 / 3.14 in one shared `plugin_host` subprocess**: "ensures the entire editor will not crash due to a poorly written plugin"; "All plugins are run in a single plugin_host process, and share a single Python environment" ([api environments](https://www.sublimetext.com/docs/api_environments.html)) | `.python-version` file; channel JSON entry | Fork `package_control_channel`, add `{name, details: <GitHub URL>, releases: [{sublime_text, tags: true}]}`, run the test command, PR; "We review changes" ([submitting](https://packagecontrol.io/docs/submitting_a_package)) | commands, event listeners, views | Process isolation only (crash, not security) | semver git tags, checked "approximately once per hour" | plugin_host reloads `.py` on save [unverified] | custom repository JSON | Settings JSON files |

**Patterns that matter for Assistant** [inference over the table]:

- Every ecosystem uses a **small declarative manifest** with id, name, version, author, description, entry point, and a **minimum host version**. Obsidian and Home Assistant both bind **id == folder name** and forbid changing it. Nobody has a working permissions field; Raycast and Flow have none, Obsidian explicitly says it cannot enforce one.
- The two hosts with **host-rendered declarative settings** (Flow `SettingsTemplate.yaml`, Raycast `preferences`) make the smallest plugins possible: no UI code. Home Assistant's config flows are the Python precedent.
- **Python-based hosts split cleanly**: in-process with full API and zero isolation (Keypirinha, Anki, Calibre, Home Assistant, Albert), or subprocess with a data-only protocol (Flow per-request, Wox per-language host, Sublime one shared host). Flow's per-request spawn works for a launcher because a query is a one-shot; it cannot host a long-running card source.
- **Hot reload is rare**: only Keypirinha (file watch) and Sublime (plugin_host) do it natively; Obsidian and Stream Deck rely on a helper or a restart command.
- **Catalogs are all "a JSON list in a GitHub repo, fed by PRs, light human review, GitHub Releases as the artifact store"**, with a CI job that refreshes metadata from the release (Flow every 3 h, Package Control hourly). Lag is days (Flow) and the review burden is volunteer time.
- **Install-from-URL** exists natively only in HACS (custom repositories) and via add-ons in Obsidian (BRAT) and Flow (`pm install`).

### Q3. Plugin kinds Assistant needs — confidence: Medium (design proposal grounded in Q2 precedents and the reference README; [inference])

Design rule that everything below follows: **plugins exchange plain data (dicts / dataclasses, strings, bytes), never UI widgets.** The host renders. This is what lets the same plugin run in-process in v1 and in a subprocess host in v2 without changing plugin code (Flow results are JSON; Raycast preferences are JSON; Home Assistant flows are schemas).

Host-provided module `assistant.plugin_api` (sketch; names are illustrative):

```python
from dataclasses import dataclass, field
from typing import Protocol, Literal

@dataclass
class CardAction:
    id: str                 # "open_run", "snooze_1h"
    label: str              # button text
    shortcut: str | None = None

@dataclass
class Card:
    title: str
    detail: str = ""
    kind: Literal["waiting", "done", "meeting", "reminder", "custom"] = "custom"
    priority: int = 3       # 1..5, ntfy-style; >=4 is sticky by default
    sticky: bool | None = None
    url: str | None = None  # default click
    icon: str | None = None # path relative to plugin folder
    actions: list[CardAction] = field(default_factory=list)
    id: str | None = None   # set by host if None; reuse to update in place
    # host adds: source (plugin id), created (timestamp)

@dataclass
class Result:
    title: str
    subtitle: str = ""
    icon: str | None = None
    path: str | None = None       # enables built-in file actions + previewers
    url: str | None = None
    data: dict = field(default_factory=dict)   # opaque, passed back to run()
    score: int = 50               # 0..100, Flow-style
    preview: "Preview | None" = None

@dataclass
class Preview:
    kind: Literal["markdown", "text", "html", "image", "table"]
    content: str | bytes | list[list[str]]
    truncated: bool = False

class Context(Protocol):
    settings: dict            # values from the manifest [[settings]] schema
    data_dir: str             # ~/.assistant/plugins-data/<id>/ (survives upgrades, like Anki user_files)
    def emit(self, card: Card) -> str: ...        # returns card id
    def update(self, card_id: str, **fields) -> None: ...
    def dismiss(self, card_id: str) -> None: ...
    def log(self, msg: str) -> None: ...
    def every(self, seconds: float, fn) -> None: ...   # host-owned scheduler; plugin never spawns threads
    def send_to_claude(self, text: str, path: str | None = None) -> None: ...  # file-search brief Q12 routes
```

| Extension point | What it contributes | Minimal Python interface | Manifest fields |
|---|---|---|---|
| **Card source** (CI finished, email, RSS, NAS alert, timer) | Emits / updates / dismisses cards; receives button presses | `class X(CardSource): def start(self, ctx) ...; def stop(self) ...; def on_action(self, card_id, action_id, ctx) ...` — `start` registers a poll with `ctx.every()` rather than owning a thread | `[provides] card_sources = ["X"]`; optional `[permissions] network = ["api.github.com"]` |
| **Card action** (button on a card made by *another* source or the HTTP API) | Adds a button to cards matching a filter (e.g. "Open in VS Code" on every Claude card) | `class X(CardActionProvider): def actions(self, card) -> list[CardAction]; def run(self, card, action_id, ctx)` | `[provides] card_actions = ["X"]`; `applies_to_kinds = ["waiting", "done"]` |
| **Search mode** (bookmarks, clipboard history, snippets, a second notes vault) | A mode in the Tab cycle with its own keyword; returns results on Enter (the reference searches on Enter, not per keystroke, so latency budget is ~hundreds of ms) | `class X(SearchMode): keyword = "bm"; label = "Bookmarks"; def search(self, query, ctx) -> list[Result]; def run(self, result, action_id, ctx)` — `run` with `action_id="open"` is the Enter default | `[provides] search_modes = ["X"]` |
| **Result action** (applies across modes: "send to Claude", "copy as markdown link", "move to Recycle Bin") | Context-menu item + shortcut on results it applies to | `class X(ResultAction): id="send_claude"; label="Ask Claude"; shortcut="Ctrl+L"; def applies(self, result) -> bool; def run(self, result, ctx)` | `[provides] result_actions = ["X"]` |
| **Previewer** (a renderer for a file type) | Turns a path into a `Preview` the host renders; host enforces size caps and the secrets deny-list from the file-search brief Q13 | `class X(Previewer): extensions = (".ipynb",); def render(self, path, ctx) -> Preview` | `[provides] previewers = ["X"]` |
| **Settings page** | Declarative; host renders a form and persists values; plugin reads `ctx.settings` and gets `on_settings_changed()` | no code; optional `def on_settings_changed(self, ctx)` | `[[settings]] key, type (text/password/bool/int/choice/folder/file), label, default, description, options` (Flow + Raycast type sets) |

Manifest (`plugin.toml`; TOML because `tomllib` is in the Python 3.11 standard library, no dependency, comments allowed; JSON accepted as an alternative if the PRD prefers parity with Obsidian/HA):

```toml
[plugin]
id = "hello-cards"                 # lowercase + hyphens; MUST equal the folder name (Obsidian/HA rule)
name = "Hello cards"
version = "0.1.0"                  # semver; compared to catalog / release tag for updates
author = "Your Name"
description = "Shows one card when Assistant starts."
homepage = "https://github.com/example/assistant-hello-cards"
min_assistant_version = "0.1.0"
entry = "plugin.py"                # module that defines the classes named in [provides]

[provides]
card_sources = ["HelloSource"]
search_modes = []
result_actions = []
card_actions = []
previewers = []

[permissions]                      # declared, shown on install, logged; NOT enforced in-process (see Q5)
network = []                       # list of hosts, or ["*"]
filesystem = []                    # extra roots beyond data_dir; e.g. ["~/Documents"]
subprocess = false
clipboard = false

[requirements]
packages = []                      # pinned, e.g. ["feedparser==6.0.11"]; installed into ./lib with pip --target

[[settings]]
key = "greeting"
type = "text"
label = "Greeting"
default = "Hello from a plugin"
```

### Q4. Easy-add mechanics for Python — confidence: High on documented mechanics (Python packaging, pip, importlib docs read), Medium on the recommendation ([inference])

**Discovery options**

| Option | How | Pros | Cons | Precedents |
|---|---|---|---|---|
| (a) Folder drop: `~/.assistant/plugins/<id>/plugin.toml + plugin.py`, discovered by scanning and `importlib.util.spec_from_file_location` | Copy a folder (or let the app download a release zip into it) | Zero packaging knowledge; visible on disk; enable/disable/uninstall = rename/delete folder; works offline; agents (Claude) can write it in one shot; corporate proxy irrelevant for local drop | Dependencies need a separate step; two plugins may vendor conflicting libs (Anki: first import wins [community, unverified]); no PyPI-style upgrade semantics | Obsidian, Home Assistant, Anki, Calibre, Keypirinha, Stream Deck, Flow (zip to plugin dir) |
| (b) pip-installable packages exposing `[project.entry-points."assistant.plugins"]`, discovered with `importlib.metadata.entry_points(group="assistant.plugins")` ([packaging guide](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/), [spec](https://packaging.python.org/en/latest/specifications/entry-points/), [importlib.metadata](https://docs.python.org/3/library/importlib.metadata.html)) | `pip install assistant-plugin-foo` (PyPI, wheel URL, or a GitHub archive zip URL — no git needed for zip URLs) | pip resolves and upgrades dependencies properly; standard for Python developers; `pluggy`/`stevedore` build on it | Requires a `pyproject.toml` and a build (not non-developer friendly); install/uninstall is a shell command, not a UI; everything lands in one shared site-packages (conflicts resolved by pip, but one plugin can break the app's own pins); `pip install git+https://` needs `git.exe`, which may be absent or blocked on the work laptop [inference]; entry-point names are case-sensitive and groups should be prefixed by a project name you own (spec) | pytest (pluggy), OpenStack (stevedore), Flask naming convention |
| (c) Both, one registry | Loader 1 scans the folder; loader 2 iterates entry points whose object returns the same manifest dict | Covers non-developers and developers; the entry-point loader is ~15 lines once the folder loader exists | Two code paths to test; dependency story differs | — |

Supporting library facts: `pluggy` (1,713 stars, pushed 2026-11-03, used by pytest/tox/devpi) gives hookspec/hookimpl, LIFO call order, `tryfirst/trylast`, `firstresult`, `load_setuptools_entrypoints`, **but one failing hookimpl aborts the whole hook call** ("no further callbacks are invoked") ([pluggy docs](https://pluggy.readthedocs.io/en/stable/)), so per-plugin containment must be written by the host anyway. `stevedore` adds `on_load_failure_callback` and `propagate_map_exceptions=False` ("logged and ignored") ([stevedore reference](https://docs.openstack.org/stevedore/latest/reference/index.html)). Both are entry-point-only and add little for a folder loader; a 150-line custom registry is simpler [inference].

**Dependency handling**

- `pip install --target <plugin>/lib -r requirements.txt` is the folder-drop answer, with two documented traps: `--target` "will not replace existing files/folders" unless `--upgrade`, and with `--upgrade` pip erases whole folders and dumps the new install over the old ("We should never be simply dumping a new install over an old one" — pip maintainer, [pypa/pip#10110](https://github.com/pypa/pip/issues/10110), open) ([pip install reference](https://pip.pypa.io/en/stable/cli/pip_install/)). Mitigation: treat `lib/` as disposable — delete and reinstall on every plugin update. Home Assistant's rule is the model: requirements "pinned to an exact version", installed before load, "If installing fails, the integration does not load" ([manifest doc](https://contributors.home-assistant.io/core/integrations/manifest/)).
- Add `--only-binary :all:` so no plugin dependency runs a `setup.py` at install time, and allow optional `--require-hashes` lines ([pip secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/)). Pure-Python wheels are the norm; C-extension deps need a wheel for the user's exact Python (Anki notes the same constraint, [python modules](https://docs.ankiweb.net/addons/python-modules)).
- Shared venv alternatives: one venv per plugin (`python -m venv`, no admin) only makes sense with a subprocess host; `pipx inject` is the same "one venv per app, inject extras" model ([pipx](https://pipx.pypa.io/stable/)) and does not resolve cross-plugin conflicts either.
- In-process consequence: each plugin's `lib/` is appended to `sys.path` at load; two plugins vendoring different versions of the same library will collide — "first import wins" [inference, matches Anki's community-reported issue]. Acceptable for v1 with a loud log line; a subprocess host removes it in v2.

**Isolation and crash containment**

| Model | Crash containment | Latency | Fits card sources (long-running)? | Fits previewers/settings? | Complexity | Precedent |
|---|---|---|---|---|---|---|
| In-process, every hook wrapped in `try/except`, run on a worker thread with a timeout, 3 errors → auto-disable + card | Catches exceptions (the overwhelmingly common failure for pure Python); does **not** survive a hard crash (C-extension segfault) or a plugin that blocks the UI thread if it is ever called there | Lowest | Yes | Yes (data-only contract still applies) | Lowest; hello world = 2 files | Keypirinha, Anki, Calibre, Home Assistant, Obsidian |
| One process per request (Flow Launcher) | Total | Python interpreter start per query [inference: tens of ms, not measured]; fine on Enter, not for polling | **No** — no persistent state or scheduler | Previewers yes (return data); settings via host | Medium | Flow ([source](https://raw.githubusercontent.com/Flow-Launcher/Flow.Launcher.JsonRPC.Python/master/flowlauncher/FlowLauncher.py)) |
| One long-lived plugin-host subprocess for all plugins, JSON-RPC over stdio | Host UI survives any plugin crash ("ensures the entire editor will not crash due to a poorly written plugin", [Sublime](https://www.sublimetext.com/docs/api_environments.html)); one bad plugin still kills its siblings | Low after start | Yes | Yes, via data-only contract | Medium: IPC layer, serialisation, supervisor, restart | Sublime Text |
| One subprocess per plugin | Full crash + dependency isolation (each can have its own venv); hot reload = kill + respawn | Low after start; N interpreters resident (~15–30 MB each [inference]) | Yes | Yes | Highest | Wox per-language hosts, Stream Deck |

None of these is a **security** boundary; see Q5.

**Hot reload**: `importlib.reload` keeps old objects alive, does not rebind `from … import` names, leaves instances on the old class, and "is not thread-safe" ([importlib docs](https://docs.python.org/3/library/importlib.html#importlib.reload)). The reliable in-process approach is: call `stop()`, drop `sys.modules` entries for the plugin, re-import under a fresh module name (`assistant_plugins.<id>.<n>`), re-register. Trigger it from a "Reload plugins" menu item in v1; add a `watchdog` observer on the plugins folder (v7.1.0, 2026-11-05, Windows backend `ReadDirectoryChangesW`, [PyPI](https://pypi.org/project/watchdog/)) in v1.x, mirroring Keypirinha's Live Packages watch. Under a subprocess host, reload is kill + respawn.

**Recommendation for v1** [inference]: **(a) folder drop, in-process, data-only contract**, with the host registry written so that (b) entry points and a subprocess host can be added without touching plugins:

1. Non-developers (and Claude writing a plugin for them) get the smallest possible unit: one folder, two files. Every Python-based desktop precedent with a large user base (Anki, Calibre, Home Assistant, Keypirinha) ships exactly this.
2. In-process keeps v1 free of an IPC layer; the data-only contract plus the host-owned scheduler (`ctx.every`) keeps plugins ignorant of threads, so moving them into a Sublime-style shared plugin host in v2 is a host-side change only.
3. Containment that v1 must implement: every hook call wrapped; calls run on a plugin worker thread with a timeout (search 2 s, preview 5 s, card poll 30 s — numbers for the PRD to confirm); a plugin that raises three times in a row is disabled and announces it with a purple card (mirrors the project's three-strike rule); plugin load failures never block app start (stevedore's `on_load_failure_callback` pattern).
4. Entry points (b) become v1.1: ~15 lines using `importlib.metadata.entry_points(group="assistant.plugins")`, each entry returning the same manifest dict.

**Hello world** (2 files, 9 + ~8 lines; drop into `~/.assistant/plugins/hello-cards/`):

`plugin.toml`
```toml
[plugin]
id = "hello-cards"
name = "Hello cards"
version = "0.1.0"
author = "you"
description = "One card on start, and a 'hi' search mode."
min_assistant_version = "0.1.0"
entry = "plugin.py"

[provides]
card_sources = ["Hello"]
search_modes = ["HiMode"]
```

`plugin.py`
```python
from assistant.plugin_api import CardSource, SearchMode, Card, Result

class Hello(CardSource):
    def start(self, ctx):
        ctx.emit(Card(title="Hello from a plugin", detail="Drop a folder, get a card."))

class HiMode(SearchMode):
    keyword, label = "hi", "Hi"
    def search(self, query, ctx):
        return [Result(title=f"You typed: {query}", subtitle="hello-cards plugin")]
```

Non-developer path, in order of least effort: (1) ask Claude Code, with Assistant's `PLUGIN_AUTHORING.md` in context, to "write me a plugin that shows a card when the NAS watch service posts an alert" — the Wox stance; (2) paste a GitHub URL into Settings → Plugins → Install from URL (Q6); (3) copy a folder someone sent you. Developers additionally get `assistant plugin new <id>` (scaffold), `assistant plugin reload`, and later `pip install`.

### Q5. Security of third-party plugins — confidence: High (primary statements quoted)

**Risk model.** A plugin is arbitrary Python running as the logged-in user with the same access as Assistant: every file the user can read (including the notes vault, `token.txt`, browser profiles), loopback services (Assistant's own HTTP API with its token on disk, the MCP server), the network, and `subprocess`. On the work laptop this means a malicious or careless plugin can exfiltrate corporate documents; AppLocker is not bypassed (python.exe is already approved) but the security team may well view a plugin loader as a script-execution vector [inference]. This is the same posture every surveyed host states openly:

- Obsidian: "Due to technical limitations, Obsidian cannot reliably restrict plugins to specific permissions or access levels… plugins will inherit Obsidian's access levels"; "Community plugins can access files on your computer… connect to internet… install additional programs" ([plugin security](https://obsidian.md/help/plugin-security)).
- VS Code: "The extension host has the same permissions as VS Code itself… any action that VS Code can perform, an extension can also perform" ([extension runtime security](https://code.visualstudio.com/docs/editor/extension-runtime-security)).
- Raycast: extensions are "not further sandboxed as far as policies for file I/O, networking" ([security](https://developers.raycast.com/information/security)).

**What is unrealistic to enforce in-process Python** (do not promise it in the PRD):

- Any sandbox. pysandbox post-mortem: "There are too many ways to escape the untrusted namespace using the various introspection features of the Python language… A single bug is enough to escape the whole sandbox… To build a secure sandbox, the whole Python process must be put in an external sandbox" ([LWN](https://lwn.net/Articles/574215/)). RestrictedPython: "RestrictedPython is not a sandbox system or a secured environment" ([docs](https://restrictedpython.readthedocs.io/en/latest/)). PEP 578 audit hooks: "This is not sandboxing, as this proposal does not attempt to prevent malicious behavior"; they exist "to enable logging rather than creating a sandbox" ([PEP 578](https://peps.python.org/pep-0578/)).
- Enforced network or filesystem permissions. A declared `network = []` cannot stop `urllib` in-process. Even a subprocess is only a crash boundary unless wrapped in an AppContainer, for which no maintained pip-installable library was found [unverified]; Sublime describes its plugin_host split purely as crash isolation.

**Sane defaults that are realistic** [inference, grounded in the precedents above]:

1. **Plugins off until the user enables them** (Obsidian "Restricted Mode"). Built-in modes (Files, Ask notes, Claude hooks) are not plugins from the user's point of view and stay on.
2. **Declared permissions shown on install and on every update**, with the honest sentence "Assistant cannot enforce these; the plugin runs as you" (Obsidian wording). A permissions change on update requires re-consent. Install screens show author, homepage, version, and the files in the zip.
3. **Audit log, not enforcement**: register a PEP 578 audit hook that records `socket.connect`, `subprocess.Popen`, `open` outside `data_dir` with the calling plugin identified from the stack, and surfaces "plugin X used the network but declared none" as a card. Observability is what audit hooks are for ([PEP 578](https://peps.python.org/pep-0578/)).
4. **Dependencies pinned `==`, installed with `--only-binary :all:`, optional `--require-hashes`** ([pip secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/)); no `setup.py` ever executes during a plugin install.
5. **No silent auto-update** of plugins (Alfred's Gallery rule "Workflows must not update themselves"; contrast Raycast). Updates are offered, the version diff and permission diff are shown, the user clicks.
6. **Secrets never in the plugin folder**: `type = "password"` settings are stored by the host (Windows Credential Manager via `keyring` or at minimum a separate file with user-only ACL) and handed to the plugin at runtime, like Raycast's encrypted password preferences.
7. **Previewers run through the host's existing deny-list and size caps** (file-search brief Q13: `.env*`, keys, kdbx, etc.) so a previewer plugin cannot be used to surface secrets into the MCP channel by accident.
8. **Catalog-level hygiene** (when a catalog exists): source must be a public GitHub repo with a tagged release; metadata refreshed from the release, not from PR text (Flow); VirusTotal scan of the zip (Flow); a `removed.json` for pulled plugins (Obsidian, HACS).
9. **Documentation for the work laptop**: a one-paragraph policy statement the user can show IT — plugins are user-installed scripts, Assistant does not download or run code without an explicit install action, and the plugin folder can be made read-only or pointed at an allow-listed path.

### Q6. Discovery and sharing — confidence: High on mechanics, Medium on cost estimates ([inference])

| Pattern | How it works | What it costs | Who does it |
|---|---|---|---|
| **Paste a GitHub URL** | App parses `owner/repo`, downloads `https://github.com/<o>/<r>/releases/latest/download/<id>.zip` (a redirect on github.com, not the REST API) or falls back to the branch archive `https://github.com/<o>/<r>/archive/refs/heads/main.zip`; validates `plugin.toml` inside; unzips into `~/.assistant/plugins/<id>/`; records `source_url` + version for later "check for updates" | ~100–150 lines in the app; zero infrastructure; needs outbound HTTPS to github.com (may be blocked by a corporate proxy, in which case folder drop remains); if the REST API is used for release metadata, the unauthenticated limit is "60 requests per hour" per IP ([GitHub rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)) — so avoid `api.github.com` or accept an optional token | HACS "Custom repositories" (native), Obsidian via BRAT ("Simply add the GitHub repository path"), Flow `pm install` |
| **Community catalog: JSON index in a git repo** | A repo `assistant-plugins` with `index.json` (id, name, author, description, repo, min_assistant_version, permissions summary, date_added) and `removed.json`; authors submit a PR adding one file under `plugins/`; CI validates the manifest, fetches the latest release zip, checks `plugin.toml` consistency, and regenerates `index.json`; the app fetches the index over raw.githubusercontent.com (not rate-limited like the API) and installs via the URL path above | A repo, a CI workflow (~1 day to write), a review habit for every PR (Flow: team approval; Package Control: "We review changes"; Obsidian: automated review + manual for popular/flagged), a periodic refresh job (Flow: every 3 h; Package Control: hourly), and a policy page. Lag between submission and visibility is days in Flow ("sometimes up to a week"). Worth it only once there are more plugins than a README list can hold [inference] | Flow PluginsManifest, Obsidian community-plugins.json, HACS default, Package Control channel |
| **In-between: README list** | A table in the Assistant README with name, URL, one line; install by pasting the URL | Zero code beyond the URL installer | Keypirinha's contributions page (27 packages), Calibre's forum index |

Recommendation [inference]: v1 ships **folder drop + paste-a-GitHub-URL + a README list**; the catalog repo and in-app "store" page are v2, triggered by having more than roughly ten third-party plugins or a second active author. The URL installer should write the `source_url` into a host-owned `installed.json` so the catalog can be layered on later without re-installing.

### Q7. Risks and open questions for the product role

1. **Stack contradiction across briefs.** The overlay brief recommended WPF/.NET self-contained; this dispatch pins pure Python because AppLocker allows the approved `python.exe` and blocks unknown executables (reference README: "A script run by an already-approved Python is allowed"). The file-search brief leaned toward web-technology previews. The PRD must pick once: the AppLocker constraint on the work laptop is the hardest requirement in the set, so pure Python with pip-only dependencies wins there, and the plugin design above assumes it. Python UI toolkit (tkinter + pywin32 as in the reference, vs PySide6) determines nothing for plugins because of the data-only contract, but it determines everything for previewers' fidelity — flag for the planner. [inference]
2. **Honesty about security.** No surveyed host enforces plugin permissions; Obsidian says so in its help. The PRD must not promise "sandboxed plugins". Promise: off by default, declared permissions shown, audit log, pinned deps, no auto-update. [High]
3. **Work-laptop policy risk.** A plugin loader is a user-controlled code-execution path. If corporate policy forbids running unapproved scripts, plugins (and arguably Assistant itself) are a policy question for the user, not a technical one. The PRD should include the IT-facing paragraph (Q5 item 9). [inference]
4. **In-process trade-offs are real.** Hard crashes and UI-thread blocking are not contained in v1; vendored-dependency collisions are possible. Decide whether a Sublime-style plugin host is v2 or v1.1 based on how many plugins exist by then. [Medium]
5. **Latency budget for search modes.** The reference searches on Enter, which makes in-process plugins comfortable. If the PRD switches to search-as-you-type (Everything, Flow, Raycast all do), plugin search needs debouncing and cancellation, and slow plugins become visible. Decide the interaction model before fixing timeouts. [inference]
6. **Card model needs `source`, `priority`, `actions`, `url`, and update-in-place** to serve both plugins and the existing HTTP API/MCP/hooks. Changing the HTTP card schema is a breaking change for the user's existing hook configs; version the API (`/v1/cards`). [inference]
7. **Scope creep via plugins.** Clipboard history, snippets, bookmarks, calculator, timer are all "plugin" above. Decide which two or three ship as *bundled* plugins to prove the API (candidates: timer card source, bookmarks search mode, "send to Claude" result action — one of each kind). [inference]
8. **Plugin authoring by Claude** is the non-developer path this user will actually use. It requires a tight `PLUGIN_AUTHORING.md` (interfaces, manifest, hello world, test command) kept in the repo and a `--doctor`/`plugin check <folder>` command Claude can run to verify its own output. [inference]
9. **Settings schema is a public contract.** Once plugins declare `[[settings]]`, the type set (text/password/bool/int/choice/folder/file) is frozen; add types, never remove. Same for `Card`/`Result` fields. [inference]
10. **Unverified items worth a 10-minute probe during planning**: whether `pip install <github zip URL>` works through the corporate proxy; Python interpreter startup time on the work laptop (decides whether a per-request model is ever viable); whether `git.exe` exists on the work laptop (affects `pip install git+`). [unverified]

## Competing solutions

| Solution | Covers Assistant's job? | What Assistant borrows | Why it is not the answer |
|---|---|---|---|
| Flow Launcher + Python plugins | Search/launch yes; alerts no | `plugin.json` fields, `SettingsTemplate.yaml` declarative settings, PluginsManifest catalog + CI refresh, `pm install` | Per-request Python processes cannot host card sources; .NET host is an exe (AppLocker); no overlay |
| PowerToys Command Palette | Search/launch yes; alerts no | Monitor-choice options, Extension Gallery UX, compact search box | Extensions are WinRT/C# packages via WinGet; no Python; no cards |
| Wox v2 | Search/launch + AI; alerts no | "ask an agent to write the plugin" stance, Doctor, Backup | Go/Flutter exe; no overlay |
| ntfy / Gotify + Windows client | Alerts yes (server-side) | Priority levels, 3 action buttons, click URL, topics/apps, update-in-place ask | Needs a server; no first-party Windows client; no snooze/stack; no search |
| Keypirinha | Search yes, Python plugins in-process, hot reload | Live-reload design, file-driven config, scoring | Closed-source exe; no alerts; manual package install |
| Home Assistant + HACS | Not this category, but the best Python plugin + URL-install precedent | `manifest.json` with pinned `requirements` installed before load, folder == domain rule, HACS custom repositories | Server product |
| Obsidian + BRAT | Not this category, but the best "catalog + release-tag" precedent | Restricted Mode default, honest security page, `minAppVersion` + `versions.json`, BRAT URL install | JavaScript |
| **Assistant (proposed)** | Alerts + search + Claude + Python plugins | all of the above | — |

## Risks / unknowns

See Q7. The three that most affect the PRD: (a) the Python-vs-WPF stack contradiction between briefs must be settled in favour of the AppLocker constraint; (b) plugin security can only be "declared and observed", never "enforced" — the PRD wording must say so; (c) in-process v1 is a deliberate simplicity trade whose exit (a shared plugin-host subprocess) must be kept open by the data-only contract.

## Recommendation

**Build**, as a Python app with a folder-drop plugin system: the category's users demonstrably want extensibility (it is the top request of the one launcher without it, and every launcher with a store leads with it), no Windows tool combines a snoozable card overlay with search and Claude integration, and every Python-based desktop host with a large user base (Anki, Calibre, Home Assistant, Keypirinha) ships the same two-file, in-process plugin model proposed here. Ship v1 with plugins off by default, declared-not-enforced permissions, pinned binary-only dependencies, a paste-a-GitHub-URL installer and a README list; defer the catalog repo and the subprocess plugin host until plugin count justifies them. Before the PRD is final, probe `pip install <github zip>` and `git.exe` availability on the work laptop.

## Recommendations for PRD

**1. v1 feature set (from the Q1b table, "core")**

- Alerts: stacked cards beside the draggable icon on every monitor (toggle), card fields `kind, priority (1–5), sticky, title, detail, url, icon, actions[], source`, update-in-place by id, snooze presets, blink + sound until hovered, mute with duration, per-source mute, unread count; sources: Claude Code hooks, MCP tool, HTTP API `127.0.0.1` + token (versioned `/v1/cards`), file drop, plugins.
- Search: configurable global hotkey (plus icon click), modes with keywords and Tab cycling, Files mode (per the file-search brief), Ask-notes mode, Enter-to-search, result context menu with shortcuts (Open, Reveal, Copy path, Ask Claude), preview pane (md/markmap/pdf/docx/xlsx + previewer plugins), full-page toggle, open-on-monitor options (beside icon default; mouse / focused / primary / last).
- Reminders and calendar: natural-language reminders with a sticky option; meeting cards from ICS URL and classic Outlook COM (overlay brief).
- Claude: hooks, MCP server, "Ask Claude about this file", `PLUGIN_AUTHORING.md` + `assistant plugin new <id>` scaffold so Claude can write plugins.
- Personalisation: light/dark/system, sound per kind + test + off, icon position remembered, config in `~/.assistant/` as TOML/JSON files.
- Settings window with a Plugins page (enable/disable, install from URL, reload, uninstall, per-plugin declarative settings).
- Install: `git clone` + `install.ps1` (pip-only, no admin), autostart shortcut, uninstall, `demo.py`, user-triggered `assistant update` with no nags, `--doctor`.
- Quality bar from the issue trackers: per-monitor DPI correctness, no duplicate windows on Open, no update nags.

**Later (v1.x / v2):** card history list, markdown card bodies, custom sounds, font scale, recurring reminders, frecency ranking, query history, Everything-as-engine option, merged no-prefix search, folder watcher hot reload, catalog repo + in-app store, shared plugin-host subprocess, entry-point (pip) plugin discovery, Windows toast fallback, settings import/export UI. **Never:** cloud sync, built-in AI chat, telemetry, background auto-update, run-as-admin.

**2. Plugin kinds for v1:** card sources, card actions, search modes, result actions, previewers, declarative settings (Q3 table and interfaces). Bundle one of each as reference plugins: `timer` (card source: "timer 25m" → card at T-0), `bookmarks` (search mode over Edge/Chrome bookmark files), `send-to-claude` (result action), a `.ipynb` or `.csv` previewer, and the Ask-notes mode itself refactored as a plugin to prove the API.

**3. Plugin mechanism:** folder drop into `~/.assistant/plugins/<id>/` with `plugin.toml` + `plugin.py`, id == folder name, discovered at start and on "Reload plugins", loaded in-process via `importlib.util.spec_from_file_location` under a fresh module name per load, per-plugin `lib/` filled by `pip install --target lib --only-binary :all: -r` from pinned `[requirements]` (wipe-and-reinstall on update), every hook call wrapped and run on a plugin worker thread with a timeout, three consecutive failures → disabled + card. Plugins exchange data only (Card, Result, Preview dataclasses); host owns threads, timers, rendering and persistence. Hello world is the 2-file, ~17-line example in Q4. Entry-point discovery and a subprocess plugin host are explicitly deferred and kept possible by that contract.

**4. Security defaults:** plugins disabled until enabled by the user; install dialog shows author, homepage, version, file list and declared `[permissions]` with the sentence "Assistant cannot enforce these — the plugin runs with your access"; permission or dependency changes on update require re-consent; dependencies pinned `==`, binary-only, hashes optional; PEP 578 audit hook logs network/subprocess/file access per plugin and raises a card on undeclared network use; `password` settings stored by the host, not in the plugin folder; previewers subject to the host deny-list and size caps; no silent plugin auto-update; URL installs only from `https://github.com/` release or archive URLs, never `api.github.com` without a token; IT-facing policy paragraph in the README.

## URLs fetched (2026-11-06)

Researcher:
- https://raw.githubusercontent.com/Flow-Launcher/Flow.Launcher.JsonRPC.Python/master/flowlauncher/FlowLauncher.py
- https://albertlauncher.github.io/python/ (404)

Subagent A — launchers:
- https://api.github.com/repos/{Flow-Launcher/Flow.Launcher, Wox-launcher/Wox, oliverschwendener/ueli, albertlauncher/albert, microsoft/PowerToys, raycast/extensions}
- https://learn.microsoft.com/en-us/windows/powertoys/run
- https://learn.microsoft.com/en-us/windows/powertoys/command-palette/overview
- https://learn.microsoft.com/en-us/windows/powertoys/command-palette/settings
- https://github.com/Flow-Launcher/Flow.Launcher
- https://raw.githubusercontent.com/Flow-Launcher/docs/main/usage-tips.md
- https://github.com/Wox-launcher/Wox and https://raw.githubusercontent.com/Wox-launcher/Wox/master/README.md
- https://www.woxlauncher.com/
- https://keypirinha.com/ , https://keypirinha.com/first.html , https://keypirinha.com/faq.html
- https://raw.githubusercontent.com/oliverschwendener/ueli/main/README.md (https://ueli.app/ returned title only)
- https://www.raycast.com/ , https://www.raycast.com/windows , https://manual.raycast.com/ (index only), https://www.raycast.com/changelog/windows/2-0
- https://en.wikipedia.org/wiki/Alfred_(software) (alfredapp.com refused connections)
- https://albertlauncher.github.io/ , https://albertlauncher.github.io/basics/ , https://albertlauncher.github.io/faq/
- GitHub search API, issues sorted by +1 reactions, for Flow.Launcher, PowerToys (labels Product-PowerToys Run and Product-Command Palette), ueli, albert, Wox

Subagent B — notification hubs and finders:
- https://api.github.com/repos/{binwiederhier/ntfy, Aetherinox/ntfy-desktop, gotify/server, seird/gotify-tray, Windos/BurntToast, KDE/snoretoast, ezWhy/WReminder, QL-Win/QuickLook}
- https://docs.ntfy.sh/publish/ , https://docs.ntfy.sh/subscribe/web/
- https://github.com/Aetherinox/ntfy-desktop , https://github.com/seird/gotify-tray , https://github.com/Windos/BurntToast , https://github.com/KDE/snoretoast , https://github.com/ezWhy/WReminder , https://github.com/QL-Win/QuickLook
- https://github.com/binwiederhier/ntfy/issues?q=is%3Aissue+is%3Aopen+sort%3Areactions-%2B1-desc
- https://github.com/gotify/server/issues?q=is%3Aissue+is%3Aopen+sort%3Areactions-%2B1-desc
- https://github.com/QL-Win/QuickLook/issues?q=is%3Aissue+is%3Aopen+sort%3Areactions-%2B1-desc
- https://www.zhornsoftware.co.uk/stickies/
- https://www.producthunt.com/products/taskcue-unignorable-desktop-reminders
- https://www.voidtools.com/ , https://www.voidtools.com/forum/viewtopic.php?t=9787
- https://www.listary.com/ , https://fluentsearch.net/
- https://learn.microsoft.com/en-us/windows/powertoys/peek
- https://docs.toastit.dev/commands/new-burnttoastnotification (DNS failure)

Subagent C — plugin ecosystems:
- https://raw.githubusercontent.com/Flow-Launcher/Flow.Launcher.PluginsManifest/plugin_api_v2/plugins.json
- https://github.com/Flow-Launcher/Flow.Launcher.PluginsManifest , https://github.com/Flow-Launcher/docs
- https://raw.githubusercontent.com/Flow-Launcher/docs/main/{develop-python-plugins.md, plugin.json.md, json-rpc.md, json-rpc-settings.md, plugins.md}
- https://github.com/Wox-launcher/Wox , https://www.woxlauncher.com/ , https://www.woxlauncher.com/development/
- https://developers.raycast.com/information/manifest , https://developers.raycast.com/information/security , https://developers.raycast.com/basics/publish-an-extension
- https://alfred.app/ , https://alfred.app/submit/ , https://alfred.app/security-and-privacy/
- https://github.com/albertlauncher/python
- https://keypirinha.com/ , https://keypirinha.com/api/overview.html , https://keypirinha.com/contributions.html
- https://github.com/oliverschwendener/ueli
- https://docs.obsidian.md/Reference/Manifest , https://docs.obsidian.md/Plugins/Releasing/Submit+your+plugin , https://obsidian.md/help/plugin-security , https://github.com/obsidianmd/obsidian-releases , https://github.com/TfTHacker/obsidian42-brat
- https://contributors.home-assistant.io/core/integrations/manifest/ , https://hacs.xyz/docs/publish/integration/ , https://hacs.xyz/docs/faq/custom_repositories/ , https://github.com/hacs/default
- https://docs.elgato.com/streamdeck/sdk/references/manifest/ , https://docs.elgato.com/streamdeck/sdk/introduction/getting-started/
- https://docs.ankiweb.net/addons/addon-config , https://docs.ankiweb.net/addons/sharing
- https://manual.calibre-ebook.com/creating_plugins.html
- https://www.sublimetext.com/docs/api_environments.html
- https://packagecontrol.io/docs/submitting_a_package
- Failed: flowlauncher.com/docs/#/* (SPA), alfredapp.com/help/* (ECONNREFUSED), albertlauncher.github.io/extending/* (404), keypirinha.com/package.html (404), github.com/Keypirinha/PackageControl (404), docs.elgato.com/streamdeck/sdk/guides/distribution/ (404)

Subagent D — Python mechanics and security:
- https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/
- https://packaging.python.org/en/latest/specifications/entry-points/
- https://docs.python.org/3/library/importlib.metadata.html , https://docs.python.org/3/library/importlib.html
- https://pluggy.readthedocs.io/en/stable/ , https://api.github.com/repos/pytest-dev/pluggy
- https://docs.openstack.org/stevedore/latest/ , https://docs.openstack.org/stevedore/latest/user/patterns_loading.html , https://docs.openstack.org/stevedore/latest/reference/index.html
- https://pip.pypa.io/en/stable/cli/pip_install/ , https://pip.pypa.io/en/stable/user_guide/ , https://pip.pypa.io/en/stable/topics/secure-installs/
- https://github.com/pypa/pip/issues/10110 (and #7629, unrelated)
- https://contributors.home-assistant.io/core/integrations/manifest/
- https://docs.ankiweb.net/addons/python-modules
- https://manual.calibre-ebook.com/creating_plugins.html
- https://pipx.pypa.io/stable/
- https://pypi.org/project/flowlauncher/ , https://github.com/Flow-Launcher/Flow.Launcher.JsonRPC.Python , https://raw.githubusercontent.com/Flow-Launcher/docs/main/json-rpc.md
- https://www.sublimetext.com/docs/api_environments.html
- https://lwn.net/Articles/574215/ , https://peps.python.org/pep-0578/ , https://restrictedpython.readthedocs.io/en/latest/
- https://pypi.org/project/watchdog/
- https://docs.pypi.org/trusted-publishers/ , https://blog.pypi.org/
- https://obsidian.md/help/plugin-security , https://hacs.xyz/docs/faq/custom_repositories/ , https://developers.raycast.com/information/security , https://code.visualstudio.com/docs/editor/extension-runtime-security
- https://github.com/Flow-Launcher/Flow.Launcher.PluginsManifest , https://raw.githubusercontent.com/obsidianmd/obsidian-releases/master/community-plugins.json , https://docs.obsidian.md/Plugins/Releasing/Submit+your+plugin , https://hacs.xyz/docs/publish/start/ , https://github.com/TfTHacker/obsidian42-brat
- https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api

Seen in search results only, not fetched (marked [unverified] where used): voidtools forum threads p=29858, p=36603, p=58639, p=65806, p=42237, p=79451; forums.ankiweb.net bundling-modules wiki; borncity.com Raycast Windows beta review; makerstack.co Alfred review; Microsoft Store "Notification Logger".
