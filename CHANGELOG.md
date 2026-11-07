# Changelog

## 2026-11-06

- Project scaffold created.

## 2026-11-06
- Commits 9f7334c … e07693c on main (no pull requests yet; the repo has no remote): M1, the overlay that replaces the alert half of the reference popup
  (plan section 7.1, tasks M1-1 to M1-9). Rebuilt as a Python app that needs no admin rights: a round icon with a card stack
  (four kinds, snooze, dismiss, mute), Claude Code hooks giving one waiting and one done card per session, a local HTTP card
  and reminder API behind a bearer token, a `~/.assistant/events/` file drop, a `send`/`demo`/`hooks` command line, reminders
  with a four-shape grammar, and `install.ps1` / `uninstall.ps1`. Why: retire the reference popup's alert half on the author's
  laptops, and let other developers install it with Python and pip alone.
  - Dispatched by tech-lead. Drafted by dev (M1-1 to M1-6). Tests and driver by qa (M1-7). Security review by security
    (`docs/security/2026-11-06-m1-review.md`: Review 3 BLOCK on B1, the token and card body sent to an environment proxy;
    lifted on re-check, which reports zero blocking findings). qa PASS on M1-4, M1-5 and M1-9 as cited in that review.
    Docs by doc-keeper (M1-9: `README.md`, `docs/api.md`). Merged by git-keeper (pending).
  - Verified numbers, as supplied in the tech-lead's M1-9 brief: hook event to card visible in 0.4 s or less (PRD target: 2 s,
    section 12); file drop to card visible in 1.3 s or less (PRD target: 2 s, AC-CS-12); idle CPU 0.19 % (PRD: about 0 %
    averaged over 10 minutes); resident memory about 36 MB (PRD proposal: 80 MB, section 12).
    Source: the qa full-pass and dev reports relayed through the tech-lead; those reports live in the session transcripts,
    not in the repo, so the only in-repo artifacts for these numbers are the commit messages (dffeaad) and the security
    review's own measurements in `docs/security/2026-11-06-m1-review.md`.
  - Not yet observed for M1: the on-screen visual pass of the overlay; `install.ps1` and `uninstall.ps1` on a clean Windows
    user profile (AC-IN-8); the interactive hook probe P6 (AskUserQuestion and plan-approval coverage); the probes on the
    managed laptop (`docs/probes/` has only the personal-laptop file); whether Claude Code honours `HTTP_PROXY` for its hook
    requests (Review 3 B1 tail); the six overlay end-to-end tests, which need an unlocked desktop.
  - Open items carried from the review: R4-1 and R4-2 (a missing or stale `assistant.pid` can misdirect or refuse a start),
    R4-3 (asyncio reset noise in `app.log`), R4-5 (`--doctor` does not name the port owner), N3 (control characters kept in
    card text), N7 (`uninstall.ps1` does not check the process image before `Stop-Process`), N9 (`pillow` in the dev venv only).
  - Not in M1: search and previews, Ask notes, Ask Claude, MCP, meetings, plugins, the Settings window, the `update` command.
    The Search and Settings menu entries are placeholders.
  - Docs: `README.md` rewritten as the user-facing README (install, use, reminder grammar, CLI, troubleshooting, features,
    security and privacy facts, status). `docs/api.md` added: every `/v1` route as shipped, the hook contract, the card model
    with caps and kind defaults, the file-drop format, and a list of differences from PRD section 8.
