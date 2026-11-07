---
name: run-assistant
description: How an agent starts, drives, inspects and screenshots the Assistant overlay app on Windows. Use whenever a task must observe the overlay on screen (icon, cards, menu, reminders) instead of inferring it from code.
---

# Driving the Assistant overlay

Everything goes through `tools/driver.py` (stdlib + ctypes; Pillow only for screenshots) and the test-mode
route `GET /v1/_debug/ui`. Use the repo venv: `.venv\Scripts\python.exe`. Run from the repository root.

Rules that make an observation count:

1. Always start with `--test-mode` and a **scratch** `--home` (a folder under your session scratchpad). Never
   point `--home` at `~/.assistant`, and never leave a started instance running when you finish.
2. Screenshots go to the session scratchpad, never into the repo. `git status` must stay clean of PNGs.
3. The oracle is `/v1/_debug/ui` (rectangles, kinds, colours, seen, blinking). A screenshot is evidence next
   to it, not a replacement. Quote driver output verbatim in your report.
4. Only one driver-managed instance at a time (its pid/port/home live in `%TEMP%\assistant-driver\state.json`).

## Commands

```
$py = ".venv\Scripts\python.exe"
$home = "<scratchpad>\assistant-home"

& $py tools/driver.py start --home $home --port 17421 --test-mode   # launches pythonw -m assistant, waits for /v1/health
& $py tools/driver.py start --home $home --port 17421 --test-mode --console   # python.exe instead, if you need a crash trace in console.txt
& $py tools/driver.py post --title "Needs you" --kind waiting --detail "Permission needed" --id demo1
& $py tools/driver.py ui                    # icon rect/styles/count/muted, menu order, one row per card window
& $py tools/driver.py ui --json             # the raw payload
& $py tools/driver.py hover demo1           # move the pointer onto a card (marks it seen)
& $py tools/driver.py hover icon
& $py tools/driver.py click demo1 Dismiss   # click a named button; needs "buttons" rects in the debug payload
& $py tools/driver.py click icon --right    # open the icon menu
& $py tools/driver.py move 1200 800         # raw pointer position, physical pixels
& $py tools/driver.py clickxy 1200 800 [--right]
& $py tools/driver.py drag 1800 1000 1600 800   # press, move in steps, release
& $py tools/driver.py hotkey                # presses the hotkey from settings.json (default ctrl+alt+space)
& $py tools/driver.py type "driver selftest in 10m"
& $py tools/driver.py key enter             # also: esc tab up down backspace f11 ctrl+f ...
& $py tools/driver.py screenshot --out "<scratchpad>\shot.png"   # all monitors, PIL.ImageGrab
& $py tools/driver.py fullpage --query "design md" --screenshot "<scratchpad>\full.png"   # Edge app mode; needs the M3b build
& $py tools/driver.py stop                  # POST /v1/shutdown, falls back to a polite taskkill, then force
```

`stop` prints whether `logs\app.log` has the "shutting down" line; "False" means it was not a graceful exit and
is a finding. `start` fails loudly with `console.txt` if the process dies early.

## Reading the output

- `/v1/_debug/ui` 404 means the app was not started with `--test-mode` (or the route is not wired yet).
- Coordinates are physical pixels in the virtual desktop, the same space as the rectangles in the payload; the
  driver makes itself per-monitor DPI aware so they line up on mixed-scale displays.
- Expected icon styles in `exstyle_flags`: TOOLWINDOW, NOACTIVATE, TOPMOST (no APPWINDOW).
- Card colours are the named constants in `assistant/overlay/gfx.py` (AMBER waiting, GREEN done, BLUE meeting,
  PURPLE reminder/custom); compare to those, do not hard-code hex in a report.

## Without a screen

Most behaviour has no UI dependency. `python -m pytest -q` runs the card store, grammar, scheduler, API and hook
tests; `tests/e2e/test_overlay.py` runs the on-screen checks and skips itself when the debug route is absent.
Use `-rs` to see skip reasons.

## Not verifiable here

Work-laptop behaviour (corporate policy, second monitor with a different DPI) is user-run. Mark such lines
"unverified on work laptop until the user's paste arrives".
