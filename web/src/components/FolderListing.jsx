import { useEffect, useRef } from "react";
import { fmtDate, fmtSize, plainPath } from "../format.js";
import { CloseIcon, CloudOffIcon } from "./Icons.jsx";

export default function FolderListing({ listing, canGoBack, onBack, onOpenFolder, onOpenFile, onClose }) {
  const ref = useRef(null);

  useEffect(() => {
    ref.current?.focus();
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onClose();
      } else if (event.key === "Backspace" && canGoBack) {
        event.preventDefault();
        event.stopPropagation();
        onBack();
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [onClose, onBack, canGoBack]);

  const rows = listing.rows || [];
  return (
    <div className="scrim" onMouseDown={onClose}>
      <section
        ref={ref}
        tabIndex={-1}
        className="dialog listing"
        role="dialog"
        aria-modal="true"
        aria-label="Folder listing"
        onMouseDown={(event) => event.stopPropagation()}
        data-testid="listing"
      >
        <header className="listing-head">
          <h2 title={plainPath(listing.folder)}>{plainPath(listing.folder)}</h2>
          {canGoBack && (
            <button type="button" className="btn" onClick={onBack} data-testid="listing-back">
              Back
            </button>
          )}
          <button type="button" className="icon-btn" aria-label="Close listing" onClick={onClose} data-testid="listing-close">
            <CloseIcon />
          </button>
        </header>
        <p className="dim" data-testid="listing-note">
          {listing.truncated ? `Showing ${rows.length} of ${listing.total}.` : `${rows.length} item${rows.length === 1 ? "" : "s"}.`}
          {listing.hidden_protected ? ` ${listing.hidden_protected} protected item(s) not shown.` : ""}
        </p>
        <ul className="listing-rows" data-testid="listing-rows">
          {rows.map((item) => (
            <li
              key={item.path}
              className="listing-row"
              data-testid="listing-row"
              data-path={item.path}
              onDoubleClick={() => (item.is_dir ? onOpenFolder(item) : onOpenFile(item))}
            >
              <span className="listing-name">
                {item.is_dir ? "[folder] " : ""}
                {item.name}
                {item.placeholder && (
                  <span className="placeholder-mark" title="Not downloaded (cloud placeholder)">
                    <CloudOffIcon width={14} height={14} />
                  </span>
                )}
              </span>
              <span className="num dim">{item.is_dir ? "" : fmtSize(item.size)}</span>
              <span className="num dim">{fmtDate(item.mtime)}</span>
            </li>
          ))}
        </ul>
        {rows.length === 0 && <p className="dim">This folder is empty.</p>}
        <p className="dim listing-hint">Double-click a folder to list it, a file to open it. Esc closes.</p>
      </section>
    </div>
  );
}
