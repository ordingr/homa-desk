import { useEffect, useRef, useState } from "react";
import { Notice } from "./Notices.jsx";
import { useZoom } from "./zoom.js";

const ASSET_BASE = `${import.meta.env.BASE_URL}pdfjs/`;

let pdfjsPromise = null;
function loadPdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = Promise.all([
      import("pdfjs-dist/build/pdf.mjs"),
      import("pdfjs-dist/build/pdf.worker.min.mjs?url"),
    ]).then(([pdfjs, worker]) => {
      pdfjs.GlobalWorkerOptions.workerSrc = new URL(worker.default, window.location.href).href;
      return pdfjs;
    });
  }
  return pdfjsPromise;
}

export default function PdfView({ blob }) {
  const canvasRef = useRef(null);
  const wrapRef = useRef(null);
  const [doc, setDoc] = useState(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const zoom = useZoom();

  useEffect(() => {
    let cancelled = false;
    let loaded = null;
    (async () => {
      try {
        const pdfjs = await loadPdfjs();
        const data = new Uint8Array(await blob.arrayBuffer());
        const task = pdfjs.getDocument({
          data,
          isEvalSupported: false,
          enableXfa: false,
          standardFontDataUrl: `${ASSET_BASE}standard_fonts/`,
          wasmUrl: `${ASSET_BASE}wasm/`,
          iccUrl: `${ASSET_BASE}iccs/`,
        });
        loaded = await task.promise;
        if (cancelled) {
          loaded.destroy();
          return;
        }
        setDoc(loaded);
      } catch (err) {
        if (!cancelled) setError(String(err?.message || err));
      }
    })();
    return () => {
      cancelled = true;
      if (loaded) loaded.destroy();
    };
  }, [blob]);

  useEffect(() => {
    if (!doc || !canvasRef.current || !wrapRef.current) return undefined;
    let cancelled = false;
    let task = null;
    (async () => {
      try {
        const pdfPage = await doc.getPage(page);
        if (cancelled) return;
        const base = pdfPage.getViewport({ scale: 1 });
        const available = Math.max(200, wrapRef.current.clientWidth);
        const scale = Math.min(3, available / base.width) * zoom;
        const ratio = window.devicePixelRatio || 1;
        const viewport = pdfPage.getViewport({ scale: scale * ratio });
        const canvas = canvasRef.current;
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.style.width = `${Math.floor(viewport.width / ratio)}px`;
        canvas.style.height = `${Math.floor(viewport.height / ratio)}px`;
        task = pdfPage.render({ canvas, viewport });
        await task.promise;
      } catch (err) {
        if (!cancelled && err?.name !== "RenderingCancelledException") setError(String(err?.message || err));
      }
    })();
    return () => {
      cancelled = true;
      if (task) task.cancel();
    };
  }, [doc, page, zoom]);

  if (error) return <Notice title="This PDF could not be displayed">{error}</Notice>;
  const count = doc ? doc.numPages : 0;
  return (
    <div className="pv-pdf" data-testid="preview-pdf">
      <div className="pv-pdf-bar">
        <button type="button" className="pv-btn" disabled={page <= 1} onClick={() => setPage(page - 1)}>
          Previous
        </button>
        <span className="dim num" data-testid="preview-pdf-page">
          {doc ? `Page ${page} of ${count}` : "Loading..."}
        </span>
        <button type="button" className="pv-btn" disabled={!doc || page >= count} onClick={() => setPage(page + 1)}>
          Next
        </button>
      </div>
      <div ref={wrapRef} className="pv-pdf-page">
        <canvas ref={canvasRef} />
      </div>
    </div>
  );
}
