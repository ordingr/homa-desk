import { fmtCount } from "../format.js";

export const ROW_CAP = 500;

export default function TableView({ rows, truncated, sheets, shownSheet = 0 }) {
  const shown = rows.slice(0, ROW_CAP);
  const [head, ...body] = shown;
  const clipped = rows.length > ROW_CAP;
  return (
    <div className="pv-table-wrap" data-testid="preview-table">
      {sheets && sheets.length > 0 && (
        <p className="dim pv-note">
          Sheet {shownSheet + 1} of {sheets.length}: {sheets[shownSheet]}
          {sheets.length > 1 && " (other sheets are not previewed)"}
        </p>
      )}
      {rows.length === 0 ? (
        <p className="dim">No rows.</p>
      ) : (
        <div className="pv-scroll">
          <table className="pv-table num">
            <thead>
              <tr>
                {head.map((cell, i) => (
                  <th key={i}>{cell}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, i) => (
                    <td key={i}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="dim pv-note" data-testid="preview-table-note">
        {clipped
          ? `Showing ${fmtCount(ROW_CAP)} of ${fmtCount(rows.length)}${truncated ? "+" : ""} rows`
          : `${fmtCount(rows.length)} rows`}
        {truncated && " (the file has more than the preview reads)"}
      </p>
    </div>
  );
}
