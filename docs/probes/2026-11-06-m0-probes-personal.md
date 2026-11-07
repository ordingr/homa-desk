# M0 probes: personal laptop (2026-11-06)

- Date: 2026-11-06
- Machine: personal laptop, Windows 11, Python 3.13.1
- Plan: `docs/plans/2026-11-06-assistant-v1-plan.md` section 6 (probe table), task M0-4
- PRD: `docs/prds/2026-11-06-assistant-v1.md` section 5 (probe pass column), section 12 (targets)
- Commands run and observed by: tech-lead, on this laptop, 2026-11-06. Results were relayed in the dispatch. doc-keeper did not re-run any probe.
- Recorded by: doc-keeper. P7 and P9 are verbatim copies of the raw files. P6 is summarised from the raw JSONL. Other probes show the output as relayed; where only part of the output was recorded, the section says so.
- Windows username is shown as `<user>` throughout.
- Work-laptop counterpart: `docs/probes/2026-11-06-m0-probes-work.md` (does not exist yet).

## Summary

| Probe | Verdict | Basis |
|---|---|---|
| P1 | PASS | Both import checks printed `ok`. The pip install step was not run. |
| P2 | PASS | Edge app window opened with no address bar; server logged `GET / 200`. |
| P3 | N/A | Work-laptop probe per plan section 6 and PRD section 5. |
| P4 | PASS | `git version 2.45.2.windows.1` |
| P5 | PASS | `2.1.317 (Claude Code)`, minimum 2.1.233 |
| P6 | PARTIAL | CLI only. PermissionRequest seen for a Write and a Bash call that needed approval. The pre-permitted Bash case raised none. Desktop app and plan approval not run. |
| P7 | PARTIAL | Warm run only. Cold run not done. The 0.3M warm figure projects to about 2.7x over target. |
| P8 | PASS | Classic Outlook answers over COM, version `16.0.0.20527`. |
| P9 | PASS | Blank overlay window only. Probe records private bytes, not working set. |

## Probes

### P1: compiled wheels import

Plan pass: both print `ok`, exit 0. Gates everything in M1 and later.

Command:

```powershell
python -c "import win32gui, PIL; print('ok')"
python -c "import pydantic_core, cryptography, rpds; print('ok')"
```

The plan's `python -m pip install --user --only-binary :all: pydantic cryptography jsonschema` step was not run. The packages were already present.

Output (as relayed):

```text
ok
ok
```

Exit codes were not recorded.

Verdict: PASS. Both imports printed `ok`. The binary-only install behaviour was not exercised on this laptop.

### P2: chrome-less Edge window over local HTTP

Plan pass: a chrome-less window opens listing `$env:TEMP`. PRD section 5 pass: the window opens showing the served page. Gates M3b.

Deviations from plan section 6: port 17198 (plan: 17171); the served directory held a one-line `index.html` (plan: `$env:TEMP`).

Command:

```powershell
python -m http.server 17198 --bind 127.0.0.1 --directory <dir>
Start-Process msedge.exe -ArgumentList "--app=http://127.0.0.1:17198/"
```

Output (as relayed):

- Edge window opened with no address bar.
- Window title: `127.0.0.1_/`
- Server log contains `"GET / HTTP/1.1" 200`.

Verdict: PASS. The record does not describe the rendered page content beyond the 200 response and the window title.

### P3: URL install through the corporate proxy

Not run on this laptop. Plan section 6 and PRD section 5 scope P3 to the work laptop.

Verdict: N/A.

### P4: git on PATH

Command: `git --version`

Output: `git version 2.45.2.windows.1`

Verdict: PASS.

### P5: Claude Code version

Command: `claude --version`

Output: `2.1.317 (Claude Code)`. Minimum supported version is 2.1.233 (PRD AC-CS-10).

Verdict: PASS.

### P6: hook events, CLI only

Plan pass: a table with at least PermissionRequest for case (a) in both clients, and rows for (b) and (c) filled in with whatever fired, including "nothing". Gates M1-4 and the AC-CS-9 coverage sentence.

Verdict: PARTIAL.

Setup (as relayed):

- `tools/probes/hook_probe_settings.py --port 17199 --print` was written to a scratch settings file. That file was passed to `claude -p` with `--settings`. The user's `~/.claude/settings.json` was not modified.
- `tools/probes/hook_logger.py --port 17199` received the POSTs and wrote `p6-hooks.jsonl` in the session scratchpad. 13 POSTs were logged across three sessions.
- The generated hook entries use `type: http`, `url`, and `onFailure: continue`. The tech-lead observed Claude Code 2.1.317 accepting them.
- `tools/probes/hook_probe_report.py` was not run for this record. The table below was built directly from the JSONL.

Commands: `claude -p "<prompt>" --settings <scratch settings file>`. Case (a2) also used `--permission-mode default`. The logged `permission_mode` for cases (a) and (b) was `auto`.

Prompts, as they appear in the logged UserPromptSubmit payloads:

- (a) `Run this exact shell command with the Bash tool and tell me its output: echo P6-CASE-A`
- (b) `Before doing anything else, use the AskUserQuestion tool to ask me whether I prefer red or blue, then stop.`
- (a2) `Use the Write tool to create a file named p6-write-test.txt containing the word hello, in the current directory. Then run with Bash: curl -s https://example.com | head -c 50`

Results:

| Case | Client | Logged permission_mode | Events, in order | Notes |
|---|---|---|---|---|
| (a) Bash `echo P6-CASE-A` | CLI, `-p` | auto | UserPromptSubmit, PostToolUse (Bash), Stop, SessionEnd | The command was already permitted, so no PermissionRequest fired. Output was `P6-CASE-A`. |
| (b) AskUserQuestion | CLI, `-p` | auto | UserPromptSubmit, PostToolUse (ToolSearch), Stop, SessionEnd | Claude reported that AskUserQuestion is not available in this session. ToolSearch found no match. No hook event named AskUserQuestion fired. |
| (a2) Write and curl | CLI, `-p`, `--permission-mode default` | default | UserPromptSubmit, PermissionRequest (Write), PermissionRequest (Bash), Stop, SessionEnd | Both calls were blocked because non-interactive mode cannot answer the prompt. Relayed: the JSON result's `permission_denials` named both tools. |
| (c) Plan approval | CLI | n/a | not run | Needs an interactive session. |
| (a) Bash needing approval, and (b), (c) | CLI, interactive | n/a | not run | Needs the user. |
| (a), (b), (c) | Desktop app | n/a | not run | Needs the user. |

PermissionRequest payload, one example (case a2, Write). Fields: `session_id`, `transcript_path`, `cwd`, `scratchpad_dir`, `prompt_id`, `permission_mode`, `effort`, `hook_event_name`, `tool_name`, `tool_input`, `permission_suggestions`. The Bash PermissionRequest in the same case has the same key set.

Stop payload, one example (case a). Fields: `session_id`, `transcript_path`, `cwd`, `scratchpad_dir`, `prompt_id`, `permission_mode`, `effort`, `hook_event_name`, `stop_hook_active`, `last_assistant_message`, `background_tasks`, `session_crons`. The other two Stop events have the same key set.

Not logged in `-p` mode: SessionStart, Notification, StopFailure.

No payload carried a project-name field. The `session_title` branch of AC-CS-4 was therefore not exercised, and the project label must come from the basename of `cwd`.

Verdict: PARTIAL. The plan's pass column asks for PermissionRequest for case (a) in both clients. That is not met. Case (a) as run was pre-permitted and raised no PermissionRequest. PermissionRequest was observed only in case (a2), on the CLI. The desktop app, case (c), and interactive case (a) were not run. What is confirmed: the HTTP hook contract is accepted, and PermissionRequest and Stop fire with the fields above.

### P7: crawl, warm run only

Plan pass: numbers recorded and compared with PRD section 12. Section 12 targets: first cold crawl 0.3M ≤ 30 s and 2M ≤ 5 min; warm full rescan 0.3M ≤ 15 s and 2M ≤ 2.5 min.

Verdict: PARTIAL.

Command (warm, no reboot):

```powershell
python tools/probes/crawl_probe.py --roots "C:\Users\<user>" "C:\AI Projects" --excludes default
```

The cold run was not done. It needs a reboot and the plan schedules it last.

Raw output, verbatim from `p7-warm-run1.txt` with the username redacted:

```text
run: warm/unlabelled
roots: ['C:\\Users\\<user>', 'C:\\AI Projects']
excludes: default
entries: 552245
dirs: 70169
seconds: 74.50
peak working set MB: 18.5
permission/OS errors: 1
reparse points not followed: 26
top 20 directories by entry count:
      30084  C:\Users\<user>\AppData\Local\Claude-Data\claude_pzs8sxrjxfjjc\Code Cache\js
      15355  C:\Users\<user>\AppData\Local\Microsoft\Office\16.0\OfficeFileCache\0\0\CGGKBQHFC5H5ATFMCAAIDBEQFABXBIFI
      15354  C:\Users\<user>\AppData\Local\Microsoft\Office\16.0\OfficeFileCache\0\0\GUCBE7CBDKFXDZEZCEAKHPEGF7EPFWGY
       7404  C:\Users\<user>\AppData\Roaming\Zoom\data\Emojis
       6951  C:\Users\<user>\AppData\Roaming\Python\Python311\site-packages\torch\include\ATen\ops
       4998  C:\Users\<user>\AppData\Local\Microsoft\VSApplicationInsights\vstelAIF-312cbd79-9dbb-4c48-a7da-3cc2a931cb70
       3876  C:\Users\<user>\AppData\Local\Microsoft\Olk\EBWebView\Default\Service Worker\CacheStorage\571988c4897ef87c20ec4d6b13878cf6c36e1caf\3cd1bed6-bb95-4d49-a3f0-4e14039677a3
       3838  C:\Users\<user>\AppData\Roaming\Python\Python311\site-packages\open_webui\frontend\assets\emojis
       3774  C:\Users\<user>\AppData\Local\Microsoft\OneDrive\ListSync\settings\cdn\assets\files\odsp-web-prod_2026-10-30.004\odbspartan
       3760  C:\Users\<user>\AppData\Roaming\Notion\Partitions\notion\Service Worker\CacheStorage\614a0024a405b02cc875d3e091267a8eb895f9ee\9356a569-ab76-4dd0-957e-9a6e716162f6
       3759  C:\Users\<user>\AppData\Local\Microsoft\OneDrive\ListSync\settings\cdn\assets\files\odsp-web-prod_2026-10-23.009\odbspartan
       3754  C:\Users\<user>\AppData\Roaming\Notion\Partitions\notion\Service Worker\CacheStorage\614a0024a405b02cc875d3e091267a8eb895f9ee\328fbbc3-97c9-44d0-8482-2abffd5d6f18
       3751  C:\Users\<user>\AppData\Local\Microsoft\OneDrive\ListSync\settings\cdn\assets\files\odsp-web-prod_2026-10-16.006\odbspartan
       3751  C:\Users\<user>\AppData\Local\Microsoft\OneDrive\ListSync\settings\cdn\assets\files\odsp-web-prod_2026-10-16.005\odbspartan
       3170  C:\Users\<user>\AppData\Local\Microsoft\Olk\EBWebView\Default\Service Worker\CacheStorage\571988c4897ef87c20ec4d6b13878cf6c36e1caf\3a92eea9-0b66-4c85-a070-1b27418ea556
       2946  C:\Users\<user>\AppData\Roaming\Cursor\Partitions\cursor-browser-9dd123427c4c96d90327cba770c699dd\Code Cache\js
       2809  C:\Users\<user>\AppData\Local\Android\Sdk\platforms\android-34\data\res
       2380  C:\Users\<user>\AppData\Local\Claude-Data\claude_pzs8sxrjxfjjc\Cache\Cache_Data
       2008  C:\Users\<user>\AppData\Roaming\Notion\Partitions\notion\Shared Dictionary\cache
       1664  C:\Users\<user>\.vscode\extensions\vscode-icons-team.vscode-icons-13.0.0\icons
```

Comparison with PRD section 12. The figures below are linear projections from this run. No run at 0.3M or 2M entries was made.

- Measured rate: 74.50 s / 552,245 entries = 134.9 µs per entry.
- 0.3M warm: 0.3M x 134.9 µs = about 40.5 s. The warm target is 15 s, so this is about 2.7x over. The cold target is 30 s, so this is about 1.35x over. A cold run is not expected to beat a warm run, so the cold target is not a pass path either.
- 2M warm: 2M x 134.9 µs = about 270 s, or 4.5 min. The warm target is 2.5 min, so this is about 1.8x over.
- 2M cold: about 4.5 min against a 5 min target. This is within target, but it is a projection and the cold run was not done.
- The plan's trigger (plan section 6, P7 row: "miss by > 2x") is therefore met on the 0.3M warm projection. It is not met on the 2M warm projection. The tech-lead's summary cited only the 2M figures.

Excludes caveat. `tools/probes/crawl_probe.py` `DEFAULT_EXCLUDES` (lines 10-42) does not exclude the AppData cache and app-data trees that dominate the top-20 list. Nineteen of the twenty are under `AppData`; the twentieth is under `.vscode`. PRD AC-FI-1 names AppData caches among the default excludes. This run therefore measured a narrower exclude set than the PRD specifies, and the 552,245 count includes those trees. By the probe's per-directory counts, the top 20 directories hold 125,386 entries (22.7% of the total).

RAM caveat. The 18.5 MB peak working set is for the crawl alone. The PRD section 12 RAM target (300 MB at 2M) applies to the built index and was not measured by this probe.

Verdict: PARTIAL. Warm only. The cold run is outstanding. Projected warm figures are above target, and the 0.3M projection exceeds the plan's 2x trigger.

### P8: classic Outlook versus new Outlook

Plan pass: the third command prints a version (classic Outlook present) or raises (new Outlook only). Gates M2-1.

Commands:

```powershell
reg query HKCR\Outlook.Application\CLSID
Get-AppxPackage Microsoft.OutlookForWindows | Select Name, Version
python -c "import win32com.client as w; o=w.Dispatch('Outlook.Application'); print(o.Version)"
```

Output (as relayed; the tech-lead recorded only these values):

- `reg query` value: `{0006F03A-0000-0000-C000-000000000046}`
- `Get-AppxPackage Microsoft.OutlookForWindows`: Version `1.2026.713.100`. New Outlook is also installed.
- Python COM: `16.0.0.20527`

Verdict: PASS. Classic Outlook answers over COM.

### P9: idle overlay window, 10 minutes

Plan pass: numbers recorded against PRD section 12 (overlay 80 MB proposal; idle CPU about 0%). Gates the M1-3a and M1-3b targets and the A10 combined RAM target.

Command:

```powershell
python tools/probes/tk_idle_probe.py --minutes 10
```

Raw output, verbatim from `p9-tk-idle-10min.txt`:

```text
t=  10.0s  private=16.7 MB  cpu=0.16%
t=  20.0s  private=16.8 MB  cpu=0.16%
t=  30.0s  private=16.8 MB  cpu=0.00%
t=  40.0s  private=16.8 MB  cpu=0.31%
t=  50.0s  private=16.7 MB  cpu=0.16%
t=  60.1s  private=16.7 MB  cpu=0.31%
t=  70.1s  private=16.7 MB  cpu=0.16%
t=  80.1s  private=16.7 MB  cpu=0.16%
t=  90.1s  private=16.7 MB  cpu=0.00%
t= 100.1s  private=16.7 MB  cpu=0.00%
t= 110.1s  private=16.7 MB  cpu=0.16%
t= 120.1s  private=16.7 MB  cpu=0.00%
t= 130.1s  private=16.7 MB  cpu=0.16%
t= 140.1s  private=16.7 MB  cpu=0.16%
t= 150.1s  private=16.7 MB  cpu=0.00%
t= 160.1s  private=16.7 MB  cpu=0.00%
t= 170.1s  private=16.7 MB  cpu=0.00%
t= 180.2s  private=16.7 MB  cpu=0.16%
t= 190.2s  private=16.7 MB  cpu=0.00%
t= 200.2s  private=16.7 MB  cpu=0.00%
t= 210.2s  private=16.7 MB  cpu=0.16%
t= 220.2s  private=16.7 MB  cpu=0.16%
t= 230.2s  private=16.7 MB  cpu=0.00%
t= 240.2s  private=16.7 MB  cpu=0.16%
t= 250.2s  private=16.7 MB  cpu=0.00%
t= 260.2s  private=16.7 MB  cpu=0.00%
t= 270.2s  private=16.7 MB  cpu=0.00%
t= 280.2s  private=16.7 MB  cpu=0.00%
t= 290.2s  private=16.7 MB  cpu=0.16%
t= 300.3s  private=16.8 MB  cpu=0.00%
t= 310.3s  private=16.8 MB  cpu=0.00%
t= 320.3s  private=16.8 MB  cpu=0.00%
t= 330.3s  private=16.8 MB  cpu=0.00%
t= 340.3s  private=16.8 MB  cpu=0.00%
t= 350.3s  private=16.8 MB  cpu=0.16%
t= 360.3s  private=16.8 MB  cpu=0.00%
t= 370.3s  private=16.8 MB  cpu=0.16%
t= 380.3s  private=16.8 MB  cpu=0.00%
t= 390.3s  private=16.8 MB  cpu=0.00%
t= 400.4s  private=16.8 MB  cpu=0.00%
t= 410.4s  private=16.7 MB  cpu=0.16%
t= 420.4s  private=16.7 MB  cpu=0.00%
t= 430.4s  private=16.7 MB  cpu=0.16%
t= 440.4s  private=16.7 MB  cpu=0.16%
t= 450.4s  private=16.7 MB  cpu=0.00%
t= 460.4s  private=16.7 MB  cpu=0.16%
t= 470.4s  private=16.7 MB  cpu=0.31%
t= 480.4s  private=16.7 MB  cpu=0.00%
t= 490.4s  private=16.7 MB  cpu=0.00%
t= 500.4s  private=16.7 MB  cpu=0.16%
t= 510.4s  private=16.7 MB  cpu=0.00%
t= 520.4s  private=16.7 MB  cpu=0.00%
t= 530.4s  private=16.7 MB  cpu=0.00%
t= 540.5s  private=16.7 MB  cpu=0.16%
t= 550.5s  private=16.7 MB  cpu=0.00%
t= 560.5s  private=16.7 MB  cpu=0.16%
t= 570.5s  private=16.7 MB  cpu=0.00%
t= 580.5s  private=16.7 MB  cpu=0.00%
t= 590.5s  private=16.7 MB  cpu=0.16%
t= 600.0s  private=16.8 MB  cpu=0.16%
samples: 60
average CPU %: 0.078 (of one core)
max private working set MB: 16.8
total process CPU seconds: 0.80
```

Caveats:

- The probe output labels the column `private` and the summary `max private working set MB`. The plan (section 6, P9) specifies private working set. The tech-lead's note is that the probe measures private bytes. The 16.8 MB figure should be read as private bytes until the probe is corrected.
- This was a blank window. PRD section 12's 80 MB target is for an overlay with one ticking `waiting` card, which was not tested.
- The PRD's idle CPU target covers daemon plus overlay. This probe measured the overlay alone.

Verdict: PASS for a blank window, with the metric caveat above.

## Consequences for the plan

1. P7 needs a decision. Tighten the probe's default excludes so they match PRD AC-FI-1 (AppData caches), re-measure warm, and cold-run after a reboot. Record the results in a dated Revision to the plan. Plan section 6 names "enable scandir-rs on the personal laptop" as the action for a miss over 2x. The 0.3M warm projection in this record is about 2.7x, so the tech-lead should decide whether to act on that now or after the re-measure.
2. P6 interactive half is still open. Case (a) with a permission prompt, case (c) plan approval, and all desktop-app cases need the user to run them. AskUserQuestion fired no hook event in the CLI, which bears on the AC-CS-9 coverage sentence. The sentence cannot be finalised until (b) and (c) are checked interactively.
3. No scope change from P1, P2, P4, P5, P8 or P9 on this laptop.
4. The work-laptop file, `docs/probes/2026-11-06-m0-probes-work.md`, does not exist yet. M0-4 stays open until the user runs P1 to P9 on the work laptop and pastes the output back. The planner still needs to append the Revision recording the measured section 12 values.
5. 2026-11-06 (later): P7 trigger cleared on warm runs after the M0-5 excludes; the projected 0.3M warm figure is 6.7 to 11.8 s against the 15 s target (see "### P7 re-measure after M0-5 excludes (2026-11-06, later)"). Excludes-first decision stands. Compiled accelerator not needed on this laptop on the warm evidence. Cold run after a reboot is still outstanding and user-run.

### P7 re-measure after M0-5 excludes (2026-11-06, later)

Supersedes (in part): "### P7: crawl, warm run only", its Verdict line and its 0.3M warm projection. The original measurement is not changed and remains the baseline for this section. The P7 row in the Summary table above is also unchanged.

- Date: 2026-11-06 (later), same personal laptop as the rest of this file.
- Source: dev re-runs (two runs) and an independent qa run of the same command, relayed in the dispatch for M0-5. The figures below are summarised from those relayed results; no raw output file was recorded for these runs.
- Plan: `docs/plans/2026-11-06-assistant-v1-plan.md` section 14.2 (action 2, re-measure) and task M0-5. PRD: section 12 (warm target 0.3M ≤ 15 s; 2M ≤ 2.5 min).
- Command (warm, no reboot; same command and roots as the original run):

```powershell
python tools/probes/crawl_probe.py --roots "C:\Users\<user>" "C:\AI Projects" --excludes default
```

What changed between the original run and these runs: M0-5 added these AppData cache excludes to the probe's default list:

- `AppData/Local|Roaming/*/{Code Cache,Cache,Cache_Data,GPUCache,Service Worker,Shared Dictionary,OfficeFileCache}/`
- `AppData/Local/npm-cache/` and `AppData/Roaming/npm-cache/`
- `AppData/Local/NuGet/{v3-cache,plugins-cache}/`

Results (all warm):

| Run | Entries | Dirs | Seconds | µs per entry | Peak working set MB |
|---|---|---|---|---|---|
| Original (earlier in this file) | 552,245 | 70,169 | 74.50 | 134.9 | 18.5 |
| Dev run 1 | 393,459 | 38,216 | 15.47 | 39.3 | 18.7 |
| Dev run 2 | 393,467 | 38,217 | 8.78 | 22.3 | 18.9 |
| qa independent run | 393,477 | 38,217 | 9.75 | 24.8 | 18.8 |

In all runs: permission/OS errors 1 and reparse points not followed 26. The original run also had 1 and 26.

Entries removed by the new rules: 158,786 (28.8 % of 552,245, using dev run 1). Per-rule matched directory counts from dev run 1 (counts are matched directories; their contents were not walked):

- Local `Cache` 40
- Roaming `Cache` 37
- Roaming `Code Cache` 28
- Roaming `Shared Dictionary` 24
- Roaming `GPUCache` 22
- Local `GPUCache` 20
- Local `Code Cache` 20
- Local `Shared Dictionary` 19
- Roaming `Service Worker` 8
- Local `Service Worker` 4
- `npm-cache` 1
- NuGet `v3-cache` 1
- `OfficeFileCache` 1

New top 20 after the change is dominated by categories that plan 14.2 deliberately leaves unexcluded: Zoom `Emojis` (7,404), a torch include tree under `AppData\Roaming\Python\Python311\site-packages\torch\include\ATen\ops` (6,951), `VSApplicationInsights` (4,998), and OneDrive `odbspartan` asset folders (about 3,750 each).

Timing caveat. The three post-change runs range from 8.78 s to 15.47 s on nearly identical entry sets (393,459 to 393,477 entries). Disk-cache state alone moves warm time by about 1.8x (15.47 / 8.78). The original 74.50 s was probably measured colder or under load; this was not established. Only the 28.8 % reduction in entries is attributable to the excludes with certainty.

Against the warm target (0.3M ≤ 15 s), using linear scaling by µs per entry to 0.3M entries (a projection, not a 0.3M run):

- Dev run 1: about 11.8 s
- Dev run 2: about 6.7 s
- qa run: about 7.4 s

Unscaled, dev run 1 at 393,459 entries took 15.47 s, which is above 15 s. Dev run 2 and the qa run were below 15 s at that size.

Verdict on the warm target: PASS on the projection, with the slowest run (projected 11.8 s) the closest to the target. The 2M warm target (2.5 min) was not re-measured. The cold run after a reboot is outstanding and user-run.

qa verification of the probe change: fixture tree went from 27 to 17 entries, with the expected per-rule counts; a `proj\Cache` directory outside AppData was kept. Verdict: PASS.

Note for M3a-1 (raised by qa). `fnmatch` `*` crosses `/`, so `AppData/Local/*/Cache/` matches at any depth under `AppData\Local`. This is what catches `Microsoft/Office/16.0/OfficeFileCache`. The product exclude matcher (`assistant/index/excludes.py`) must pin this behaviour deliberately, with a test.

P9 label fix. The probe now reports "private bytes (PrivateUsage)" instead of private working set. A 0.2-minute re-run printed max private bytes 16.7 MB and average CPU 0.078 %. The P9 section above still uses the earlier label and is unchanged.
