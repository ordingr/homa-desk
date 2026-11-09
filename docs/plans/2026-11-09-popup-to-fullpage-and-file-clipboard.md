# Plan: double-click from the popup to the full page, and multi-select Copy / Cut of files

- Date: 2026-11-09
- Author: planner (TeamAgents)
- Status: first version. Append-only (archaeology rule): changes go in dated "Revision" sections at the end. Every
  dispatch cites this file by path.
- Names: written against the `homa` names of `docs/plans/2026-11-08-rename-to-homa.md` (branch `rename-homa`, worktree
  `C:\AI Projects\Assistant-homa`, read on 2026-11-09 with the rename still uncommitted).
- Hand-off: tech-lead dispatches P0 → D7 in the order of section 5.
- **Ordering gate: no generator (D1, D2, D3) starts until `rename-homa` has merged to `main`.** The work then happens on a
  new branch in its own worktree, never in the live checkout (section 5, P0).

User request (2026-11-09, verbatim): "if I double click on the halfway mode, after the search, it should go to the full
page mode and show the preview first. add multi select and copy / cut, so we can move or copy files to another file
explorer folder too".

## 1. Goal and non-goals

**Goal.** (a) Double-clicking a file row in the search popup opens the full page with the same query, chips, date filter
and sort, with that row selected and its preview showing. (b) In both the popup and the full page the user can select
several rows and Copy or Cut them to the Windows clipboard as real files, so Paste in File Explorer copies them (Copy)
or moves them (Cut).

**Non-goals.**
- Drag-out of rows into Explorer (assessed in section 3.4; deferred).
- Paste *into* Homa, a "cut" dimming of rows, an undo, an MCP tool for the clipboard.
- Homa moving or deleting files itself. Explorer performs the move when the user pastes; Homa only writes the clipboard.
- Handing Ask-notes (`?` mode) results to the full page; the full page has no notes mode.
- Reusing an already open full-page window (section 4, decision 3).
- Any change to `homa/overlay/fullpage.py`, the token model, or the routes not named in section 5.

## 2. What the code does today (read 2026-11-09) and where it differs from the brief

| Fact | Where |
|---|---|
| "Halfway mode" is the Tk search popup `SearchPopup`. The brief's interpretation holds. | `homa/overlay/search_ui.py` |
| **The popup has no double-click binding.** A double-click is two single clicks: `_press`/`_release` → `click("hit:row:N")` → `select(N)`. Nothing is opened. So nothing is replaced; double-click is new behaviour. (Brief guessed "probably opens the file"; it does not.) | `search_ui.py` `_bind_keys`, canvas binds near line 357, `_release`, `click` |
| Enter on unchanged results opens the selected row (`submit` → `invoke("open")`). Stays as is. | `search_ui.py` `submit` |
| Popup selection is single: `self.sel` (int). No Ctrl/Shift handling; `click()` discards the event's modifier state. | `select`, `click` |
| Popup Ctrl+C (`copy_path`, default chord) copies the path **as text** via `shell.copy_path` → `_set_clipboard` (pywin32 `win32clipboard`, CF_UNICODETEXT, opened with no owner window, 5 retries). Only when `_row_keys_live()` (results showing, text unchanged since search, no text selected in the entry). | `search_ui.py` `_copy_key`; `homa/actions/shell.py` `_set_clipboard`, `copy_path` |
| F11 / the "Full page" button calls `on_fullpage(query)` → `app._open_fullpage(query)` → `fullpage.open(query, port, auth.mint_nonce)`. Only the query text travels (in the URL as `q=`); chips, date, sort, mode and the selected row do not. On success the popup closes. | `search_ui.py` `fullpage`; `homa/app.py` `_on_fullpage`, `_open_fullpage` |
| `fullpage.open` **closes the window it opened earlier, then launches a new one** (so "replace" is already the behaviour). | `homa/overlay/fullpage.py` `open` |
| Launch security: `mint_nonce()` makes a one-shot 32-byte nonce (30 s TTL, max 16 outstanding); the page reads `?launch=` from the URL, strips it with `history.replaceState`, and trades it at `POST /v1/session/exchange` (bearer-exempt, Host/Origin still checked) for the bearer token. All `/v1` routes need the bearer; Host must be 127.0.0.1/localhost; a present Origin must be too. | `homa/server/auth.py`; `web/src/api.js` `boot`, `exchangeNonce` |
| The full page **already has multi-select**: click, Ctrl+click toggle, Shift+click range, Shift+Arrow/Home/End extend (`selected` Set, `anchor`, `activeId`; `aria-multiselectable`). **No Ctrl+A.** | `web/src/App.jsx` `onRowClick`, `move`; `components/ResultList.jsx` |
| Full page Ctrl+C copies the selected rows' **paths as text** through the browser clipboard (`copyPaths`, `copyText`), gated by `rowLive`. Double-click opens the file (Ctrl+double-click reveals). The context menu acts on the right-clicked row; Copy path acts on all selected rows (`targetsFor`). | `App.jsx` `onKeyDown`, `copyPaths`, `onRowDoubleClick`, `targetsFor`; `components/ContextMenu.jsx` |
| The full page searches files only, `PAGE_SIZE = 100` per page with "load more" (`offset`); the popup asks for up to 200 rows. A row picked in the popup can sit past the full page's first page. | `App.jsx`; `homa/search/modes.py` `limit: int = 200` |
| Popup sort `""` = full page `"best"`; date keys are the same (`today`, `7d`, `30d`, or `YYYY-MM-DD..YYYY-MM-DD`); `normaliseModified()` in `App.jsx` already maps them. Chips are the same six keys. | `homa/search/modes.py` `SORT_CHOICES`, `MODIFIED_CHOICES`, `CHIPS` |
| Popup `Row` has no id; full-page rows carry the index id. The common key is the **path**. | `modes.Row`, `homa/index/search.py` `to_dict` |
| Clipboard threading: `_set_clipboard` is already called from the Tk main thread (popup) and from Starlette's thread pool (`POST /v1/actions/copy_path` via `run_in_threadpool`). Win32 only requires Open/Close on the same thread; **no uibus hop is needed** (contradicts the brief's assumption; see risk R2 for the fallback). `uibus.submit(fn)` exists if one is ever needed. | `shell.py`; `homa/server/routes_actions.py`; `homa/uibus.py` |
| `pywin32==312` is already a dependency (`win32clipboard`, `win32con`). No new dependency is needed. | `requirements.txt` |
| **`settings.json` stores the whole `shortcuts` dict** (`DEFAULTS["shortcuts"] = dict(DEFAULT_SHORTCUTS)`, saved in full by `Settings.save`). Every existing install therefore has `"copy_path": "Ctrl+C"` written to disk; changing the default alone would not free Ctrl+C. A migration rule is required (D1). | `homa/settings.py` `DEFAULTS`, `_merged`, `save` |
| In-app shortcut list: `SHORTCUT_LABELS` in the Settings window (About page) and the context-menu accelerators in both UIs. | `homa/overlay/settings_ui.py`; `search_ui.py` `MENU_ITEMS`; `ContextMenu.jsx` `MENU_ITEMS` |
| Path vetting already exists: `shell._vet(index, path, protected_ok, not_root)` — absolute, resolved inside an indexed root (`pathcheck.resolve_in_roots`, which refuses `\\.\` and ADS), not inside Homa's home, deny-list (when `protected_ok=False`), exists, in the index. **But `_vet` first calls `pathcheck._plain`, which strips a `\\?\` prefix**, so `\\?\` must be refused explicitly before `_vet`. | `shell.py` `_vet`; `homa/security/pathcheck.py` |
| Docs drift found: `README.md` line ~498 says "The page has no Ctrl+C …" but `App.jsx` binds Ctrl+C to copy paths. Recorded for D7, not fixed here. | `README.md` |
| Headless full-page e2e infrastructure exists: `tests/e2e/test_fullpage_m43.py` + `m43_driver.cjs` (playwright-core, `channel: "msedge"`, headless, test-mode app, mocked `/v1/session/exchange`). | `tests/e2e/` |

## 3. Approaches considered

### 3.1 How the selection travels from the popup to the full page

- **A. Attach a handoff to the launch nonce; return it from `/v1/session/exchange` (chosen).** `mint_nonce(handoff)` stores
  `{query, chips, modified, sort, select_path}` beside the nonce; the exchange response becomes
  `{"token": ..., "handoff": {...}}` and consumes both at once. No new route, no path in any URL, same one-shot 30 s
  lifetime, only the page that holds the nonce can read it. `fullpage.py` is untouched: the app passes
  `lambda: auth.mint_nonce(handoff)` as the existing `mint` callable.
- B. Put `chips`, `sort`, `select=<path>` in the launch URL. Smallest diff, but file paths end up in Edge's history in the
  app profile and in the command line of `msedge.exe`. Rejected (brief: no paths in URLs).
- C. A separate `GET /v1/fullpage/handoff` the page calls after the exchange. Needs a second key (the nonce is gone after
  the exchange), a second store with its own expiry and a new route for security to review. More moving parts than A for
  the same result.

### 3.2 How files reach the Windows clipboard

- **A. Python writes CF_HDROP + "Preferred DropEffect" with `win32clipboard` (chosen).** `SetClipboardData(CF_HDROP,
  bytes)` with a `DROPFILES` header (`pFiles=20`, `pt=(0,0)`, `fNC=0`, `fWide=1`) followed by UTF-16LE paths, each
  NUL-terminated, plus a final NUL; and `SetClipboardData(RegisterClipboardFormat("Preferred DropEffect"),
  struct.pack("<I", 1 or 2))` (DROPEFFECT_COPY = 1, DROPEFFECT_MOVE = 2), both inside one Open/Empty/Close. Same
  library and retry pattern as the existing `_set_clipboard`; the DROPFILES bytes come from a pure function QA can test
  without a clipboard. ctypes (`GlobalAlloc`/`SetClipboardData`) would do the same with more code; pywin32 is already a
  dependency, so the brief's "via ctypes" is met in spirit (no new dependency) and ctypes is used by QA to read it back.
- B. Shell out to PowerShell `Set-Clipboard -Path`. Copy only (no cut), slow, and it is shell execution. Rejected.
- C. Browser clipboard API in the full page. Cannot write files (text/HTML/PNG only), and the popup is not a browser.
  Rejected; the full page must call the backend.

### 3.3 Full-page window when one is already open

- **A. Replace (chosen):** keep `fullpage.open`'s close-then-launch. Zero new code, and the new window starts from a
  clean nonce exchange.
- B. Reuse: push the handoff into the running page (polling or server-sent events) and bring the window to the front.
  Needs a push channel, a page-side listener, and focus-stealing handling. Not worth it for this request.

### 3.4 Drag-out to Explorer (assessed, **deferred**)

- Full page: a browser can only drag real files out with Chromium's `DownloadURL` drag format, which makes Explorer
  download the file over HTTP from a URL. The request would carry no bearer header, so the URL would need a token or a
  one-shot secret in it, it is copy-only, and it streams through the server. Not acceptable under SEC-2.
- Popup: possible with OLE `pythoncom.DoDragDrop` plus a Python `IDataObject`/`IDropSource` COM server on the Tk thread,
  coordinated with Tk's mouse capture. Roughly a day of work plus a spike, and hard to test headlessly.
- Recommendation: defer. Copy/Cut + Paste covers the user's "move or copy files to another folder". Revisit as a
  popup-only spike if the user asks for drag.

## 4. Chosen approach and why

Handoff on the nonce (3.1 A), Python clipboard writer behind one new authenticated route and one in-process call
(3.2 A), replace the window (3.3 A), no drag-out (3.4). It reuses every existing security boundary (nonce, bearer,
Host/Origin, `_vet`) and touches no launcher code, so the new attack surface is one route and one extra field in an
existing response.

### 4.1 Behaviour specification (what "done" looks like to the user)

**Popup**
1. Double-click on a **file or folder row of a files-mode search** → the full page opens (Edge app window, same as F11)
   with: the text that produced the rows (`self.last_text`), the popup's chips, date filter and sort, and that row
   selected with the preview pane open. The popup closes as it does after F11 today. In a folder listing (Ctrl+F view)
   or on Ask-notes rows, a double-click keeps today's behaviour (selects only). A double-click that started a drag does
   nothing.
2. F11 / "Full page" also sends chips, date and sort, and the selected row when results are showing (one code path;
   assumption A3).
3. Click selects one row (as today). Ctrl+click toggles a row in the marked set; Shift+click marks the range from the
   anchor; Ctrl+A marks every row shown (gated like Ctrl+C today; a second Ctrl+A when all are already marked falls
   through to the entry, so it still selects the query text). Shift+Up/Down extends. Marked rows draw with a visible
   fill distinct from the cursor row. Any new search, folder listing enter/leave, or Escape out of results clears the
   marks.
4. Ctrl+C = Copy files, Ctrl+X = Cut files, Ctrl+Shift+C = Copy path (text, all marked rows, newline-separated as the
   full page does) — subject to user decision 1. The targets are the marked rows, or the cursor row when none are
   marked. Same `_row_keys_live()` gate as today, so Ctrl+C/X with text selected in the entry still edit text.
5. Right-click menu gains "Copy" and "Cut" at the top (accelerators from the chords). With several rows marked they act
   on all marked rows and the label says so ("Copy 3 files"); the other verbs keep acting on the clicked row. Copy and
   Cut are disabled on note rows.
6. The details line reports the result: "Copied 3 files — paste in File Explorer", "Cut 3 files — paste in File
   Explorer to move them", or the refusal ("2 copied; 1 skipped: protected file").

**Full page**
7. On a launch with a handoff: state starts from `query/chips/modified/sort`; the first search runs; the row whose path
   equals `select_path` (case-insensitive, `\`-normalised) becomes the only selected and active row, the pane opens,
   the row scrolls into view. If it is not in the first page, the page loads further pages up to 300 rows total; if
   still absent, it keeps no selection and shows a toast "The file you picked is not in these results."
8. Ctrl+A selects all loaded rows (same `rowLive` gate as Ctrl+C; second press falls through to the input). Ctrl+C =
   Copy files, Ctrl+X = Cut files, Ctrl+Shift+C = Copy path(s) as text (existing `copyPaths`). Context menu gains "Copy"
   and "Cut" above "Copy path", acting on `targetsFor(row)`. Ctrl+right-click keeps copying paths. Double-click still
   opens.
9. Copy/Cut call `POST /v1/files/clipboard` and toast the result (counts and skipped reasons).

### 4.2 API contract (D1 builds it, D3 consumes it)

- `POST /v1/session/exchange` → `{"token": "...", "handoff": {...}}`; `handoff` present only when the nonce was minted
  with one. Shape: `{"query": str<=1000, "chips": [str in CHIPS], "modified": str (valid_modified), "sort": str in
  {"", "name", "ext", "date"}, "select_path": str<=32767 or null}`. Validated at mint time; invalid input raises
  `ValueError` in the app, never reaches the page. The exchange body cap stays 1024 bytes (request side only).
- `POST /v1/fullpage/open` body may additionally carry `chips`, `modified`, `sort`, `select_path` (same validation;
  422 on bad values); this is what lets QA drive the real nonce + handoff path through Edge app mode.
- `POST /v1/files/clipboard` (bearer + Host/Origin via the existing middleware; registered in `routes_actions.py`):
  body `{"paths": [str], "op": "copy" | "cut"}`, max body 256 KiB, 1 to 1000 paths.
  - 200 `{"ok": true, "op": "copy", "count": n, "skipped": [{"path": p, "reason": "refused: ..."}]}` when at least one
    path was placed on the clipboard.
  - 403 `refused` when every path is refused; 422 `invalid_parameter` for shape errors (`field` = `paths` or `op`);
    413 over the size cap; 500 `error` when Windows will not open the clipboard; 503 when the index is not ready.
- In-process: `shell.clipboard_files(paths: list, op: str, *, index) -> dict` (same result dict as above), used by the
  route and by the popup through `app._on_clipboard(paths, op)`. One `action_log` line per call: verb `copy_files` /
  `cut_files`, path field = `"<n> paths"` (no list of paths in the log).

### 4.3 Path rules for the clipboard (security)

For each path, in order: must be `str`, non-empty, no NUL, length ≤ 32767; **refuse raw prefixes `\\?\`, `\\.\`,
`//?/`, `//./` before any normalisation**; then `shell._vet(index, path, protected_ok=False, not_root=(op == "cut"))`
— absolute, resolves inside an indexed root (UNC only if a root is UNC), not inside Homa's home, not on the secrets
deny-list (given or resolved path), exists, in the index. For `cut`, the path placed on the clipboard is the one
`_link_or_target` picks (the link itself when the row was a link, as rename/recycle do); for `copy`, the resolved path.
Duplicates are dropped. No shell execution, no file I/O beyond the `_vet` checks; the only side effect is the clipboard.

## 5. Work breakdown (dispatches)

Every dispatch cites this plan and loads `fable-parity`. Agents never touch the user's live instance (pythonw on port
17171), the live checkout `C:\AI Projects\Assistant`, the real `~/.homa`, or real `settings.json`; all runs use the test
harness (`launch_app` in `tests/conftest.py`: private `HOMA_HOME`, free port, `HOMA_TEST_MODE=1`). No employer or personal
identifiers anywhere (project CLAUDE.md); paths in docs and tests use placeholders.

| Step | Role | Depends on | Parallel with |
|---|---|---|---|
| P0 | git-keeper | `rename-homa` merged to `main` (rename plan R8/R9) | – |
| D1 | dev (backend) | P0 | – |
| D2 | dev (popup) | D1 | D3 |
| D3 | ui (full page + rebuild) | D1 | D2 |
| D4 | qa | D2 and D3; **and** the other qa task currently editing `tests/` in the rename worktree has finished | – |
| D5 | security | D4 pass | – |
| D6 | git-keeper | D4 + D5 pass | – |
| D7 | doc-keeper | D6 | – |

### Touchpoint table

| File | Owner | Change |
|---|---|---|
| `homa/server/auth.py` | D1 | `mint_nonce(handoff: Optional[dict] = None)`; store `(expiry, handoff)`; new `take_nonce(candidate) -> tuple[bool, Optional[dict]]` used by `_exchange`; `consume_nonce` keeps its bool contract; exchange response adds `handoff` when present. |
| `homa/actions/shell.py` | D1 | `dropfiles_bytes(paths) -> bytes` (pure); `_set_clipboard_files(paths, effect)` (Open/Empty/Set CF_HDROP + Preferred DropEffect/Close, same retry loop as `_set_clipboard`); `clipboard_files(paths, op, *, index)` applying section 4.3; `MAX_CLIPBOARD_PATHS = 1000`; action-log lines. |
| `homa/server/routes_actions.py` | D1 | `POST /v1/files/clipboard` handler (body cap 256 KiB, shape checks, `run_in_threadpool(clipboard_files, ...)`, status mapping of 4.2); route appended to `routes`. |
| `homa/server/routes_files.py` | D1 | `fullpage_open` accepts the optional handoff fields, validates, calls `opener(query, handoff)`. |
| `homa/settings.py` | D1 | `DEFAULT_SHORTCUTS`: `copy_files: "Ctrl+C"`, `cut_files: "Ctrl+X"`, `copy_path: "Ctrl+Shift+C"`, `select_all: "Ctrl+A"`. Migration in `_merged`: when the loaded `shortcuts` has no `copy_files` key and `copy_path` equals `"Ctrl+C"` (case-insensitive), set `copy_path` to `"Ctrl+Shift+C"`; a user-chosen `copy_path` is left alone; if a user-chosen chord collides with a new default, the user's chord wins and the new action falls back to having no chord (log one line). (Subject to user decision 1.) |
| `homa/overlay/settings_ui.py` | D1 | `SHORTCUT_LABELS` gains Copy files, Cut files, Select all; Copy path label unchanged. |
| `homa/app.py` | D1 then D2 (serial) | D1: `_on_fullpage(query, handoff=None)`, `_open_fullpage(query, handoff=None)` passing `lambda: auth.mint_nonce(handoff)`; new `_on_clipboard(paths, op) -> dict` calling `shell.clipboard_files`. D2: pass `on_clipboard=self._on_clipboard` to `SearchPopup`. |
| `homa/overlay/search_ui.py` | D2 | Marked set + anchor; modifier-aware `click`; `<Double-Button-1>` on the canvas → handoff + `on_fullpage(query, handoff)`; F11 sends the handoff; key bindings `copy_files`, `cut_files`, `copy_path` (new default), `select_all`, Shift+Up/Down extend; `MENU_ITEMS` "Copy"/"Cut"; `_draw_row` marked fill; `debug_state` adds `marked` (sorted indices) and `marked_count`; new constructor kwarg `on_clipboard` (default `None` → Copy/Cut disabled). |
| `web/src/api.js` | D3 | `exchangeNonce` keeps `body.handoff`; `boot()` returns `{status, q, handoff}`; `copyFiles(paths, op)` → `POST /v1/files/clipboard`. |
| `web/src/App.jsx` | D3 | `Workspace` takes `initialHandoff`; initial state from it; post-search select-by-path with paging to 300 rows; Ctrl+A; `copy_files` / `cut_files` chords and `perform` verbs; toasts. |
| `web/src/shortcuts.js` | D3 | Same four `DEFAULT_SHORTCUTS` changes as `settings.py`. |
| `web/src/components/ContextMenu.jsx` | D3 | "Copy" and "Cut" items (local scope marker), count label when several are selected (new prop). |
| `web/src/styles.css` | D3 | Only if needed for the new menu items. |
| `homa/static/app/**` | D3 | Rebuilt committed bundle (`npm run build` with `HOMA_WEB_OUT` pointing at `homa/static/app`, after `npm ci` in `web/`). |
| `tests/test_clipboard_files.py`, `tests/test_launch_handoff.py`, `tests/test_shortcuts_migration.py`, `tests/e2e/test_popup_multiselect.py`, `tests/e2e/test_fullpage_clipboard.py`, `tests/e2e/clip_driver.cjs` | D4 | New files (section 6). Existing test files only where an assertion encodes the old Ctrl+C = copy path default (QA lists each one it changes). |
| `docs/security/2026-11-09-clipboard-handoff-review.md` | D5 | New review. |
| `README.md`, `CHANGELOG.md`, `docs/api.md` | D7 | Append/update (section 5, D7). |

### P0 — git-keeper: branch and worktree

- Goal: an isolated place to build on top of the merged rename.
- Precondition (check, do not assume): `git -C "C:\AI Projects\Assistant" log main --oneline` shows the rename commit(s)
  and `git branch --merged main` lists `rename-homa`. If not, stop and report to tech-lead.
- Scope: git metadata only. Branch `feat/popup-handoff-file-clipboard` from `main`, in a new worktree
  `C:\AI Projects\Assistant-clip` with its own `.venv` set up the way the rename plan's section 3 describes (the live
  instance imports lazily from the live checkout, so the live tree must not change under it).
- Done: `git worktree list` shows the new path on the new branch; `git status` clean.

### D1 — dev: backend (auth handoff, clipboard writer, route, shortcuts, app wiring)

- Goal: the full contract of section 4.2 and the path rules of 4.3 work in-process and over HTTP.
- Scope (exact): `homa/server/auth.py`, `homa/actions/shell.py`, `homa/server/routes_actions.py`,
  `homa/server/routes_files.py`, `homa/settings.py`, `homa/overlay/settings_ui.py`, `homa/app.py` (D1 part of the
  touchpoint table only). No `tests/`, no `web/`, no `search_ui.py`.
- Done criteria (dev shows the output of each, run in the worktree against a test-mode instance it starts itself with a
  temp `HOMA_HOME` and a free port — figure the launch out from `tests/conftest.py` `launch_app`):
  1. A scratchpad script mints a nonce with a handoff, exchanges it once (200, token + identical handoff), a second
     exchange of the same nonce returns 403.
  2. `POST /v1/files/clipboard` with two fixture files and `op=copy` returns 200 `count: 2`; reading the clipboard back
     with ctypes `DragQueryFileW` lists both paths and "Preferred DropEffect" reads 1; same with `op=cut` reads 2; the
     files are still present afterwards.
  3. Refusals return the statuses of 4.2 for: a `\\?\` path, a `\\.\` path, a deny-listed fixture (`.env`), a path in
     the test `HOMA_HOME`, a path outside the roots, a missing file, 1001 paths, `op=move`, no bearer (401), a bad
     Origin (403).
  4. A settings file with `"shortcuts": {"copy_path": "Ctrl+C"}` loads as `copy_path = Ctrl+Shift+C`,
     `copy_files = Ctrl+C`; one with `"copy_path": "Ctrl+Alt+P"` keeps it.
  5. `python -m pytest -q tests` (the existing suite) still passes, apart from tests that assert the old Ctrl+C
     default — list them by name for QA; do not edit them.
- Hand-off: qa (via tech-lead; D4 runs after D2 and D3).

### D2 — dev: popup (double-click handoff, multi-select, Copy/Cut)

- Goal: behaviours 1–6 of section 4.1 in the Tk popup.
- Scope (exact): `homa/overlay/search_ui.py`; in `homa/app.py` only the `on_clipboard=self._on_clipboard` argument.
- Notes for the implementer: Tk delivers Press, Release, Double-Press, Release, so the double-click handler must not
  fight `_press`/`_release` (check `self._drag["moved"]`); read Ctrl (`event.state & 0x4`) and Shift (`& 0x1`) in
  `_release` and pass them to `click`; keys are bound on `self.entry`, so a handler that returns `""` lets Tk's Entry
  class binding (text select-all, cut, copy) run.
- Done criteria: in a test-mode instance (`HOMA_TEST_MODE=1`, temp home, free port), using `/v1/_debug/ui` and the
  existing in-process popup pattern of `tests/e2e/test_search_popup.py` as a model (scratchpad script, not a test
  file): show the snapshot after Ctrl+click of rows 0 and 2 (`marked: [0, 2]`), Shift+click to row 4
  (`marked: [0..4]` from the anchor), Ctrl+A (all), Ctrl+C (`last_action` / details "Copied 5 files" and a CF_HDROP
  read-back), a double-click on row 1 (the `on_fullpage` callback received `query`, `chips`, `modified`, `sort` and that
  row's path — capture via a stub, do not launch Edge from the script). Plus a screenshot of the popup with marked rows
  and of the right-click menu showing Copy/Cut (`tools/driver.py` grab, saved to the scratchpad).
- Hand-off: qa.

### D3 — ui: full page (handoff selection, Ctrl+A, Copy/Cut) and rebuild

- Goal: behaviours 7–9 of section 4.1 in the React page, and the committed bundle rebuilt.
- Scope (exact): `web/src/api.js`, `web/src/App.jsx`, `web/src/shortcuts.js`, `web/src/components/ContextMenu.jsx`,
  `web/src/styles.css` (only if needed), `homa/static/app/**` (rebuild output). No Python.
- Done criteria: Chrome DevTools / headless Edge run against a test-mode instance with D1 in place (temp home, free
  port): a launch whose exchange returns a handoff for a row that is on page 2 ends with that row `aria-selected`,
  active, scrolled into view and the preview pane showing; chips, date and sort controls show the handed-off values;
  Ctrl+click 3 rows + Ctrl+C sends one `POST /v1/files/clipboard` with those 3 paths and `op: "copy"` and shows the
  toast; Ctrl+X sends `op: "cut"`; the context menu shows Copy and Cut with the right chords; Ctrl+A selects all loaded
  rows and a second Ctrl+A in the input selects its text; Ctrl+Shift+C copies paths as text; no console errors.
  `git status` shows a changed `homa/static/app/` and only the scoped `web/src` files.
- Hand-off: qa.

### D4 — qa: tests and verdict

- Goal: prove sections 4.1–4.3 by observation and gate the merge.
- Scope: new files listed in the touchpoint table; existing tests only where they encode the old Ctrl+C default (list
  each). Never the live instance, never the real home or clipboard history beyond what the tests restore.
- Tests to write: section 6. Run the whole suite plus the new files; paste the commands and their output.
- Hand-off: security (D5), then git-keeper.

### D5 — security: review

- Change classes: authentication flow (nonce payload, exchange response), user-supplied paths (clipboard route,
  `fullpage/open` body), a new local endpoint. Review against SEC-2/3/4/6 of
  `docs/prds/2026-11-06-assistant-v1.md` and section 4.3 here. Specific questions: can any web origin reach
  `/v1/files/clipboard` (Origin/Host rules, CORS preflight); can the handoff ever be read twice or by a page other than
  the one launched; can a path escape `_vet` via `\\?\`, 8.3 short names, junctions, trailing dots/spaces, or a UNC
  path when no root is UNC; is Cut ever able to target an indexed root, Homa's home, or a deny-listed file.
- Writes: `docs/security/2026-11-09-clipboard-handoff-review.md`. Hand-off: git-keeper (merge needs qa + security pass).

### D6 — git-keeper: merge

- Commit on `feat/popup-handoff-file-clipboard`, merge to `main` after both verdicts (ff-only where possible). Then tell
  tech-lead that the user must restart Homa to pick it up (agents do not restart the live instance).

### D7 — doc-keeper: docs

- `README.md`: "Search files" (double-click, multi-select, Copy/Cut), "Full page and previews" (handoff, Ctrl+A,
  Copy/Cut; correct the "page has no Ctrl+C" sentence in a dated note, without deleting the old text), "Actions on a
  file" table (Copy, Cut, Copy path on Ctrl+Shift+C, Select all), "Features", "Security & privacy" (clipboard rules of
  4.3, drag-out not offered), "Settings" (the shortcut migration).
- `docs/api.md`: new `## 15. Popup handoff and file clipboard, 2026-11-09` (exchange `handoff`, `fullpage/open` fields,
  `/v1/files/clipboard`). `CHANGELOG.md`: dated entry. The in-app shortcut list is code (`SHORTCUT_LABELS`, D1).

## 6. Verification strategy

| Test file (D4) | What it proves | How |
|---|---|---|
| `tests/test_clipboard_files.py` | DROPFILES layout; real clipboard round-trip; every refusal of 4.3; caps; no file moved or deleted | `dropfiles_bytes` byte-for-byte (header `20,0,0,0,1`, UTF-16LE, double NUL). Round-trip: `shell.clipboard_files` on a fixture tree + fixture index (pattern of `tests/test_actions_shell.py`), then ctypes `OpenClipboard(None)` / `GetClipboardData(15)` / `DragQueryFileW` and `RegisterClipboardFormatW("Preferred DropEffect")` → DWORD 1 (copy) / 2 (cut). Save the clipboard's text before and restore it after (best effort). Refusals: `\\?\`, `\\.\`, `//./`, ADS (`file.txt:x`), `.env` fixture, path in temp `HOMA_HOME`, outside roots, missing, not indexed (excluded folder), root folder with `cut`, 1001 paths, mixed list (skips reported, rest placed). After `cut`, every file still exists. |
| `tests/test_launch_handoff.py` | Handoff is one-shot, expires, validates, never in the URL | `mint_nonce(handoff)` + `take_nonce` / HTTP exchange via a test-mode app: 200 with the handoff once, 403 after; TTL via the module clock; bad chips/sort/modified/oversized query raise; `fullpage.build_url` output contains no `select_path`; `POST /v1/fullpage/open` with a bad `sort` → 422; HTTP route tests for `/v1/files/clipboard`: 401 without bearer, 403 bad Origin/Host, 413, 422 shapes, 403 all-refused, 200 partial. |
| `tests/test_shortcuts_migration.py` | Old stored default moves to Ctrl+Shift+C; custom chords are kept; collisions resolved as specified | Settings loaded from temp `HOMA_HOME` files only. |
| `tests/e2e/test_popup_multiselect.py` | Popup behaviours 1–6 | In-process Tk popup (pattern of `tests/e2e/test_search_popup.py`): generate Ctrl/Shift clicks and `<Double-Button-1>` with `event_generate`; stub `on_fullpage` and assert the handoff dict; stub-free Ctrl+C with a real `on_clipboard` bound to the fixture index and a CF_HDROP read-back; folder-listing and notes-mode double-click do not call `on_fullpage`; menu items present and disabled on notes rows. |
| `tests/e2e/test_fullpage_clipboard.py` + `clip_driver.cjs` | Full page 7–9, headless | playwright-core headless Edge (pattern of `test_fullpage_m43.py`, same `HOMA_QA_PLAYWRIGHT_DIR` skip rule) against a test-mode app serving a fresh build. The exchange is mocked to return the real token **plus** a handoff whose `select_path` is a fixture file on the second page; assert selection, pane, filters. Multi-select + Ctrl+C / Ctrl+X / menu Copy / Cut let `/v1/files/clipboard` reach the real server; the Python side then reads CF_HDROP and asserts the paths and effect. Ctrl+A, second Ctrl+A, Ctrl+Shift+C text. |
| (manual, headed) Edge app mode | Real nonce → handoff path end to end | `POST /v1/fullpage/open` with `select_path` on the test-mode app (pattern of `tests/e2e/test_fullpage.py` layer 3), screenshot shows the selected row and preview. Can be a test that skips without a desktop session. |

What cannot be automated, and how the risk is contained: **Explorer actually pasting and moving.** Contained by (a) the
CF_HDROP + Preferred DropEffect bytes being exactly what Explorer's own Copy/Cut writes (checked by round-trip), and
(b) a user-run acceptance step on the personal laptop after D6, using throw-away files: Copy 3 files in the popup →
paste in a temp folder → 3 copies; Cut 2 files in the full page → paste → files moved, originals gone, Homa's index
updates. Work-laptop results are user-run and pasted; never claimed otherwise.

### Acceptance checks (verifiers accept against these)

- AC-H1 Double-click on a files-mode popup row opens the full page with the same query, chips, date, sort, that row
  selected and its preview visible; popup closes. Folder-listing and notes rows: no change.
- AC-H2 No path appears in the launch URL; the handoff is readable once, by the exchange only, within 30 s.
- AC-H3 Enter in the popup still opens the selected file; full-page double-click still opens.
- AC-M1 Popup: Ctrl+click, Shift+click, Shift+Arrow, Ctrl+A mark rows visibly; new search clears marks.
- AC-M2 Full page: Ctrl+A added; existing multi-select unchanged.
- AC-C1 Ctrl+C / menu Copy puts the marked files on the clipboard as CF_HDROP with Preferred DropEffect 1; Ctrl+X /
  menu Cut with 2; both UIs.
- AC-C2 Ctrl+Shift+C copies paths as text in both UIs; an install whose settings hold the old `copy_path: Ctrl+C`
  migrates.
- AC-C3 Refusals of section 4.3 hold over HTTP and in-process; partial success reports skipped paths; Homa never moves,
  deletes or opens a file during Copy/Cut.
- AC-C4 User-run: paste in Explorer copies / moves as expected.
- AC-S1 Security review passes; README, api.md, CHANGELOG updated; in-app shortcut list shows the new chords.

## 7. Risks and rollback

| # | Risk | Mitigation / rollback |
|---|---|---|
| R1 | Changing Ctrl+C surprises the user's muscle memory. | User decision 1; the migration is one function; reverting is one default + one line. |
| R2 | `SetClipboardData` with no owner window fails on some Windows builds (MS docs warn that `OpenClipboard(NULL)` + `EmptyClipboard` leaves no owner). The existing text copy already works this way, so it is expected to be fine. | QA round-trip catches it. Fallback (only if observed): run the write through `uibus.submit` on the Tk thread and open the clipboard with the root window's handle. |
| R3 | Explorer ignores the drop effect for some targets (e.g., cross-volume Cut becomes copy+delete prompt, or a cloud folder). | That is Explorer's own behaviour, identical to its own Cut; documented in the README. Homa never deletes. |
| R4 | QA's clipboard tests overwrite whatever the user had on the clipboard on the dev laptop. | Tests save and restore the clipboard text; note it in the test docstring. Not run on the work laptop by agents. |
| R5 | The row picked in the popup is not in the full page's results (index changed, different page size). | Paging to 300 rows, then a toast and no selection. |
| R6 | Tk double-click interferes with click/drag handling (double toggle of marks, accidental drag). | Double-click handler ignores moved drags and Ctrl/Shift; covered by D4 popup tests. |
| R7 | A large selection (1000 folders) produces a big clipboard payload. | Cap of 1000 paths and 256 KiB body; Explorer copes with far more. |
| R8 | The handoff widens the exchange response. | Validated at mint, one-shot, same TTL; reviewed in D5. Rollback: mint without a handoff (the page ignores a missing field). |
| R9 | Work starts before the rename merges and conflicts with it. | Ordering gate at the top; P0 checks it. |
| Rollback (whole feature) | | Revert the merge commit on `main`; no data migration besides the `copy_path` chord written to `settings.json`, which the user can set back in the file (or a follow-up restores it). |

## 8. Assumptions

- A1 "Halfway mode" = the Tk search popup. Confirmed by the code (section 2).
- A2 Double-click on popup rows has no current action to replace (confirmed); Enter keeps opening the file.
- A3 F11 / "Full page" also carries chips, date, sort and the selected row (same code path as double-click).
- A4 A new full page replaces any open one (existing behaviour).
- A5 Copy/Cut are refused for deny-listed (protected) files even though Copy path works on them: a file on the clipboard
  can be pasted into a mail or chat, a path cannot.
- A6 Mixed selections succeed partially and report what was skipped.
- A7 Cap: 1000 paths per Copy/Cut. Ctrl+A in the full page selects the loaded rows only.

## 9. User decisions (short list)

1. **Ctrl+C becomes Copy files; Copy path moves to Ctrl+Shift+C** (existing installs migrate). Alternative: keep Ctrl+C
   = Copy path and put Copy files on Ctrl+Shift+C. Plan defaults to the first.
2. Protected (deny-listed) files: refuse Copy/Cut as files (default, A5) or allow like Copy path.
3. A selection with some refused files: copy the rest and report (default, A6) or refuse the whole operation.
4. Drag-out to Explorer: deferred (section 3.4). Say if you want a popup-only drag spike later.

## 10. Open questions

- None blocking. Decisions 1–3 can be answered before D1 starts; if unanswered, D1 builds the defaults.
