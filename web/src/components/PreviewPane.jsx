import { fmtDateTime, fmtSize, plainPath } from "../format.js";
import Preview from "../preview/Preview.jsx";
import { CloseIcon, CopyIcon } from "./Icons.jsx";

export default function PreviewPane({ row, extraSelected, onCopy, onClose }) {
  return (
    <aside className="pane" aria-label="Preview" data-testid="pane">
      <div className="pane-head">
        <div className="pane-title">
          <strong title={row.name}>{row.name}</strong>
          <span className="dim" title={plainPath(row.path)}>
            {plainPath(row.path)}
          </span>
          <span className="dim num">
            {row.is_dir ? "Folder" : fmtSize(row.size)} | {fmtDateTime(row.mtime)}
          </span>
        </div>
        <div className="pane-actions">
          <button
            type="button"
            className="icon-btn"
            aria-label="Copy path"
            title="Copy path"
            onClick={() => onCopy(row)}
            data-testid="pane-copy"
          >
            <CopyIcon />
          </button>
          <button
            type="button"
            className="icon-btn"
            aria-label="Close preview"
            title="Close preview"
            onClick={onClose}
            data-testid="pane-close"
          >
            <CloseIcon />
          </button>
        </div>
      </div>
      {extraSelected > 0 && (
        <p className="dim pane-note">+ {extraSelected} more selected</p>
      )}
      <div className="pane-body" data-testid="pane-body">
        {row.is_dir ? (
          <p className="dim">Folders have no preview.</p>
        ) : (
          <Preview key={row.path} row={row} />
        )}
      </div>
    </aside>
  );
}
