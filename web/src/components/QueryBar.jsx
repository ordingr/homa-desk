import { useState } from "react";
import { SearchIcon, StarIcon } from "./Icons.jsx";

export const CHIPS = [
  ["docs", "Docs"],
  ["sheets", "Sheets"],
  ["pdf", "PDF"],
  ["images", "Images"],
  ["code", "Code"],
  ["folders", "Folders"],
];

export const SORTS = [
  ["best", "Best"],
  ["name", "Name"],
  ["ext", "Ext"],
  ["date", "Date"],
];

const MODIFIED = [
  ["", "Any time"],
  ["today", "Today"],
  ["7d", "Last 7 days"],
  ["30d", "Last 30 days"],
  ["custom", "Custom range"],
];

export default function QueryBar({
  inputRef,
  text,
  onText,
  onSubmit,
  chips,
  onToggleChip,
  modifiedKind,
  onModifiedKind,
  rangeFrom,
  rangeTo,
  onRange,
  sort,
  onSort,
  canSave,
  onSave,
  savedOpen,
  onToggleSaved,
  savedCount,
  ghost,
  onAcceptGhost,
  onDismissGhost,
}) {
  const [focused, setFocused] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveName, setSaveName] = useState("");

  const submitSave = (event) => {
    event.preventDefault();
    if (!saveName.trim()) return;
    onSave(saveName.trim());
    setSaving(false);
    setSaveName("");
  };

  return (
    <section className="querybar" aria-label="Search">
      <div className="inputrow">
        <div className="inputwrap">
          <SearchIcon className="inputicon" />
          <input
            ref={inputRef}
            type="text"
            className="queryinput"
            value={text}
            placeholder="Search files, e.g. design md"
            spellCheck={false}
            autoComplete="off"
            autoFocus
            onChange={(event) => onText(event.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            onKeyDown={(event) => {
              const input = event.currentTarget;
              const atEnd = input.selectionStart === input.value.length && input.selectionEnd === input.value.length;
              if (ghost && atEnd && !event.ctrlKey && !event.altKey && !event.metaKey) {
                if (event.key === "Tab" && !event.shiftKey) {
                  event.preventDefault();
                  event.stopPropagation();
                  onAcceptGhost();
                  return;
                }
                if (event.key === "Escape") {
                  event.preventDefault();
                  event.stopPropagation();
                  onDismissGhost();
                  return;
                }
              }
              if (event.key === "Enter" && !event.ctrlKey && !event.altKey) {
                event.preventDefault();
                event.stopPropagation();
                onSubmit();
              }
            }}
            data-testid="query"
          />
          {ghost && focused && (
            <div className="queryghost" aria-hidden="true" data-testid="query-ghost">
              <span className="queryghost-typed">{text}</span>
              <span className="queryghost-hint">{ghost}</span>
            </div>
          )}
        </div>
        <button type="button" className="btn primary" onClick={onSubmit} data-testid="search-btn">
          Search
        </button>
        <button
          type="button"
          className={`btn ${savedOpen ? "active" : ""}`}
          onClick={onToggleSaved}
          aria-expanded={savedOpen}
          data-testid="saved-toggle"
        >
          <StarIcon /> Saved{savedCount ? ` (${savedCount})` : ""}
        </button>
      </div>

      <div className="filterrow">
        <div className="chips" role="group" aria-label="Type filters">
          {CHIPS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`chip ${chips.includes(key) ? "on" : ""}`}
              aria-pressed={chips.includes(key)}
              onClick={() => onToggleChip(key)}
              data-testid={`chip-${key}`}
            >
              {label}
            </button>
          ))}
        </div>

        <label className="field">
          <span>Modified</span>
          <select
            value={modifiedKind}
            onChange={(event) => onModifiedKind(event.target.value)}
            data-testid="modified"
          >
            {MODIFIED.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {modifiedKind === "custom" && (
          <span className="range">
            <input
              type="date"
              value={rangeFrom}
              aria-label="Modified from"
              onChange={(event) => onRange(event.target.value, rangeTo)}
              data-testid="range-from"
            />
            <span className="dim">to</span>
            <input
              type="date"
              value={rangeTo}
              aria-label="Modified to"
              onChange={(event) => onRange(rangeFrom, event.target.value)}
              data-testid="range-to"
            />
          </span>
        )}

        <div className="segmented" role="group" aria-label="Sort">
          <span className="segmented-label">Sort</span>
          {SORTS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={sort === key ? "on" : ""}
              aria-pressed={sort === key}
              onClick={() => onSort(key)}
              data-testid={`sort-${key}`}
            >
              {label}
            </button>
          ))}
        </div>

        <span className="spacer" />
        {saving ? (
          <form className="savebox" onSubmit={submitSave}>
            <input
              type="text"
              autoFocus
              value={saveName}
              maxLength={80}
              placeholder="Name this search"
              onChange={(event) => setSaveName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.stopPropagation();
                  setSaving(false);
                }
              }}
              data-testid="save-name"
            />
            <button type="submit" className="btn primary" data-testid="save-confirm">
              Save
            </button>
          </form>
        ) : (
          <button
            type="button"
            className="btn"
            disabled={!canSave}
            onClick={() => setSaving(true)}
            data-testid="save-search"
          >
            Save search
          </button>
        )}
      </div>
    </section>
  );
}
