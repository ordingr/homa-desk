import { CloseIcon } from "./Icons.jsx";

export default function SavedPanel({ items, onRun, onDelete }) {
  return (
    <section className="saved" aria-label="Saved searches" data-testid="saved-panel">
      {items.length === 0 ? (
        <p className="dim">No saved searches yet. Run a search, then choose Save search.</p>
      ) : (
        <ul>
          {items.map((item) => (
            <li key={item.name}>
              <button type="button" className="saved-run" onClick={() => onRun(item)}>
                <strong>{item.name}</strong>
                <span className="dim">
                  {item.query}
                  {item.chips && item.chips.length ? `  |  ${item.chips.join(", ")}` : ""}
                  {item.modified ? `  |  ${item.modified}` : ""}
                  {item.sort && item.sort !== "best" ? `  |  by ${item.sort}` : ""}
                </span>
              </button>
              <button
                type="button"
                className="icon-btn"
                aria-label={`Delete saved search ${item.name}`}
                onClick={() => onDelete(item)}
              >
                <CloseIcon />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
