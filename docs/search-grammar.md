# Files search grammar, as shipped in M3a

- Date: 2026-11-07
- Written by: doc-keeper (M3a-10, TeamAgents), from the code as read on 2026-11-07:
  `assistant/index/query.py` (parser), `assistant/index/search.py` (executor and caps), `assistant/index/rank.py`
  (matchers and tiers), `assistant/server/routes_search.py` (`KNOWN_EXTS`, HTTP parameters), `assistant/server/mcp.py`
  (`search_files`), `assistant/search/modes.py` and `assistant/search/notes_bm25.py` (Ask notes, `?` prefix).
- Requirements: PRD section 6.4 (AC-FI-8 to AC-FI-15, with AC-FI-12 naming the five reference examples), DEC 5, US-FI-2;
  plan section 7.3, task M3a-3 (done criteria) and M3a-10 (this document).
- Verification: the examples and interpretation strings below were traced by reading the parser and `_describe`. No run
  of this document's examples is recorded here. The executable record is `tests/test_query_grammar.py`, run by qa under
  M3a-8. The qa result is not in the brief for this entry: qa (full M3a pass, 2026-11-07): all 15 rows of the section 8 interpretation table were run through the real parser and executor and matched exactly; suite 711 passed non-e2e..
- Security review of the parser: Review 7, `docs/security/2026-11-07-m3a-review.md` (query parsing section: 100,000-character
  query truncated in 0.05 ms; 5,000 tokens reduced to 32; hostile quote and `!` runs parsed without error).
- Supersedes: nothing. First reference for the grammar.

This file describes what the code does. Where the PRD and the code differ, the difference is listed in section 9.

---

## 1. Where the grammar applies

- **Files mode** (the popup's default mode, `GET /v1/search`, and the MCP tool `search_files`) uses this grammar.
- **Ask notes** does not. A query that starts with `?` (after optional spaces), or a query typed after pressing Tab into
  Ask notes, is sent to the notes search, which takes plain words and ignores operators. Punctuation such as `-`, `!`, `"`
  and `:` is dropped, words are stemmed, and a short stop list is removed. Ask notes ranks passages with BM25.
- Plain text matches file names case-insensitively. Nothing in the grammar uses regular expressions.

## 2. Limits

| Limit | Value | What happens when it is exceeded | Source |
|---|---|---|---|
| Query length | 512 characters | The text is cut at 512. The response carries the note `query truncated to 512 characters`. | `query.py` `MAX_QUERY_LEN` |
| Tokens | 32 | Later tokens are dropped. Note: `only the first 32 words were used`. | `query.py` `MAX_TOKENS` |
| Result rows, HTTP | `limit` default 50, clamped to 500 | A larger `limit` is clamped to 500 without an error. A non-integer or negative `limit` returns 422. | `routes_search.py` `MAX_LIMIT` |
| Result rows, MCP | `limit` 1 to 500 | Outside that range the tool returns an error (`limit: must be between 1 and 500`). | `mcp.py` `SEARCH_MAX_LIMIT` |
| Offset | 0 to 100,000 | HTTP clamps a larger offset to 100,000. MCP returns an error. | `routes_search.py`, `mcp.py` |
| Candidates for a single plain term | 100,000 | The scan stops early and `truncated` is `true`. `total` then counts only the candidates examined. | `search.py` `DENSE_CAP` |
| Empty query or a query with no literal text (`*`, `?`) | newest 10,000 entries, sorted by date | `truncated` is `true`. `sort=best` changes to `date` silently. | `search.py` `NEWEST_CAP` |
| Time budget | 0.4 s wall clock | The scan stops at the next check and `truncated` is `true`. | `search.py` `TIME_BUDGET_S` |
| Saved search query | 500 characters | Cut to 500 when saved. | `search/saved.py` `MAX_QUERY` |

Caps were added after Review 7 found full-scan queries costing 2.5 to 7 s on a synthetic 2,000,000-entry index (R7-F2).
The fix is recorded as fixed 2026-11-07 (M3a-11). Security has not yet re-checked it in `docs/security/`.

> **Correction, 2026-11-07 (M3b):** the re-check is recorded in `docs/security/2026-11-07-m3a-review.md`, section "Re-check after M3a-11/12 (2026-11-07)": PASS, with R7-F1 and R7-F2 closed by observation. The sentence above is kept as written.

## 3. Tokens

The query is split on spaces. These forms are recognised, in this order:

| Form | Example | Meaning |
|---|---|---|
| Quoted phrase | `"quoted phrase"` | A literal substring, spaces included. Wildcards inside quotes are not special. An unclosed quote runs to the end of the query. An empty pair `""` is ignored. |
| Negation | `!draft` or `!"big file"` | Excludes any result whose full path contains the word or phrase. Only `!` directly followed by a non-space character counts. |
| Extension flag | `-e yml`, `-e md,py` | Extension filter. The value is split on commas, semicolons and spaces, a leading `*.` or `.` is removed, and case is ignored. Consumes the next token. |
| Path flag | `-p Documents` | The full path must contain the value. Consumes the next token. |
| Long extension form | `ext:md,py` | Same as `-e md,py`. |
| Long path form | `path:docs`, `path:"my docs"` | Same as `-p`. A quoted value stays one token. |
| Bare known extension | `design md` | Extension filter, but only when the query has two or more tokens and the word is in the known list (section 5). |
| Any other word | `design`, `-x`, `md` alone | A plain term. |

Notes on the forms:

- `-e` or `-p` at the end of the query, or followed by a `!` token, is an ordinary plain term. `-e` alone is a literal name
  term that almost never matches.
- `-x foo` is two plain terms, `-x` and `foo`. A hyphen followed by anything other than `e` or `p` is never NOT. Use `!x`
  for negation.
- `.md` (with a dot) is a plain term, not an extension filter, because the bare-extension check compares the word
  as written against the list without dots. Use `md`, `-e md` or `ext:md`.
- `*.md` is a plain term with a wildcard. It matches any name that contains `.md`, including `notes.md.bak`. Use `md`
  for the extension filter.

## 4. Which term must match where

Evaluation, after tokenising:

1. **Name term.** The first plain term must match the file name (case-insensitively, as a substring or with wildcards).
   Folders are matched the same way. If there is no plain term but there is a quoted phrase, the first phrase is the name
   term.
2. **Path terms.** Each further plain term, and each quoted phrase when a plain term exists, must match the file name or
   the full path. This is what makes `design doc` find `…\docs\design-notes.md`: `design` matches the name, and `doc` matches
   the path.
   - Quoted phrases that come after a plain term are path terms. `"x y" design` requires `design` in the name and `x y`
     in the full path.
3. **Path flags** (`-p`, `path:`). Each one must be contained in the full path. Several flags are AND.
4. **Negations** (`!`). Each one is excluded if it is contained in the full path, so a word in a folder name also excludes.
5. **Extension filters** (`-e`, `ext:`, bare known extensions). A file matches if its extension is **any** of the listed
   values (OR). Two extension tokens such as `md py` therefore match `.md` and `.py` files. Folders never match an
   extension filter.
6. **Chips** (`docs`, `sheets`, `pdf`, `images`, `code`, `folders`). Several chips are OR. A chip is combined with the rest
   of the query by AND, so `design md` with the Code chip matches `.md` files only if they are also in a Code extension,
   which in practice returns nothing.
7. **Modified filter** (`today`, `7d`, `30d`). AND with everything else.

Matching is case-insensitive throughout (casefold).

## 5. Known extensions (bare-word filters)

A bare word becomes an extension filter only when it is in this list and the query has at least two tokens. The list is
fixed in the code and not configurable in this build.

> **Correction, 2026-11-07 (M3b):** the list is configurable from M3b-0 (f), as `index.known_exts`. See section 11.3. The sentence above is kept as written.

```
md markdown txt rtf pdf docx xls xlsx xlsm csv tsv ppt pptx odt ods json xml yml yaml toml ini cfg log
py js mjs ts tsx jsx html htm css scss java kt cs cpp c h go rs rb php sh ps1 bat sql
png jpg jpeg gif bmp svg webp ico zip
```

`doc` is deliberately not in the list. See section 6.

Any extension works with `-e` or `ext:`, even one that is not in the list. `-e doc`, `-e xyz` and `ext:odp` are all valid.

## 6. `doc` and `docx`

- `design docx` is an extension filter. It returns `.docx` files whose name contains `design`.
- `design doc` is **not** an extension filter. `doc` is a path term. It returns names or paths that contain `doc`, so a folder
  called `docs` or `Docs`, or a file called `design-doc.md`, matches. The parsed interpretation shows
  `name contains "design" · path contains "doc"`.
- To filter old Word files by extension, use `design -e doc`, `design ext:doc`, or the Docs chip. The Docs chip includes
  `.doc` and `.docx` and other document types. It is not the same as an exact extension match.

Why: the PRD's own example is `design doc`, which "narrows by folder, exactly as my old tool did" (US-FI-2). Treating `doc`
as an extension would have removed that behaviour. The trade-off is that someone who types `design doc` expecting Word
files gets folder and path matches. The escape hatches (`-e doc`, `ext:doc`, the Docs chip) are the documented way to ask
for Word files.

## 7. Ranking and wildcards

Each name term gets a tier. The best sort order uses the worst (largest) tier across the name term and the path terms,
then the sum of the tiers, then the shorter name, then the name.

| Tier | Number | Meaning |
|---|---|---|
| Exact | 0 | The match starts at the beginning and ends at the end of the name, or at the start of the extension. `design` is exact for `design.md`. |
| Prefix | 1 | The match starts at the beginning of the name. |
| Word | 2 | The match starts after a character that is not a letter or digit (space, `-`, `_`, `.`). |
| Substring | 3 | The match is inside the name. |
| Path | 4 | The term is found only in the full path. |

Sort options (`sort` in HTTP and MCP): `best` (the default, above), `name`, `ext`, `date` (newest first). The popup has no
sort control in M3a (see section 9).

Wildcards:

- `*` matches any run of characters, including none. `?` matches exactly one character.
- Patterns are **not anchored**. `rep*` finds every name that contains `rep`, the same as `rep`. `*.md` finds every name
  that contains `.md`. Use the extension filter when you mean an extension.
- In path matching, `*` and `?` also cross `\` and `/`. The path is compared with backslashes.
- A quoted phrase is literal. `"a*b"` looks for the characters `a*b`.
- Matching cost is linear in the length of the name. Pathological patterns were tested with 500 stars and 5,001-character
  names (Review 7).

## 8. Interpretation strings

The header of each result shows the interpretation. HTTP returns it as `interpretation`, and MCP as `interpretation`.
Parts are joined with ` · ` in this order: name terms, path terms, phrases after the first, path flags, extensions,
negations, then chips and the date filter.

| You type | Interpretation |
|---|---|
| `design md` | `name contains "design" · ext: md` |
| `design doc` | `name contains "design" · path contains "doc"` |
| `config -e yml` | `name contains "config" · ext: yml` |
| `report -p Documents` | `name contains "report" · path contains "Documents"` |
| `design` | `name contains "design"` |
| `"quoted phrase"` | `name contains "quoted phrase"` |
| `"x y" design` | `name contains "design" · path contains "x y"` |
| `design doc md` | `name contains "design" · path contains "doc" · ext: md` |
| `design md -p Documents` | `name contains "design" · path contains "Documents" · ext: md` |
| `ext:md,py` | `ext: md, py` |
| `path:docs` | `path contains "docs"` |
| `!draft` | `excluding "draft"` |
| `rep*` | `name matches "rep*"` (the word `matches` is used whenever a wildcard is present; the match is not anchored, see section 7) |
| `-x foo` | `name contains "-x" · path contains "foo"` |
| *(empty)* | `all files` |

With chips and a date filter, the extra parts are appended. A chip adds `type: Docs, PDF`. A date adds `modified: today`,
`modified: last 7 days`, `modified: last 30 days`, or `modified: since <date>` (the last form comes only from the MCP
`filters.modified_after` argument). When the query is empty, the extras stand alone, for example `type: Code`.

## 9. Differences from the PRD and the plan (recorded, not fixed)

1. **Known extensions are not configurable.** AC-FI-9 says the list is "configurable". It is a constant in
   `routes_search.py`.
2. **`doc` is absent from the known list.** Deliberate, to keep US-FI-2. See section 6. AC-FI-9 lists "common document"
   extensions as the seed, and `doc` is the one common Word extension left out.
3. **Sort buttons (AC-FI-13).** `sort` works over HTTP and MCP. The popup has no sort control and always uses `best`.
4. **Saved searches store `sort`.** The popup does not apply it on recall (AC-FI-16).
5. **Custom date ranges (AC-FI-15).** The popup and HTTP `modified` accept only `today`, `7d` and `30d`. The MCP
   `filters.modified_after` gives a lower bound only.
6. **MCP `filters.ext` values are not normalised the same way as the query.** `md` and `.md` work, because the executor
   removes a leading dot and casefolds. A value such as `*.md` matches nothing.
7. **Truncation notes are not shown everywhere.** HTTP returns a `notes` array. The popup does not display it, and the MCP
   result has no `notes` field, so a truncated query is silent over MCP.
8. **`sort=best` with an empty query** returns the newest 10,000 entries in date order. The response does not say the sort
   changed. Only `truncated: true` is shown.
9. **Ask notes** is not part of AC-FI-8 to AC-FI-12 and has no operators; see section 1.

> **Correction, 2026-11-07 (M3b):** items 1, 3 and 4 are closed (sections 11.2 and 11.3). Item 5 is closed for HTTP, MCP and the full page (section 11.1); the popup's date control still offers presets only. Item 6 is closed per plan 18.3 (h), not re-checked by doc-keeper. Item 7 is closed for the popup header and for MCP `search_files` (`notes`), per plan 18.3 (h) and the code docstring. Item 8 is not checked and is left as written. Items 2 and 9 stay as written. The items are kept as written above.

## 10. People and agents

- Requirement and examples: PRD by `product` (section 6.4, DEC 5).
- Parser, ranking, executor and caps: `dev` (M3a-3). Caps added after Review 7 (R7-F2): `dev`, M3a-11 (agent name not
  given in the brief).
- Tests: `qa` (M3a-8). Parser security review: `security` (M3a-9, Review 7).
- Documentation: `doc-keeper` (M3a-10).

---

## 11. M3b additions, as shipped (2026-11-07)

Supersedes: section 5 (the known-extension list is fixed and not configurable; see the correction there), section 4 item 7 (the date filter is presets only; section 11.1 adds a closed range), and section 9 items 1, 3, 4, 5 and 7 in part (the correction under section 9 says which). The earlier text is kept as written.

- Date: 2026-11-07. Written by doc-keeper (M3b-8, TeamAgents) from the code as read on 2026-11-07: `assistant/index/search.py` (`_date_filter`, `_day_range`, `_interpretation`), `assistant/search/modes.py` (`valid_modified`, `MODIFIED_CHOICES`, `SORT_CHOICES`), `assistant/search/saved.py`, `assistant/server/routes_search.py` (`known_exts`), `assistant/server/mcp.py` (`search_files` filters), `assistant/settings.py` (`index.known_exts`), `assistant/overlay/search_ui.py` (popup date and sort controls), `web/src/format.js`, `web/src/components/QueryBar.jsx`.
- Verification: traced by reading only. doc-keeper ran no tests. The tests that cover these items are in `tests/test_m3b_0.py` and the extensions of `tests/test_search.py`, `tests/test_routes_search.py`, `tests/test_mcp_files.py` and `tests/e2e/test_search_popup.py` (plan 18.3). Their results are not in the brief.

### 11.1 Custom date range

- Form: `YYYY-MM-DD..YYYY-MM-DD`. A closed range of local days, both ends inclusive. The first day must not be after the last.
- HTTP: `GET /v1/search?modified=2026-01-01..2026-01-31` and the `modified` field of a saved search. A malformed or reversed range returns `422 invalid_parameter` with `field: "modified"`.
- Interpretation: `modified: <first day> to <last day>` in the results header. The presets keep their own text (`modified: today`, `modified: last 7 days`, `modified: last 30 days`).
- MCP `search_files`: `filters.modified_after` and `filters.modified_before`, each an ISO date (`YYYY-MM-DD`). A `modified_before` date covers the whole of that day. Either bound alone reads `modified: since <day>` or `modified: until <day>`. The MCP `modified` argument keeps the presets only (section 11.5 of `docs/api.md`).
- Web page: two date inputs. When only one is filled, the page says `Pick both dates for a custom range.` and does not search.
- Popup: the date control (Ctrl+D) cycles the presets only. A range cannot be typed in the popup. A saved search that stores a range runs it, and the popup's label shows the stored text.

### 11.2 Sort values

- Values: `best` (default), `name`, `ext`, `date` (newest first). The same values are used by HTTP `sort`, MCP `sort` and the `sort` field of a saved search. An empty saved `sort` means `best`.
- Popup: a **Sort** control in the results header reads `Sort: Best`, `Sort: Name`, `Sort: Ext` or `Sort: Date`. Ctrl+S cycles it in that order. Recalling a saved search applies its stored sort.
- Web page: the sort control in the query bar, and Ctrl+S, which also fires for Ctrl+Shift+S in the page (`docs/api.md` section 12.9 item 7).

### 11.3 `index.known_exts`

- Replaces the built-in bare-word list of section 5 for HTTP, MCP and the popup. The default value is that list, with `doc` left out (section 6 still applies).
- Each entry is letters and digits only, at most 16 characters, without a leading dot. At most 200 entries. Anything else falls back to the default for that key, with a warning in `app.log`.
- Matching is case-insensitive (`query.parse` casefolds the list).
- The Search settings page edits the list as space-separated text.
- Adding `doc` to the list makes `design doc` an extension filter. That is the behaviour section 6 avoids by default. Adding it is a deliberate change of the US-FI-2 example.

### 11.4 `doc` and `docx`

Unchanged. `doc` is not in the default list, and section 6 stands. For Word files use `design -e doc`, `design ext:doc`, the Docs chip, or add `doc` to `index.known_exts` (section 11.3), knowing what that does to `design doc`.

### 11.5 Still open after M3b

- The popup has no range entry (section 11.1).
- Whether an empty query with `sort=best` reports the change to date order in `notes` was not checked (section 9 item 8 stands).
- Section 9 item 9 (Ask notes has no operators) is by design.
