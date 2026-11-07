import { fmtDateTime, fmtSize, plainPath } from "../format.js";

export function Notice({ title, children, tone }) {
  return (
    <div className={`pv-notice${tone ? ` pv-${tone}` : ""}`} role="status">
      <strong>{title}</strong>
      {children && <p className="dim">{children}</p>}
    </div>
  );
}

export function MetaCard({ row, data, reason }) {
  const ext = (row.name.includes(".") ? row.name.split(".").pop() : "").toUpperCase();
  const size = data?.size ?? row.size;
  const mtime = data?.mtime ?? row.mtime;
  return (
    <div className="pv-meta" data-testid="preview-meta">
      <dl>
        <dt>Name</dt>
        <dd>{row.name}</dd>
        <dt>Path</dt>
        <dd>{plainPath(row.path)}</dd>
        <dt>Size</dt>
        <dd className="num">{fmtSize(size)}</dd>
        <dt>Modified</dt>
        <dd className="num">{fmtDateTime(mtime)}</dd>
        <dt>Type</dt>
        <dd>{ext ? `${ext} file` : "File"}</dd>
      </dl>
      {reason && <p className="pv-reason">{reason}</p>}
    </div>
  );
}
