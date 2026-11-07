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
