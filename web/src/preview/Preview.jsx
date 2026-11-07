import { useEffect, useState } from "react";
import { fmtSize } from "../format.js";
import HtmlView from "./HtmlView.jsx";
import ImageView from "./ImageView.jsx";
import MarkdownView from "./MarkdownView.jsx";
import { MetaCard, Notice } from "./Notices.jsx";
import { DocxView, PptxView } from "./OfficeView.jsx";
import PdfView from "./PdfView.jsx";
import TableView from "./TableView.jsx";
import TextView from "./TextView.jsx";
import { loadPreview } from "./load.js";
import "./preview.css";

function useServerPreview(path, hydrate) {
  const [state, setState] = useState({ status: "loading" });
  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    loadPreview(path, { hydrate }).then(
      (result) => !cancelled && setState({ status: "ready", ...result }),
      (error) => !cancelled && setState({ status: "error", message: error.message || "Preview failed." }),
    );
    return () => {
      cancelled = true;
    };
  }, [path, hydrate]);
  return state;
}

function Placeholder({ data, row, hydrated, onHydrate }) {
  return (
    <div className="pv-placeholder" data-testid="preview-placeholder">
      <Notice title="Cloud placeholder - not downloaded">
        {data.reason || "This file is stored in the cloud and is not on this PC."}
      </Notice>
      {hydrated ? (
        <p className="pv-reason" data-testid="preview-hydrate-result">
          Cloud placeholders are never downloaded by Assistant; open the file in Explorer to download it
        </p>
      ) : (
        <button type="button" className="pv-btn" onClick={onHydrate} data-testid="preview-hydrate">
          Download and preview
        </button>
      )}
      <MetaCard row={row} data={data} />
    </div>
  );
}

function Rendered({ state, row, hydrated, onHydrate }) {
  if (state.blob) {
    if (state.type.startsWith("image/")) return <ImageView blob={state.blob} name={row.name} />;
    if (state.type === "application/pdf") return <PdfView blob={state.blob} />;
    return <MetaCard row={row} reason={`Unexpected content type: ${state.type || "unknown"}`} />;
  }
  const data = state.json;
  switch (data.kind) {
    case "markdown":
      return <MarkdownView path={row.path} initial={data} />;
    case "text":
      return <TextView path={row.path} name={row.name} initial={data} />;
    case "html":
      return <HtmlView path={row.path} initial={data} />;
    case "table":
    case "xlsx":
      return <TableView rows={data.rows || []} truncated={data.truncated} sheets={data.sheets} shownSheet={data.shown_sheet} />;
    case "docx":
      return <DocxView data={data} />;
    case "pptx":
      return <PptxView data={data} />;
    case "protected":
      return (
        <Notice title="Protected file - not previewed" tone="warn">
          {data.reason}
        </Notice>
      );
    case "placeholder":
      return <Placeholder data={data} row={row} hydrated={hydrated} onHydrate={onHydrate} />;
    case "too_large":
      return (
        <Notice title="File too large to preview">
          {`This file is ${fmtSize(data.size ?? row.size)}; previews are limited to ${fmtSize(data.cap)}.`}
        </Notice>
      );
    case "meta":
      return <MetaCard row={row} data={data} reason={data.reason} />;
    default:
      if (typeof data.text === "string") return <pre className="pv-code">{data.text}</pre>;
      return <MetaCard row={row} data={data} reason={data.reason || "No preview for this type."} />;
  }
}

export default function Preview({ row }) {
  const [hydrate, setHydrate] = useState(false);
  const state = useServerPreview(row.path, hydrate);
  if (state.status === "loading") return <p className="dim" data-testid="preview-loading">Loading preview...</p>;
  if (state.status === "error") return <Notice title="Preview not available" tone="warn">{state.message}</Notice>;
  return <Rendered state={state} row={row} hydrated={hydrate} onHydrate={() => setHydrate(true)} />;
}
