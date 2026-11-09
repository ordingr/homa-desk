# Homa — project rules

Homa (formerly Assistant, renamed 2026-11-08) is a pure-Python Windows 11 desktop app: an always-on-top alert
overlay (cards from Claude Code hooks, MCP, HTTP, file drop, plugins), an
Everything-like file search with previews, an MCP server, and a plugin
system. See `README.md` and the documents below.

## Team model

This repo is built by the TeamAgents team defined in
`C:\AI Projects\TeamAgents` (CLAUDE.md, `.claude/agents/`, `.claude/skills/`,
`.claude/hooks/`). Those rules apply here in full: tech-lead dispatches,
generators hand off to verifiers, one file one owner per task, three-strike
loop exit, archaeology rule on `docs/`, no agent edits agent definitions.
Every dispatched task loads the `fable-parity` skill before its first read
or edit.

## Source of truth, in order

1. `docs/plans/2026-11-06-assistant-v1-plan.md` — the plan dispatches cite
   (task ids M0-1 … M5-9, files owned, AC ids, done criteria).
2. `docs/prds/2026-11-06-assistant-v1.md` — requirements and acceptance
   criteria (AC-<AREA>-<n>), decisions (DEC 1–14), security rules (SEC-1–13).
3. `docs/research/*.md` — evidence briefs behind the PRD.
4. `docs/reference/2026-11-06-assistant-popup-readme.md` — behavioural
   spec of the app being rebuilt.
5. `docs/plans/2026-11-08-rename-to-homa.md` — the rename plan (product
   name, package, home folder, environment variables, MCP server name).
   For names, item 5 overrides items 1 to 4 (its section 4.1). Dispatches
   cite it by path.

If code and these documents disagree, say so in the report; do not quietly
pick one.

## Hard constraints (from the PRD's Decisions — do not reopen in code)

- Python 3.10+, pip-only dependencies, no compiled executable of our own,
  no admin rights, no service. Must run on a locked-down corporate laptop.
- Nothing leaves the PC except the explicit "Ask Claude" action, which
  confirms what it will send, and the read-only MCP file tools, which run
  only when the user's own Claude session calls them (added 2026-11-07,
  per PRD AC-MC-3/4). No telemetry, no cloud sync, no auto-update.
- Secrets deny-list (SEC-3) applies to search results, previews and MCP.
- Recycle Bin only; never a permanent delete.
- No employer or personal identifiers in anything committed. Paths in
  docs and tests use placeholders.

## Layout

- `homa/` — the package (`python -m homa`); tkinter only under
  `homa/overlay/`. The product is named Homa (rename of 2026-11-08).
- `assistant/` — 0.2.x shim only: `python -m assistant` and
  `python -m assistant.mcp_stdio` print a notice and run Homa. Removed in 0.3.0.
- `web/` — React source of the full-page UI; built output is committed
  under `homa/static/app/` (never ignored).
- `tools/probes/` — stdlib-only probe scripts (plan §6).
- `tests/` — pytest; `tests/fixtures/` builds fixture trees from JSON.
- `docs/probes/` — raw probe output per laptop, dated.
- Runtime state lives in `HOMA_HOME` (default `~/.homa/`), never
  next to the code. A pre-rename `~/.assistant/` is moved to `~/.homa/` by the
  first start (`homa/migrate.py`; README, "Upgrading (2026-11-08)").

## Verification

Reports carry the exact commands run and their output. Work-laptop results
are user-run and pasted; never claim one that was not observed.
