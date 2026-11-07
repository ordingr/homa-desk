import { useEffect, useRef } from "react";
import { extClass, fmtDate, fmtSize, highlightParts } from "../format.js";
import { CloudOffIcon } from "./Icons.jsx";

function ExtTag({ row }) {
  const cls = extClass(row);
  const label = row.is_dir ? "?" : row.ext ? `.${row.ext}`.slice(0, 7) : "-";
  return <span className={`exttag ext-${cls}`}>{label}</span>;
}

export default function ResultList({
  rows,
  selected,
  activeId,
  onRowClick,
  onRowDoubleClick,
  onRowContextMenu,
}) {
  const activeRef = useRef(null);
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest" });
  }, [activeId]);

  return (
    <ul className="results" role="listbox" aria-multiselectable="true" data-testid="results">
      {rows.map((row, index) => {
        const isSelected = selected.has(row.id);
        return (
          <li
            key={row.id}
            ref={row.id === activeId ? activeRef : null}
            role="option"
            aria-selected={isSelected}
            className={`row ${isSelected ? "selected" : ""} ${row.id === activeId ? "active" : ""}`}
            data-testid="row"
            data-path={row.path}
            onClick={(event) => onRowClick(event, row, index)}
            onDoubleClick={(event) => onRowDoubleClick(event, row)}
            onContextMenu={(event) => onRowContextMenu(event, row, index)}
          >
            <ExtTag row={row} />
            <div className="rowmain">
              <div className="rowname">
                <span className="name">
                  {highlightParts(row.name, row.spans).map((part, i) =>
                    part.hit ? <mark key={i}>{part.text}</mark> : <span key={i}>{part.text}</span>,
                  )}
                </span>
                {row.placeholder && (
                  <span
                    className="placeholder-mark"
                    title="Not downloaded (cloud placeholder)"
                    data-testid="placeholder-mark"
                  >
                    <CloudOffIcon width={14} height={14} />
                    <span className="sr-only">not downloaded</span>
                  </span>
                )}
              </div>
              <div className="rowpath" title={row.path}>
                {row.parent}
              </div>
            </div>
            <span className="rowsize num">{row.is_dir ? "" : fmtSize(row.size)}</span>
            <span className="rowdate num">{fmtDate(row.mtime)}</span>
          </li>
        );
      })}
    </ul>
  );
}
