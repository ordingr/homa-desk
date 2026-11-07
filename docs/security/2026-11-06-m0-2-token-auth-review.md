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
