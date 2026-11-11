# Security review: card timeouts and "Close all" (branch card-timeouts), 2026-11-11

Reviewer: security. Base: main 2fb484a, changes uncommitted in the worktree.
Scope: homa/cards/store.py, homa/settings.py, homa/app.py, homa/server/routes_cards.py,
homa/server/mcp.py, homa/overlay/cards_ui.py, homa/overlay/reminder_ui.py, homa/overlay/settings_ui.py.

## Verdict: PASS (0 BLOCK, 4 NOTE)

## What was checked

| Area | Result |
|---|---|
| POST /v1/cards/dismiss_all: Host, Origin, bearer token | Same AuthMiddleware as every route: no token 401, wrong token 401, `Origin: https://evil.example` 403, `Origin: null` 403, `Host: evil.example` 403 (all with a valid token where relevant) |
| Routing ambiguity with a card whose id is `dismiss_all` | None. Only POST is taken by the literal route; GET/PATCH/DELETE `/v1/cards/dismiss_all` and POST `/v1/cards/dismiss_all/snooze` still reach that card (200/200/204/200). PUT gives 405, `DISMISS_ALL` gives 405. Before this change, POST `/v1/cards/{id}` did not exist, so no earlier behaviour is shadowed |
| GET /v1/cards/dismiss_all with no such card | Falls through to get_card, which returns 404 card-not-found. Harmless |
| Request body | Ignored by the handler; 2 MB body gives 413 from BodyLimitMiddleware |
| Cross-site request forgery | Needs a bearer header (needs a CORS preflight, which is refused) and an allowed Origin. Not reachable from a web page |
| MCP tool dismiss_all_cards | Served at /mcp behind the same AuthMiddleware, so callers need the same token as dismiss_card. Takes no arguments. Gives no ability beyond calling dismiss_card on every id from list_cards |
| dismiss_all includes cards held back while muted, skips snoozed ones | A token holder could already DELETE each card by id. No new abuse path |
| Crafted settings.json `cards` values | `true`, `1.5`, `10**400`, `"12"`, `null`, `[]`, nested objects, `-5`, `0`, `1e309`/inf, `"cards"`, `5`: all repaired to defaults by `_merged`, no exception |
| Zero or negative timeouts (busy loop) | Not possible. info_seconds is clamped to at least 5 s; waiting_minutes 0 or below means "never fades" (no deadline). See NOTE 1 for a separate re-arm loop |
| Store `_fades` when `timing` raises or returns None/{} | Logged, defaults used |
| peek_waiting_minutes | Fixed path `paths.settings_file()`, read-only, UTF-8. Missing file, empty, truncated JSON, invalid UTF-8, BOM, top-level array, `cards: []`, bool, 1e999, 5000-digit integer: all return the default 15. Deep nesting: see NOTE 2 |
| Lock ordering (store RLock, then Settings._lock inside `timing`) | Settings._lock is only held for deepcopy and never calls into the store; listeners run after release through uibus. 3,000 store calls racing a thread doing `settings.set("cards", ...)` in a loop finished with no deadlock |
| New outbound HTTP client code | None in the diff |
| Settings UI entry | `int()` with range check; bad input shows an error line and is not saved |
| Targeted tests | `test_card_timing.py test_api_cards.py test_mcp_cards.py test_cards_store.py`: 166 passed |

## Findings

### NOTE 1: a seen card that is snoozed past its fade time re-arms the store tick about 16 times a second until the snooze ends
- Where: homa/cards/store.py `next_deadline` (the `_fade_at` append, about line 249) compared with `_sweep` (about line 336), which skips fading while `snoozed_until` is set; homa/app.py `_arm_tick`/`_run_tick` (lines 557-576).
- Cause: snooze does not clear `seen_at`. `next_deadline` returns `seen_at + span` even for snoozed cards, so once that moment passes the deadline sits in the past, `_arm_tick` schedules `now + TICK_SLACK_S` (0.05 s), the sweep changes nothing, and the cycle repeats until the snooze ends. Snoozes can be up to 7 days.
- Before this change: the same thing already happened for non-sticky cards (the old code also added `seen_at + FADE_SECONDS` without checking snooze). This change adds sticky cards: a waiting card seen and then snoozed for more than 15 minutes now hits it too.
- Repro: drove the real `App._arm_tick`/`_run_tick` with the real `Scheduler` and `CardStore`, card seen, snoozed 120 min, fade time passed:
  `sticky=False tick runs in 5 s: 80 cpu 0.03s` and `sticky=True tick runs in 5 s: 80 cpu 0.00s`.
- Impact: constant wake-ups with negligible CPU in the measurement; keeps the scheduler thread from sleeping. A token holder could cause it on purpose, but gets nothing beyond what a normal snooze does. Not a BLOCK.
- Fix: in `next_deadline`, skip the fade deadline when `c.snoozed_until is not None`, the same way `_sweep` does (the snooze end is already in the list). Alternatively clear `seen_at` in `snooze()`.

### NOTE 2: deeply nested settings.json raises RecursionError out of peek_waiting_minutes
- Where: homa/settings.py `peek_waiting_minutes` (about line 516). It catches `(OSError, ValueError)`; `json.loads` of about 100,000 nested `[` raises `RecursionError`.
- Repro: `peek [[[[... RAISED RecursionError maximum recursion depth exceeded while decoding a JSON array`.
- Impact: opening the reminder input (reminder_ui.py line 62) would fail. Only the user (settings.json owner) can write the file, and `Settings._read`, `peek_port` and `peek_update_source` already share this pattern, so startup would fail first. Not new.
- Fix: catch `RecursionError` as well (here and in the peer functions), or `except Exception`.

### NOTE 3: `_clamped` and `_fades` trust that `timing()` returns a dict of numbers within float range
- Where: homa/cards/store.py `_fades` (about line 260): `raw.get(...)` runs outside the `try`, so a non-dict truthy return (list, str) raises AttributeError on every store call. `_clamped` (about line 432) calls `math.isfinite` on any int, and an int too big for a float raises OverflowError.
- Repro: `fades junk RAISED AttributeError 'list' object has no attribute 'get'`; `clamp info 10**400 RAISED OverflowError`.
- Impact: none today. `Settings._merged` always turns `cards` into a dict of ints in range (checked with all the crafted values above), and that is the only `timing` source. Hardening only.
- Fix: `if not isinstance(raw, dict): raw = {}`; in `_clamped`, reject ints outside the range before `math.isfinite`, or wrap the check in try/except OverflowError.

### NOTE 4: the "dismiss_all" card id is still allowed
- Ids are free text, so a client can make a card with id `dismiss_all`. As shown above, it can still be read, patched, snoozed and deleted. Only `POST /v1/cards/dismiss_all` means "dismiss all", and creating or changing a card with that id uses other routes. No fix needed; recorded so future routes under `/v1/cards/<word>` get the same check.

## Commands run
- `.venv/Scripts/python.exe -m pytest -q tests/test_card_timing.py tests/test_api_cards.py tests/test_mcp_cards.py tests/test_cards_store.py`: `166 passed in 43.77s`
- In-process probes from the session scratchpad (Starlette TestClient on create_app, throwaway HOMA_HOME, no sockets, no windows): route auth and routing, crafted settings, peek, clamping, deadlock race, tick re-arm count. Nothing was left running; the live instance on 17171 and the user's home folder were not touched.
