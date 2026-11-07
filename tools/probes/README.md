# Probe scripts (plan section 6)

Standard library only, Python 3.10+, Windows. Replace `<repo>` with the clone path.
Raw output is recorded in `docs/probes/`.

## P6 - Claude Code hook coverage

1. Start the logger (keeps running until Ctrl+C; appends one JSON line per POST, each with a `received_at` timestamp):

   ```powershell
   python <repo>\tools\probes\hook_logger.py --port 17199 --out $env:TEMP\hooks.jsonl
   ```

2. Print the hooks block for the eight AC-CS-2 events (`PermissionRequest`, `Notification`, `Stop`, `StopFailure`, `UserPromptSubmit`, `PostToolUse`, `SessionStart`, `SessionEnd`; each `type: http` to `http://127.0.0.1:17199/hook` with `onFailure: continue`):

   ```powershell
   python <repo>\tools\probes\hook_probe_settings.py --port 17199 --print
   ```

   Back up first, then paste the block into `~\.claude\settings.json`:

   ```powershell
   Copy-Item ~\.claude\settings.json ~\.claude\settings.json.bak
   ```

   Alternative: `python <repo>\tools\probes\hook_probe_settings.py --port 17199 --write ~\.claude\settings.json` merges the entries into that file (existing hooks kept, entries already pointing at the same URL not duplicated) after writing a `.bak` copy next to it (a timestamped `.bak` if one already exists).

3. In the CLI and then in the desktop app: (a) ask Claude to run a shell command that needs permission, wait 10 s, approve; (b) ask Claude to "ask me a question using AskUserQuestion before continuing", answer it; (c) enter plan mode, let Claude present a plan, approve it.

4. Print the report, then restore `settings.json.bak`:

   ```powershell
   python <repo>\tools\probes\hook_probe_report.py $env:TEMP\hooks.jsonl
   ```

   Report design: the script cannot know where one case or client ends, so it prints every event in arrival order, grouped by `session_id` (each client run is a different session), with columns time, `hook_event_name`, matcher / `notification_type` (falls back to `tool_name`), `cwd`, `session_title` (blank if absent), and a line of distinct `hook_event_name` values per session. Map sessions to case x client by the times at which you performed each step; write them in the probe record.

## P7 - crawl cost

```powershell
python <repo>\tools\probes\crawl_probe.py --roots "C:\Users\<you>" "C:\<other roots>" --excludes default
```

- `--excludes default | none | <file>`: `default` is the list from the file-search brief Q6; a file holds one gitignore-style pattern per line (`#` comments; trailing `/` = directories only; a pattern containing `/` matches the tail of the root-relative path; otherwise it matches the entry name; a `C:\...` pattern matches that absolute directory). The default list includes AppData application caches (`Code Cache`, `Cache`, `Cache_Data`, `GPUCache`, `Service Worker`, `Shared Dictionary`, `OfficeFileCache`, npm and NuGet caches) written as `AppData/Local/*/<name>/` and `AppData/Roaming/*/<name>/`, so a project folder named `Cache` elsewhere is not excluded.
- `--cold-hint` only labels the run as cold in the output. Run once after a reboot (cold) and once again (warm).
- Output: entries (files plus directories, excluded entries not counted), dirs, seconds, peak working set MB, OS/permission errors skipped, reparse points not followed, entries skipped per exclude rule (the matched entry only), and the top 20 directories by direct-child count. Junctions and symlinks are counted as entries but never followed.

## P9 - idle tkinter cost

```powershell
python <repo>\tools\probes\tk_idle_probe.py --minutes 10
```

Opens a small topmost window with a label updated at 1 Hz, samples private bytes and process CPU time every 10 s, then prints average CPU % (of one core), max private bytes in MB (`PrivateUsage`, committed private memory; not the private working set), and exits. `--minutes 0.2` is a quick smoke run.
