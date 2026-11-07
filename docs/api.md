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
