# Security review — M0-2 token and auth path

Append-only log (archaeology rule). New reviews go in a new dated section at the end; never
edit or delete an earlier section.

## 2026-11-06 — M0-2: token file, ACL, loopback bind, Host/Origin/bearer middleware

**Reviewer:** `security` verifier (read-only). **Preceded by:** `qa` PASS on M0-2.
**Scope reviewed (all untracked at review time, pre-commit):** `assistant/security/token.py`,
`assistant/server/auth.py`, `assistant/server/app.py`, `assistant/server/routes_health.py`,
`assistant/settings.py`, `assistant/paths.py`, `assistant/doctor.py`, `assistant/app.py`,
`assistant/__main__.py`, `requirements.txt`, `pyproject.toml`, `.gitignore` diff,
`tests/test_health_auth.py`, `tests/conftest.py`.
**Requirements checked against:** PRD SEC-1, SEC-2, SEC-13, AC-MC-9; plan section 9 (M0 row:
"token creation and ACL; loopback bind; Host/Origin check").

### Verdict: PASS — no BLOCK findings. One FIX-BEFORE-M1, seven NOTEs.

git-keeper may commit M0-2. The FIX-BEFORE-M1 item (log line injection via the request path)
is cheap and must land before the M1 hook receiver, which is the first consumer of the auth log.

### Findings, ranked

#### FIX-BEFORE-M1

**F1. Log line injection through the request path.** `assistant/server/auth.py:62`
`log.warning("rejected %s %s with %d (%s)", scope["type"], scope.get("path", ""), ...)`.
Uvicorn percent-decodes the request target into `scope["path"]`, so a request for
`/v1/health%0aINJECTED%20LINE` writes a forged second line into `~/.assistant/logs/app.log`.
Observed on a throwaway instance (ASSISTANT_HOME in temp, port 62359):

```
GET /v1/health%0aINJECTED%20LINE no token  -> 401
app.log:
  '2026-11-06 13:09:32,199 WARNING assistant.server.auth: rejected http /nonexistent with 401 (unauthorized)'
  'INJECTED LINE with 401 (unauthorized)'
```

Attack: any local process (the only kind that can reach the loopback port) can forge entries in
the one audit trail the user will read when a hook misbehaves. Severity: Low (local attacker,
log forging only, no code execution). Fix: log the path with `%r` (repr) or strip control
characters, and cap its length (e.g. 200 chars). The query string is correctly not logged
(verified: a `?token=QUERYLEAK` request left no trace in app.log).

#### NOTE (hardening, tracked)

**N1. `whoami` / `icacls` resolved by CreateProcess search order.** `token.py:14` and `:24`
call `subprocess.run(["whoami", ...])` / `["icacls", ...]` by bare name. On Windows,
CreateProcess searches the application directory and the current working directory before
System32, so a `whoami.exe` or `icacls.exe` planted in the repo folder (the app's cwd via
`run.cmd` and the planned Startup shortcut) would run instead. Exploit value is near zero: the
planter is already the same user and can read the token directly. The list-form subprocess call
is shell-injection-safe; `str(path)` is always an absolute resolved path so icacls cannot read it
as a switch. Fix: use `%SystemRoot%\System32\icacls.exe` explicitly, or replace both calls with
pywin32 (`win32security.GetTokenInformation(..., TokenUser)` plus `SetNamedSecurityInfo` with a
single-ACE DACL). The pywin32 route also removes two fragilities on the corporate laptop:
AppLocker blocking `icacls`/`whoami`, and localised `whoami` output.

**N2. `~/.assistant/` folder, `settings.json`, `logs/app.log` inherit the parent ACL.**
`paths.py:17-22`, `settings.py:49-55`, `app.py:16-17`. Only `token` carries an explicit ACL.
Observed on the throwaway home (under the user's Temp): `token` -> exactly one ACE
`<user>:(F)`; `settings.json` and `logs\app.log` -> inherited `SYSTEM:(I)(F)`,
`Administrators:(I)(F)`, `OWNER RIGHTS:(I)(F)`. Under the default home (inside the user profile)
this is already user-only in practice. Under a non-profile `ASSISTANT_HOME` (e.g.
`C:\tools\assistant`) the inherited ACL can include `BUILTIN\Users` read. Nothing secret is in
those files today (verified: the token appears in neither app.log nor the console). SEC-12 (M2)
will put ICS secret URLs into settings.json. Fix for M2-1/M2-2 at the latest: apply
`/inheritance:r /grant:r *SID:(OI)(CI)(F)` to the home folder once in `ensure_layout()` so every
file under it is user-only by inheritance; keep the per-file token ACL as belt-and-braces.
Flagged for the M2 security review.

**N3. icacls failure surfaces as an unhandled traceback.** `token.py:24-25` (`check=True`),
`:40-45`, `app.py:42`. Behaviour is fail-closed and correct: `CalledProcessError` propagates,
the temp file is unlinked, no token file is left behind, the app does not start. Verified
`icacls` returns exit code 3 on failure, so `check=True` is a reliable signal. The exception text
contains the icacls command line (path and SID), not the token. Hardening: catch in
`App.run()` and log one line ("could not secure the token file; refusing to start") so the
corporate-laptop user sees a sentence, not a stack trace.

**N4. SID validation is prefix-only.** `token.py:17` accepts anything starting with `S-1-`.
Not exploitable (the SID comes from a system tool, and the subprocess is list-form), but
`re.fullmatch(r"S-1-[\d-]+", sid)` costs nothing and documents the contract.

**N5. No body-size cap on the server.** `server/app.py:26` uses uvicorn defaults (no request
body limit; h11 header limit 16 KB). Irrelevant for `/v1/health` (never reads a body). M1-4 /
M1-5 (card routes, file drop) must add per-route byte caps and reject oversized bodies before
parsing. Flagged for the M1 security review.

**N6. Browser-side token delivery for the full page and WebSocket.** `AuthMiddleware` wraps
the whole Starlette app including future `Mount`s, so the browser full page cannot fetch its
own static files or open a WebSocket without the token, and browsers cannot set
`Authorization` on a WebSocket. Whatever M3b designs (nonce exchange per plan section 9), do not
put the token in a URL: today nothing logs the query string (`access_log=False`, `_reject`
logs only the path), and that invariant must survive (SEC-2 "never in a URL that is logged").

**N7. Plan vs code: `--doctor` is read-only.** Plan section 10.2 step 3 says `--doctor`
"creates `~/.assistant/` and the token on first run". The code's `doctor.report()` creates
nothing (`paths.home()`, `settings.peek_port()`, `token.present()` are all read-only), and the
test `test_corrupt_settings_falls_back_to_defaults_and_is_left_alone` depends on that. The
read-only doctor is the safer behaviour; the plan or `install.ps1` (run the app once instead)
should be aligned. Per CLAUDE.md, reported rather than silently resolved.

**N8. Dependency pins have no hashes.** `requirements.txt` pins all 29 packages exactly and the
pins match the venv. Adding `--hash` lines (`pip-compile --generate-hashes`) is a cheap
supply-chain hardening for a tool that installs on a corporate laptop. Not required for M0.

### What was checked and found sound (PASS checklist)

- **Token entropy:** `secrets.token_urlsafe(32)` = 256 bits, 43 URL-safe characters
  (observed `token_len=43`); `read()` rejects anything under 32 chars.
- **Creation race:** `os.open(O_CREAT|O_EXCL)` creates an empty file, `icacls` restricts it,
  only then is the token written, then `os.replace` renames it into place. The ACL travels with
  the rename (observed: final `token` has exactly one ACE, no `(I)` flag, matching
  `test_token_file_acl_is_current_user_only`). The pre-ACL window exposes an empty file only.
- **Fail-closed on ACL failure:** see N3.
- **Token leakage:** token not present in `app.log`, not in the console, not in `--doctor`
  output (observed on the probe instance and asserted by three tests). Log messages in
  `token.py` are fixed strings. `ensure()` logs a warning on a malformed file without content.
- **Subprocess injection:** list-form `subprocess.run`, no `shell=True`, arguments are the
  resolved absolute path and a system-sourced SID. See N1 for the resolution-order note.
- **Check order:** Host -> Origin -> bearer. A rebinding page gets 403 with or without the
  token (observed `Host: evil.example`, no token -> 403), so there is no 401/403 oracle.
- **Host allow-list exactness (observed):** `127.0.0.1` 200, `127.0.0.1:<port>` 200,
  `LOCALHOST:<port>` 200 (case-insensitive), `localhost:99999` 200 (port ignored, harmless:
  the socket fixes the port), `[::1]:<port>` 403 (consistent with v4-only bind), `localhost.`
  403, `127.1` 403, `127.0.0.1.evil.example` 403, HTTP/1.0 with no Host 403, duplicate Host
  headers rejected by h11 with 400 before the middleware runs.
- **Origin handling (observed):** `null` 403, `""` 403, `http://localhost.evil.example` 403,
  `http://[::1]:3000` 403, `http://localhost:3000` 200. `http://evil.example@localhost` is
  accepted because `urlsplit().hostname` strips userinfo; browsers never serialise userinfo into
  Origin (RFC 6454), and a non-browser client holds the token anyway, so this is not reachable.
- **Origin absent -> bearer alone:** acceptable under SEC-2. A browser cannot attach
  `Authorization` cross-origin without a CORS preflight, and the server emits no
  `Access-Control-Allow-*` headers (observed response headers: `date`, `server`,
  `content-length`, `content-type` only), so cross-origin JavaScript can neither send the bearer
  nor read any response; form/img requests carry no Authorization and get 401. The Host check
  stops DNS rebinding independently of Origin.
- **Constant-time compare:** `hmac.compare_digest` on UTF-8 bytes; token length is constant,
  so the length short-circuit leaks nothing. Scheme compare is case-insensitive (`bearer` 200);
  trailing garbage 401; empty bearer 401; `Basic <token>` 401 (test).
- **Regeneration takes effect immediately:** `token_provider` is called per request
  (`auth.py:56`), satisfying AC-MC-9's "invalidates the old one".
- **Middleware coverage (observed):** raw ASGI middleware ahead of the router; unknown path
  without token 401 (not 404, so no route enumeration); `OPTIONS` 401; `HEAD` 401;
  `/v1/health/../v1/health` 401; method check (`POST` -> 405) happens only after auth;
  `websocket` scopes are rejected with close code 1008; lifespan passes through.
- **Bind address:** `BIND_HOST = "127.0.0.1"` is a module constant with no setting or env
  override. Observed on a throwaway instance: `Get-NetTCPConnection` LocalAddress `127.0.0.1`
  for the listening port (owning pid is the real interpreter behind the venv launcher);
  connections to `192.168.1.x`, the Tailscale `100.x` address, both IPv6 addresses and `[::1]`
  all failed; `127.0.0.1` connected. Uvicorn's startup line confirms
  `Uvicorn running on http://127.0.0.1:<port>`.
- **Version disclosure:** `server: uvicorn` header without a version; `/v1/health` returns the
  app version only to an authenticated caller; Starlette debug is off (generic 500 bodies).
- **Rate limiting:** none, and none needed for a 256-bit token on loopback (brute force
  infeasible). Rejected requests log one WARNING line each; disk impact is bounded by
  `RotatingFileHandler(maxBytes=2 MiB, backupCount=4)`.
- **settings.py / paths.py:** `ASSISTANT_HOME` is user-controlled environment (equivalent to
  the user); `resolve()` follows junctions by design; empty value falls back to the default.
  Corrupt `settings.json` is left untouched and defaults are used (test). Port validated to
  1024-65535 on load and set.
- **doctor.py:** prints version, home path, Python, dependency versions, wheel import status,
  port state, and `present`/`missing` for the token. No secret is read or printed. Process
  note: the home path contains the Windows username; when work-laptop doctor output is pasted
  into `docs/probes/`, replace it with a placeholder per CLAUDE.md.
- **requirements.txt:** 29 exact pins, matching the installed venv (`pip list` checked for
  starlette, uvicorn, h11, anyio, mcp, pydantic, cryptography, python-multipart, PyJWT, httpx2).
  `h11==0.16.0` is at or above the fix for CVE-2025-43859 (chunked request smuggling);
  `python-multipart==0.0.33` is above the 0.0.18 fix for CVE-2024-53981;
  `starlette==1.10.0` is above the 0.47.2 fix for CVE-2025-54121. `httpx2`/`httpcore2` are
  required by `mcp` (pip show: `Required-by: mcp`), authored by Tom Christie under the pydantic
  GitHub org — a renamed httpx line, not a typosquat. **Unverified:** no live advisory database
  was consulted (`pip-audit` is not installed); advisories after the reviewer's knowledge cutoff
  are not covered.
- **Repo hygiene:** `grep -rniE "<owner-name>|C:\\Users\\[a-z]"` across all untracked files returned
  nothing; no `token` or `.env` file in the pending set; `.gitignore` diff adds
  `*.egg-info/`, `.pytest_cache/` and un-ignores `assistant/static/`.

### Commands run (all read-only or against throwaway instances in the user's Temp)

```
git status --short; git log --oneline -5; git diff --stat; git ls-files --others --exclude-standard
git diff .gitignore
grep -rniE "<owner-name>|C:\\Users\\[a-z]" assistant tests pyproject.toml requirements*.txt run.cmd .gitignore   -> none
.venv\Scripts\python.exe --version                                           -> Python 3.13.1
.venv\Scripts\python.exe -m pip list --format=freeze | grep <server deps>    -> all match requirements.txt
.venv\Scripts\python.exe -m pip show httpx2 httpcore2 mcp                    -> httpx2 Required-by: mcp
.venv\Scripts\python.exe -m pytest tests/test_health_auth.py -q              -> 14 passed in 4.15s
.venv\Scripts\python.exe -c "import secrets; print(len(secrets.token_urlsafe(32)))"   -> 43
C:\Windows\System32\icacls.exe C:\nonexistent\x.tmp /inheritance:r ; echo $?        -> exit=3
whoami /user /fo csv /nh   (in Git Bash resolves to GNU whoami; the app resolves the Windows one)
probe_auth.py  (scratchpad): starts python -m assistant with ASSISTANT_HOME=<temp>, sends the
               edge-case requests listed above via http.client and raw sockets, dumps icacls for
               home/token/settings.json/logs/app.log, greps app.log and console for the token,
               stops the instance with taskkill (no /F).
probe_bind.py  (scratchpad): same start-up, then NETSTAT.EXE -ano -p TCP, Get-NetTCPConnection
               -LocalPort <port>, and connect attempts to every non-loopback address, ::1 and
               127.0.0.1.
```

### Follow-ups by milestone

- **M1 (before M1-4/M1-5 merge):** F1 (path repr in `_reject` log), N5 (body caps on card and
  file-drop routes).
- **M2 (M2-1/M2-2):** N2 (ACL the home folder before credential-like settings land).
- **M3b:** N6 (token never in a URL; nonce exchange design).
- **Any time:** N1 (absolute paths or pywin32 for ACL), N3 (friendly fail-closed message), N4
  (SID regex), N7 (align plan 10.2 with the read-only doctor), N8 (hash-pinned requirements).

## Review 2 — M1-0 hardening (2026-11-06)

**Reviewer:** `security` verifier (read-only). **Preceded by:** `qa` PASS on M1-0 (by observation; the suite
has no 413, ACL-idempotence or N3-simulation tests yet, see R2-12).
**Scope reviewed (uncommitted at review time):** `assistant/security/acl.py` (new),
`assistant/security/token.py`, `assistant/paths.py`, `assistant/server/app.py`, `assistant/server/auth.py`,
`pyproject.toml`, `tests/conftest.py` (qa's `Api` helper). `assistant/app.py` was read for call order but is
unchanged (M1-6's file). The other untracked files in the working tree (`assistant/cards/`,
`assistant/overlay/`, `assistant/reminders/`, new `tests/test_*.py`) belong to M1-1/M1-2/M1-3a/M1-7 and were
**not** reviewed; they must not ride along in the M1-0 commit.
**Requirements checked against:** plan section 14.6 (M1-0 Done cell, A16), PRD SEC-1, SEC-2, AC-MC-9.

### Verdict: PASS — no BLOCK findings. One FIX-BEFORE-M1-4, two FIX-BEFORE-M1-5, nine NOTEs.

git-keeper may commit M1-0 (the seven in-scope files only). The cap, the ACLs and the log fix all hold under
the attacks below; the fix-before items are contract and blast-radius issues, not exploits.

**Status of the Review 1 items:** F1 closed. N1 closed (no subprocess left in `token.py`; pywin32 calls
only). N2 closed for the home folder, with two conditions (R2-2, R2-3) and one gap (R2-11, same as planned
for M2). N3 closed (one ERROR line, exit 1, no token or `.tmp` left; reason text missing, R2-5). N4 closed
(`re.fullmatch(r"S-1-[\d-]+")`, `acl.py:8,16`). N5 closed server-wide; the per-route caps stay with M1-4
under A16, and R2-1 is the contract M1-4 must respect. N6, N7, N8 unchanged (recorded in plan 14.6).

### Findings, ranked

#### FIX-BEFORE-M1-4

**R2-1. `_BodyTooLarge` is an `Exception`, so route code that catches `Exception` around a body read turns
the 413 into its own error response.** `assistant/server/app.py:19` (`class _BodyTooLarge(Exception)`),
`:46-53`. The streamed-body cap works by raising inside `receive()`, i.e. inside the route's
`await request.json()`. Observed by driving the real `create_app()` in-process with a 1.2 MB streamed body
(no `Content-Length`) against throwaway routes:

```
plain route (await request.body())            -> 413 {"error": "payload too large"}   delivered=1200000
exactly MAX_BODY_BYTES streamed               -> 200 {"got":1048576}
MAX_BODY_BYTES+1 streamed                     -> 413
route with try/except Exception -> 400        -> 400 {"error":"bad json","swallowed":"_BodyTooLarge"}
content-length: 10 + 1.2 MB streamed          -> 413   (lying declared length does not help)
```

Memory stays bounded either way (Starlette had buffered one 600 KB chunk when the signal fired; the second
was counted, not stored), so this is a contract weakness, not a resource exploit. But "bad JSON -> 400" is
exactly the pattern M1-4's card and hook routes will write, and A16 promises 413. Fix (pick one, before
M1-4 merges): (a) `class _BodyTooLarge(BaseException)` with a one-line comment — nothing between the
middleware and the route catches `BaseException` (Starlette's `ExceptionMiddleware` and
`ServerErrorMiddleware` both catch `Exception`; no `BaseHTTPMiddleware`/task group in the stack), and the
middleware's own `except _BodyTooLarge` still receives it; or (b) a written rule for M1-4 that body reads
catch only `json.JSONDecodeError`/`UnicodeDecodeError`/`ValueError`, plus a qa test "a route that catches
`Exception` still yields 413". (a) is the robust one.

#### FIX-BEFORE-M1-5 (`--init` / `install.ps1` is the first time real users point `ASSISTANT_HOME` anywhere)

**R2-2. `ensure_layout()` re-ACLs whatever tree `ASSISTANT_HOME` points at, with propagation, and strips
SYSTEM and Administrators from every unrelated file under it.** `assistant/paths.py:23-25`. The M0-2 code
touched only the `token` file; this version rewrites the DACL of the folder and, through
`SetNamedSecurityInfo`'s inheritance propagation, every descendant's inherited ACEs. Observed on a scratch
tree under `%TEMP%` with foreign content:

```
before: foreign_root\other-tool\data\report.txt  NT AUTHORITY\SYSTEM:(I)(F)  BUILTIN\Administrators:(I)(F)  OWNER RIGHTS:(I)(F)
ASSISTANT_HOME=foreign_root ; paths.ensure_layout()
after : foreign_root\other-tool\data\report.txt  <domain>\<user>:(I)(F)
```

Not a privilege gain (the env var is set by the same user), but a one-line misconfiguration such as
`ASSISTANT_HOME=D:\` or `=%USERPROFILE%` rewrites an entire tree in the background on every first start, locks
out other local accounts on a shared folder, and takes an administrator's take-ownership to undo. Answering
the dispatch question "does SYSTEM/Administrators losing access matter": for the app's own folder, no —
Defender and backup tools read via `SeBackupPrivilege`, Windows Search (SYSTEM) *not* indexing the folder is
desirable, the user is the owner (observed `owner==me: True`) and can always re-grant, and administrators
can take ownership. It matters only when the folder is not ours. Fix: apply the inheriting ACL only when
`root` was created by this call (`mkdir` just made it) or when its entries are a subset of the known layout
(`_SUBDIRS` + `settings.json`, `token`, `token.tmp`, `console.txt`); otherwise log one WARNING naming the
folder and skip — the token keeps its own protected ACL regardless. Refuse outright for drive roots and
`Path.home()` itself.

**R2-3. The "warn and continue" WARNING never reaches `app.log`.** `assistant/paths.py:27-28` emits the
warning, but `assistant/app.py:39-40` calls `paths.ensure_layout()` *before* `_setup_logging()`, so the root
logger has no handlers and Python's last-resort handler prints to stderr only. Observed in-process with the
ACL call patched to raise `OSError(50)`:

```
root handlers before _setup_logging(): []
stderr got: 'could not restrict the home folder to the current user (OSError)\n'
app.log exists: False
```

Under the planned Startup shortcut (`pythonw`, no console) the only evidence that the home folder is
unprotected is lost. **Verdict on warn-and-continue vs fail-closed:** warn-and-continue is the right policy
*today* — the token has its own fail-closed per-file ACL, nothing else under the home folder is secret yet,
and failing closed would turn a folder created by someone else (admin-provisioned `C:\tools\assistant`, a
redirected profile on a non-NTFS share) into a hard refusal for no gain. Two conditions: (1) the warning
must persist (this finding); (2) M2-1/M2-2 re-decide when `settings.json` gains ICS secret URLs — apply
`acl.restrict_to_current_user(settings_file)` on write and fail closed if *that* fails while secret fields
are present (see R2-11). Fix for (1): split the ACL step out of `ensure_layout()` into
`paths.secure_home()` (or make `ensure_layout()` return the failure) and call it from `App.run()` after
`_setup_logging()`. This touches `assistant/app.py` (M1-6's file) and `paths.py` — tech-lead to serialise.

#### NOTE (hardening, tracked)

**R2-4. `ensure()` trusts an existing token file's ACL.** `assistant/security/token.py:59-61` reads the
file without re-applying the ACL. Observed: a pre-existing `token` with an extra explicit `Everyone:(R)` ACE
is accepted and used (`Set calls during ensure(): 0`; `icacls token -> Everyone:(R)  <domain>\<user>:(I)(F)`).
The folder ACL does not fix it (R2-11). Same-user-only scenario (copied/restored token file), so NOTE.
Fix: call `acl.restrict_to_current_user(paths.token_file())` on the existing-file branch (idempotent, one
`GetNamedSecurityInfo` when already correct) and treat failure as `TokenSecurityError`.

**R2-5. The N3 ERROR line discards the cause.** `token.py:21` builds `TokenSecurityError(type(exc).__name__)`
(observed `args: ('OSError',)`), but `token.py:67` logs the fixed string only. The type name is not secret
(pywin32 raises `pywintypes.error`; FAT gives `error(50, ...)`). Fix: `log.error("could not secure the token
file (%s); refusing to start", exc)`. Keeps "no traceback" and gives the corporate-laptop user one word to
search for.

**R2-6. Roughly one in five oversize declared POSTs sees a connection reset instead of the 413.**
`assistant/server/app.py:40-42, 68-74`. The 413 carries `connection: close` and the server closes the socket
while the client is still sending the body; the kernel's RST discards the unread response. Observed against
a throwaway instance with `http.client`: 15 oversize POSTs (1,048,577 and 2,000,000 bytes declared) ->
12 x `413 {"error": "payload too large"}`, 3 x `ConnectionResetError`. The cap holds in every case (the body
is never read, uvicorn's `HIGH_WATER_LIMIT = 65536` pauses the socket), so this is robustness for hook
clients, not security: a `curl` hook will sometimes report "connection reset" rather than 413. Option: for a
declared length up to, say, 8 x `MAX_BODY_BYTES`, drain the body before sending the 413 so the client always
sees it; above that, close as now. Document the behaviour for M1-4 either way.

**R2-7. The 413 path logs nothing.** Auth rejections write one WARNING each (`auth.py:62`); a capped body
writes none, so the audit trail cannot show that a hook was dropped for size. Fix: one
`log.warning("rejected http %r with 413 (payload too large)", scope.get("path", "")[:200])` in `_reject`,
same shape as `auth.py` so the F1 escaping applies.

**R2-8. `--doctor` now requires pywin32 just to import.** `assistant/doctor.py:8` imports `security.token`,
which imports `security.acl` at module level (`token.py:6`), which imports `win32security` (`acl.py:3-6`).
Observed: with `win32security` unavailable, `import assistant.doctor` dies with `ModuleNotFoundError` from
`acl.py:6`, so the doctor's own `pywin32 MISSING` line (`_DEPS`) can never be printed on the laptop it was
written for. Fix: import `acl` function-locally in `token._restrict_to_current_user()`, or have `doctor`
test `paths.token_file().is_file()` instead of importing `token`.

**R2-9. FAT/exFAT or network `ASSISTANT_HOME` — unverified.** Only NTFS volumes exist on this machine
(`Get-Volume`), and creating a FAT volume is a state change this role does not make. Expected behaviour:
`GetNamedSecurityInfo`/`SetNamedSecurityInfo` return `ERROR_NOT_SUPPORTED` (50) on FAT, so
`_already_restricted` returns False, the folder ACL logs the (currently stderr-only, R2-3) warning, and the
token ACL raises -> `TokenSecurityError` -> refuse to start. That is fail-closed and correct; the user would
see only "could not secure the token file" (R2-5). On an SMB share backed by NTFS the SID is stored as-is
and works; on a Samba/NAS share the server may reject the DACL -> same fail-closed path. Confidence: Medium.

**R2-10. WebSocket scopes bypass the body cap by design.** `app.py:31-33` passes non-http scopes through.
Uvicorn's `ws_max_size` default is 16 MiB per message. M3b's live-view socket must set a smaller
`ws_max_size` in `uvicorn.Config` or cap frames in the handler.

**R2-11. Children with explicit ACEs are not corrected by the folder ACL (NTFS semantics, not a bug).**
Observed on a scratch tree: a child with explicit `Everyone:(R)` kept it after the parent was re-ACLed;
only inherited ACEs were replaced (`Everyone:(R)  <domain>\<user>:(I)(F)`). Consequence for M2: when
`settings.json` gains secret URLs, apply the per-file ACL on write (as Review 1 N2 already said) rather
than relying on inheritance alone. Also relevant to R2-4.

**R2-12. No regression tests for the hardening.** `grep -rn "413\|_BodyTooLarge\|restrict_to_current_user\|acl\." tests/`
returns nothing; `tests/test_health_auth.py` still has the 14 M0-2 tests (`test_token_file_acl_is_current_user_only`
covers the token file only). qa verified M1-0 by observation, which satisfies the plan's Done cell, but F1,
N3-simulation, home-folder ACL idempotence and the 413 boundary are one bad merge away from silent
regression. Recommend qa (M1-7 owns `tests/`) adds them; the in-process driver used for R2-1 needs no
server process and runs in well under a second.

### What was checked and found sound (PASS checklist)

- **DACL construction** (`acl.py:38-46`): one `ACCESS_ALLOWED_ACE_TYPE` ACE (type 0), mask `0x1F01FF`
  (`FILE_ALL_ACCESS`), flags `OBJECT_INHERIT|CONTAINER_INHERIT` (3) on the folder and 0 on the token file,
  `PROTECTED_DACL_SECURITY_INFORMATION` set (observed `protected: True` on both), owner left untouched and
  equal to the current user (observed `owner==me: True`), SACL and group untouched (`_INFO` is DACL only).
  `icacls` view: home `<domain>\<user>:(OI)(CI)(F)`, token `<domain>\<user>:(F)`, `settings.json` and
  `logs\app.log` `(I)(F)`, sub-folders `(I)(OI)(CI)(F)` — matching the plan's Done cell exactly.
- **Idempotence** (`acl.py:21-32`): four calls (2 x file, 2 x folder) produced exactly 2
  `SetNamedSecurityInfo` calls (counted by wrapping the function in-process). A single ACE with the right SID
  but `(M)` instead of `(F)`, or the right ACE with inheritance re-enabled (`/inheritance:e`), is rejected
  and rewritten. An ACE with `(I)`, `INHERIT_ONLY`, `NO_PROPAGATE`, a deny type, or a different SID fails the
  exact-equality checks. It cannot be "fooled" into a True without the DACL actually being the intended one.
  (Side note: `icacls` silently drops `(OI)(CI)` on a plain file, so the file test with those flags was a
  no-op, not a miss.)
- **SID source and validation**: process token `TokenUser`, `ConvertSidToStringSid`, `re.fullmatch` — no
  subprocess, no locale dependence, no `CreateProcess` search order (N1, N4 closed). Under `runas`/a
  scheduled task the SID is the running identity's, which is the correct owner for those files.
- **Token TOCTOU**: `ensure_layout()` applies the inheriting ACL before `token.ensure()` runs, so
  `token.tmp` is user-only from `os.open(O_CREAT|O_EXCL)` onwards by inheritance, and the explicit protected
  ACL then replaces it before the token bytes are written. The pre-ACL window from Review 1 (empty file
  with the parent's ACL) now exists only when the folder ACL itself failed (R2-3 path).
- **Fail-closed token path**: `TokenSecurityError` -> one ERROR line -> `SystemExit(1)`; `BaseException`
  cleanup unlinks `.tmp` (`token.py:36-41`). The exit path lives in `token.ensure()`, not `assistant/app.py`,
  as plan 14.6 required.
- **Body cap, declared length** (observed on a throwaway instance, token valid unless stated):
  2,000,000 and 1,048,577 bytes -> 413 (or reset, R2-6); exactly 1,048,576 -> passes to the router (405 on
  the GET-only route); without token -> 401 and body never read; with `Host: evil.example` -> 403; with
  `Origin: http://evil.example` -> 403. Order Host -> Origin -> bearer -> cap confirmed.
- **Body cap, malformed framing**: h11 rejects before the ASGI app runs (400, fixed-string
  `Invalid HTTP request received.` WARNING with no request content): `Content-Length: -5`, two differing
  `Content-Length` headers, `+5`, `0x10`, `1e3`. Surrounding whitespace (` 5`, `5 `) is stripped and accepted
  — harmless. `Content-Length` together with `Transfer-Encoding: chunked`: h11 keeps both and frames by
  chunked; the middleware's declared check sees the small value, and the streamed count catches the real
  size the moment a route reads (observed 413 with `content-length: 10` + 1.2 MB streamed). HTTP/2 is not
  served by uvicorn, so not applicable.
- **Body cap, streamed**: boundary exact (1,048,576 -> 200, +1 -> 413, in-process). A route that reads
  part of the body and stops (600 KB of 1.2 MB) gets 200 and the rest is never pulled. A route that never
  reads gets its normal response and the body stays in the socket: uvicorn pauses reading above 64 KB
  (`flow_control.HIGH_WATER_LIMIT = 65536`, `h11_impl.py:266-267`), so nothing accumulates in the app.
- **413 response**: static body `{"error": "payload too large"}`, `content-type`, `content-length`,
  `connection: close`; no path, size or version echoed. Section 8 error shape kept.
- **Log line injection (F1)**: `%0a`, `%0d%0a`, `%E2%80%A8` (U+2028), `%E2%80%AE` (U+202E), `%00` and a
  1000-character path each produced exactly one line, with the path repr'd (`'/v1/health\\nINJECTED LINE'`,
  `'/v1/h\\u2028X'`, `'/v1/h\\x00X'`) and the long path cut to 200 characters (observed 202 including the
  quotes; line length 295). `str.isprintable()` governs `repr` escaping, so every control, format and
  line/paragraph separator code point is escaped.
- **Other log inputs**: query string (`?token=QUERYLEAK&x=%0aINJ`), `Host: HOSTLEAK.example`,
  `Origin: http://ORIGINLEAK.example`, `Authorization: Bearer AUTHLEAK` and the real token: none appear in
  `app.log` or the console (`leaks present: []`). The 403 message is the fixed string "forbidden host", not
  the header value. `app.py:43` logs `paths.home()`, which contains the Windows username — same caveat as
  Review 1 for pasted logs.
- **Dependency surface**: `pywin32==312` pinned in both `requirements.txt` and `pyproject.toml`; the venv
  matches. `win32security`, `win32api`, `win32con`, `ntsecuritycon` are compiled modules from the same wheel
  the plan already requires (P1 gate); no new package. Import now happens at startup for every entry point
  including `--doctor` (R2-8).
- **`pyproject.toml`**: single added line `pythonpath = ["."]` under `[tool.pytest.ini_options]`, as the
  plan specified.
- **Suite**: `.venv\Scripts\python.exe -m pytest -q` from the repo root with no `PYTHONPATH` ->
  `120 passed, 65 skipped in 16.21s`; every skip reads "POST /v1/hooks/claude-code is not served yet
  (M1-4)" — M1-7's pre-written route tests, not M1-0 failures.
- **Repo hygiene**: no username or employer identifier in the seven in-scope files; probes and scratch
  trees lived under the session scratchpad and `%TEMP%` only.

### Commands run (read-only, or against throwaway instances and scratch trees under `%TEMP%`)

```
git status --short; git log --oneline -5; git diff --stat; git ls-files --others --exclude-standard
git diff -- assistant/paths.py assistant/security/token.py assistant/server/auth.py pyproject.toml tests/conftest.py
grep -rn "413\|_BodyTooLarge\|restrict_to_current_user\|acl\." tests/                         -> none
.venv\Scripts\python.exe -m pip show pywin32                                                  -> 312
.venv\Scripts\python.exe -c "import ntsecuritycon, win32security; print(hex(ntsecuritycon.FILE_ALL_ACCESS), ...)"
                                                                                               -> 0x1f01ff, SE_DACL_PROTECTED=4096, OI=1, CI=2
.venv\Scripts\python.exe -c "import sys; sys.modules['win32security']=None; import assistant.doctor"
                                                                                               -> ModuleNotFoundError at acl.py:6
.venv\Scripts\python.exe -m pytest -q                                                         -> 120 passed, 65 skipped
.venv\Scripts\python.exe -m pytest -q -rs | grep -i skip                                      -> all M1-4 route skips
powershell Get-Volume | Select DriveLetter, FileSystem                                        -> NTFS only
sed -n 165,216p .venv/Lib/site-packages/h11/_headers.py ; grep -n HIGH_WATER_LIMIT uvicorn/protocols/http/*.py
grep -n "Invalid HTTP request\|logger\.(warning|error)" uvicorn/protocols/http/h11_impl.py
icacls <scratch file> /inheritance:r /grant:r *<SID>:(OI)(CI)(F) ; icacls <scratch file>       -> flags dropped on a file
probe_m10.py   (scratchpad): starts python -m assistant with ASSISTANT_HOME=%TEMP%\asst_sec_<rand>; sends the
               declared-length, raw-socket (negative/duplicate/+/hex/exp Content-Length, CL+chunked, chunked
               2 MB), Host/Origin-with-body, log-injection and header-leak requests above; dumps icacls and
               win32security descriptors for home/token/settings.json/logs; stops with taskkill (no /F) -> exit 0.
probe_acl.py   (scratchpad): in-process; wraps SetNamedSecurityInfo to count calls; idempotence, mask/flag/
               protection mismatch, explicit-child-ACE survival, foreign-tree blast radius, warning-before-
               logging capture, TokenSecurityError args, ensure() on a pre-existing token with Everyone:(R).
probe_route.py (scratchpad): drives create_app() as a plain ASGI callable with streamed bodies against
               throwaway routes (plain read, except-Exception, partial read, never reads, lying Content-Length).
```

### Follow-ups by milestone

- **M1-4 (before merge):** R2-1 (`_BodyTooLarge` must survive `except Exception`, or the narrow-except rule
  plus a test); R2-7 (log the 413). M1-4 also owns the per-route caps under A16.
- **M1-5 (before `--init`/`install.ps1`):** R2-2 (guard the inheriting ACL to our own folder); R2-3 (persist
  the home-ACL warning — needs `assistant/app.py`, so tech-lead serialises with M1-6).
- **M1-7 (qa):** R2-12 (regression tests for F1, N3 simulation, home ACL idempotence, 413 boundary, R2-1).
- **M2-1/M2-2:** R2-11 (per-file ACL on `settings.json` when secrets land), re-decide warn-and-continue then.
- **M3b:** R2-10 (`ws_max_size`), N6 unchanged.
- **Any time:** R2-4 (re-ACL an existing token on start), R2-5 (reason in the N3 line), R2-6 (drain or
  document the reset), R2-8 (lazy pywin32 import so `--doctor` can report it missing), R2-9 (verify FAT/exFAT
  behaviour on a USB stick when one is at hand).
