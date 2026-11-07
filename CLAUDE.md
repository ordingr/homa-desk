# Assistant — project rules

Assistant is a pure-Python Windows 11 desktop app: an always-on-top alert
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

- `assistant/` — the package (`python -m assistant`); tkinter only under
  `assistant/overlay/`.
- `web/` — React source of the full-page UI; built output is committed
  under `assistant/static/app/` (never ignored).
- `tools/probes/` — stdlib-only probe scripts (plan §6).
- `tests/` — pytest; `tests/fixtures/` builds fixture trees from JSON.
- `docs/probes/` — raw probe output per laptop, dated.
- Runtime state lives in `ASSISTANT_HOME` (default `~/.assistant/`), never
  next to the code.

## Verification

Reports carry the exact commands run and their output. Work-laptop results
are user-run and pasted; never claim one that was not observed.
