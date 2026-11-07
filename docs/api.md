# Assistant local HTTP API (`/v1`), as shipped in M1

- Date: 2026-11-06
- Written by: doc-keeper (TeamAgents), from the M1 code as read on 2026-11-06.
- Sources read: `assistant/app.py`, `assistant/server/{app,auth,routes_cards,routes_reminders,routes_hooks,routes_health}.py`,
  `assistant/cards/{model,store,claude_sessions,filedrop}.py`, `assistant/reminders/{grammar,store}.py`,
  `assistant/hooks_installer.py`, `assistant/send.py`, `assistant/settings.py`, `assistant/paths.py`.
- Compared against: PRD section 8 (`docs/prds/2026-11-06-assistant-v1.md`) and plan M1-9
  (`docs/plans/2026-11-06-assistant-v1-plan.md`). Differences are listed in "Differences from PRD section 8" at the end.
- Verification of the behaviour below: M1 security review, Review 3 and its re-check (`docs/security/2026-11-06-m1-review.md`),
  which observed most of the status codes and caps against a live instance. Anything not in that review is marked "code only".
- Supersedes: nothing. This is the first API reference. Later milestones append to this file.

This file describes what the code does. Where the code and PRD section 8 disagree, the code is described here and the
difference is recorded at the end, so the PRD remains the requirement and this file is the observed state.

---

## 1. Transport, address and authentication

| Item | Value | Source |
|---|---|---|
| Base URL | `http://127.0.0.1:<port>` | `assistant/server/app.py` (`BIND_HOST`) |
| Port | `port` in `~/.assistant/settings.json`, default `17171`, valid 1024 to 65535 | `assistant/settings.py` |
| Bind address | `127.0.0.1` only. Not reachable from other machines. | `assistant/server/app.py` |
| Token | `~/.assistant/token`, at least 32 characters, created on first run | `assistant/security/token.py` |
| Token header | `Authorization: Bearer <token>` on every route, including the hook receiver and `/v1/shutdown` | `assistant/server/auth.py` |
| Token value | Never printed by the app, the CLI or `--doctor`. Do not paste it into docs or logs. | `assistant/send.py`, `assistant/doctor.py` |

Check order on every request, applied before routing:

1. `Host` header must be `127.0.0.1` or `localhost` (a port suffix is allowed). Otherwise `403`.
2. If an `Origin` header is present, its host must be `127.0.0.1` or `localhost`. Otherwise `403`.
3. `Authorization` must be `Bearer <token>` with the exact token (constant-time compare). Otherwise `401`.
4. Body size cap (section 2.3). Otherwise `413`.
5. Route match. An unknown path with a valid token returns `404` as plain text (`Not Found`). A known path with the wrong
   method returns `405` as plain text (`Method Not Allowed`).

Because the token check runs before routing, an unknown path without a token returns `401`, not `404`.

Observed in Review 3 and its re-check: `/v1/shutdown` without a token returns 401, a bad `Host` returns 403, a bad `Origin`
returns 403, a `GET` with a valid token returns 405, and `/v1/hooks/claude-code` returns 401 / 403 / 403 (`Origin: null`) / 200 in the
same order. No CORS headers are sent.

## 2. Request and response conventions

### 2.1 Content

- JSON in, JSON out, UTF-8. `Content-Type` is not checked.
- Strict JSON. `NaN`, `Infinity` and numbers that overflow to infinity are rejected with `400 invalid_json`.
- An empty or whitespace-only body is treated as `{}` on the JSON routes.
- Malformed JSON, invalid UTF-8, and nesting deep enough to hit the recursion limit return `400 invalid_json`. A leading UTF-8 BOM is accepted.

### 2.2 Error shape

Two shapes are in use. Both are JSON except the plain-text 404 and 405 from the router.

**Object shape** (card, reminder and cancel routes; `400 invalid_json`; all 422 validation errors):

```json
{"error": {"code": "invalid_field", "message": "priority must be an integer from 1 to 5.", "field": "priority"}}
```

- `code` is snake_case. Codes in use: `invalid_json`, `invalid_body`, `missing_field`, `invalid_field`, `unknown_action`,
  `didnt_understand`, `too_many_cards`, `not_found`.
- `field` is usually present. Two quirks: validation errors raised by the card model always include `field`, even when it is
  the empty string (`"field": ""`, seen for `invalid_body` and `too_many_cards`), while errors raised by the reminder routes omit
  `field` when it is empty.

**String shape** (produced by middleware, not by the route handlers):

| Status | Body | Produced by |
|---|---|---|
| `401` | `{"error": "unauthorized"}` plus `WWW-Authenticate: Bearer` | `assistant/server/auth.py` |
| `403` | `{"error": "forbidden host"}` or `{"error": "forbidden origin"}` | `assistant/server/auth.py` |
| `413` | `{"error": "payload too large"}`, with `Connection: close` | `assistant/server/app.py`, `routes_cards.py` |
| `503` | `{"error": "ui thread busy"}` (test mode only, section 3.7) | `assistant/app.py` |

The PRD asks for the object shape everywhere. See "Differences from PRD section 8".

### 2.3 Body caps

| Scope | Limit | Response when exceeded |
|---|---|---|
| Whole server, every route | 1 MiB (1,048,576 bytes) | `413` before the route runs |
| `POST /v1/cards`, `PATCH /v1/cards/{id}` | 64 KiB (65,536 bytes) | `413` |
| `POST /v1/cards/{id}/snooze` | 4 KiB (4,096 bytes) | `413` |
| `POST /v1/reminders` | 16 KiB (16,384 bytes) | `413` |
| `POST /v1/hooks/claude-code` | the server cap only (1 MiB) | `413` |
| File drop (`events/*.json`) | 64 KiB (65,536 bytes) | moved to `events/rejected/` (section 6) |

Declared `Content-Length` above the cap is refused without reading the body. Streamed bodies are counted as they arrive.
Observed in Review 3: a 2 MB declared body returned `413` before any route ran; a 1 MiB + 1 body returned `413`.

## 3. Routes

All routes require the bearer token (section 1). Times are seconds since the epoch as floats, matching `time.time()`.

### 3.1 Cards

#### `POST /v1/cards`: create, or update in place

- Body: a card object (section 5). `title` is required.
- `201 Created` with the stored card when a new card is made.
- `200 OK` with the updated card when `id` matches a card that is open (not dismissed). Content fields are replaced, the
  card is re-shown, and `renotify` controls whether its seen state is cleared. `source` is set to `"http"` on every write.
- A dismissed card with the same `id` is treated as new: the old record is dropped and a fresh card is created.
- Errors: `422` for field problems (`field` names the field); `422 too_many_cards` when 200 cards are open and all of them are
  sticky (section 5.4); `413` above 64 KiB; `400 invalid_json`. A non-object body returns `422 invalid_body`.
- Illustrative request and response (values are examples, not captured output):

```
POST /v1/cards
Authorization: Bearer <token>
Content-Type: application/json

{"title": "Build finished", "kind": "done", "detail": "125 tests passing"}
```

```
201 Created
{"id": "<32 hex chars>", "kind": "done", "priority": 2, "sticky": false, "title": "Build finished",
 "detail": "125 tests passing", "url": null, "icon": null, "actions": [], "source": "http",
 "timer_started_at": null, "expires_at": null, "renotify": false, "created_at": <epoch>,
 "seen_at": null, "snoozed_until": null, "dismissed_at": null}
```

#### `GET /v1/cards`: list open cards

- Query: `kind=<kind>` and `source=<source>` filter the list. Neither is validated, so an unknown value returns an empty list.
- Returns open cards, oldest first by `created_at`. This includes snoozed cards and cards queued while muted. Dismissed cards are excluded.
- `200 OK` with a JSON array of card objects.

#### `GET /v1/cards/{card_id}`: one card

- `200 OK` with the card. Dismissed cards are also returned, with `dismissed_at` set, until they fall out of the last 100 dismissed.
- `404 not_found` (`field: "id"`) when the id is unknown or was pruned.

#### `PATCH /v1/cards/{card_id}`: partial update

- Body: any subset of the input fields (section 5.2). Host-set fields are ignored.
- Changing `id` returns `422` (`field: "id"`, "id cannot be changed.").
- A PATCH that omits `renotify` sets it to `false`. A PATCH clears `snoozed_until`, so a snoozed card is shown again at once.
- Dismissed or unknown ids return `404`.
- `200 OK` with the updated card.

#### `POST /v1/cards/{card_id}/snooze`: snooze

- Body: `{"minutes": <number>}`. Accepted range: 1 to 10080 (7 days). Booleans, strings and non-numbers return `422`
  (`field: "minutes"`). A missing `minutes` also returns `422`.
- Hides the card until `now + minutes`. Then it is shown again and blinks as if new. An `update` posted during the snooze shows it at once.
- `200 OK` with the card. `404` for an unknown or dismissed card. `413` above 4 KiB.
- Observed in Review 3 re-check: `1e308` returns `422`; `10080` returns `200`; `NaN`, `Infinity` and `1e999` (raw JSON) return `400 invalid_json`.

#### `DELETE /v1/cards/{card_id}`: dismiss

- `204 No Content` on success. Dismissing an already dismissed card also returns `204`.
- `404` for an id that was never known or was pruned.

#### `GET /v1/cards/{card_id}/events?since=<cursor>`: event log

- `since` is an integer cursor. Default `0`. Returns events with `seq > since`, oldest first. Use the last `seq` you received as the next cursor.
- A non-integer `since` returns `422` (`field: "since"`). An unknown card returns `404`.
- Each event is `{"seq": <int>, "at": <epoch>, "type": "<type>"}`, plus `"action_id": "<id>"` for `pressed`.
- Types recorded: `seen` (hover in the overlay), `snoozed`, `dismissed`, `pressed`.
- A card that fades after 20 s, expires at `expires_at`, or is dismissed by the source is recorded as `dismissed` (code only). There is no separate `expired` type.
- Presses of a card's extra action buttons are recorded when the user clicks them in the overlay. There is no HTTP route that presses a button or marks a card seen.

### 3.2 Reminders

Reminders are stored in `~/.assistant/reminders.json`, survive restarts, and fire as cards (see "How a reminder fires" below).

#### `POST /v1/reminders`: add a reminder

Two body forms. If the body has a `text` key it is parsed with the text grammar and any `title`, `at` or `detail` keys are ignored.

1. Text form: `{"text": "driver selftest in 10m", "sticky": false}`
2. Structured form: `{"title": "...", "detail": "...", "at": <epoch seconds>, "sticky": false}`. `title` is required and is
   stripped. `detail` is optional, up to 500 characters. `at` must be a finite number no more than 366 days ahead.

- `sticky` is optional, a boolean, default `false`. `null` is treated as `false`.
- `201 Created` with `{"id": "<12 hex chars>", "title": "...", "at": <epoch>, "interpretation": "Reminder '...' at HH:MM today"}`.
- Text grammar errors return `422` with `code: "didnt_understand"` and `field: "text"`. The message is
  `didn't understand — try 'text in 10m' or 'text at 3pm'`. The grammar is in the README.
- Text longer than 120 characters after parsing returns `422` (`field: "text"`).
- Structured form: missing title returns `422 missing_field` with `field: "text"` (code uses that field name); an `at` that is not
  a finite number, or is more than 366 days ahead, returns `422` (`field: "at"`); a `detail` longer than 500 characters returns `422` (`field: "detail"`).
- Body must be an object: otherwise `422 invalid_body`. Body cap 16 KiB.

#### `GET /v1/reminders`

- `200 OK` with a JSON array of pending reminders, sorted by `at`: `{"id", "title", "detail", "at", "sticky", "overdue"}`.

#### `DELETE /v1/reminders/{reminder_id}`

- `204 No Content` when the reminder was pending. `404 not_found` (`field: "id"`) otherwise.

#### How a reminder fires

- On time, the reminder becomes a card with `id = "reminder-<reminder id>"`, `kind = "reminder"`, `source = "reminders"`,
  `sticky` as given. The scheduler fires it within the timing tolerance in the PRD.
- If the app was not running at the due time, the reminder fires at the next start. Its title gets `" (was due <time>)"` appended and `overdue` is set.
- Reminders due more than 366 days out cannot be created through the API.

### 3.3 Claude Code hook receiver

#### `POST /v1/hooks/claude-code`

Full contract in section 4.

- `200 OK` with body `{}` for any JSON object. The response never contains a decision key. Claude Code's own prompt always proceeds.
- `400 invalid_body` when the body is valid JSON but not an object. `400 invalid_json` when it is not valid JSON.
- The handler's exceptions are logged and still answered with `200 {}`.

### 3.4 Health

#### `GET /v1/health`

- Requires the token.
- `200 OK` with `{"status": "ok", "version": "0.1.0"}` in M1.
- PRD section 8 also lists `uptime_s`, `index` and `plugins`. Those fields are not in M1 (see "Differences").

### 3.5 Shutdown

#### `POST /v1/shutdown`

- Requires the token. Any holder of the token can stop the app; this is by design.
- `202 Accepted` with `{"status": "shutting down"}`. The overlay then exits, the server stops and `assistant.pid` is removed.
  Observed in Review 3: exit code 0 within 15 s, pid file removed.
- `GET` returns `405`. Not in PRD section 8 (see "Differences").
- The CLI `python -m assistant shutdown` calls this route.

### 3.6 Routes not in M1

PRD section 8 lists these for later milestones. They are not registered in M1, so a request returns `404 Not Found` (plain text) once the token check passes:

- `GET /v1/search`, `GET /v1/files/{...}/preview`, `POST /v1/actions/...` (search and previews, M3)

> **Correction, 2026-11-07 (M3a):** `GET /v1/search`, `GET /v1/roots` and the `/v1/saved` routes are registered from M3a. The
> preview and action routes are still not registered (M3b). See section 11.
>
> **Correction, 2026-11-07 (M3b):** the preview, full-page and action routes are registered from M3b. See section 12. The text above is kept as written.
- MCP endpoint (M2)

### 3.7 Test-mode debug route (not a contract)

#### `GET /v1/_debug/ui`

- Registered only when the app is started with the environment variable `ASSISTANT_TEST_MODE=1`. Otherwise it is not registered, and
  a token-authenticated request returns `404`. Without a token it returns `401`. Observed in Review 3.
- When enabled, the app logs `WARNING ASSISTANT_TEST_MODE=1: serving /v1/_debug/ui`.
- Returns a snapshot of overlay state for the driver: `icon`, `cards[]` (id, kind, colour, title, priority, rect, hidden, seen,
  blinking, timer, buttons, window style flags), `menu`, `mute_choices`, `sound_on`, `reminder_input_open` and `strips`.
  Card `detail`, `url` and the token are not included.
- Returns `503 {"error": "ui thread busy"}` if the UI thread does not answer within 3 s.
- Not a stable contract. Do not build on it.

## 4. Claude Code hook receiver contract

### 4.1 What is installed

`python -m assistant hooks install` (or `install.ps1`) adds one `http` handler per event to `~/.claude/settings.json`. Each
entry has this shape (the token is the real value in the file; this document shows a placeholder):

```json
"PermissionRequest": [ { "hooks": [ {
  "type": "http",
  "url": "http://127.0.0.1:<port>/v1/hooks/claude-code",
  "headers": { "Authorization": "Bearer <token>" },
  "onFailure": "continue"
} ] } ]
```

- Installed events (eight): `PermissionRequest`, `Notification`, `Stop`, `StopFailure`, `UserPromptSubmit`, `PostToolUse`,
  `SessionStart`, `SessionEnd`.
- `onFailure: "continue"`: if Assistant is not running, Claude Code carries on.
- The port is written into the URL at install time. Changing `port` in `settings.json` needs `python -m assistant hooks install` again.
- The token is written as plain text into `~/.claude/settings.json`. See the README "Security & privacy".
- `hooks remove` deletes only Assistant's handlers. It restores the pre-install backup when the file has not changed since install.

### 4.2 Processing

- The payload is read with the normal JSON rules (section 2.1). Only `hook_event_name`, `session_id`, `cwd`, `session_title`,
  `tool_name`, `tool_input`, `notification_type`, `message`, `last_assistant_message`, `background_tasks`, `session_crons`
  and the error fields are read. Other fields are ignored.
- Each card is keyed by `session_id`, truncated to 100 characters. A missing id is treated as `unknown-session`.
- Project label: `session_title` if present, otherwise the last path segment of `cwd` (split on `/` or `\`), otherwise `Claude Code`.
  Labels are whitespace-collapsed and capped at 120 characters.
- Card ids: `cc-wait-<session_id>` for the waiting card, `cc-done-<session_id>` for the done card. Source is `"hooks"`.
- Passive: the receiver never changes Claude Code's behaviour and never returns a permission decision.

### 4.3 Event table

| Event | Effect on the session's cards | Card text |
|---|---|---|
| `PermissionRequest` | Upserts the waiting card. Timer starts now. Renotify on. | Title: project label. Detail: `Permission needed: <tool_name or "a tool">` + ` — <summary>` when a summary exists. |
| `Notification` with `permission_prompt` | If a waiting card is open, nothing changes. Otherwise creates the waiting card. | Message from the payload, or the default "Claude needs your permission". |
| `Notification` with `idle_prompt`, `elicitation_dialog`, `agent_needs_input` | Updates the open waiting card's detail. Creates one if none is open, except that `idle_prompt` creates nothing while a done card is open. | Message from payload, or defaults: "Claude is waiting for your input", "Claude is asking you a question", "Claude needs your input". |
| `Notification` with `elicitation_complete`, `elicitation_response`, `quota_auto_resume_fired` | Dismisses the waiting card. | None. |
| `Notification`, any other type | No effect. | None. |
| `Stop` | Dismisses the waiting card. Upserts the done card (renotify on). | See 4.4. |
| `StopFailure` | Upserts the waiting card (timer starts now). | `Claude stopped: <error>`. `<error>` comes from `error`, `error_type` or `error_class`, underscores replaced by spaces; `API error` when none is given. |
| `UserPromptSubmit` | Records the prompt time for elapsed-time text. Dismisses the waiting card. | None. |
| `PostToolUse`, `SessionStart` | Dismisses the waiting card. | None. |
| `SessionEnd` | Forgets the prompt time. Dismisses the waiting card. | None. |
| `PreToolUse` | Dismisses the waiting card if the receiver is ever sent this event. The installer does not register it. | None. |
| Any other event name | If no waiting card is open, creates one with detail `Waiting on <event> (unrecognised event)`. | Not in PRD (see "Differences"). |

Summary text for a permission card: the first non-empty of `command`, `file_path`, `path`, `url`, `pattern`, `query`, `description`,
`prompt` from `tool_input` (300 characters), otherwise the JSON of `tool_input` (300 characters).

### 4.4 Done card text

`Stop` produces a green card whose detail is one or two lines:

- If `background_tasks` or `session_crons` is set: `Paused — background work running`, plus ` (after <elapsed>)` when the prompt time is known.
- Otherwise, if the prompt time is known: `Task completed in <elapsed>`.
- Otherwise, when there is no snippet: `Task finished`.
- Then, if `last_assistant_message` is a string, its first 160 characters, whitespace-collapsed.
- Elapsed format: `42s`, `6m 12s`, `2h 5m`.
- Total detail is capped at 500 characters.

### 4.5 What the receiver shows and stores

Summaries are shown on the card and written to `~/.assistant/state.json`. They are not written to `app.log` (Review 3 observed
a planted `curl -H 'Authorization: Bearer ...'` command appearing on the card and in `state.json`, and not in the log).
Control and format characters are not yet stripped from titles or details (Review 3 N3, still open).

## 5. Card data model

### 5.1 Input fields

| Field | Type | Required | Default | Limits and rules |
|---|---|---|---|---|
| `title` | string | yes | none | Non-empty after trimming. At most 120 characters. |
| `id` | string | no | server-generated 32 hex characters | Non-empty after trimming, at most 200 characters. Sending an open card's id updates it. |
| `kind` | string | no | `custom` | One of `waiting`, `done`, `meeting`, `reminder`, `custom`. `null` means default. |
| `priority` | integer | no | by kind (5.3) | 1 to 5. Booleans are rejected. |
| `sticky` | boolean | no | by kind (5.3) | Overrides the kind default. |
| `detail` | string | no | none | At most 500 characters. Plain text. |
| `url` | string | no | none | At most 2048 characters. Opened in the browser on click only when it starts with `http://` or `https://`, case-insensitive. |
| `icon` | string | no | none | At most 256 characters. Accepted and stored; not rendered in M1. |
| `actions` | list | no | `[]` | At most 3 items. Each item: `id` (non-empty, at most 64 characters, unique within the card), `label` (non-empty, at most 40 characters; drawn truncated to 20), `shortcut` (optional string, at most 16 characters). |
| `timer_started_at` | number | no | none | Epoch seconds. Drives the `waiting m:ss` counter. Not capped (cosmetic, Review 3). |
| `expires_at` | number | no | none | Epoch seconds. The card is dismissed at this time. At most 366 days ahead. |
| `renotify` | boolean | no | `false` | On update, clears the seen state so the card blinks and sounds again. |

Unknown fields are ignored and logged as a warning, once per distinct name, for the first 100 distinct names. After that the
log says that further unknown fields are ignored without logging.

### 5.2 Host-set fields

These are returned in responses and are ignored if sent: `source`, `created_at`, `seen_at`, `snoozed_until`, `dismissed_at`.

- `source`: `"http"` for `POST /v1/cards`, `"file"` for the file drop, `"hooks"` for the Claude Code receiver, `"reminders"` for fired reminders.
- `created_at`, `seen_at`, `snoozed_until`, `dismissed_at`: epoch seconds or `null`.

### 5.3 Kind defaults

| Kind | Priority | Sticky | Colour | Label on card | Lifetime |
|---|---|---|---|---|---|
| `waiting` | 4 | yes | amber `#F5A623` | `!! CLAUDE NEEDS YOU` | stays until dismissed, snoozed or cleared by its source |
| `done` | 2 | no | green `#2E9E5B` | `CLAUDE FINISHED` | fades 20 s after it is first seen |
| `meeting` | 4 | yes | blue `#2F7DD1` | `!! MEETING` | stays until acted on; dismissed at `expires_at` if set |
| `reminder` | 3 | no | purple `#8E5BD6` | `REMINDER` | fades 20 s after it is first seen |
| `custom` | 3 | no | purple `#8E5BD6` | `NOTIFICATION` (mapped in `overlay/cards_ui.py`; the PRD gives no label for `custom`) | fades 20 s after it is first seen |

- Priority 5 cards sort nearest the icon (bottom of the stack). Other cards stack newest at the bottom.
- A card that is not sticky and has never been seen never fades. Fading starts at the first hover.
- Each kind plays its own generated sound once when it appears and repeats every 5 s until it is hovered.

### 5.4 Store limits

- At most 200 open cards. When a new card would exceed this, the oldest non-sticky open card is dismissed and a warning is logged.
  If every open card is sticky, the request is refused with `422 too_many_cards`.
- Updating an existing open card never triggers the cap.
- The last 100 dismissed cards are kept for `GET` and `events`. Older ones are dropped.
- Persisted to `~/.assistant/state.json`, written atomically. Non-finite numbers are never written.
- Mute state is stored with the cards. While muted, incoming cards are stored and counted on the icon but not shown. On unmute they appear as new.

### 5.5 Response fields

`id`, `kind`, `priority` (resolved, never null), `sticky` (resolved, never null), `title`, `detail`, `url`, `icon`, `actions`,
`source`, `timer_started_at`, `expires_at`, `renotify`, `created_at`, `seen_at`, `snoozed_until`, `dismissed_at`.

### 5.6 Snooze and dismiss behaviour

- Snooze buttons on the card: `Snooze 5m` and `Snooze 15m` (5 and 15 minutes). Via HTTP, 1 to 10080 minutes.
- Snoozing hides the card and its icon count drops by one. When the snooze ends the card is shown again and blinks as new.
- Dismiss is permanent for that card id. A later `POST /v1/cards` with the same id creates a new card.

## 6. File drop (`events/`)

- Folder: `~/.assistant/events/`, created on first run. Only top-level `*.json` files are read. The extension is matched case-insensitively.
- Body: one JSON object in the card format of section 5. It is the same body as `POST /v1/cards`. `source` is recorded as `"file"`.
- A file is read only after it has been unchanged for 0.25 s, so a file being written is not read half-finished. The folder is polled every 0.5 s while there is work, and every 1.5 s when idle. At most 100 files per poll, in name order.
- Success: the card is created or updated (by `id`), and the file is deleted.
- Failure: the file is moved to `~/.assistant/events/rejected/`, and `<name>.error.txt` is written beside it with the reason. If a file of the same name is already there, the moved file is renamed `<stem>.<milliseconds><suffix>`.
- Reasons used:
  - `File is larger than 65536 bytes.`
  - `The file is not valid JSON.`
  - the card validation message, plus ` (field: <name>)`, for example `kind must be one of: waiting, done, meeting, reminder, custom. (field: kind)`.
- Skipped without rejection: symbolic links, junctions and directories named `*.json`, and files that do not end in `.json`.
- Known limit (Review 3 N10): hard links to files elsewhere are read. Creating one needs write access to the target, and `events/` is user-only.
- Recommendation for writers (not enforced by the code): write the file somewhere else and move it into `events/`, so the settle window never has to protect a partial file.

Example drop file:

```json
{"id": "ci-build-42", "title": "Build finished", "kind": "done", "detail": "125 tests passing"}
```

## 7. Command-line sender

`python -m assistant send` posts to `POST /v1/cards` on the running app.

| Flag | Maps to |
|---|---|
| `--title <text>` (required) | `title` |
| `--detail <text>` | `detail` |
| `--kind custom\|waiting\|done\|meeting\|reminder` (default `custom`) | `kind` |
| `--priority <1-5>` | `priority` |
| `--sticky` | `sticky: true` |
| `--url <url>` | `url` (the overlay opens it on click only when it starts with `http://` or `https://`) |

- On success prints the card id and exits 0.
- If the app is not running, prints `Assistant is not running on 127.0.0.1:<port>` to stderr and exits 1.
- If the port is held by another process, refuses, prints who holds it, sends nothing, and exits 1.
- A server-side validation error prints `Assistant answered <status>: <message>` and exits 1.
- The CLI has no `--id`, so it cannot update a card in place. Use HTTP or a file drop for that.
- The CLI uses an opener with no proxy and no redirect following, so the token never goes to an environment proxy (Review 3 B1, closed).

`python -m assistant shutdown` calls `POST /v1/shutdown`. It prints `shutdown requested` and exits 0. When nothing is listening it prints the not-running message and exits 0 too.

## 8. Differences from PRD section 8

These are recorded so the trail stays visible. The PRD is not edited.

1. **Health payload.** PRD section 8 lists `{version, uptime_s, index, plugins}`. M1 returns `{status, version}`. The index and plugin fields belong to later milestones.
2. **Error shape.** PRD section 8 requires `{"error": {"code", "message", "field"?}}` on every error. M1 returns string-form errors for `401`, `403`, `413` and `503`, plain text for router `404` and `405`, and `"field": ""` in some object errors.
3. **Hook receiver status codes.** PRD section 8 says `200 {}` always. M1 returns `400` for valid JSON that is not an object and for invalid JSON. The hook still never returns a decision.
4. **Routes not in PRD section 8.** `POST /v1/shutdown` and `GET /v1/_debug/ui` (test mode) exist in M1. The first is needed by `uninstall.ps1`; the second by the test driver.
5. **`source` values.** PRD section 7 defines `http:<client-name>`. M1 records `"http"` with no client name, and fired reminders use `"reminders"`, which the PRD does not list.
6. **Event types.** Expiry and fade are recorded as `dismissed`, so a client cannot tell a fade from a user dismiss.
7. **Undocumented hook behaviour.**
   - Any unrecognised event name creates a waiting card (section 4.3). Any holder of the token can trigger this.
   - `SessionStart`, `elicitation_complete`, `elicitation_response` and `quota_auto_resume_fired` clear the waiting card. The PRD lists only five clearing events.
   - `idle_prompt` creates no card while a done card is open.
   - `PreToolUse` is treated as clearing but is not installed.
8. **Card id collisions.** Hook cards use `cc-wait-<session_id>` and `cc-done-<session_id>`. A client that posts an open card with the same id takes it over, and its `source` becomes `"http"`. Token holder only.
9. **Kind label.** `custom` cards show `NOTIFICATION`, an explicit mapping in `overlay/cards_ui.py`. The PRD does not give a label for `custom`.
10. **PATCH side effects.** PATCH clears `snoozed_until`, and resets `renotify` to `false` unless sent. The PRD does not say.
11. **Undocumented reads.** `GET /v1/cards/{id}` returns dismissed cards for up to 100 dismissed cards of history.
12. **Missing routes for the browser UI.** No HTTP route to press an action button or mark a card seen. The PRD does not require them in section 8.
13. **Missing field name.** A structured reminder without `title` returns `missing_field` with `field: "text"`.
14. **Not in M1.** `GET /v1/search`, `GET /v1/files/{...}/preview`, `POST /v1/actions/...` and the MCP endpoint are not registered (section 3.6).

> **Correction, 2026-11-07 (M3b):** item 14 is out of date. `GET /v1/files/preview`, `POST /v1/fullpage/open`, `POST /v1/fullpage/close`, `POST /v1/actions/{verb}` and `POST /v1/session/exchange` are registered from M3b (section 12). The MCP endpoint has been registered since M2. The item is kept as written.

---

## 9. M2 additions, as shipped

Supersedes: section 2.2 (the string shape of `401`, `403` and `413`, which are now the object shape with the codes in 9.1; read in `assistant/server/auth.py` and `assistant/server/app.py` on 2026-11-06), section 3.4 (the `GET /v1/health` payload `{status, version}`), section 3.6 (the MCP endpoint listed as not in M1) and section 8, items 1 and 14 (health payload and MCP route not registered). The M1 text above is kept as written.

- Date: 2026-11-06. Written by doc-keeper (M2-7) from the M2 code as read on 2026-11-06.
- Sources read: `assistant/server/{mcp,app,auth,routes_health}.py`, `assistant/app.py` (health override and the `/mcp` mount), `assistant/mcp_stdio.py`, `assistant/calendar/{ics,meetings,outlook}.py`, `assistant/settings.py`, `assistant/overlay/settings_ui.py`, `assistant/security/token.py`, `assistant/cards/store.py` (`patch`), `assistant/server/routes_reminders.py` (text grammar errors).
- Verification: the M2 security review, `docs/security/2026-11-06-m2-review.md` (Review 5), observed the MCP routes, status codes, `mcp.log` and the shim against a live instance. Anything not in that review is marked "code only".
- Plan: `docs/plans/2026-11-06-assistant-v1-plan.md`, sections 7.2 and 16.3 to 16.4.

### 9.1 MCP transport, address and authentication

| Item | Value |
|---|---|
| Endpoint | `/mcp` on the running app's loopback port (`127.0.0.1:<port>`). `POST /mcp` and `POST /mcp/` both answer; `/mcp/<anything>` returns 404. |
| Transport | MCP Streamable HTTP in stateless mode with JSON responses. Each POST carries one JSON-RPC message. No session is kept. |
| Auth | The same chain as section 1: Host, then Origin, then the bearer token, then the body cap. |
| Token | `Authorization: Bearer <token>`, the value of the `token` file in the home folder. |

Rejections, produced by the app's middleware before the MCP library runs:

- `Host` is not `127.0.0.1` or `localhost`: `403` with `{"error": {"code": "forbidden_host", "message": ..., "field": null}}`.
- `Origin` is present and is not loopback: `403` with code `forbidden_origin`.
- No token, or a wrong one: `401` with code `unauthorized` and `WWW-Authenticate: Bearer` (observed).
- Body over 1 MiB, declared or streamed: `413` with code `payload_too_large` (observed: a declared 1,048,803-byte body and a chunked 1,114,112-byte body both returned a clean 413).

Observed in Review 5: `POST /mcp` and `POST /mcp/` return `tools/list` with 200 and no redirect; `/mcp?x=1` returns 200; `/mcp/extra` returns 404; a bogus `Mcp-Session-Id` is ignored; `tools/call` works without `initialize`.

**9.1.1 The Accept header.** The library answers `406` with a JSON-RPC error body when the request has no `Accept` header or lists only `text/event-stream`. `application/json` alone and `*/*` both return 200. Claude Code, the stdio shim and the MCP client send `application/json, text/event-stream`.

**9.1.2 Other methods and bodies (observed).** `DELETE` returns 405 ("Session termination not supported"). `PUT` and `OPTIONS` return 405. A `GET` with `Accept: text/event-stream` opens a stream that does not end (R5-N2, open). Bodies: invalid or deeply nested JSON returns 400 (`Parse error: recursion limit exceeded` for nesting); an empty body returns 400; `{}` returns 400 with a validation error; a JSON-RPC batch returns 400 (batching is not supported); a notification (no `id`) returns 202 with an empty body; `Content-Type: text/plain` returns 400. No case returned 500 in the review run.

### 9.2 Tools

Four tools. All are annotated `readOnlyHint: false`, `destructiveHint: false` and `openWorldHint: false`; `dismiss_card` is also `idempotentHint: true`. There is no tool that opens, reveals, renames, deletes, runs or lists anything (AC-MC-7). The read-only file tools of AC-MC-3 belong to M3a and are not in M2.

> **Correction, 2026-11-07 (M3a):** the four read-only file tools are registered from M3a (section 11.5). The four card and
> reminder tools above are unchanged.

| Tool | Arguments | Returns | Notes |
|---|---|---|---|
| `send_card` | `title` (required), `detail`, `kind` (`waiting`, `done`, `meeting`, `reminder`, `custom`), `priority` (1 to 5), `sticky` (boolean), `url`, `actions` (up to 3 objects, each `{"id", "label"[, "shortcut"]}`), `id` | `{"id": "<card id>"}` | Same validation as `POST /v1/cards` (section 5). Passing the id of an open card replaces it. Source is `"mcp"`. A dismissed id creates a new card. |
| `update_card` | `id` (required); any of `title`, `detail`, `kind`, `priority`, `sticky`, `url`, `actions` | `{"id": "<card id>"}` | Only the fields passed change. There is no `renotify` argument, and the seen state is not cleared (code only). |
| `dismiss_card` | `id` (required) | `{"ok": true}` | Dismissing an already dismissed card is not an error. |
| `add_reminder` | `text` (required), `sticky` (boolean, default `false`) | `{"id": "<12 hex>", "interpretation": "<text>"}` | The text form of `POST /v1/reminders` (section 3.2 and the README "Reminders" grammar). |

Tool errors come back as an MCP tool result with `isError: true` and this text:

- Validation: `<field>: <message>`, for example `kind: kind must be one of: waiting, done, meeting, reminder, custom.` and `title: title must be at most 120 characters.` (observed).
- Unknown or dismissed id, in `update_card` and `dismiss_card`: `id: No card with that id.` (observed).
- Reminder text that is not understood: `didn't understand — try 'text in 10m' or 'text at 3pm'`, the same text as the HTTP route.
- Reminder text over the title limit: `title must be at most 120 characters.`
- A wrong argument type is rejected by the MCP library. Its message can echo the value the caller sent (R5-N8). The app's own errors name the field, not the value.

### 9.3 `mcp.log`

Path: `<home>/logs/mcp.log`, by default `~/.assistant/logs/mcp.log`. It has one line for each `tools/list` and each `tools/call`, including calls that fail validation.

Format:

```
<stamp> <method> tool=<name or -> args=[<key>=<shape>,...] result_bytes=<n> <ok|error>
```

- `<stamp>`: local time with its UTC offset, to the second.
- `<method>`: `tools/call` or `tools/list`. The tool is `-` for `tools/list`.
- `<shape>`: `str[<length>]`, `list[<count>]`, `dict[<count>]`, or the Python type name (`int`, `bool`, `float`, `NoneType`). No argument value is written.
- `result_bytes`: the size of the JSON result. `ok`, or `error` when the result is a tool error.
- If the call raised an exception the library did not turn into a result, the line ends with `error=<exception name>` in place of `result_bytes`.

Example, as written by the code and quoted in Review 5:

```
<stamp> tools/call tool=send_card args=[title=str[11],url=str[19],id=str[12]] result_bytes=142 ok
```

Observed in Review 5: none of the planted argument values appeared in the log. Known issue R5-N1: the tool name and argument keys are written without removing newlines, so a token holder can add a forged line. The file rotates at 1 MiB to `mcp.log.1`, which is kept as one backup.

### 9.4 `GET /v1/mcp/log`

- Requires the token. Without it: `401` (observed).
- Query `lines`: a whole number, default 200, clamped to 1 to 1000. A value that is not a whole number falls back to 200.
- Returns `{"lines": ["<line>", ...]}`: the last `lines` lines of the current `mcp.log`, oldest first. It does not read `mcp.log.1`, and it reads at most the last 512 KiB of the file.

### 9.5 The stdio shim, `python -m assistant.mcp_stdio`

Used by Claude Desktop, which starts a local program. The shim is in `assistant/mcp_stdio.py` and uses only the standard library.

- Reads one JSON-RPC message per line from stdin. For each line it POSTs to `http://127.0.0.1:<port>/mcp/` and writes each response line to stdout. A notification writes nothing.
- The token is read from the home folder before each line, so a token change needs no change to the shim's configuration.
- The port is the `port` value in `settings.json`, read without writing anything. Before sending, the shim checks that the listener on that port is the Assistant (the same check as the `send` command). A mismatch sends nothing.
- It uses an opener with no proxy and no redirect following, a 30-second timeout and a 4 MiB response cap. A server-sent-events response is turned into one output line per `data:` line.
- Failures are JSON-RPC errors with code `-32000`, carrying the request id (or `null` for an unparsable line):
  - `Parse error: the line is not valid JSON.`
  - `No usable token at <token path>; run: python -m assistant --init`
  - the listener-mismatch text from the check above
  - `Assistant answered HTTP <code> on /mcp.`
  - `Assistant is not running on 127.0.0.1:<port>.`
- The home folder is set by `ASSISTANT_HOME` when it is set, and otherwise is `~/.assistant`.

### 9.6 `GET /v1/health` (M2 fields)

Requires the token. Example shape (placeholder values):

```json
{"status": "ok", "version": "0.1.0", "uptime_s": 1234, "index": null, "plugins": [],
 "calendars": [{"label": "calendar.example", "kind": "ics", "host": "calendar.example",
                "last_ok": 1793000000.0, "last_error": null, "events": 3}]}
```

- `version`: the package version, `0.1.0`.
- `uptime_s`: whole seconds since the app object was created, not since logon.
- `index`: `null` until M3. `plugins`: `[]` until M5.
- `calendars`: one object per calendar source in use.
  - `label`: for an ICS source, the host; for Outlook, `Outlook calendar (this PC)`.
  - `kind`: `ics` or `outlook`.
  - `host`: the ICS host, or `null` for Outlook.
  - `last_ok`: epoch seconds of the last successful read, or `null`.
  - `last_error`: a short text, or `null` after a successful read. It never contains the address (see 9.7).
  - `events`: the number of events held from the last successful read.

Observed in Review 5: `last_error: "HTTP 404"` with `host: "127.0.0.1"`.

### 9.7 Calendar card fields

Calendars are read by `calendar/meetings.py`. Two card kinds are produced.

**Meeting card** (one per event, after de-duplication):

| Field | Value |
|---|---|
| `id` | `meeting-` followed by 16 hex characters. The hash is of the normalised title and the start minute, so the same event keeps its id across polls. |
| `kind` | `meeting` (blue; kind defaults in 5.3: priority 4, sticky). |
| `source` | `calendar`. |
| `title` | `<subject> starts in <N> minutes`, or `<subject> starts now` when the lead time is 0. The subject is cut so the title fits. |
| `detail` | `HH:MM–HH:MM`, then the location (cut to 80 characters) when there is one, then the join label (`Zoom`, `Teams`, `Meet`, `Webex`, or `Join`) when there is a join link. Joined with ` · `. |
| `url` | The join link, when one was found. It opens on click only when it starts with `http://` or `https://` (section 5.1). |
| `expires_at` | The event end time. The card is dismissed then. |
| `renotify` | Set when a card with the same id is updated with a new end time. |

Creation time: the card appears at the start minus the lead time. A poll that finds the start already inside the lead window shows it at once. Events that have started, all-day events, declined events and cancelled events produce no card. Two events from different sources with the same normalised title and starts within 60 seconds produce one card. The card reads `source: "calendar"`, which is not in the mute list (9.10).

**Stale-feed card** (one per calendar, after one hour without a successful read):

| Field | Value |
|---|---|
| `id` | `calendar-error-` followed by 12 hex characters (a hash of the source label). |
| `kind` | `custom`, shown with the label `NOTIFICATION`. Not sticky: it fades 20 seconds after it is first seen. |
| `source` | `calendar`. |
| `title` | `Calendar not updating: <label>`. |
| `detail` | `Last error: <error>. No meeting cards come from this calendar until it recovers.` |

The card is dismissed when the calendar reads successfully again. The `error` text is one of the values listed under `last_error` above, or the exception class name.

**Error texts** from the calendar readers, all free of the address: `HTTP <code>`, `response is not an iCalendar feed`, `feed too large`, `connection failed`, `<exception class>: <reason>` for a network error, `classic Outlook is not installed`, `Outlook COM failed (<exception class>)`.

**Address handling.** An ICS address must be `http`, `https` or `webcal` with a host; `webcal` is converted to `https`. The source keeps the full address in a private field. Its repr and every log line show `scheme://host/...`. The list in Settings shows `host  (path hidden)`. `file:`, `ftp:` and host-less addresses are refused by the source (observed).

**Time zones.** A `TZID` is resolved in this order: the tzdata zone of the same name, then the Windows zone name in a built-in table of 17 names, then the feed's own `VTIMEZONE`, then UTC for `UTC` and `GMT`. Anything else is read as UTC, with one warning per name in `app.log` (code only; the warning text is the name, which comes from the feed).

**Recurrence.** `FREQ=DAILY` and `FREQ=WEEKLY` are expanded, with `INTERVAL`, `BYDAY`, `COUNT`, `UNTIL` and `EXDATE`. An override with `RECURRENCE-ID` replaces that occurrence. `MONTHLY`, `YEARLY` and other frequencies show only the first occurrence (code only). The window is 24 hours from the read.

**Join links.** Found in this order: the event `URL` property, `LOCATION`, `DESCRIPTION`, `X-GOOGLE-CONFERENCE` and `X-MICROSOFT-SKYPETEAMSMEETINGURL`. The hosts are `zoom.us`, `zoom.com`, `zoomgov.com`, `teams.microsoft.com`, `teams.live.com`, `meet.google.com` and `webex.com`, and their subdomains. The first match wins. R5-F1 applies: the host check accepts an `@` after an allowed host.

### 9.8 `settings.json`

Path: `<home>/settings.json`. Created on the first run with the defaults. Written with two-space indentation and sorted keys. Defaults and ranges, from `assistant/settings.py`:

```json
{
  "schema": 1,
  "port": 17171,
  "hotkey": "",
  "theme": "dark",
  "sound_on": true,
  "cards_every_monitor": true,
  "mute_sources": {"hooks": false, "http": false, "mcp": false, "file": false, "reminders": false},
  "calendars": {"ics_urls": [], "outlook": true, "lead_minutes": 5, "poll_minutes": 5},
  "claude_code": {},
  "mcp": {}
}
```

| Key | Allowed | Applies |
|---|---|---|
| `schema` | `1` | Written; not read for behaviour. |
| `port` | integer, 1024 to 65535 | After restart. |
| `hotkey` | string | Reserved for M3. Not read. |
| `theme` | string | Stored; no effect in M2. |
| `sound_on` | boolean | The General checkbox writes it. Nothing reads it (9.10). |
| `cards_every_monitor` | boolean | At once. |
| `mute_sources.hooks`, `.http`, `.mcp`, `.file`, `.reminders` | boolean each | At once. |
| `calendars.ics_urls` | list of strings, each `http`, `https` or `webcal` with a host | At once; a source change starts a poll. |
| `calendars.outlook` | boolean | At once. |
| `calendars.lead_minutes` | integer, 0 to 120 | At once. |
| `calendars.poll_minutes` | integer, 1 to 60 | At once; the poll timer is replaced. |
| `claude_code` | object | Reserved. No key read. |
| `mcp` | object | Reserved. No key read. |

Behaviour:

- A file change, by hand or by the app, is checked every 5 seconds (`Settings.watch`). Listeners run for each key whose value changed.
- An invalid value for a scalar key is logged as a warning and the default is used. An invalid value inside `calendars` or `mute_sources` resets that whole object to its defaults (R5-F5 covers `calendars`).
- The app's own saves write a temporary file and replace the old one, retrying up to 10 times on a permission error.
- Once `calendars.ics_urls` is not empty, the file is given a single user-only permission entry on each save (R2-11). If that fails, the save is refused and the calendar addresses are not written. Hand edits keep that entry.
- Unknown keys are kept.

### 9.9 Known limits from Review 5

Listed in `docs/security/2026-11-06-m2-review.md`. Fixes are scheduled before M3 unless noted.

- R5-F1: the join-link check accepts a URL of the form `https://<allowed host>@<other host>/...`, and the browser goes to `<other host>`. Fix pending.
- R5-F2: one event that raises an error in the parser (an extreme duration or date) drops every event from that feed. Fix pending.
- R5-F3: `token.regenerate()` can fail while another reader has the token file open. The old token stays valid. Fix pending.
- R5-F4: `hooks remove` can restore a backup that holds Assistant's own entries, after a reinstall. Fix pending.
- R5-F5: classic Outlook is on by default (`calendars.outlook: true`), and an invalid `calendars` value resets the section. Fix pending.
- R5-N1 (mcp.log line injection), R5-N2 (`GET /mcp` streams that do not end), R5-N3 (ICS redirects followed to `http`, `https` and `ftp` targets; the default proxy is used on purpose), R5-N4 (unbounded set of unknown time zone names in the log), R5-N5 (bearer text masked only in tool input), R5-N6 (token copies in the clipboard, shell history and `~/.claude.json`), R5-N7 (no cap on calendar addresses; sources are read one after another), R5-N8 (validation text can echo a value), R5-N9 (no per-file ACL re-applied on external edits), R5-N10 (a python listener with no pid file still receives the token from `send`), R5-N11 (AC-MC-8 viewer missing; Claude Desktop snippet caveat).
- Carried: R2-6, R2-9 (FAT or exFAT home folder), R2-10 (`ws_max_size`, M3b), Review 1 N8 (hash-pinned requirements, M4-6).

### 9.10 Differences from the PRD and the plan (recorded, not fixed)

1. **Mute list.** The per-source mute list is `hooks, http, mcp, file, reminders` (plan 16.3, A18). Calendar cards have `source: "calendar"`, which is not in the list, so calendar cards cannot be muted by source. PRD AC-ST-4 says "any source id".
2. **Outlook default.** PRD AC-MT-2 says the Outlook source "can be enabled". The code enables it by default (`calendars.outlook: true`) (R5-F5).
3. **Lead-time range.** PRD AC-MT-3 says "configurable 0–30". The settings, the window and `meetings.py` allow 0 to 120.
4. **Per-source status line.** PRD AC-MT-1 says a fetch failure is "shown as a status line in Settings". The Calendars page has no status line; the state is in `/v1/health` only. The same clause says each ICS URL has a label. The page has no label field; the label is the host.
5. **Duplicate rule.** PRD AC-MT-5 says "same UID or same subject+start". The code uses the same normalised subject with a start within 60 seconds, and does not compare UIDs across sources.
6. **Declined events.** PRD AC-MT-6. The ICS source decides "declined" from the calendar name (`X-WR-CALNAME`, when it holds an address) or from every attendee having declined. The app gives the source no user address (code only).
7. **Recurrence.** Only daily and weekly ICS rules are expanded (9.7). PRD AC-MT-2 asks for recurring instances in the Outlook source; the Outlook source reads them through `IncludeRecurrences`.
8. **MCP call log.** PRD AC-MC-8 says the log "is viewable from Settings". The Settings window has no viewer. The log is available only through `GET /v1/mcp/log` (R5-N11 (a)).
9. **MCP log content.** PRD AC-MC-8 says "arguments with path only". The code writes argument names and shapes, and no values (9.3). This is stricter than the PRD.
10. **File tools.** AC-MC-3 (read-only file tools) is in M3a. Plan 7.2 lists AC-MC-3 against M2-7, but only AC-MC-6 (the card and reminder tools) is met in M2.
11. **Desktop snippet.** AC-MC-2 asks for the Claude Desktop config. The Settings page writes `"command": "python"`, not a path to `python.exe`. It adds `env.ASSISTANT_HOME` only when that variable is set in the Settings process (R5-N11 (c)).
12. **General page, sound.** The "Play sounds for new cards" checkbox writes `sound_on` in `settings.json`. The sound state the overlay uses is kept in `state_ui.json`, and the menu's **Sound on** / **Sound off** item uses it. Nothing reads `sound_on`, so the checkbox has no effect on sound.
13. **Keys stored but not read.** `theme`, `hotkey`, `claude_code` and `mcp` are stored or shown but not read in M2.
14. **Port and hooks.** Changing `port` in Settings takes effect after restart (AC-ST-3). It does not rewrite the Claude Code hook entries, which carry the port; run **Install hooks** again (section 4.1).
15. **Health payload.** PRD section 8 lists `version, uptime_s, index, plugins`. M2 returns those plus `status` and `calendars`, as the plan's 16.4 expects.

---

## 10. M2-9 corrections (Review 5 fixes), 2026-11-06

Supersedes: section 9.1.2 (other methods on `/mcp`, and the open `GET` stream, R5-N2); section 9.3 (the R5-N1 known issue in `mcp.log`); section 9.7 "Time zones" (the warning count, R5-N4) and "Join links" (the host check, R5-F1); section 9.8 (the `calendars.outlook` default, the `calendars.ics_urls` limits, and the rule that an invalid value resets `calendars`); section 9.9 items R5-F1 to R5-F5, R5-N1, R5-N2, R5-N3, R5-N4, R5-N5, R5-N7 and the snippet part of R5-N11, as far as this section says they are fixed; section 9.10 items 2 (Outlook default) and 11 (Desktop snippet). The M2 text in sections 9.1.2, 9.3, 9.7, 9.8, 9.9 and 9.10 is kept as written.

- Date: 2026-11-06. Written by doc-keeper (M2-9 correction notes).
- Sources: the code as read on 2026-11-06 (`assistant/server/mcp.py`, `assistant/calendar/meetings.py`, `assistant/calendar/ics.py`,
  `assistant/settings.py`, `assistant/security/token.py`, `assistant/hooks_installer.py`, `assistant/cards/claude_sessions.py`,
  `assistant/overlay/settings_ui.py`), and `tests/test_m2_9.py`.
- Verification: `tests/test_m2_9.py`, 47 tests, qa PASS by observation (reported in the tech-lead brief for M2-9). The suite
  total (396 passed) is also as reported in that brief. doc-keeper did not run either. The M2-9 security re-check is not yet
  in `docs/security/`. Until it is, treat the "fixed" statements here as qa-verified only.
- Commit: M2-9 is not committed at the time of writing. The commit hash is a placeholder for git-keeper to fill in.

### 10.1 MCP methods (R5-N2)

- Any method other than `POST` on `/mcp` or `/mcp/` answers `405 Method Not Allowed`, with the header `Allow: POST` and a
  plain-text body. The check is in the ASGI wrapper in `assistant/server/mcp.py` (`__call__`), so it runs after the Host,
  Origin, token and body-size checks of section 9.1.
- The 405 body is plain text, like the router's 405 in section 1. It is not the JSON error object of section 2.2.
- Observed by tests: `GET`, `PUT`, `DELETE` and `PATCH` on both paths return 405 with `Allow: POST`
  (`test_n2_non_post_on_mcp_is_405_allow_post`). `POST` `initialize` still returns 200 on both paths
  (`test_n2_post_initialize_is_still_200`), and a real MCP client still lists `send_card` (`test_n2_real_client_still_lists_tools`).
- Not covered by M2-9: the 406 rule in section 9.1.1 and the body rules in section 9.1.2 for `POST`.

### 10.2 Join links (R5-F1)

- `find_join_url` accepts a candidate only if all of these hold (`_is_join_url` in `assistant/calendar/meetings.py`):
  - the scheme is `https` (case-insensitive);
  - there is no user name and no password in the URL;
  - the port is absent or 443;
  - the host (lower case) equals an allowed domain, or ends with `.` followed by one.
- Allowed domains (`JOIN_DOMAINS`): `zoom.us`, `zoom.com`, `zoomgov.com`, `teams.microsoft.com`, `teams.live.com`,
  `meet.google.com`, `webex.com`.
- **Inconsistency, open:** the M2-9 brief's list does not include `zoom.com`. The code includes it. Security should decide
  whether it stays.
- The text is still scanned in the order of section 9.7, and the first candidate that passes is used. A refused candidate does
  not stop the scan (`test_f1_mixed_text_returns_the_good_link_not_the_first_match`).
- Refused forms, from the tests: `https://zoom.us@<other>/...`, `https://teams.microsoft.com@<other>/...`,
  `https://zoom.us:443.<other>/...`, `http://zoom.us/...`, `https://zoom.us:8443/...`, `https://user:pw@zoom.us/...`,
  `https://evilzoom.us/...`, `https://zoom.us.<other>/...`.
- Accepted forms, from the tests: subdomains such as `us02web.zoom.us`, `teams.microsoft.com`, `meet.google.com`, and
  `zoom.us:443`.
- The 2048-character limit on `url` (section 5.1) still applies.

### 10.3 Calendar feed, one bad event (R5-F2)

- A parse error in one `VEVENT` skips only that event. The feed status is still `last_ok`, with `last_error` null
  (`test_f2_hostile_events_do_not_silence_the_feed_and_source_is_marked_ok`).
- The warning for a skipped event is `skipping a calendar event that could not be read (uid length N, subject length M)`.
  It gives the lengths of the UID and SUMMARY fields, not their text, and it does not give the address (`ics.py`,
  `_describe_event`). One warning is logged per skipped event.
- Clamps (`assistant/calendar/ics.py`):
  - A `DURATION` longer than 366 days is capped at 366 days (`MAX_DURATION`, `_parse_duration`). An oversized duration gives an
    event of 366 days, not an error (`test_f2_parse_level_events_and_clamps`).
  - Event dates are accepted only in the years 1970 to 2100 (`MIN_YEAR`, `MAX_YEAR`). A date outside that range skips the event.
- The per-event isolation is at the parser level (`ics.py`). The feed-level size limit of 8 MB (section 9.7 and the README)
  is unchanged.

### 10.4 Token Regenerate under readers (R5-F3)

- On Windows, the token file is read with `FILE_SHARE_DELETE` (and read and write sharing), so a reader does not block the
  replace in `regenerate()` (`assistant/security/token.py`, `_read_text`). Off Windows, or without `pywin32`, a plain read is
  used.
- `_replace` retries `os.replace` on `PermissionError` for up to 5 seconds (`REPLACE_BUDGET_S`), with a back-off that starts at
  10 ms and doubles to a 200 ms cap. If the budget runs out, the error is raised. The new token is then not in place, and the
  old token stays valid. This last part is from the code, not from a test.
- Observed by tests: 60 regenerations under four polling readers, and under four spinning readers, never fail
  (`test_f3_sixty_regenerations_under_*`). A transient `PermissionError` is retried (`test_f3_replace_retries_a_transient_permission_error`).
  The running app serves the new token on the next request and refuses the old one with 401 (`test_f3_live_app_serves_the_new_token_on_the_next_request`).

### 10.5 `hooks remove` and backups (R5-F4)

- When `hooks remove` restores a backup, it first removes Assistant's own hook entries from the backup bytes
  (`_without_ours`, `assistant/hooks_installer.py`). A restored file therefore has no bearer token and no
  `/v1/hooks/claude-code` URL.
- Observed by test (`test_f4_install_edit_regenerate_remove_leaves_no_bearer`): install, edit the file, regenerate, remove.
  The regenerate takes a new backup of the edited file, which holds the old token, and `remove` restores that backup. The test
  checks that the restored file keeps the user's own keys and hook, and that `installed()` reports `no`.

### 10.6 Classic Outlook and `calendars` validation (R5-F5)

- `calendars.outlook` defaults to `false` (`DEFAULTS` in `assistant/settings.py`). With no settings file, Outlook is off
  (`test_f5_default_outlook_is_off_with_no_settings_file`). This replaces the default `true` in section 9.8 and 9.10 item 2.
- Validation is per key inside `calendars`. A bad `lead_minutes` (for example 999) falls back to its default, and `ics_urls`
  and `outlook` are kept (`test_f5_a_bad_lead_does_not_discard_the_urls_and_outlook_defaults_off`). This replaces the
  "resets the whole section" rule for `calendars` in section 9.8. The rule still applies to `mute_sources`.
- `--doctor` prints `0 ICS feed(s), Outlook off` when the key is absent, and `Outlook on` when it is `true`
  (`test_doctor_says_outlook_off_when_the_key_is_absent`).

### 10.7 Calendar address caps and redirects (R5-N7, R5-N3)

- `calendars.ics_urls` holds at most 20 addresses (`MAX_ICS_URLS`), and each is at most 2048 characters (`MAX_ICS_URL_CHARS`),
  in `assistant/settings.py`.
  - `Settings.set` refuses a list of 21 addresses, or an address longer than 2048 characters, with `ValueError`
    (`test_n7_overlong_url_is_dropped_and_set_rejects_21_and_long`).
  - An edited file is not refused. Addresses that are not strings or are longer than 2048 characters are dropped, and the list
    is trimmed to 20. The log then has one warning that gives the count and no address (`test_n7_external_edit_with_25_urls_keeps_20_and_warns`).
- ICS redirects (`_HttpRedirects`, `assistant/calendar/ics.py`): a redirect to any scheme other than `http` or `https` is not
  followed, and the fetch fails with a source error. At most 3 redirects are followed (`MAX_REDIRECTS`), so a fourth is refused.
  Observed by tests: a redirect to `ftp://` is refused, a four-redirect chain is refused, and a two-redirect chain is followed
  (`test_n3_*`). The system proxy is still used on purpose (`ProxyHandler` in `ics.py`). This replaces the statement in section 9.9
  that redirects are followed to `ftp` targets.

### 10.8 Masking in hook card text (R5-N5)

- The bearer masking of section 4.5 now also applies to the text of `Stop` (the last assistant message), `Notification` (the
  message) and `StopFailure` (the error). It masks `Bearer <token>` text and bare token-like strings, which show as `***`
  (`assistant/cards/claude_sessions.py`, `_mask`). Plain prose is not changed (`test_n5_*`, including a 43-character bare string).
- Section 4.5 is otherwise unchanged.

### 10.9 Claude Desktop snippet (R5-N11, snippet path)

- `desktop_snippet()` writes `"command"` as `sys.executable`. When that is `pythonw.exe` and a `python.exe` sits in the same
  folder, the console `python.exe` is written instead. `env.ASSISTANT_HOME` is added only when the variable is set
  (`assistant/overlay/settings_ui.py`, `_python_command`). Tests: `test_n11_snippet_uses_the_interpreter_path_not_bare_python`,
  `test_n11_pythonw_is_swapped_for_the_console_python`.
- This replaces section 9.10 item 11, which said the page writes `"command": "python"`.
- Not changed by M2-9: the README caveat that `-m assistant.mcp_stdio` needs Python to import the package from the folder
  Claude Desktop starts the program in ("not yet checked on both laptops"). That is a different part of R5-N11, and it stays open.

### 10.10 Log lines and time-zone warnings (R5-N1, R5-N4)

- Tool names and argument keys written to `mcp.log` have control characters removed and line breaks replaced with spaces, so a
  crafted name cannot start a new log line (`test_n1_control_characters_in_tool_name_and_keys_stay_on_one_log_line`). This
  replaces the known issue in section 9.3.
- Unknown `TZID` names are logged at most 50 times. After that, one line says that further unknown calendar time zones are not
  logged (`test_n4_unknown_tzid_warnings_are_capped_with_one_closing_line`). This narrows section 9.7 ("one warning per name").

### 10.11 Still open after M2-9

- The M2-9 security re-check is not yet recorded in `docs/security/`.
- The `zoom.com` question in 10.2.
- R5-N11 (a): the Settings window has no viewer for the MCP call log (section 9.10 item 8). Not changed by M2-9.
- Review 5 items that the M2-9 brief does not name. This section does not restate their status. The review file is the record.
- The work-laptop probes listed in section 9.9 and the README, which M2-9 does not touch.

---

## 11. M3a additions, as shipped (2026-11-07)

Supersedes: section 3.6 (`GET /v1/search` and the saved-search routes listed as not registered), section 8 item 14 (same),
section 9.2 sentence "The read-only file tools of AC-MC-3 belong to M3a and are not in M2", and section 9.6 (`index: null
until M3`). The earlier text is kept as written, with correction notes at the places above.

- Date: 2026-11-07. Written by doc-keeper (M3a-10) from the code as read on 2026-11-07: `assistant/server/routes_search.py`,
  `assistant/server/mcp.py`, `assistant/server/routes_health.py`, `assistant/app.py` (health and route wiring),
  `assistant/index/{query,search,rank,manager,crawler,excludes,store}.py`, `assistant/search/{saved,modes,notes_bm25}.py`,
  `assistant/security/denylist.py`, `assistant/settings.py`.
- Verification: the M3a security review, `docs/security/2026-11-07-m3a-review.md` (Review 7, verdict PASS, zero BLOCK),
  observed routes, refusals and `mcp.log` against throwaway live instances. Anything not in that review is marked "code only".
  The two FIX-BEFORE-M3B items (R7-F1, R7-F2) are recorded as **fixed 2026-11-07 (M3a-11)** per the tech-lead's brief. The
  code paths are cited below. Security has not yet re-checked them in `docs/security/`.
- Query grammar: `docs/search-grammar.md`.

### 11.1 `GET /v1/search`

Requires the token (section 1). Parameters:

| Parameter | Type and default | Rules |
|---|---|---|
| `q` | string, default empty | The Files grammar. 512 characters and 32 tokens, then truncated (section 2 of `docs/search-grammar.md`). |
| `chips` | repeatable, and/or comma-separated | `docs`, `sheets`, `pdf`, `images`, `code`, `folders`. Unknown value: 422, `field: "chips"`. Chips are OR. |
| `modified` | string, default empty | `""`, `today`, `7d`, `30d`, and the aliases `last7`, `last7days`, `7days`, `last30`, `last30days`, `30days`. Unknown: 422, `field: "modified"`. |
| `sort` | string, default `best` | `best`, `name`, `ext`, `date`. Unknown: 422, `field: "sort"`. |
| `limit` | integer, default 50 | Non-integer or negative: 422 `invalid_parameter`, `field: "limit"`. Above 500: clamped to 500, no error. |
| `offset` | integer, default 0 | Non-integer or negative: 422. Above 100,000: clamped to 100,000. Clamps are code only (R7-F2 fix). |

`200 OK`:

```
{"rows": [ <row>, ... ], "total": <int>, "elapsed_ms": <float>, "interpretation": "<text>",
 "built_at": <epoch seconds or 0.0>, "truncated": <bool>, "notes": ["..."]}
```

- `notes` appears only when the query was truncated (512 characters or 32 tokens).
- `truncated: true` means the result is partial: the 100,000-candidate cap for one plain term, the 10,000 newest entries for an
  empty or literal-free query, or the 0.4 s time budget. `total` counts only the candidates examined in those cases.
- Row: `{"id", "name", "path", "parent", "ext", "size", "mtime", "is_dir", "tier", "spans"}`. `ext` is empty for folders.
  `tier` is 0 exact, 1 prefix, 2 word start, 3 substring, 4 path only. `spans` is a list of `[start, end]` character offsets
  into `name` (empty when the name's casefold changes its length). Placeholder (cloud not downloaded) status is **not** in
  the row (see 11.7).
- Errors: 422 `invalid_parameter`; 503 `index_unavailable` before the index is loaded; 401 and 403 per section 1. The error body
  is the object shape: `{"error": {"code", "message", "field"}}`.

Hidden and deny-listed entries are not returned. The one exception is `show_protected_by_name` (section 11.8): when it is `true`,
the names of protected files are returned here, with no content.

### 11.2 `GET /v1/roots`

Returns the index status object (section 11.4) with `roots` filled. Before the first scan completes, `roots` lists each
configured folder with `"state": "pending"`, `"entries": 0`.

Root `state` values, from the crawler: `pending` (configured, not scanned), `walking`, `ok`, `stopped` (shutdown), `missing`
(`folder not found` for a local folder, `folder not reachable` for a network root), `timeout` (`no answer within <N> s`, for a
network root), `error` (exception class name).

### 11.3 Saved-search routes

| Route | Body | Result |
|---|---|---|
| `GET /v1/saved` | none | `200` `{"searches": [ {"name", "query", "chips", "modified", "sort", "mode"} ]}` |
| `POST /v1/saved` | `{"name", "query", "chips": [strings], "modified", "sort", "mode"}` | `201` with the stored item. Same name replaces the old item. |
| `DELETE /v1/saved/{name}` | none (URL-encode the name) | `200` `{"ok": true}`, or `404 not_found` |

- `name` is trimmed and cut to 80 characters. `query` is cut to 500 characters. `mode` defaults to `files`.
- Errors on POST: body over 4 KiB is `413` (`payload_too_large`). A missing `name` or `query` is `422 invalid_parameter`
  ("a saved search needs a name and a query"). Non-list `chips`, or non-string `name`, `query`, `modified`, `sort` or `mode`:
  `422` with the field named. A list or other non-object body: `422 invalid_body`. More than 200 saved searches: `422`.
- Stored in `<home>/saved_searches.json`, per machine. Written atomically.

### 11.4 `GET /v1/health`, the `index` object

In M3a `index` is always an object (no longer `null`). Keys:

| Key | Meaning |
|---|---|
| `entries` | Entries in the current index (persisted or last completed scan). |
| `dirs` | Folder count from the last completed scan, or `null`. |
| `built_at` | Epoch seconds of the completion of the last full scan. This is the freshness shown in the popup. `null` before any scan and after a load that has no stored time. |
| `walking` | `true` while a full scan runs. |
| `rescanning` | Same as `walking`, added by the health route. |
| `roots` | One object per configured root: `path`, `state`, `entries`, `last_walk_s`, `error`, `errors` (count of unreadable entries). |
| `excludes` | Map of exclude rule text to the number of entries it excluded in the last scan. |
| `overflow_count` | Watcher overflows since start. Each one schedules a re-scan. |
| `rewalks_scheduled` | Re-scans requested after overflows. |
| `delta_ops` | Watcher changes applied since the last full scan, not yet folded in. |
| `last_walk_s` | Duration of the last full scan, seconds. |
| `loaded_from_disk` | `true` until the first scan replaces the persisted index. |

`plugins` stays `[]` (unchanged). Numeric values are not recorded in this file. They vary per machine.

### 11.5 MCP file tools

Registered on the same `/mcp` endpoint as the card tools (section 9.2). Each has `readOnlyHint: true`, `destructiveHint:
false`, `openWorldHint: false`. Code: `assistant/server/mcp.py`, `_add_file_tools`. The tools live in `mcp.py`, not in a separate
`mcp_files.py`, as the planner decided in plan 17.4.

| Tool | Arguments | Returns |
|---|---|---|
| `search_files` | `query` (required), `limit` (1 to 500, default 50), `offset` (0 to 100,000, default 0), `sort` (`best`, `name`, `ext`, `date`; default `best`), `filters` (object: `ext` list, `modified_after` ISO date, `folders_only` boolean; any other key is refused), `chips` (list), `modified` (`today`, `7d`, `30d`) | `{"rows": [{"path", "name", "size", "mtime", "is_dir", "placeholder"}], "total", "interpretation", "built_at", "truncated"}` |
| `get_file_info` | `paths` (list, 1 to 20 absolute paths) | `{"files": [ metadata or {"path", "error"} ]}`. Metadata: `path`, `name`, `size`, `mtime`, `is_dir`, `placeholder`. Metadata only, never content. Refusals are reported per path, in the result, not as a tool error. |
| `read_text_preview` | `path` (absolute), `max_bytes` (1 to 65,536, default 16,384), `max_lines` (1 or more, optional) | The text, wrapped (11.6). |
| `list_indexed_roots` | none | `{"roots": [{"path", "state", "entries"}], "built_at", "entries"}` |

`search_files` drops protected names and anything under the Assistant's own folder from its rows. The MCP tools never read
`show_protected_by_name`.

Tool errors come back with `isError: true` and one of these texts:

Argument errors:
- `limit: must be between 1 and 500`
- `offset: must be between 0 and 100000`
- `filters: only ext, modified_after and folders_only are supported`
- `filters.ext: expected a list of extensions`
- `filters.modified_after: expected an ISO date such as 2025-01-31`
- `paths: give between 1 and 20 paths`
- `max_bytes: must be between 1 and 65536`
- `max_lines: must be 1 or more`
- Query-parser messages such as `unknown sort: ...`, `unknown chip: ...`, `unknown date filter: ...`, unprefixed.
- Type errors from the MCP library. Their text can echo the value the caller sent (R7-N7, R5-N8).

Path refusals (`read_text_preview` raises them; `get_file_info` returns them per path). From `_vet` and the preview code:
- `refused: not a usable path`: not a string, empty, or contains a NUL character.
- `unavailable: the file index is not ready yet`: the index has no store yet.
- `refused: give an absolute path`: relative path.
- `refused: the path is outside the indexed roots`: outside every root, after junctions, `..`, trailing dots, case and 8.3 names are resolved. Also for a UNC path when no root is UNC.
- `refused: the Assistant's own data`: the path is inside the Assistant's home folder (`ASSISTANT_HOME`, default `~/.assistant`). Added for R7-F1, fixed 2026-11-07 (M3a-11).
- `refused: protected file (secrets deny-list)`: the path matches the deny-list (section 11.8).
- `refused: the path is not in the index (excluded or not indexed yet)`: the resolved path is not an index entry. Excludes and junctions out of the root end here.
- `refused: .<ext> is a binary file type, only text is previewed`: the extension is in the binary list (for example `exe`, `dll`, `zip`, `png`, `pdf`, `docx`, `xlsx`, `pptx`, `db`, `pyc`).
- `refused: that is a folder, not a file`
- `refused: the file is a cloud placeholder that is not downloaded; it is never fetched`: checked before the file is opened.
- `refused: the file content looks binary, only text is previewed`: a NUL byte, a known binary signature (MZ, ELF, zip, PDF, PNG, OLE, RAR, gzip), or more than 5% control characters in the first 8 KiB.
- `refused: the file contains a private key (secrets deny-list)`: a PEM private-key header in the first 1 MiB.
- `unavailable: the file could not be read`: the stat or open failed.

`read_text_preview` reads at most 1 MiB to check for binary content and private keys, and returns at most `max_bytes`. The
returned text is cut at `max_lines` lines when that is given.

Content wrapper (`read_text_preview` output):

```
<<<UNTRUSTED FILE CONTENT path=<resolved path> bytes=<n>[ truncated=yes]>>>
<text>
<<<END UNTRUSTED FILE CONTENT>>>
```

- Any occurrence of `<<<END UNTRUSTED FILE CONTENT` or `<<<UNTRUSTED FILE CONTENT` in the file is changed by inserting a
  zero-width space, so the file can contain only one real end marker (observed, Review 7).
- The tool description tells the model to treat the text as data and not to follow instructions in it. The wrapper is a
  labelled boundary. It does not filter the content.

### 11.6 `mcp.log` for the file tools

The file tools use the same shape-only lines as the card tools (section 9.3). Argument values, paths and query text are never
written. Example shape: `<stamp> tools/call tool=read_text_preview args=[path=str[71]] result_bytes=182 ok`. Observed in Review
7 across 86 calls: no planted path, token, query text or marker appeared. R6-2 (line breaks in names) is closed in the code
(`_one_line` drops `Zl`/`Zp`).

### 11.7 Index results and the placeholder flag

- `GET /v1/search` rows and the popup do not carry a placeholder flag. Placeholder entries are indexed by name, never opened,
  and shown as ordinary rows. This is short of AC-FI-6, which asks for a "not downloaded" marker.
- `search_files` rows and `get_file_info` carry `placeholder: true|false`.

> **Correction, 2026-11-07 (M3b-0):** `GET /v1/search` rows now carry `placeholder: true|false` as well, and the popup draws a cloud marker on those rows. The bullet above is kept as written. See section 12.8.

### 11.8 `show_protected_by_name` and the deny-list

- Setting: `index.show_protected_by_name` (default `false`), set on the Search settings page. The popup and `GET /v1/search`
  honour it. MCP never does.
- When `true`, protected files are listed by name and path only, in the popup and `GET /v1/search`. Their content is never
  returned, previewed or sent to Claude.
- The deny-list (`assistant/security/denylist.py`):
  - name patterns: `.env*`, `*.pem`, `*.key`, `*.pfx`, `*.p12`, `*.ppk`, `*.kdbx`, `id_rsa*`, `id_ed25519*`, `id_ecdsa*`,
    `id_dsa*`, `*.asc`, `*.p8`, `*.jks`, `.pgpass`, `.htpasswd`, `.git-credentials`, `.netrc`, `.npmrc`, `.pypirc`,
    `kubeconfig`, `*.kubeconfig`, `.claude.json`
  - path tails: `.aws/credentials`, `.docker/config.json`, `.kube/config`, `.claude/settings.json`
  - folders anywhere in a path: `.assistant`, `.ssh`, `.gnupg`, `.azure`, `.config/gh`, browser profile folders (Chrome, Edge,
    Brave, Vivaldi, Opera, Firefox), and password-manager data folders (KeePass, KeePassXC, Bitwarden, 1Password, LastPass,
    Dashlane, Enpass, NordPass, Keeper)
  - content: PEM private-key headers (RSA, OPENSSH, PKCS#8, encrypted, EC, DSA, PGP)
- Notes search (Ask notes) filters these names at query time. The notes index file still holds the text of such notes (R7-N5,
  open).
- R7-F1 fixed 2026-11-07 (M3a-11): `.assistant` and `.claude/settings.json` and `.claude.json` were added to the deny-list, and
  the home folder is excluded from the index (`manager._own_data_rules`), so a root that contains the home folder does not
  expose the token or the ICS addresses.

### 11.9 Differences from the PRD and the plan (recorded, not fixed)

1. AC-FI-1: hidden and system files are **indexed** in this build. The crawler sets a flag on them but no rule excludes them,
   and there is no toggle. PRD says they are excluded by default.
2. AC-FI-4: a full scan is requested at **every** start (`IndexManager.start`), not only when the last scan is older than a
   threshold.
3. AC-FI-4: `pause_on_battery` defers only the daily scan (retried every 15 minutes). Start, manual and overflow scans are not paused.
4. AC-FI-6: no placeholder marker in the HTTP route or the popup (11.7).
5. AC-FI-15: no custom date range in the popup or HTTP (only the MCP lower bound).
6. Plan M3a-6: "Enter opens" a note. The popup's action callback is not wired in `app.py` (`on_action` is `None`), so Enter, Ctrl+Enter,
   Ctrl+M and the folder key do nothing in M3a. Wiring is M3b-5.
7. Full page and F11 are not wired in M3a (M3b). `on_fullpage` is `None`.
8. `hotkey_skip_fullscreen` (AC-SH-8) is read by `app.py` and written by the settings page but has no `DEFAULTS` entry or validation
   (R7-N11).
9. Known extensions are a constant, not a setting (AC-FI-9).
10. Review 7 notes carried open (per the brief): R7-N1 (index file element types not checked on load), R7-N2 (check-then-open
    window in `read_text_preview`), R7-N5 (notes index keeps denied-name passages), R7-N7 and R7-N8 (validation echoes, asyncio
    log noise), R7-N10 (`\\?\` paths refused as UNC). R7-N3, R7-N4, R7-N6 and R7-N9 are carried by the brief, but the code now
    contains what those notes asked for (deny-list DIR_PATTERNS and NAME_PATTERNS, `OSError` catch in `search_files`, route caps,
    the reserved-combo list in `hotkey.py`). Security to confirm which are closed.

> **Correction, 2026-11-07 (M3b):** items 1, 2, 4, 6, 7, 8 and 9 above are closed in M3b (section 12). Item 3 (sort control in the popup) is closed for the popup header, but see 12.9 item 4 for the popup's action feedback. Item 5 (custom date range) is closed for HTTP, MCP and the full page, but the popup has no way to type a range (12.9 item 5). Item 10 is partly closed: R7-N1 is closed by code read in the M3a re-check (`docs/security/2026-11-07-m3a-review.md`, "Re-check after M3a-11/12"), R7-F1 and R7-F2 are closed there too, and R7-N2, R7-N5 and R7-N10 are shipped under plan 18.3 (n) to (p). R7-N7 and R8 items are not closed by this note. The items above are kept as written.

---

## 12. M3b additions, as shipped (2026-11-07)

Supersedes: section 3.6 (the preview and action routes listed as not registered), section 8 item 14 (the same), section 11.5 (`filters.modified_after` as the only date bound, which M3b-0 (d) and (h) changed), section 11.7 (no placeholder flag in the HTTP route and the popup, which M3b-0 (c) changed), section 11.1 (the HTTP `modified` parameter accepts only `today`, `7d` and `30d`, which M3b-0 (d) changed), and section 11.9 items 1, 2, 4 to 9 (closed as the correction at the end of section 11.9 says). The earlier text is kept as written, with correction notes at the places above.

- Date: 2026-11-07. Written by doc-keeper (M3b-8, TeamAgents) from the code as read on 2026-11-07.
- Sources read (read only): `assistant/server/routes_files.py`, `assistant/server/static.py`, `assistant/server/auth.py`, `assistant/preview/extract.py`, `assistant/overlay/fullpage.py`, `assistant/actions/{names,shell}.py`, `assistant/server/routes_actions.py`, `assistant/overlay/search_ui.py`, `assistant/app.py` (wiring), `assistant/settings.py`, `assistant/server/mcp.py` (`_vet`, `read_text_preview`, `search_files`), `assistant/search/modes.py`, `assistant/index/search.py`, `assistant/index/excludes.py`, `assistant/security/denylist.py`, `web/src/{api.js,App.jsx,components/ContextMenu.jsx,preview/*.jsx}`, `web/package.json`, `scripts/build-web.ps1`.
- Verification: doc-keeper ran no tests and did not run the server. Every statement below is traced to the code named. Sections marked "reported" come from the caller's M3b brief and are not in repo files. The M3b security review (M3b-7) is **pending**: nothing in this section is a security verdict.

### 12.1 Routes added in M3b

| Route | Auth | Body | Success | Failure |
|---|---|---|---|---|
| `GET /v1/files/preview` | bearer | none | `200` JSON, or a stream with `206`, `416` | see 12.2 |
| `POST /v1/fullpage/open` | bearer | `{"query": "<text>"}`, 4 KiB cap, query at most 1,000 characters | `200` `{"ok": true, "opened": <bool>}` | `422 invalid_parameter` (`field: "query"`), `413` |
| `POST /v1/fullpage/close` | bearer | ignored | `200` `{"ok": true, "closed": <bool>}` | none listed |
| `POST /v1/actions/{verb}` | bearer | JSON object, 4 KiB cap (12.6) | `200` (12.6) | 12.6 |
| `POST /v1/session/exchange` | none (nonce) | `{"nonce": "<text>"}`, 1 KiB cap | `200` `{"token": "<token>"}` | `403 invalid_nonce`, `413` |
| `GET /app/`, `GET /app/{path}`, `GET /app`, `GET /favicon.ico` (and `HEAD`) | none | none | the static file | `404` text, `307` for `/app` |

Host and Origin checks (section 1) still run on every route, including the exempt ones. The `/v1/fullpage/*` and `/v1/actions/*` routes are registered only in the running app (`app.py` passes an opener and the index). A route answers `404` when the index is not passed to `create_app`.

### 12.2 `GET /v1/files/preview`

Query: `path` (absolute path, required) and `range` (optional, text kinds only; form `start-end`, both ends inclusive, or `start-` for the default window).

Checks, in order (`routes_files._preview_sync`; the first two steps are `mcp._vet`, the same as the MCP file tools):

1. `_vet`: the path must be a string, non-empty, without NUL, absolute, inside an indexed root, not inside the Assistant's own folder, not on the secrets deny-list, and an index entry.
2. `os.lstat` on the resolved path. A folder is refused. A cloud placeholder (`FILE_ATTRIBUTE_OFFLINE`, `FILE_ATTRIBUTE_RECALL_ON_OPEN` or `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS`) is answered before the file is opened, so its content is never read.
3. The size is compared with the cap (12.2.2). Executable and binary extensions are answered as `meta` before the file is opened.
4. The file is opened, then the open handle is checked (`GetFinalPathNameByHandle`, `pathcheck._plain`) against the roots, the deny-list and the Assistant's folder. A failed check is `403 refused` (`R_MOVED`).
5. The kind is chosen by extension (12.2.1) and the answer is built from the requested range only, with a private-key scan over the range plus 64 bytes on each side.

**12.2.1 Kinds.** The `200` JSON body always has `kind`.

| `kind` | Extensions | Fields |
|---|---|---|
| `markdown` | `md`, `markdown` | `text`, `truncated`, `size`, `start`, `end`, `name`, `mtime` |
| `html` | `html`, `htm`, `xhtml` | as `markdown`. The text is returned as JSON text; the server never sends `text/html`. |
| `text` | every other text type, including `txt`, `log`, `py`, `json`, `ipynb` (shown as JSON text) | as `markdown` |
| `table` | `csv`, `tsv` | `rows` (at most 1,000 rows and 50 columns, cells cut at 1,000 characters), `truncated`, `size`, `start`, `end`, `name`, `mtime` |
| `xlsx` | `xlsx`, `xlsm` | `rows` (first sheet, at most 1,000 rows), `sheets` (names), `shown_sheet` (always `0`), `truncated`, `size`, `name`, `mtime` |
| `docx` | `docx` | `text` (cut at 1,000,000 characters), `truncated`, `size`, `name`, `mtime` |
| `pptx` | `pptx` | `text` (slide blocks, at most 200 slides), `slide_count`, `media` (names, at most 200), `truncated`, `size`, `name`, `mtime` |
| `protected` | any name on the deny-list | `reason` (200, not an error) |
| `placeholder` | any cloud placeholder not downloaded | `name`, `size`, `mtime`, `reason` |
| `too_large` | any file over the cap | `name`, `size`, `mtime`, `cap` |
| `meta` | executables, binaries, HEIC, Office parse errors, binary content | `name`, `size`, `mtime`, `reason` |

Streamed (not JSON):

- `image/png`, `image/jpeg`, `image/gif`, `image/webp`, `image/avif`, `image/bmp`, `image/x-icon` (`png jpg jpeg gif webp avif bmp ico`): the file bytes. `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox`.
- `image/svg+xml` (`svg`): the file bytes, at most 8 MiB (`SVG_MAX_BYTES`), with the same CSP. Larger SVG is answered as `too_large` with `cap` 8,388,608.
- `application/pdf` (`pdf`): the file bytes.

`meta` reasons, as coded: `executable file: not previewed` (`exe dll sys msi msp bat cmd ps1 scr com`); `HEIC images are not supported` (`heic`, `heif`); `binary file type: not previewed` (the MCP binary list, for example `doc odt ods odp zip 7z rar tif psd mp3 mp4 db`); `refused: the file content looks binary, only text is previewed` (NUL, a binary signature, or more than 5 % control characters in the first 8 KiB); and the `extract.ExtractError` texts (`not a readable Office file (damaged, encrypted or an old format)`, `the workbook has no worksheets`, `the file unpacks to more than the 50 MB safety limit`, `the archive member is larger than it declares`).

**12.2.2 Size cap.** `preview.max_bytes` (default `50000000`; allowed 1,048,576 to 4,294,967,296; read by `_cap` on every request, so a change applies without a restart). A file larger than the cap is `too_large`. Office files also stop at 50 MiB unpacked (`extract.UNPACK_CAP`).

**12.2.3 `range` parameter (text kinds).**

- Form `start-end`, both inclusive. `start-` (or no parameter) returns the default window: 262,144 bytes for text, markdown and html, and 1,048,576 bytes for tables.
- The window is at most 1,048,576 bytes. A longer request is cut to that.
- `422 invalid_parameter` with `field: "range"` for a malformed value (`range must look like 0-65535.`), a start at or past the end of the file (`range starts past the end of the file.`), or an end before the start (`range ends before it starts.`).
- Answer fields: `start` (first byte returned), `end` (the offset after the last byte returned), `truncated` (`true` when bytes remain). A UTF-8 character split at either edge of the window is not shown; `end` still counts its bytes.

**12.2.4 HTTP `Range` header.**

- For `image/*` and `application/pdf`: `bytes=a-b`, `bytes=a-` and `bytes=-n` give `206` with `Content-Range`. An unsatisfiable range gives `416` with `Content-Range: bytes */<size>` and no body. A malformed or multi-range header is ignored and the whole file is sent with `200`.
- For text kinds (markdown, html, text, table): a satisfiable header gives `206 text/plain; charset=utf-8` with the bytes, at most 4,194,304 bytes, and `Content-Range`. A binary or private-key result is still answered as JSON (`meta` or `protected`).
- For SVG, Office files and `meta`/`protected`/`placeholder`/`too_large`: the header is ignored.

**12.2.5 Headers.** Streams carry `Accept-Ranges: bytes`, `Content-Disposition: inline`, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`. JSON answers carry `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.

**12.2.6 Errors.** Object shape (section 2.2).

| Status and code | When |
|---|---|
| `401 unauthorized` | no bearer (section 1) |
| `403 forbidden_host`, `403 forbidden_origin` | section 1 |
| `422 invalid_parameter` (`field: "path"`) | `refused: not a usable path`; `refused: give an absolute path`; `refused: that is a folder, not a file` |
| `422 invalid_parameter` (`field: "range"`) | the three `range` messages in 12.2.3 |
| `403 refused` (`field: "path"`) | `refused: the path is outside the indexed roots`; `refused: the Assistant's own data`; `refused: the file is no longer where it was checked (outside the indexed roots or protected)` |
| `404 not_found` (`field: "path"`) | `refused: the path is not in the index (excluded or not indexed yet)`; the file cannot be stat'ed or opened (`unavailable: the file could not be read`) |
| `503 index_unavailable` (`field: "path"`) | `unavailable: the file index is not ready yet` |
| `416` (no body) | an unsatisfiable HTTP `Range` (12.2.4) |

The deny-list refusal is **not** an error: it is `200` with `kind: "protected"` and `reason: "refused: protected file (secrets deny-list)"`, or `refused: the file contains a private key (secrets deny-list)` when the content matches. Placeholders are likewise `200` with `kind: "placeholder"`.

**12.2.7 The `hydrate` parameter.** The page sends `hydrate=1` when the user presses **Download and preview** (`web/src/preview/load.js`). The route does not read it. The placeholder is never fetched. See 12.9 item 1.

### 12.3 `POST /v1/fullpage/open` and `POST /v1/fullpage/close`

- Open: `overlay/fullpage.open(query, port, mint_nonce)`. It mints a launch nonce, closes the window opened earlier by Assistant, and starts `msedge.exe --app=http://127.0.0.1:<port>/app/?launch=<nonce>&q=<query>` with `--user-data-dir` set to `%LOCALAPPDATA%\Assistant\edge-profile` (or `<home>\edge-profile` when `LOCALAPPDATA` is unset), `--no-first-run` and `--no-default-browser-check`. It then waits up to 15 seconds for a visible top-level window of class `Chrome_WidgetWin_1` that has a title, and records its handle and process id.
- The Edge paths checked are the two standard install locations under `Program Files (x86)` and `Program Files`. If neither exists, the route answers `opened: false` and `app.log` says `msedge.exe not found; the full page cannot open`.
- If Edge starts but its window is not found in 15 seconds, the route still answers `opened: true`, and `app.log` says the window was not found (Esc in the page cannot close it).
- Close: `overlay/fullpage.close()` posts `WM_CLOSE` to the window Assistant recorded, and nothing else. `closed: false` when there is no such window.

### 12.4 Static app: `/app/`, `/favicon.ico`

- Served from `assistant/static/app/`, or from the folder in `ASSISTANT_STATIC_DIR` when that environment variable is set (dev and qa only; `static.static_dir`).
- Only `GET` and `HEAD` are exempt from the bearer check (`auth.static_path_exempt`). Paths with `..`, a backslash, or a dot-file segment get `404`. A directory, a missing file or a link out of the folder gets `404`. `/app` redirects to `/app/` with `307`.
- Headers on every answer: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`. `index.html` is `no-store`; other files are `no-cache`.
- Every `/v1/*` route still needs the bearer. Static files contain no user data.

### 12.5 Launch nonce: `POST /v1/session/exchange`

- Mint: `auth.mint_nonce()` returns `secrets.token_urlsafe(32)`, valid 30 seconds (`NONCE_TTL_S`), at most 16 outstanding (`NONCE_MAX_OUTSTANDING`). Redeemable once (`consume_nonce` deletes it on a match).
- Exchange: body `{"nonce": "..."}` (at most 1,024 bytes). A missing, used or expired nonce gives `403 invalid_nonce`, message `The launch code is missing, used or expired.`, `field: null`. A valid nonce gives `200 {"token": "<the bearer token>"}` with `Cache-Control: no-store`.
- The page (`web/src/api.js`) keeps the token in a module variable only. It reads `launch` and `q` from the URL once and removes both with `history.replaceState`. A reload loses the token, so the page then shows "Open this page from Assistant".
- The nonce is in the Edge command line for the window's launch (visible to processes that can list the command lines of the same Windows user). It is single use and lasts 30 seconds.

### 12.6 `POST /v1/actions/{verb}`

Verbs (`actions/shell.VERBS`): `open`, `open_with`, `properties`, `reveal`, `copy_path`, `copy_path_quoted`, `rename`, `recycle`, `md_vault`, `folder_listing`, `ask_claude`. An unknown verb gives `404 unknown_action` (`field: "verb"`).

Body (object; at most 4,096 bytes; `routes_actions._body`):

- `path`: non-empty string, required (`422 invalid_parameter`, `field: "path"`).
- `new_name`: string, required for `rename` (`422 invalid_parameter`, `field: "new_name"` when not a string).
- `confirm`: boolean, default `false` (`422 invalid_parameter`, `field: "confirm"` when not a boolean).
- A non-object body: `422 invalid_body`.
- Index not ready: `503 index_unavailable`.

Result shape, `200`: `{"ok": true, "message": "..."}`, plus per verb: `copy_path` and `copy_path_quoted` add `plain` and `quoted`; `rename` adds `path` (the new full path); `folder_listing` adds `folder`, `rows` (up to 500 by default, at most 5,000 when asked), `total`, `truncated` and `hidden_protected` (the count of protected children left out).

Confirmation: a result that needs confirmation is `200` with `"ok": false, "needs_confirmation": true, "message": "..."` and the action has not run. Repeat with `"confirm": true`. This applies to `open` on an executable or script, `recycle`, `md_vault` on an executable with no MD Vault URL, and `ask_claude`. `ask_claude` also returns a `summary` with `path`, `bytes_to_send` (at most 32,768), `file_bytes`, `whole_file` and `what`.

Failure mapping (`routes_actions`):

| Internal kind | HTTP | `error.code` | `field` | Typical message |
|---|---|---|---|---|
| `refused` | `403` | `refused` | null | `refused: the path is outside the indexed roots`; `refused: the Assistant's own data`; `refused: that is an indexed root folder, not a file inside one`; `refused: the path is not in the index (excluded or not indexed yet)`; `Not moved: it is on a network location, so there is no Recycle Bin to hold it. Assistant never deletes permanently.` |
| `invalid` (not `rename`) | `422` | `invalid_parameter` | `path` | `Open with needs a file, not a folder.`; `MD Vault opens files, not folders.` |
| `invalid` (`rename`) | `422` | `invalid_name` | `new_name` | `The name contains a character Windows does not allow: ...` (see the list below) |
| `not_found` | `404` | `not_found` | null | `That file or folder no longer exists.` |
| `error` | `500` | `error` | null | `The action failed: <reason>` |
| `not_implemented` | `501` | `not_implemented` | null | `Sending to Claude is not available in this build; nothing was sent.` (`ask_claude` after confirmation) |

Name rules (`actions/names.validate`), each with its own message: empty; only spaces; longer than 255 characters; a control character (code below 32, or 127); any of `< > : " / \ | ? *`; ends with a space or a period; a reserved device name (`CON PRN AUX NUL CONIN$ CONOUT$ COM1`–`COM9 LPT1`–`LPT9`, with or without an extension). Renaming onto an existing name is refused with `Something with that name already exists in this folder.`

Recycle Bin (`actions/shell.recycle`): the only deletion-type call in `assistant/actions/` is `SHFileOperation` with `FO_DELETE` and `FOF_ALLOWUNDO`. It is refused (`refused`, 403) with `Not moved: <reason>, so there is no Recycle Bin to hold it.` when the path is a UNC path, or when the drive type is not fixed (removable, network, optical, RAM disk or unknown), or when the file system is not NTFS or ReFS (FAT32, exFAT and others).

Reveal (`actions/shell.reveal`): if an Explorer window already shows the folder (found through `Shell.Application`), that window selects the item and is shown; otherwise `SHOpenFolderAndSelectItems` opens one. A drive root is refused (`invalid`).

Copy path: the value is the path with `\\?\` removed. The clipboard is written with `CF_UNICODETEXT`. If the clipboard is unavailable, the result is `error` with `plain` and `quoted` in the body.

`md_vault` (`actions/shell.md_vault`): if `settings["md_vault"]["url"]` is an `http://` or `https://` string, that address is opened with `ShellExecuteEx`. Otherwise the file is opened with `os.startfile`, after the executable confirmation.

`ask_claude`: refuses folders, binary extensions, cloud placeholders, files whose first 1 MiB has a private key, and protected files (`protected_ok=False`). Without confirmation it returns the summary. With confirmation it returns `501` and sends nothing.

Action log: every call appends `{"t", "verb", "path", "result"}` to an in-memory log of 500 entries (`shell.action_log()`). The debug snapshot (`app.py`, `snapshot["actions"]`, last 50 entries) includes it. The log is not written to disk.

### 12.7 Settings keys added in M3b

Read from `assistant/settings.py` (validation in `_check_index`, `_check_preview`, `_check`).

| Key | Default | Allowed | Applies | Notes |
|---|---|---|---|---|
| `index.include_hidden` | `false` | `true` or `false` | at once, at query time | Hidden and system entries are dropped by `search.run` unless true. Toggle on Roots & excludes. |
| `index.scan_if_older_hours` | `12` | whole number, 1 to 168 | next start | The start scan runs only when the persisted index is older than this. |
| `index.known_exts` | the built-in list (12.8) | list of alphanumeric strings, each at most 16 characters, at most 200 entries | at once | Replaces the built-in list. `doc` is not in the built-in list. |
| `hotkey_skip_fullscreen` | `false` | `true` or `false` | at once | In `DEFAULTS` from M3b (R7-N11). |
| `preview.max_bytes` | `50000000` | whole number, 1,048,576 to 4,294,967,296 | next request | No Settings field. Edit `settings.json` by hand. |
| `md_vault.url` | not set | `http://` or `https://` text, read as given | next action | **Not in `DEFAULTS` and not validated.** No Settings field. Read as `settings["md_vault"]["url"]`. |

Under `index.excludes`, a list that the user saves **replaces** the defaults, and **Restore defaults** on Roots & excludes puts the default list back. The default list is `DEFAULT_EXCLUDES` in `index/excludes.py`; its Windows entries are drive-relative (`*:\Windows\`, `*:\ProgramData\Microsoft\Search\`) since M3b-0 (m). The index is not rebuilt for a change in `index.known_exts`; the next query uses the new list.

### 12.8 Changes to the M3a search routes (M3b-0, as built)

Sources: plan 18.3 (c) to (k), code read in `index/search.py`, `server/routes_search.py`, `search/modes.py`, `server/mcp.py`. Where a claim is from the code docstring only, it is marked.

- `GET /v1/search` rows carry `placeholder` (`to_dict`).
- `modified` accepts `YYYY-MM-DD..YYYY-MM-DD` (a closed range of local days, first day not after the last), in addition to the presets in section 11.1. A malformed range is `422` with `field: "modified"`. The interpretation reads `modified: <first day> to <last day>`.
- `sort` values are unchanged: `best`, `name`, `ext`, `date`.
- The root folder's own row is not returned (plan (k); not re-checked by doc-keeper).
- MCP `search_files`: `filters` accepts `ext`, `modified_after`, `modified_before` (ISO dates; a `modified_before` date covers the whole day) and `folders_only`; `ext` values are normalised like the query (plan (h); docstring only). The result carries `notes` (the same strings as HTTP) (docstring only).
- `index.show_protected_by_name` still applies only to HTTP and the popup. MCP never shows protected names.

### 12.9 Differences from the PRD and the plan (M3b; recorded, not fixed)

1. **Download and preview (AC-PV-5).** The button in the placeholder card sends `hydrate=1`, which the route ignores. It fetches nothing and the file stays a placeholder. The card then says `Download was requested: ... Assistant did not download the file.` The PRD's "Download and preview" behaviour is not built. Consistent with the rule that indexing and previews never trigger a download (AC-FI-6), but the button does not do what its label says.
2. **Text window (AC-PV-2).** The PRD asks for the first 1 to 2 MB of code and text. The default window is 256 KiB; "Show more" then loads 1 MiB at a time. Syntax highlighting is off above 1 MB (`TextView`).
3. **Table rows (AC-PV-2).** The server reads up to 1,000 rows and 50 columns (`extract.ROW_CAP`, `COL_CAP`). The page shows the first 500 (`TableView.ROW_CAP`) and says so. The PRD gives no number.
4. **Popup action feedback and folder listing (AC-AC-1, plan 18.4 item 2).** `app._on_action` starts the action in a thread and returns `None`, so `search_ui.invoke` never receives a result. Effects as wired at 2026-11-07: (a) the popup does not show an action's result text; failures are written to `app.log` at INFO and successes are not logged; (b) the **Ctrl+F folder listing** does not appear in the popup, because `_enter_listing` is reached only with a synchronous result; (c) the confirmation for `recycle` and `open` of an executable is the message box in `app._action_done`, and the page's own dialog is not used. This needs a fix in `app.py` and `search_ui.py`. Not fixed here.
5. **Custom date range (AC-FI-15).** Works on HTTP, MCP (`filters.modified_before` and `modified_after`) and the full page (two date inputs; the page says `Pick both dates for a custom range.` when only one is set). The popup's date control (Ctrl+D) cycles presets only, so a range cannot be typed in the popup. A saved search that stores a range is shown with the raw text in the popup's label.
6. **Reveal shortcut (AC-PV-1).** The PRD says Ctrl+double-click "opens its folder". The page's Ctrl+double-click reveals the file in Explorer, which selects it in its folder.
7. **Keyboard parity (AC-AC-7).** The page has no Ctrl+C, Delete, F2, Alt+Enter, Ctrl+F or Ctrl+D. Copy path (right-click, Ctrl+right-click), Rename, Properties, Move to Recycle Bin and Ask Claude are in the page's menu. The folder listing is not in the page at all. In the page, Ctrl+Shift+S changes the sort (the handler matches `s` and `S`); in the popup, Ctrl+Shift+S saves the search.
8. **Esc chain (AC-SH-6).** The popup's Esc chain is menu, folder listing, results, box, closed. It does not include the full page. In the page, Esc clears the selection, then the query and results, and closes the window only when the page is empty. Closing the page does not re-show or re-focus the popup (no code path found).
9. **Ask Claude (AC-AC-2, DEC 10).** A stub: the menu item, the "sends to Claude" marker and the confirmation exist; nothing is sent; the result is `501 not_implemented`. The popup does not show that message (item 4).
10. **Protected files and actions (SEC-3).** The PRD asks the deny-list to apply to search results, previews and MCP (and `CLAUDE.md` says the same). It does not say whether actions apply. In this build, Open, Open with, Reveal, Copy path, Rename, Properties, Move to Recycle Bin, MD Vault and the folder listing **do** run on protected files (they are `protected_ok=True` in `shell._vet`). Ask Claude refuses them, and folder listings hide them. Opening a protected file hands it to its default app, outside Assistant. This is a product and security decision to confirm; the README says what the code does.
11. **Action names.** The popup's folder listing is labelled `Fastsearch folder` (the button label, from the old tool). The verb is `folder_listing`. AC-AC-1 calls it "list that folder's contents".
12. **Preview size.** AC-PV-6 gives 50 MB without a unit. The default is 50,000,000 bytes. The setting has no Settings field.
13. **Recycle Bin file systems.** AC-AC-5 names network and removable volumes. The code also refuses any file system other than NTFS or ReFS; the refusal names the file system the drive reports (for example FAT32 or exFAT).
14. **Edge failures are silent in the popup.** F11 (or the button) with Edge missing, or with the window not found, gives no message in the popup; the reason is in `app.log` only (12.3).
15. **Nonce and command line.** See 12.5. The plan (M3b-3 and M3b-7) asks for a single-use nonce; the code is single use. The nonce also appears in the Edge command line for up to 30 seconds, which the plan does not mention.
16. **Preview and action routes in the PRD (section 8).** PRD section 8 lists `GET /v1/files/{...}/preview` and `POST /v1/actions/...`. The routes here are `GET /v1/files/preview` (the path is a query parameter) and `POST /v1/actions/{verb}`. The PRD says the planner defines the fields; they are defined in 12.2 and 12.6.

### 12.10 Still open after M3b

- The M3b security review (M3b-7). It must cover the preview pipeline, static serving, the nonce exchange, the shell actions (`\\?\` handling, nonce single use, no permanent delete call sites), and the items in 12.9 items 1, 10 and 15.
- qa's full-suite result for M3b (M3b-6): not in the brief.
- Unlocked-desktop checks: clipboard, the real Ctrl, Delete, F2 and Alt+Enter keys in the popup, and the popup feedback in 12.9 item 4.
- The work-laptop P2 probe (Edge app mode). Until it passes, previews on the work laptop are not confirmed (plan 7.4).
- Reported, not measured by doc-keeper: the page bundle size (7.3 MB) and the browser check counts (70 and 40), listed in CHANGELOG.

## 13. 3d7bc25 additions, as shipped (2026-11-07)

Supersedes: section 1, step 5 (the plain-text `404` and `405` bodies: the router handlers in `routes_cards.py` now return the object shape, see 13.1); section 12.6 (the `ask_claude` stub that returned `501 not_implemented`); section 12.9, item 9 (Ask Claude as a stub); and section 3.6 for the Ask Claude part of the M3b text. The earlier text is kept as written.

- Date: 2026-11-07. Commit 3d7bc25 on `main`, subject `feat: M4 full page, path search, live search, theme, rounded UI, hooks safety, bounded shutdown`. Reflog timestamp 2026-11-07, -0600.
- Written by: doc-keeper, from the code at 3d7bc25, read on 2026-11-07. The commit diff was not read: this session had no shell or git tool, so `git show --stat 3d7bc25` was not run.
- Sources read: `assistant/server/{routes_settings,routes_actions,routes_cards,routes_hooks,auth,app}.py`, `assistant/actions/{shell,claude}.py`, `assistant/app.py`, `assistant/settings.py`, `web/src/App.jsx`.
- Verification: code read only. Nothing in this section was run against a live instance or a test. Status codes are from the route code, not observed.

### 13.1 `GET /v1/settings`

Source: `routes_settings.py`, `get_settings`. Returns `200` with:

```json
{"theme": "dark", "shortcuts": {"open": "Enter", "...": "..."}, "search_placement": "icon",
 "hotkey": "ctrl+alt+space", "md_vault": {"url": ""}, "index": {"known_exts": ["..."]}}
```

- `shortcuts` is the built-in map of 15 actions merged with the saved `shortcuts` setting.
- `index.known_exts` is the saved list, sorted.
- Not returned: calendar addresses, the token, index roots and excludes, the notes folder, the update source.
- No valid token: `401` with code `unauthorized` and `WWW-Authenticate: Bearer` (`auth.py`).
- Settings not attached yet: `503` with code `settings_unavailable`.

### 13.2 `POST /v1/settings/theme`

Body: `{"theme": "dark"}`, `"light"` or `"system"`. Source: `routes_settings.py`, `post_theme`. On success the value is saved with `settings.set("theme", …)`, which is what the popup listens to, and the response is the same body as 13.1.

| Case | Status and code (code read) |
|---|---|
| Valid theme | `200`, the 13.1 body. Saved to `settings.json`. |
| No token, or a wrong token | `401` `unauthorized`. The route does not run. |
| Malformed JSON | `400` `invalid_json` (`routes_cards.read_json`). |
| Body over 256 bytes | `413` `payload_too_large`. |
| Empty body, body not an object, `theme` missing, not a string, or not one of the three names | `422` `invalid_parameter`, `field: "theme"`, message `theme must be dark, light or system.` |
| Settings not attached yet | `503` `settings_unavailable`. |
| Unknown path (router) | `404` `not_found`, `No such route.` |
| `GET` on this path | `405` `method_not_allowed`. |

Points to note:

- A bad theme value is `422`, not `400`. `400` is only for malformed JSON.
- The route has no `404` of its own. A `404` on this path means the running app does not register the route, for example an older build. The page handles it: `web/src/App.jsx`, `chooseTheme`, treats `404` as "route missing", stores the choice in browser storage under `assistant.theme`, and does not show an error.
- A `401` on this route makes the page put back the theme it showed before the change and call its expired-session handler. Nothing is saved. Listed as a known issue in CHANGELOG 2026-11-07. Fixed after 3d7bc25, pending commit: the 401 now restores `<html data-theme>`. The page's own handling is in `chooseTheme`, the `catch` branch.

### 13.3 Ask Claude status and continue

Source: `routes_actions.py` (`ask_claude_status`, `ask_claude_continue`) and `actions/claude.py` (`status`, `continue_session`).

`GET /v1/actions/ask_claude/status`, optional query `refresh=1` or `refresh=true`:

- `200` with `{"available": true, "version": "<text>", "reason": null}`, or `{"available": false, "version": null, "reason": "<why>"}`.
- The probe result is cached. `refresh` forces a new probe.

`POST /v1/actions/ask_claude/continue`, body `{"path": "<file path>"}`:

- `200` with `{"ok": true, "message": "Opened Claude Code in a new window."}`.
- `422` `invalid_parameter`, `field: "path"`, when `path` is missing, blank or not a string.
- `404` `no_answer`, `field: "path"`, when there is no Claude answer for that path in this session.
- `500` `error` with the message, when continuing fails. If Claude Code is missing, the message is the probe's reason.
- `413` above 4 KiB (the action body cap).

Known issue: a caret (`^`) in the home folder path breaks Continue (CHANGELOG 2026-11-07). Fixed after 3d7bc25, pending commit: Continue now works with `^ & ! ( )` in the home path.

### 13.4 `POST /v1/actions/ask_claude`: send after confirmation, and `whole_file`

Body: `{"path": "<file path>", "confirm": false, "whole_file": false}`. `confirm` and `whole_file` are optional booleans. Any other type is `422` `invalid_parameter`. Source: `routes_actions.py` (`_body`, `action`) and `actions/shell.py` (`ask_claude`, `_ask_text`).

Two steps:

1. Without `confirm: true`, nothing is sent. The response is `200` with `"ok": false`, `"needs_confirmation": true`, a message that gives the byte count and what would be sent, and a `summary`:
   `{"path", "bytes_to_send", "file_bytes", "whole_file", "what", "mode", "office_text", "available", "reason", "account"}`.
   `mode` is `whole_file` or `preview_text`.
2. With `confirm: true` and Claude Code available, the text goes to `claude -p --no-session-persistence --tools ""` on standard input (`actions/claude.py`, `send`). The response is `200` with `{"ok": true, "message": "Claude answered about <name> (<n> bytes sent).", "answer": "<text>", "logged": true, "summary": {…}}`.

`whole_file`:

- `false` (default): the first 32,768 bytes of the file's text (`ASK_CLAUDE_BYTES`). `mode` is `preview_text` when the file is longer.
- `true`: the whole text, up to 10 MiB minus 4 KiB (`claude.STDIN_CAP - 4096`). A larger file is refused with `403` `refused`, and the message gives the limit.

Other responses (code read):

- `403` `refused`: a binary file type other than Office, binary-looking content, a cloud placeholder that is not downloaded, a file that contains a private-key header, a folder, and protected files (`_vet` with `protected_ok=False`).
- Office files (`docx`, `xlsx`, `xlsm`, `pptx`): the extracted text is sent, not the file.
- `503` `unavailable`: Claude Code is missing when confirmed. `500` `error`: `claude -p` failed, timed out or returned no answer.
- `503` `index_unavailable`: the file index is not ready.
- `422` `invalid_parameter`, `field: "path"`: `path` missing or blank.

This route is the only one that sends a file's text to Claude Code, and it sends only after `confirm: true`. The MCP file tools (section 11.5) are a separate path.

### 13.5 `POST /v1/hooks/claude-code`

The contract in sections 3.3 and 4 still describes this route. Source at 3d7bc25: `routes_hooks.py`, `claude_code_hook`.

- Authentication and Host and Origin checks as in section 1. The body cap is the server-wide 1 MiB; the route sets no cap of its own.
- `400` `invalid_json` for malformed JSON, and `400` `invalid_body` for valid JSON that is not an object.
- Otherwise `200` with `{}`. An exception in the session handler is logged and still answered with `200 {}`.
- The route never returns a permission decision.

The diff of 3d7bc25 was not read, so whether this handler changed in that commit is not recorded here.

### 13.6 Shutdown bound

Source: `assistant/app.py` (`SHUTDOWN_STEPS_S = 3.0`, `HARD_EXIT_S = 10.0`, `_run_steps`, `_hard_exit`, `shutdown`) and `assistant/server/app.py` (`STOP_JOIN_S = 4.0`, `ServerThread.stop`).

- The teardown steps (calendar, hotkey, display watch, full page, index, notes, scheduler) run side by side. They share one 3-second deadline. A step still running after that is logged and left behind, so the shutdown moves on.
- After the teardown steps, the HTTP server thread is joined for up to 4 seconds.
- A hard exit (`os._exit(1)`) runs 10 seconds after shutdown starts if the shutdown has not finished.

So "about 3 seconds" is the bound on the teardown steps. The whole shutdown can take longer, up to about 7 seconds before the hard exit at 10 seconds in the worst case. These are code values, not measurements.

Supersedes: section 3.5, the "exit code 0 within 15 s" observation from Review 3, which predates these bounds (kept as written).

### 13.7 Not observed

- Any route above, on a live instance.
- Security review of 3d7bc25: `docs/security/2026-11-07-m4-review.md`. Review 11 returned BLOCK on R11-F1 (the Continue launcher's working folder). Review 12 returns PASS for commit, with low NOTEs R12-N2 and R12-N3 open. Review 11's file list covers `assistant/actions/{claude,shell}.py`, `assistant/server/{routes_actions,routes_settings}.py` and `assistant/app.py`, which are the code of 13.1, 13.3, 13.4 and 13.6. It does not list `routes_hooks.py`, so 13.5 is not covered by that list.
- The four known issues in CHANGELOG 2026-11-07 are listed there, not re-tested here.
