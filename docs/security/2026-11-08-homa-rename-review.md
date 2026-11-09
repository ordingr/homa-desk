# Security review: rename Assistant to Homa (branch `rename-homa`)

Append-only (archaeology rule). New findings go in dated sections at the end; nothing above is edited.

## 2026-11-08 — Review 14: the rename-to-Homa change (uncommitted working tree in the `rename-homa` worktree against HEAD a8e0021)

**Reviewer:** `security` verifier (read-only on source; this file is the only write). **Hand-off:** `tech-lead`
(plan step R6 of `docs/plans/2026-11-08-rename-to-homa.md`; qa passed R5a/R5b before this review).
**Scope:** the security-relevant classes of the rename: the new one-time migration (`homa/migrate.py`, wired in
`homa/__main__.py` and `homa/app.py`), the environment fallbacks (`homa/paths.py`), the read-only hook check
(`homa/hooks_installer.py` `installed_ports()` / `mismatch()`, `homa/doctor.py` `_hooks_state()`, the two start-up
cards in `app._show_migration_cards()`), the old-name protections (`homa/security/denylist.py`, `homa/index/manager.py`,
`homa/update.py`), the `assistant/` shim, `homa/send.py`, the three `web/src` localStorage fallbacks, the new tests'
isolation (`tests/conftest.py`, `tests/test_migrate.py`, `tests/test_doctor_rename.py`, `tests/test_migration_cards.py`,
`tests/test_legacy_shim.py`, `tests/test_paths_fallback.py`) and the identifier scan. `tools/driver.py` is being
changed in parallel by dev and was **not** reviewed (follow-up below). Items are `R14-*`.

**Method.** Diff of every renamed file against its `HEAD:assistant/...` original (so the rename noise drops out and
only behaviour changes remain), then a probe script on scratch profiles (`USERPROFILE`, `HOME`, `HOMEDRIVE`,
`HOMEPATH`, `LOCALAPPDATA` and `HOMA_MIGRATE_SHORTCUT_DIRS` all pointed under the session scratchpad; `HOMA_HOME` and
`ASSISTANT_HOME` unset; `Path.home()` asserted equal to the scratch profile before every section). Two throwaway
`http.server` instances on free loopback ports played the "old instance". No app window was opened; the test that
starts a real overlay (`test_real_old_instance_on_a_scratch_port_is_shut_down_then_migrated`) was deselected here
because qa already ran it and it shows the overlay icon.

### Verdict: PASS. No BLOCK. Seven NOTEs (R14-N1 to R14-N7); N1 and N5 are recommended before the R9 cutover.

Nothing in the change crosses a privilege boundary: every input the migration trusts (`assistant.pid`, `token`,
`migrated-from.json`, `migration-notice.json`, `~/.claude/settings.json`, the environment, the `.lnk` files) is
writable only by the same Windows account that can already read the token. The NOTEs are robustness and
data-safety gaps a same-user actor or a corrupted file could trigger, each with a one- or two-line fix.

### Findings

**R14-N1 (NOTE, medium hardening) — `homa/migrate.py:27,144-149`: the shutdown opener follows redirects and
forwards the bearer.** `_opener = build_opener(ProxyHandler({}))` has no redirect refusal, unlike
`homa/send.py:19` (`build_opener(ProxyHandler({}), _NoRedirect)`). Observed: a local "old instance" answering
`POST /v1/shutdown` with `302 Location: http://127.0.0.1:<other port>/stolen` made `migrate._request_shutdown()`
issue `GET /stolen` to the second server **with `Authorization: Bearer <full token>`** (urllib's default
`HTTPRedirectHandler` keeps every header except Content-Length/Content-Type and converts the POST to GET). The same
302 through `send._opener` raised `HTTPError 302` and the second server saw nothing. Precondition: the responder must
pass `_running_old_instance()` (`:126-136`): the pid in `~/.assistant/assistant.pid` must own the listening socket
on the recorded port **and** be a `python*` image, so it is a same-user Python process that can already read
`~/.assistant/token`. No boundary crossed; still, a redirect from the shutdown route is never legitimate and the
request can leave the port it was aimed at. Fix: reuse `send._NoRedirect` (or an equivalent local handler) in the
opener, and extend `tests/test_migrate.py::test_migrate_builds_its_own_opener_and_never_the_default_urlopen` to
assert it.

**R14-N2 (NOTE, low) — `homa/migrate.py:140-143`: a non-ASCII token file raises out of `_request_shutdown()`.**
`(old / "token").read_text(encoding="ascii")` is guarded by `except OSError` only; `UnicodeDecodeError` is a
`ValueError`. Observed: token bytes `ff fe 00` → `UnicodeDecodeError` escaped. Reached only after the liveness gate
passed, i.e. when a live old instance exists; effect: `python -m homa` / `--init` ends with a traceback (silent exit
under `pythonw`, since `_report()` is never reached) **before** anything is moved, so it fails safe. A CRLF-bearing
token (`abc\r\nX-Injected: 1`) was refused by `http.client` as an invalid header value and swallowed by the existing
`except ValueError` — no header injection. Fix: `except (OSError, ValueError)` on the read (as `_read_old_instance()`
already does at `:118-123`).

**R14-N3 (NOTE, low) — `homa/migrate.py:88-93`: `status()` assumes the marker is a JSON object.** `info.get(...)`
on `[1]`, `"x"` or `null` raised `AttributeError` → `python -m homa --doctor` would end in a traceback.
`{"from": {"a": 1}, "when": [1]}` is interpolated as-is into the doctor line (`done: moved from {'a': 1} on [1]`;
stdout only, never a card). Invalid UTF-8 and truncated JSON are handled (`done (marker unreadable)`). The marker
sits inside the ACL-restricted home and is written by the app; same-user only. Fix: `isinstance(info, dict)` and
accept only `str` for `from`/`when`.

**R14-N4 (NOTE, low) — `homa/hooks_installer.py:252-254`: `isdigit()` then `int()` disagree on Unicode digits;
the start-up check can refuse to start the app.** `"²".isdigit()` is True but `int("²")` raises `ValueError`
(observed). `١٢٣` (Arabic-Indic) is accepted as port 123 (observed). Callers: `doctor._hooks_state()` (→ `--doctor`
traceback) and `app._show_migration_cards()` inside `_build_ui()`; `homa/app.py:192-197` catches, logs
`could not build the overlay`, shuts down and returns 1 — so one odd hook URL in `~/.claude/settings.json` stops
Homa from starting. That file is the user's own (same principals as the token, Review 13), so this is a self-DoS,
not an exposure. Every other hostile variant was inert: empty port, `1\x1b[31m`, `17171@evil` → not parsed, no
card; `{"hooks": []}`, `{"hooks": {"Stop": "nope"}}`, `[1]`, `{not json` → `installed_ports() == set()`,
`mismatch() is None`. The card and doctor text are built from `int`s only (`at port 62480; settings port is 17171`,
`with a token that is not this instance's`); the token is used solely as the `carries_bearer()` comparison operand
and never appears in output. Fix: `head.isascii() and head.isdigit()` (or `try: int(head) except ValueError`), and
wrap the start-up check so a hooks-file oddity logs a warning instead of aborting `_build_ui()`.

**R14-N5 (NOTE, medium, data safety) — `homa/migrate.py:255-270` and `:170-183`: generic folder names are moved
without checking they are Homa's.** `run_once()` renames `~/.assistant` whenever it exists; observed: a folder
holding only `foreign-tool.cfg` (no `token`, no `settings.json`) was moved and became `~/.homa` with a marker and
notice beside the foreign file. `_move_edge_profile()` renames the **whole** `%LOCALAPPDATA%\Assistant`, not
`Assistant\edge-profile`; observed: `Assistant\other-app\data.bin` (no `edge-profile` present) became
`Homa\other-app\data.bin`. Both names are plausible for unrelated software, and the work laptop's contents are
unknown. Nothing is deleted and the move is reversible by hand, but the other program silently loses its folder.
Fix: migrate only when `old` contains `token` or `settings.json` (or passes `paths._known_layout_only`), and move
`Assistant\edge-profile` into `Homa\edge-profile` (or require `edge-profile` to be the only child) instead of the
parent.

**R14-N6 (NOTE, low) — `homa/migrate.py:207,213`: the shortcut match is a substring test and drops extra
arguments.** `"-m assistant" in link.Arguments` also matches `-m assistant.mcp_stdio`; observed: such a `.lnk`
with this install's working directory was rewritten to `-m homa` (a stdio-shim shortcut would become an app
shortcut). `-m assistant --some-flag` was rewritten to plain `-m homa`. The working-directory match is strict and
correctly normalised (`C:\AI PROJECTS\...\` upper-cased with a trailing slash matched; observed). Only
`Assistant.lnk` in the Desktop/Startup known folders (or the `HOMA_MIGRATE_SHORTCUT_DIRS` override) is considered,
and exactly that file is deleted after `Homa.lnk` is saved. Fix: `link.Arguments.split()[:2] == ["-m",
"assistant"]` (or a regex with `(?!\.)`) and carry the remaining arguments over.

**R14-N7 (NOTE, low) — `homa/app.py:286-290` + `homa/migrate.py:231-248`: the notice card is not passed through
`strip_control` or the length caps.** `consume_notice()` type-checks (`dict`, `shown is False`, `str` title and
detail; observed: `[1]`, `{"shown": false, "title": 5, ...}`, `{"shown": "no", ...}` → `None`, and a malformed
notice is still marked shown so it never loops) but does not sanitise. `Card(...)` + `store.upsert()` do not run
`validate_fields()` (that is the API path, `homa/cards/store.py:132`). Observed: a notice title of
`\x1b[31mEvil\x00` + 3,000 characters became a card title of 3,011 characters with ESC and NUL intact. The file
lives in the ACL-restricted home and is written by the app itself (same trust domain as the token);
`update.consume_notice()` → `_show_update_notice()` has had the same shape since M2. Fix: `strip_control()` and
`[:MAX_TITLE]` / `[:MAX_DETAIL]` on both notices in one place (as `claude_sessions._clean` does for hook text).

### Checks that passed (what was looked at, with the observation)

- **Loopback only, not steerable.** The shutdown URL is the literal `f"http://127.0.0.1:{port}/v1/shutdown"`
  (`migrate.py:145`) with `port` an `int` parsed from `assistant.pid` (`:118-123`); `settings.json` is never read
  by the migration, so a hostile settings file cannot aim the request. Hostile pid files were all inert before any
  POST: `abc`, `1`, `-1 -5`, `99999999 70000`, `4294967295 17171`, empty → `_running_old_instance() is None`; only a
  pid that is a live `python*` image **and** owns the recorded port passes (observed with this probe's own pid and
  the sink port).
- **No proxy.** With `HTTP_PROXY=http://127.0.0.1:9` in the environment (`getproxies()` reported it), the POST
  arrived directly at the local server with its Authorization header; `urllib.request.urlopen` with the same
  environment failed with `WinError 10061` (it tried the proxy) and the server saw nothing. `send.py` keeps its own
  opener (`:19`, with `_NoRedirect`); `mcp_stdio.py:55` uses `send._opener`; `update.py:44-51` unchanged;
  `calendar/ics.py:58-60` keeps the default `ProxyHandler()` on purpose, as before.
- **Token never logged.** Across every probe section: 5 `homa.*` log records, 0 containing the token; captured
  stderr: 0. The only migration log line about the old instance is `asking the old instance (pid %d, port %d) to
  shut down` (`:160`); `_report()` texts carry pid and folder paths only.
- **ACL preserved by `os.rename`.** `icacls` on a scratch `.assistant` after `acl.restrict_to_current_user(...,
  inherit=True)`: one ACE `<user>:(OI)(CI)(F)`; after `os.rename` to `.homa`: identical; child `token`:
  `<user>:(I)(F)`. `paths._known_layout_only()` on the migrated folder with `homa.pid`, `assistant.pid`,
  `migrated-from.json`, `migrated-from.json.tmp`, `migration-notice.json` → `True`; `paths.secure_home()` on it
  logged no `not restricting` warning (`_KNOWN_FILES`, `paths.py:42`).
- **TOCTOU / open handle.** With a handle open on `.assistant\logs\app.log`, `run_once()` returned 1 with `Homa
  could not move ... (PermissionError) ... Nothing was changed.`; the old folder listing was unchanged and `.homa`
  did not exist; after closing the handle the retry migrated. A `.homa` appearing between the `new.exists()` check
  (`:259`) and the rename (`:270`) makes `os.rename` raise `FileExistsError`, an `OSError`, which takes the same
  "nothing moved, exit 1" path — by code reading, not observed.
- **Junction / symlink.** A `~/.assistant` junction (`mklink /J`) to a folder elsewhere: `run_once()` renamed the
  junction itself (`.homa` is a reparse point), the target kept its files and received the marker and notice
  through the junction. Same-user folders only; `paths.home()` then resolves to the junction, as any same-user
  pre-created `~/.homa` would (plan R6 note: not a boundary).
- **Both folders exist.** Nothing moved, one notice written once, `--doctor` says `not done: both folders exist` /
  `were not merged` (`tests/test_migrate.py::test_both_folders_without_marker_moves_nothing_and_says_so` and
  `tests/test_doctor_rename.py::test_both_folders_without_marker_shows_the_old_data_folder_line`, both green here).
- **Environment fallback (`paths.env`, `paths.py:17-26`).** The same four names (`HOME`, `TEST_MODE`,
  `STATIC_DIR`, `NOTES_DIR`) that `ASSISTANT_*` already controlled now resolve `HOMA_*` first, then `ASSISTANT_*`
  with one warning per process; no new consumer, no new capability — an environment the attacker controls was
  already the same account. `desktop_snippet()` (`settings_ui.py:107-111`) emits only `HOMA_HOME=<path>`, never the
  token. `home()` order explicit → `~/.homa` → `~/.assistant` → `~/.homa`; `ensure_layout()` never creates a
  competing `.homa` beside an unmigrated `.assistant` (`tests/test_paths_fallback.py`, 12 tests green).
  `HOMA_MIGRATE_SHORTCUT_DIRS` (`migrate.py:21,50-52`) is a new production env knob for tests; it only widens
  *where* the strict `.lnk` match is applied, for the same user — informational, no finding.
- **Deny-list and own-data exclusions (`denylist.matches`).** `...\.homa\token`, `...\.assistant\token`,
  `...\.homa\settings.json`, `...\AppData\Local\Homa\edge-profile\Default\Cookies`,
  `...\AppData\Local\Assistant\edge-profile\x`, `...\.HOMA\index\files.idx`, `D:\backup\.assistant\state.json`,
  `...\.homa` → all `True`; `...\.homarr\x` and `...\homa\x` → `False` (no over-blocking).
  `IndexManager._own_data_rules()` → `['**/.homa/', '**/.assistant/', <resolved home>]`;
  `update.PROTECTED_DIR == ('.homa', '.assistant')` with the case-folded membership test at `update.py:209`.
- **Shim (`assistant/`).** Three files; `__main__.py` and `mcp_stdio.py` print the notice to **stderr** only when
  `sys.stderr is not None`, then delegate to `homa.__main__.main()` / `homa.mcp_stdio.main()`; `__init__.py`
  re-exports `__version__`. No `__path__` aliasing, no change to `sys.path` semantics (`-m` still puts the working
  directory first, as before the rename). `tests/test_legacy_shim.py` (5 tests, including real `pythonw`) green.
- **`send.py`.** Diff against `HEAD:assistant/send.py`: the pid-file read tries `homa.pid` then `assistant.pid`
  (`:38-43`) and message strings; the opener line is byte-identical.
- **Web localStorage fallbacks.** `SplitHandle.jsx:11-12` (`0.15..0.85` else default), `zoom.js:12-13`
  (`ZOOM_STEPS.includes`), `App.jsx:31-32` (`THEME_PREFS.includes` else `null`): the legacy value is validated the
  same way as the new key's value before use.
- **Test isolation.** `conftest.isolated_home_env` fakes `USERPROFILE/HOME/HOMEDRIVE/HOMEPATH`, `HOMA_HOME` and
  (new) `LOCALAPPDATA`, and pops `ASSISTANT_HOME`; `launch_app` does the same per child; `_REAL_USER_ENV`
  (`conftest.py:17`) restores only the four profile variables and only for node/Edge children. `test_migrate.py`
  redirects the shortcut folders via `HOMA_MIGRATE_SHORTCUT_DIRS`, `test_doctor_rename.py` and
  `test_migration_cards.py` build their own fake profiles and `~/.claude/settings.json` under `tmp_path`. No test
  can reach the real `~/.assistant`, `~/.homa`, `%LOCALAPPDATA%` or `~/.claude/settings.json`.
- **Identifier scan.** `git grep -i -I -c --untracked -f <terms> -- . ':!web/node_modules' ':!.venv'` with the
  user's first name and the two employer names → exit 1, **0 matches** across tracked and untracked files.
- **Tests run here:** `.venv\Scripts\python -m pytest tests/test_migrate.py tests/test_paths_fallback.py
  tests/test_legacy_shim.py tests/test_rename_complete.py tests/test_doctor_rename.py -q -rs -k "not
  real_old_instance"` → `48 passed, 1 deselected in 93.47s`.

### Not reviewed (follow-up)

- `tools/driver.py` (port and throwaway `USERPROFILE` changes by dev in parallel) — review with the next delta.
- `tests/test_migrate.py::test_real_old_instance_on_a_scratch_port_is_shut_down_then_migrated` and
  `tests/test_migration_cards.py` start a real overlay; relied on qa's R5b run.
- `install.ps1` / `uninstall.ps1`: read the diff only (legacy-folder fallback for the delete prompt, `Homa.lnk` +
  legacy `Assistant.lnk` removal); the delete prompt is unchanged and still asks first. Not executed.

### Environment note

At 23:32 nothing was listening on `127.0.0.1:17171` (read-only `netstat -ano`), so the live instance named in the
brief was not running during this review. Nothing here addressed that port: every probe ran on free ports under
scratch profiles. Read-only `stat`: the real `~/.assistant/token`, `~/.assistant/settings.json`,
`~/.assistant/assistant.pid` and `~/.claude/settings.json` were last modified on 2026-11-07 (10:46, 14:29, 17:04,
12:32), all before this review; `~/.homa` and `%LOCALAPPDATA%\Homa` do not exist; `%LOCALAPPDATA%\Assistant` still
does. No real shortcut was read or written. `git status` in the worktree is as qa left it.

### Commands run (read-only, or against scratch profiles and throwaway loopback servers under the session scratchpad)

- `git status --short`; `git log --oneline -3`; `git diff --stat HEAD`; `git diff HEAD -- .gitignore uninstall.ps1
  install.ps1 web/src/... tests/conftest.py`; `diff <(git show HEAD:assistant/<f>) homa/<f>` for `hooks_installer.py`,
  `doctor.py`, `send.py`, `security/denylist.py`, `index/manager.py`, `update.py`, `server/static.py`,
  `search/notes_bm25.py`, `app.py`, `__main__.py`, `overlay/settings_ui.py`
- `probe_rename.py` (scratchpad): sections A redirect, B proxy, C token/pid-file robustness, D marker parsing,
  E hostile `~/.claude/settings.json`, F `icacls` before/after `os.rename` + `_known_layout_only` + `secure_home`,
  G foreign folders, H junction (`cmd /c mklink /J` in scratch), I open handle, J shortcut match (real `.lnk` via
  `WScript.Shell` in scratch, no window), K deny-list / own-data / protected dirs, L token-in-logs scan
- `probe_notice.py` (scratchpad): notice JSON variants → `consume_notice()` → `Card(...)`
- `pytest` command above; `netstat -ano | grep :17171`; `stat` on the four real files; existence checks on
  `~/.homa`, `%LOCALAPPDATA%\Homa`, `%LOCALAPPDATA%\Assistant`
- Privacy gate on this file after writing: `grep -inE -f <terms> docs/security/2026-11-08-homa-rename-review.md`
  → 0 lines (recorded in the hand-off).

## 2026-11-09 — Re-verification of R14-N1..N7, the `paths.looks_like_ours` gate, and the deferred `tools/driver.py` review

**Reviewer:** `security` verifier (read-only on source; this append is the only write). **Hand-off:** `tech-lead`.
**Scope:** dev's fixes in `homa/migrate.py`, `homa/hooks_installer.py`, `homa/app.py`, `homa/paths.py`; qa's
regression tests (`tests/test_rename_hardening.py` new, `tests/test_migrate.py` appended, `tests/test_paths_fallback.py`
and `tests/test_doctor_rename.py` updated); `tools/driver.py` (diff against HEAD a8e0021). Working tree still
uncommitted on `rename-homa`.

**Method.** Re-ran the original `probe_rename.py` unchanged against the new code (same scratch profiles, same
throwaway loopback redirector/sink), plus a supplement `probe_r14_reverify.py` for the cases the fixes introduced
(edge-profile with a sibling, more shortcut argument shapes, the app's card path via a stub `self` with no UI), and
`probe_driver_env.py` (builds the driver's child environment with the driver's own lines and asks a child `python -c`,
not the app, where Homa would look; calls `cmd_start` only for the two refused cases). All under the session
scratchpad; nothing addressed port 17171 (`netstat -ano | grep -c ":17171 "` → `0`, so the live instance was not
listening during this run either); no window opened; no real home, `~/.claude/settings.json` or shortcut read or
written. Two harness bugs in the original probe (Windows env is case-insensitive, so `os.environ.pop("http_proxy")`
raised `KeyError` after section B had printed; a hash-prefix name collision stopped section D after six markers)
are probe-side only; the cases they cut off are covered by the supplement.

### Verdict: PASS for merge. R14-N1..N7 all RESOLVED. `tools/driver.py`: PASS with three NOTEs (R15-N1..N3).

### Per finding, before → after

- **R14-N1 RESOLVED.** `migrate.py:29-34` defines `_NoRedirect` and builds `_opener` with `ProxyHandler({})` and
  `_NoRedirect`. Before: the 302 from the shutdown route made the sink receive `GET /stolen` with
  `Authorization: Bearer <token>`. After: `redirector saw: [('POST', '/v1/shutdown', 'Bearer SECRET-TOKEN-')]`,
  `sink saw: []`, `bearer forwarded off the original port: False`; the opener's handler list contains `_NoRedirect`
  and no default `HTTPRedirectHandler`. Proxy bypass unchanged (`sink saw direct POST: [('POST', '/v1/shutdown',
  True)]` with `HTTP_PROXY` set). Regression tests: `test_shutdown_request_does_not_follow_a_redirect_or_forward_the_bearer`
  (behavioural, two loopback servers) and the source assertion at `tests/test_migrate.py:293`.
- **R14-N2 RESOLVED.** `migrate.py:155-158` catches `(OSError, ValueError)`. Before: token bytes `ff fe 00` →
  `UnicodeDecodeError` escaped. After: `non-ASCII token: returned quietly; sink saw 0`; CRLF token still refused
  before sending (`sink saw 0`); missing token quiet. Hostile pid files unchanged: only `<own pid> <sink port>`
  passes `_running_old_instance`.
- **R14-N3 RESOLVED.** `migrate.py:100-104`. Before: `[1]`, `"x"`, `null` → `AttributeError`; dict/list values
  interpolated. After: those three → `('done (marker unreadable)', None)`; `{"from": {"a": 1}, "when": [1]}` →
  `done: moved from the old folder on an unknown date`. Residual (informational, not a finding): a *string* `from`
  containing ESC/NUL is printed as-is to `--doctor` stdout; stdout only, same-user file, never a card.
- **R14-N4 RESOLVED.** `hooks_installer.py:252-254` now `head.isascii() and head.isdigit() and 1 <= int(head) <=
  65535`. Before: `²` → `ValueError`; `١٢٣` → port 123. After: `²`, `١٢٣`, `٠`, `0`, `65536`,
  `99999999999999999999` → `installed_ports=set()`, `mismatch=None`, no card; `17171`, `62480`, `017171` still read.
  `app.py:297-302` wraps `mismatch()`/`installed_ports()` in `try/except Exception` → log warning, return: with
  `mismatch` monkeypatched to raise `ValueError`, `App._show_migration_cards(stub)` returned, logged `could not check
  the Claude Code hooks`, upserted no card (so `_build_ui()` no longer aborts on it). The mismatch card text is built
  from `int`s only (`Claude Code hooks point at port 62480; Homa listens on 17171.`).
- **R14-N5 RESOLVED.** `run_once()` and `status()` return early unless `paths.looks_like_ours(old)`;
  `_move_edge_profile()` moves only `Assistant\edge-profile` → `Homa\edge-profile` and removes the parent only if it
  is empty. Before: a `.assistant` holding only `foreign-tool.cfg` became `.homa`; the whole `Local\Assistant` (with
  `other-app\data.bin`) became `Local\Homa`. After: foreign `.assistant` → `run_once -> None`, warning logged, folder
  untouched, `status()` says `not applicable ... left alone`, `paths.home()` → `.homa`; ours +
  `Local\Assistant\{edge-profile, other-app}` → `Homa\edge-profile\Default\Cookies` moved, `Assistant\other-app\data.bin`
  left in place, marker `edge_profile: moved`; ours + only `edge-profile` → parent removed; `Homa\edge-profile`
  already present → both left, nothing overwritten.
- **`paths.looks_like_ours` gate (`paths.py:29-43`): PASS.** `home()` adopts `~/.assistant` only when it has
  `token` or `settings.json`; otherwise it resolves `~/.homa`, so `ensure_layout()` builds a fresh, ACL-restricted
  `.homa` beside the foreign folder instead of writing into it
  (`test_ensure_layout_next_to_a_foreign_dot_assistant_builds_dot_homa_and_leaves_it_alone`). Residual
  (informational): `settings.json` is a generic file name, so a foreign `.assistant` that happens to hold one is
  still treated as ours; `secure_home()` still refuses to re-ACL a folder with non-Homa files, the rename is
  reversible, nothing is deleted. Not a finding.
- **R14-N6 RESOLVED.** `migrate.py:228-236` splits `Arguments` into at most three parts, requires
  `parts[:2] == ["-m", "assistant"]`, then appends the remainder. Observed: `-m assistant` → `-m homa`;
  `-m assistant.mcp_stdio`, `-m assistantx`, `-m homa`, `-c import assistant` → `Assistant.lnk` kept, no `Homa.lnk`;
  `-m assistant --x 1` → `-m homa --x 1`; `-m  assistant<TAB>--y` → `-m homa --y`; leading spaces tolerated. The
  carried-over remainder comes from the user's own shortcut (same trust domain). Working-directory match unchanged
  (upper-cased root with trailing slash still matched).
- **R14-N7 RESOLVED.** `app.py:96-97` `_clean()` = `strip_control(text)[:limit]`, applied to title (`MAX_TITLE`
  120) and detail (`MAX_DETAIL` 500) on both the migration card and the update card. Before: a title of 3,011 chars
  with ESC and NUL intact. After, via the real app methods on a stub: migration `title_len=120 detail_len=500`, no
  Cc/Cf (ESC, NUL, U+202E and CR removed; LF kept by design); update `title_len=120 detail_len=500`, BEL removed.

### `tools/driver.py` (deferred from Review 14): PASS, three NOTEs

What changed and what was checked:
- **Live-port refusal.** `cmd_start` exits before any filesystem write when `--port == 17171` without
  `--allow-live-port`. Observed: default port and `--port 17171` → `SystemExit: driver: port 17171 is the live
  instance's port; pass a free --port, or --allow-live-port ...`; scratch `settings.json` not created. The existing
  e2e caller (`tests/e2e/test_overlay.py`) passes its own free port.
- **Port always written.** `settings.json` in `--home` is read, non-objects/corrupt replaced by `{"schema": 1}`,
  `port` overwritten, other keys kept; the state file records `args.port` instead of re-reading the file.
- **Throwaway profile.** A child built with the driver's env lines resolves `Path.home = <scratch-home>\userprofile`,
  `paths.home = <scratch-home>`, `hooks_installer.settings_path() = <scratch-home>\userprofile\.claude\settings.json`,
  `os.path.expanduser('~')` → the fake profile, `migrate.explicit_home() = True` (no migration and no shortcut
  rewrite runs in a driver-started app). `HOMEDRIVE`/`HOMEPATH` are popped; `LOCALAPPDATA`/`APPDATA` point inside the
  fake profile, so the app's Edge profile (`overlay/fullpage.py:47`) lands there too. The real
  `~/.claude/settings.json` is out of reach of a driver-started instance.
- **No secrets printed.** `start` prints pid/port/home only.

**R15-N1 (NOTE, low) — `tools/driver.py:96-109`: `--home` is not validated, and the settings write is now
unconditional.** Before this change `settings.json` was written only when missing; now an existing file is rewritten.
`--home %USERPROFILE%\.homa` (or `.assistant`) would change the live instance's port and create `userprofile\`
inside the real home (after which `secure_home()` refuses to re-ACL it as "not Homa's"). Operator error only (the
help text says "never ~/.homa"); by code reading, deliberately not exercised. Fix: refuse a `--home` that resolves
to, or contains, `Path.home() / ".homa"` or `Path.home() / ".assistant"`.

**R15-N2 (NOTE, low) — `tools/driver.py:114-120`: legacy `ASSISTANT_*` variables are inherited.** Only
`HOMA_TEST_MODE` is popped; `paths.env()` falls back to `ASSISTANT_TEST_MODE`. Observed: with `ASSISTANT_TEST_MODE=1`
in the parent environment, the child reports `TEST_MODE = 1`, so `/v1/_debug/ui` and `/v1/_debug/monitors` would be
served without `--test-mode` (still bearer-protected, scratch token). Same shape for `STATIC_DIR` and `NOTES_DIR`
under either prefix. Fix: pop `ASSISTANT_TEST_MODE`, `ASSISTANT_HOME`, `ASSISTANT_STATIC_DIR`, `ASSISTANT_NOTES_DIR`
(and the `HOMA_` equivalents not meant to pass through).

**R15-N3 (NOTE, low, pre-existing, unchanged by this diff) — `tools/driver.py:63-71`: `http()` uses the default
`urllib.request.urlopen`, which honours `HTTP_PROXY` for `127.0.0.1`** (Review 14 section B: default urlopen went to
the proxy). With a proxy variable set, the scratch instance's bearer would be sent to the proxy. Scratch token only.
Fix: a `build_opener(ProxyHandler({}))` opener as in `homa/send.py`.

Also noted, not a finding: the live-port guard compares against the constant 17171 only; if the live instance is
ever moved to another port the guard will not know.

### Tests run here

`.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_rename_hardening.py tests/test_migrate.py
tests/test_paths_fallback.py tests/test_doctor_rename.py -k "not real_old_instance"` → `66 passed, 1 deselected in
69.93s`. Full suite not run (per brief). `test_real_old_instance_*` deselected (opens an overlay).

### Commands run (read-only, or against scratch profiles under the session scratchpad)

- `git status --short`; `git log --oneline -2`; `diff <(git show HEAD:assistant/hooks_installer.py)
  homa/hooks_installer.py`; `diff <(git show HEAD:assistant/app.py) homa/app.py`; `git diff HEAD -- tools/driver.py`;
  `cat -n homa/migrate.py homa/paths.py`; greps for `looks_like_ours`, known-folder/registry/`Path.home` lookups,
  `_debug` auth, driver callers
- `probe_rename.py` (unchanged from Review 14), `probe_r14_reverify.py`, `probe_driver_env.py` (scratchpad)
- `netstat -ano | grep -c ":17171 "` → `0`
- Privacy gate on this file after appending: `grep -inE -f <terms>` → recorded in the hand-off.
