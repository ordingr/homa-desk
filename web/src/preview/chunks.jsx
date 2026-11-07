import { useCallback, useRef, useState } from "react";
import { fmtSize } from "../format.js";
import { MORE_BYTES, loadPreview } from "./load.js";

export function useChunks(path, initial) {
  const [chunk, setChunk] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);

  const more = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const { json } = await loadPreview(path, { range: `${chunk.end}-${chunk.end + MORE_BYTES - 1}` });
      if (!json || typeof json.text !== "string" || json.start !== chunk.end) {
        throw new Error(json?.reason || "The server did not return the next part of the file.");
      }
      setChunk({ ...json, text: chunk.text + json.text });
    } catch (err) {
      setError(err.message || "Could not load more.");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, [path, chunk]);

  return { ...chunk, busy, error, more };
}

export function MoreBar({ chunks }) {
  const { truncated, end, size, busy, error, more } = chunks;
  if (!truncated && !error) return null;
  return (
    <div className="pv-more">
      {truncated && (
        <>
          <span className="dim num" data-testid="preview-more-status">
            Showing {fmtSize(end)} of {fmtSize(size)}
          </span>
          <button type="button" className="pv-btn" onClick={more} disabled={busy} data-testid="preview-more">
            {busy ? "Loading..." : "Show more"}
          </button>
        </>
      )}
      {error && <span className="pv-error">{error}</span>}
    </div>
  );
}
