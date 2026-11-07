import { fmtDateTime, fmtSize, plainPath } from "../format.js";
import Preview from "../preview/Preview.jsx";
import { ZOOM_DEFAULT, ZOOM_STEPS, ZoomContext, stepZoom } from "../preview/zoom.js";
import {
  BackIcon,
  CloseIcon,
  CollapseIcon,
  CopyIcon,
  ExpandIcon,
  ZoomInIcon,
  ZoomOutIcon,
} from "./Icons.jsx";

export const EXPAND_HINT = "Ctrl+\\";

export default function PreviewPane({
  row,
  extraSelected,
  expanded,
  zoom,
  onZoom,
  onToggleExpand,
  onCopy,
  onClose,
}) {
  const onKeyDown = (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    if (event.key === "=" || event.key === "+") onZoom(stepZoom(zoom, 1));
    else if (event.key === "-" || event.key === "_") onZoom(stepZoom(zoom, -1));
    else if (event.key === "0") onZoom(ZOOM_DEFAULT);
    else return;
    event.preventDefault();
  };

  return (
    <aside
      className={`pane ${expanded ? "expanded" : ""}`}
      aria-label="Preview"
      data-testid="pane"
      data-zoom={zoom}
      tabIndex={-1}
      style={{ "--pv-zoom": zoom / 100 }}
      onKeyDown={onKeyDown}
    >
      <div className="pane-head">
        {expanded && (
          <button
            type="button"
            className="btn pane-back"
            title={`Back to results (Esc or ${EXPAND_HINT})`}
            onClick={onToggleExpand}
            data-testid="preview-back"
          >
            <BackIcon /> Back to results
          </button>
        )}
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
            aria-label="Zoom out"
            title="Zoom out (Ctrl+-)"
            disabled={zoom <= ZOOM_STEPS[0]}
            onClick={() => onZoom(stepZoom(zoom, -1))}
            data-testid="preview-zoom-out"
          >
            <ZoomOutIcon />
          </button>
          <button
            type="button"
            className="icon-btn zoom-reset num"
            aria-label="Reset zoom"
            title="Reset zoom (Ctrl+0)"
            onClick={() => onZoom(ZOOM_DEFAULT)}
            data-testid="preview-zoom-reset"
          >
            {zoom}%
          </button>
          <button
            type="button"
            className="icon-btn"
            aria-label="Zoom in"
            title="Zoom in (Ctrl+=)"
            disabled={zoom >= ZOOM_STEPS[ZOOM_STEPS.length - 1]}
            onClick={() => onZoom(stepZoom(zoom, 1))}
            data-testid="preview-zoom-in"
          >
            <ZoomInIcon />
          </button>
          <button
            type="button"
            className="icon-btn"
            aria-label={expanded ? "Back to results" : "Expand preview"}
            aria-pressed={expanded}
            title={expanded ? `Back to results (Esc or ${EXPAND_HINT})` : `Expand preview (${EXPAND_HINT})`}
            onClick={onToggleExpand}
            data-testid="preview-expand"
          >
            {expanded ? <CollapseIcon /> : <ExpandIcon />}
          </button>
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
          <ZoomContext.Provider value={zoom / 100}>
            <Preview key={row.path} row={row} />
          </ZoomContext.Provider>
        )}
      </div>
    </aside>
  );
}
