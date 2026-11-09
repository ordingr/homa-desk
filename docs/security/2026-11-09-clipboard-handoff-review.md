# Security review: popup-to-full-page handoff and file clipboard (branch `popup-clipboard`)

Append-only (archaeology rule). New findings go in dated sections at the end; nothing above is edited.

## 2026-11-09 — Review 15: handoff on the launch nonce, POST /v1/files/clipboard, shortcut migration, driver R15 follow-up

**Reviewer:** `security` verifier (read-only on source; this file is the only write). **Hand-off:** `tech-lead` →
`git-keeper` (plan step D5 of `docs/plans/2026-11-09-popup-to-fullpage-and-file-clipboard.md`; qa passed D4 before this
review). **Scope:** the uncommitted working tree of the `popup-clipboard` worktree against main `b320269`:
`homa/actions/shell.py` (`dropfiles_bytes`, `_set_clipboard_files`, `_raw_prefix`, `_clipboard_path`,
`clipboard_files`), `homa/server/routes_actions.py` (`files_clipboard`), `homa/server/auth.py` (`check_handoff`,
`mint_nonce`, `take_nonce`, the exchange response), `homa/server/routes_files.py` (`fullpage_open` fields and the
256 KiB cap), `homa/app.py` (`_on_fullpage`, `_open_fullpage`, `_on_clipboard`), `homa/overlay/search_ui.py`
(clipboard, multi-select, double-click handoff, skipped note), `homa/overlay/settings_ui.py`, `homa/settings.py`
(shortcut migration), `web/src/api.js`, `web/src/App.jsx`, `web/src/components/ContextMenu.jsx`, `web/src/shortcuts.js`,
`tools/driver.py`. The rebuilt `homa/static/app/assets/*` bundles were not diffed line by line (build output of the
reviewed `web/src`). Items are `R15C-*`.

**Method.** Diff reading with taint tracing from each new input (JSON body, handoff dict, settings.json) to its sinks
(`os.path.realpath`/`lstat` via `_vet`, the CF_HDROP payload, JSON responses, logs, the Edge launch URL, React text, Tk
canvas text). Then three harmless local reproductions, all on scratch data under the session scratchpad:

1. An in-process probe (`clip_probe.py`): a scratch tree with `.env`, `id_rsa`, a junction named `.ssh` pointing at a
   plain folder and a junction pointing at the root; `HOMA_HOME` in the scratch tree; a fake index built by
   `tests/conftest.walk_store`; `shell._set_clipboard_files` replaced by a recorder, so **the real clipboard was never
   written**; `os.path.realpath` wrapped so that any path starting with two separators is recorded and refused,
   so **no UNC path reached the network**. Route probes ran over ASGI against the real `create_app` + `AuthMiddleware`.
2. A private test-mode Homa (`live_probe.py`): `tests/conftest.launch_app` on a free port (50187, never 17171), scratch
   `HOMA_HOME`, `USERPROFILE`, `HOME`, `LOCALAPPDATA`, `APPDATA`; two malformed requests that are refused before any
   clipboard write; the instance was stopped by the helper, and a process listing afterwards showed no process from
   this worktree (the live instance's two `pythonw` processes were not touched).
3. `homa.settings._merged` on crafted `shortcuts` objects with a scratch `HOMA_HOME`.

No Edge window was opened. The real `~/.claude/settings.json` and the live home were not read or written.

### Verdict: PASS. No BLOCK. Six NOTEs (R15C-N1 to R15C-N6); N1 and N2 are cheap and recommended before merge.

Every new input is reachable only with the bearer token (clipboard route, `fullpage/open`) or a 30-second one-shot
nonce (exchange), behind the unchanged Host/Origin checks; the clipboard is already readable and writable by any
process of the same Windows account, and Homa only places a CF_HDROP list on it — Paste in Explorer is the user's own
act. The path rules of plan section 4.3 hold against every canonicalisation trick tried. The NOTEs are two 500s where
the contract says 422 (one is a sibling of the `sort` bug fixed just before this review), one pre-existing `_vet` gap
that this change makes reachable from one more route, and three low-impact hardening items.

### Checked and passing

- **Auth on POST /v1/files/clipboard.** Bearer required (qa test `test_no_bearer_or_a_wrong_one_is_401`); foreign
  Origin → 403 even with the bearer (probe: `route evil origin -> (403, ... "forbidden_origin" ...)`); foreign Host →
  403; GET → 405. A CORS preflight or a "simple" cross-site POST always carries Origin and is refused by the
  middleware before routing; no CORS headers are ever emitted.
- **Limits.** 256 KiB body cap (413, qa test), 1–1000 paths, each `str`, non-empty, no NUL, ≤ 32767 chars.
- **Raw prefixes refused before normalising** (`shell.py:326-329`), both slash styles and mixed: probe results for
  `\\?\`, `\\.\`, `\??\`, `\\?\UNC\`, `\/?/…`, `/\.\…` were all `refused: device and long-path prefixes are not
  accepted`, for copy and cut.
- **`_vet` with `protected_ok=False`, `not_root` for cut** (`shell.py:337`). Probe, copy and cut alike:
  - 8.3 short name, trailing dot, trailing space, `". . "`, `..` traversal and forward slashes to `.env`, and
    `id_rsa.` → `refused: protected file (secrets deny-list)` (the resolved long name is checked);
  - ADS `a.txt:s`, `a.txt::$DATA` → `outside the indexed roots`; `CON`, `NUL.txt` inside the root → `no longer
    exists`; bare `COM1`, `CONIN$`, relative and drive-relative `C:docs\a.txt` → `give an absolute path`;
  - Cut of the root, `root\`, `root\.`, the root's 8.3 name, and a junction pointing at the root → `that is an indexed
    root folder`; Homa's home → refused.
- **Junctions.** Copy places the resolved target; Cut places the link itself only when the link sits inside a root and
  its target passed `_vet` (`_link_or_target`); a junction named `.ssh` → deny-list refusal (but see N4).
- **DROPFILES.** Header `14000000 00000000 00000000 00000000 01000000` (pFiles 20, pt 0,0, fNC 0, fWide 1); for
  `["C:\x", "C:\<U+1F600>"]` the payload is 44 bytes = 20 + 10 + 12 (surrogate pair) + 2, ending `00de 0000 0000`
  (each path NUL-terminated, then the list terminator). Paths cannot hold NUL (checked before and after `_vet`).
- **Homa never moves, deletes or opens a file** in this feature: the only side effects are `_vet`'s stat calls and
  the clipboard (code reading; qa's audit-hook test covers it at run time).
- **Log hygiene.** `log.warning` lines carry exception type names only; the action log writes `"<n> paths"`. In the
  live probe the request paths carried a marker string; afterwards `files containing the marker path: []` across the
  scratch home's `logs/app.log` and `console.txt`, although both requests produced tracebacks.
- **Handoff.** Validated at mint (`auth.py check_handoff`), stored with the nonce, returned once:
  `exchange ok -> 200 {"token": …, "handoff": {…}}`, `exchange replay -> 403 invalid_nonce`; foreign and `null`
  Origin → 403 and the nonce is **not** burned (the next legitimate exchange succeeded). TTL unchanged (30 s, qa test
  `test_the_handoff_expires_with_the_nonce`). The launch URL is `http://127.0.0.1:1234/app/?launch=NONCE&q=q` — no
  `select_path`, chips, sort or date in it. The exchange response keeps `cache-control: no-store`.
- **The unhashable-`sort` fix is in** (`isinstance(sort, str)` before `in dict(SORT_CHOICES)`); qa's regression test
  passes. Siblings in `check_handoff`: `chips` checks `isinstance(c, str)` before `in CHIPS`, `valid_modified` checks
  type first, `select_path` is type-checked; probe `fullpage/open` with list-of-lists chips, dict chips, list
  `modified`, dict `select_path` → all 422 with the right `field`. **One sibling was found outside `check_handoff`
  (N1).**
- **app.py.** `_open_fullpage` re-validates the popup's handoff before minting; a `ValueError` is logged by
  `log.exception` whose messages name fields, never a path value. `_on_clipboard` passes through to `clipboard_files`.
- **Popup (Tk thread).** The skipped note shows only `reason` strings, which are fixed texts from `_vet` /
  `_clipboard_path` (never the caller's path), so no untrusted control characters reach the canvas from this feature.
  `select_path` travels in-process.
- **Web.** Toasts render as React text (`{toast.message}`); the only `dangerouslySetInnerHTML` uses are the existing
  Mermaid and highlight.js previews, untouched here. `skipped[].reason` and `error.message` are server constants.
  The handoff is type-checked again on the page and used only to seed state and match a row (`pathKey` comparison);
  `exchangeNonce` ignores a non-object `handoff`. `matches(event, null)` returns false, so a `""` chord is inert.
- **Settings migration.** Crafted `shortcuts` objects (`[]`, `{"open": ""}`, non-string chords, a 500-char name,
  `copy_path:"ctrl+c"` with `rename:"ctrl+shift+c"`, `__class__` as a name) all merge without exception; invalid
  entries fall back to defaults; `""` is accepted only for `copy_files`, `cut_files`, `copy_path`, `select_all`;
  `DEFAULT_SHORTCUTS` / `DEFAULTS` are not mutated (`defaults intact: Ctrl+C Ctrl+C`). `chord_sequences("")` is `[]`,
  so the popup binds nothing. settings.json is writable only by the same account.
- **tools/driver.py (Review 14 R15 follow-up).** R15-N1 RESOLVED: `_real_home_overlap` refuses a `--home` that is,
  is inside, or contains `~/.homa` or `~/.assistant`, before anything is written (`test_start_refuses_the_real_home_
  before_writing_anything`). R15-N2 RESOLVED: every inherited `ASSISTANT_*` / `HOMA_*` variable except `HOMA_HOME`
  is dropped (`test_the_child_env_drops_inherited_prefixed_variables`). R15-N3 RESOLVED for the proxy:
  `_OPENER = build_opener(ProxyHandler({}))` (`test_http_ignores_proxy_variables`); redirects: see N6.

### Findings

**R15C-N1 (NOTE, low; recommended before merge) — `homa/server/routes_actions.py:86`: an unhashable `op` is a 500,
not a 422.** `op not in CLIPBOARD_OPS` is a dict membership test, so `{"op": ["copy"]}` or `{"op": {}}` raises
`TypeError` out of the route. Same shape as the `sort` bug just fixed. Repro against the private instance:
`op list -> (500, None)`, traceback `TypeError: unhashable type: 'list'` in the scratch `logs/app.log`; the server keeps
serving (`health after -> 200`). Bearer required; no data effect. (`shell.py:364` has the same test but its caller
catches the exception and returns `error`.) Fix: `if not isinstance(op, str) or op not in CLIPBOARD_OPS:` at both
places, and add `{"op": ["copy"]}` to `test_shape_errors_are_422_with_the_field`.

**R15C-N2 (NOTE, low; recommended before merge) — `homa/actions/shell.py:377` echoed into `routes_actions.py:99/104`:
a path holding a lone UTF-16 surrogate is a 500, and in a mixed request the clipboard has already been written.**
JSON allows `"\ud800"`; it survives `_vet` as a skipped path, is echoed in `skipped[].path`, and Starlette's
`JSONResponse` (`ensure_ascii=False` then `.encode("utf-8")`) raises `UnicodeEncodeError`. Repro: `surrogate -> (500,
None)` on the private instance (ASGI probe: `UnicodeEncodeError: 'utf-8' codec can't encode character '\ud800' …
surrogates not allowed`). No path reached the log (marker check above). With one good path alongside, the files are
placed on the clipboard and the caller is told 500. Fix: refuse such paths in `_clipboard_path` with the other shape
checks (e.g. `path.encode("utf-16-le")` in a `try`, refusing on `UnicodeEncodeError`), or 422 them in the route.
(`fullpage/open` accepts a surrogate `select_path` and returns 200; the exchange uses `json.dumps` with ASCII escaping,
so that path does not fail — informational.)

**R15C-N3 (NOTE, low; pre-existing in `_vet`, newly reachable from this route) — `homa/actions/shell.py:99`: mixed-
separator UNC spellings skip the "no UNC root" check and reach `os.path.realpath`.** `_mcp._is_unc` only recognises
`\\` and `//`; Windows also treats `\/host/share` and `/\host\share` as UNC. Probe (with `realpath` guarded):
`network attempts reaching realpath: ['\\/127.0.0.1/c$/x.txt', '/\\127.0.0.1\\c$\\x.txt', …]`. Final result is still
`refused: … outside the indexed roots`, but unguarded `realpath` opens the path, which means an SMB connection
attempt (and an NTLM handshake) to an arbitrary host before the refusal. Bearer required, so only a bearer holder
(the page itself, or a same-account process) can trigger it. Fix: in `_mcp._is_unc` (or before it in `_vet`), test
the first two characters after mapping `/` to `\` — `path[:2].replace("/", "\\") == "\\\\"`; that also covers the
`/v1/actions/*` and MCP callers of the same helper.

**R15C-N4 (NOTE, low) — `homa/actions/shell.py:431` with `_vet`'s deny check: a link's own name escapes the deny-list
with a trailing dot or space, for Cut.** The deny-list matches the given text; Win32 drops trailing dots and spaces
when opening. A junction named `.ssh` that points at a plain folder: `root\.ssh` → refused, but `root\.ssh.` →
`[cut] … ok=True placed=['…\root\.ssh.']` (copy places the plain target). The **target** is always vetted on its
resolved long name, so no deny-listed content is exposed — a real `.ssh` folder, `.env` or `id_rsa` reached through
any spelling is refused (see "Checked and passing"). Impact is limited to moving a link whose name looks secret.
Fix: strip trailing dots and spaces from each component of `given` (or compare against `os.lstat`'s real name) before
`denylist.matches(given)`, and return the stripped spelling from `_link_or_target`.

**R15C-N5 (NOTE, informational; by code reading, not exercised) — Cut can name an ancestor of Homa's home when a
root is above it.** `_vet` refuses the home and anything inside it, the roots and everything outside them, but not a
folder that *contains* the home (e.g. with `C:\` indexed, Cut of `C:\Users`). Explorer would attempt the move on the
user's Paste and fail on the files in use; this is the same thing Explorer's own Cut allows, and it needs the user's
selection plus Paste. Recorded so the plan's question "can Cut target Homa's home" has an exact answer: the home
itself, no; a folder containing it, yes. Optional fix: also refuse when `_inside(own, resolved)`.

**R15C-N6 (NOTE, low) — `tools/driver.py:36`: the opener bypasses proxies but still follows redirects.**
`build_opener(ProxyHandler({}))` keeps the default `HTTPRedirectHandler`, and urllib copies `Authorization` onto the
redirected request, so whatever answers on the scratch port with a 302 to another host would receive the scratch
instance's bearer (the R14-N1 shape). Scratch token only, developer tool. Fix: add a `_NoRedirect` handler as in
`homa/migrate.py`.

Also noted, not a finding: `search.modes._DAY_RANGE` (pre-existing) ends in `$`, so `"2026-01-01..2026-01-02\n"` is a
valid `modified` (`fullpage modified with newline -> 200`); the page's `normaliseModified` copes, so the effect is
cosmetic at worst. `\d` there also accepts non-ASCII digits.

### Commands run (real output, trimmed)

```
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_clipboard_route.py tests/test_clipboard_files.py
    tests/test_launch_handoff.py tests/test_shortcuts_migration.py tests/test_driver_guard.py
→ 173 passed in 19.16s

PROBE_DIR=<scratchpad> .venv\Scripts\python.exe <scratchpad>\clip_probe.py
→ [copy] short name of .env: ok=False placed=[] reason=refused: protected file (secrets deny-list)
  [cut] mixed \/?/ prefix: ok=False placed=[] reason=refused: device and long-path prefixes are not accepted
  [cut] junction named .ssh (target non-secret) trailing dot: ok=True placed=['…\root\.ssh.'] …
  [cut] junction to root: ok=False … reason=refused: that is an indexed root folder, not a file inside one
  network attempts reaching realpath: ['\\/127.0.0.1/c$/x.txt', '/\\127.0.0.1\\c$\\x.txt', …]
  action log: [{… 'verb': 'cut_files', 'path': '1 paths', 'result': 'refused: none of the paths can go on the clipboard'}, …]
  dropfiles len 44 tail 00de00000000 header 1400000000000000000000000000000001000000
  route op as list -> ('EXC', "TypeError: unhashable type: 'list'")
  route lone surrogate path -> ('EXC', "UnicodeEncodeError: 'utf-8' codec can't encode character '\ud800' …")
  route evil origin -> (403, … "forbidden_origin" …)
  fullpage chips list of lists -> (422, … "field":"chips")   fullpage select_path dict -> (422, … "field":"select_path")
  exchange evil origin -> (403 forbidden_origin)   exchange null origin -> (403 forbidden_origin)
  exchange ok -> (200, {"token": "TTTT…", "handoff": {"query": "q", …, "select_path": "C:\\secret\\place.txt"}})
  exchange replay -> (403, invalid_nonce)
  url: http://127.0.0.1:1234/app/?launch=NONCE&q=q

PROBE_DIR=<scratchpad> .venv\Scripts\python.exe <scratchpad>\live_probe.py      (private instance, port 50187)
→ op list -> (500, None)
  surrogate -> (500, None)
  health after -> 200
  traceback in home\logs\app.log -> ["TypeError: unhashable type: 'list'", "UnicodeEncodeError: … surrogates not allowed"]
  files containing the marker path: []
```

The full test suite and the e2e browser tests were not run here (qa's D4). Unverified here: the real clipboard
round trip and Explorer's Paste (qa's tests cover the read-back; this review deliberately did not write the clipboard).

## 2026-11-09 — Review 15, re-verification of R15C-N1..N6 after dev's fixes

**Reviewer:** `security` verifier (read-only on source; this appended section is the only write). **Hand-off:**
`tech-lead` → `git-keeper`. **Scope:** the fixes now in the `popup-clipboard` worktree in `homa/actions/shell.py`
(`_win_trim`, `_vet`, `_clipboard_path`, `_clipboard_files`, `_link_or_target`), `homa/server/routes_actions.py`,
`homa/server/mcp.py` (`_vet`, the same N3 fix; also reached from `routes_files.py`), `tools/driver.py`. Same method and
the same scratch-only probes as above, extended with: `mcp._vet` on the mixed UNC spellings, `shell._vet` with
`protected_ok=True` and `False` on ordinary paths (the other actions' view of the shared changes), `_link_or_target` on
the junctions, `_win_trim` edge cases, and an N5 scenario with `HOMA_HOME` inside an indexed root. The clipboard
writer was stubbed, UNC paths were stopped before `realpath`, and the private instance (port 50343) was stopped; a
process listing afterwards showed only the live instance's two processes, untouched.

### Verdict: PASS. All six NOTEs RESOLVED. No new BLOCK. One informational item (N5 scope, below).

### Per note, before → after

- **R15C-N1 RESOLVED.** `routes_actions.py` and `shell._clipboard_files` now test `isinstance(op, str)` first.
  Before: `op list -> (500, None)`. After, private instance: `op list -> (422, {'error': {'code':
  'invalid_parameter', 'message': 'op must be copy or cut.', 'field': 'op'}})`; ASGI probe, dict `op` → the same 422.
- **R15C-N2 RESOLVED.** `_clipboard_path` refuses a path that cannot encode to UTF-16 (`refused: not a usable path`),
  and skipped paths are echoed with `backslashreplace`. Before: `surrogate -> (500, None)`. After, private instance:
  `surrogate -> (403, {… 'skipped': [{'path': '<scratch>\\root\\docs\\ZZMARKERZZ\\ud800', 'reason': 'refused: not a
  usable path'}]})`, `health after -> 200`, and still `files containing the marker path: []` in the scratch logs.
  Because the refusal happens before `_vet`, a lone surrogate can no longer ride along with good paths into a 500.
- **R15C-N3 RESOLVED** in both `shell._vet` and `mcp._vet` (`_is_unc(path.replace("/", "\\"))`). Before: `network
  attempts reaching realpath: ['\\/127.0.0.1/c$/x.txt', '/\\127.0.0.1\\c$\\x.txt', …]`. After, through the clipboard
  route, `shell._vet` and `mcp._vet`, for `\/…`, `/\…` and `//…`: `refused: the path is outside the indexed roots`, and
  `network attempts reaching realpath (round 2): []`. The other `resolve_in_roots` callers: `_link_or_target` runs only
  on a path that already passed `_vet`, and `routes_files.py:128` resolves a server-derived final path.
- **R15C-N4 RESOLVED.** `_win_trim` drops trailing dots and spaces per component, the way Win32 does
  (`C:\a. \b..\.\..\c. .` → `C:\a\b\.\..\c`, `\\srv\share\x.` → `\\srv\share\x`, `C:/x/.env .` → `C:\x\.env`; `.` and
  `..` kept). Before: `[cut] junction named .ssh … trailing dot: ok=True placed=['…\root\.ssh.']`. After: copy and cut
  of `root\.ssh.` → `refused: protected file (secrets deny-list)`, same as `root\.ssh`.
- **R15C-N5 RESOLVED for Cut** (`_clipboard_path` refuses when Homa's home is inside the resolved folder). With
  `HOMA_HOME` inside the root: `N5 [cut] folder containing home: ok=False reason=refused: that folder holds Homa's own
  data`; `[cut] home itself` → `refused: Homa's own data`; `[cut] sibling file` → ok. Copy of the containing folder
  is still allowed (`N5 [copy] folder containing home: ok=True`); see the informational note below.
- **R15C-N6 RESOLVED.** `tools/driver.py` builds its opener with `ProxyHandler({})` and `_NoRedirect`. Handlers on the
  live opener object: `['UnknownHandler', 'HTTPHandler', 'HTTPDefaultErrorHandler', 'FTPHandler', 'FileHandler',
  'DataHandler', 'HTTPSHandler', '_NoRedirect', 'HTTPErrorProcessor']` (no proxy handler, no default
  `HTTPRedirectHandler`). Behavioural test `test_http_does_not_follow_a_redirect_or_forward_the_bearer` passes.

### Regression check of the shared `_vet` / `_link_or_target` changes (other actions)

- `_vet(protected_ok=True)`, the path used by `copy_path`, `rename` and `recycle`: an ordinary file and folder resolve
  as before; `root\.ssh` and `root\.ssh.` both resolve to the junction's target (`…\root\inner`), as before for the
  plain spelling. `_vet(protected_ok=False)`: an ordinary file and folder unchanged; both `.ssh` spellings refused.
- `_link_or_target`: `root\.ssh` and `root\.ssh.` now both return the target, matching the existing rule for links
  with deny-listed names (rename and recycle act on the target, not the link). `root\toroot` (an ordinary junction)
  still returns the link itself. The only behaviour change is that the trailing-dot spelling now follows the plain
  spelling's rule.
- `_win_trim` only adds deny-list matches for spellings Win32 would open as the deny-listed name; real files and
  folders cannot end in a dot or space through Win32, so ordinary paths are not newly refused (ordinary cases above
  unchanged; the regression suites below pass).

**Informational, not a finding:** Copy (not Cut) of a folder that contains Homa's home or a deny-listed file is
allowed, and Paste in Explorer copies its contents, token included. That is the same as Explorer's own Copy, needs
the user's selection plus Paste, and duplicates only what the same account can already read; plan 4.3 checks the
named path, not the contents of folders. Also, by code reading and already the case before this branch, `recycle`
(`_vet(protected_ok=True, not_root=True)`) does not refuse a folder that contains Homa's home; that is out of scope
for this change and is listed for a future hardening pass.

### Commands run (real output, trimmed)

```
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_clipboard_route.py tests/test_clipboard_files.py
    tests/test_launch_handoff.py tests/test_shortcuts_migration.py tests/test_driver_guard.py tests/test_actions_shell.py
    tests/test_denylist.py tests/test_mcp_files.py tests/test_preview_routes.py tests/test_rename_hardening.py
→ 416 passed, 1 skipped in 104.01s (0:01:44)

PROBE_DIR=<scratchpad> .venv\Scripts\python.exe <scratchpad>\clip_probe2.py
→ [copy] junction named .ssh (target non-secret) trailing dot: ok=False placed=[] reason=refused: protected file (secrets deny-list)
  [cut]  junction named .ssh (target non-secret) trailing dot: ok=False placed=[] reason=refused: protected file (secrets deny-list)
  network attempts reaching realpath: []
  route op as list -> (422, … "field":"op")   route op as object -> (422, … "field":"op")
  route lone surrogate only -> (403, {… "skipped":[{"path":"\\ud800","reason":"refused: not a usable path"}]})
  mcp._vet '\\/127.0.0.1/c$/x.txt' -> (None, 'refused: the path is outside the indexed roots')
  mcp._vet '/\\127.0.0.1\\c$\\x.txt' -> (None, 'refused: the path is outside the indexed roots')
  network attempts reaching realpath (round 2): []
  _vet protected_ok=True b\root\.ssh. -> b\root\inner     _vet protected_ok=False b\root\.ssh. -> STOP refused: protected file (secrets deny-list)
  _link_or_target root\.ssh. -> …\root\inner              _link_or_target oot\toroot -> …\root\toroot
  N5 [cut] folder containing home: ok=False reason=refused: that folder holds Homa's own data
  (all other cases from the first run: identical results)

PROBE_DIR=<scratchpad> .venv\Scripts\python.exe <scratchpad>\live_probe.py      (private instance, port 50343)
→ op list -> (422, {'error': {'code': 'invalid_parameter', 'message': 'op must be copy or cut.', 'field': 'op'}})
  surrogate -> (403, {… 'skipped': [{'path': '<scratch>\\root\\docs\\ZZMARKERZZ\\ud800', 'reason': 'refused: not a usable path'}]})
  health after -> 200
  files containing the marker path: []
```

Not run here: the full suite and the e2e browser tests (qa), and a real clipboard write (deliberately avoided).
