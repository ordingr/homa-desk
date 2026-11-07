import createDOMPurify from "dompurify";
import { useEffect, useState } from "react";
import { useBlobUrl } from "./useBlobUrl.js";
import { Notice } from "./Notices.jsx";
import { guardStyles } from "./styleGuard.js";

const SVG_FRAME_HEAD =
  "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:\">" +
  "<style>html,body{margin:0;height:100%}svg{max-width:100%;max-height:100%;display:block;margin:auto}</style>";

const svgPurify = guardStyles(createDOMPurify(window));

export function sanitizeSvg(source) {
  return svgPurify.sanitize(source, {
    USE_PROFILES: { svg: true, svgFilters: true },
    FORBID_TAGS: ["script", "foreignobject", "a", "image", "use"],
    ALLOW_DATA_ATTR: false,
  });
}

function SvgView({ blob, name }) {
  const [srcDoc, setSrcDoc] = useState(null);
  useEffect(() => {
    let cancelled = false;
    blob.text().then((text) => {
      if (!cancelled) setSrcDoc(`<!doctype html><html><head>${SVG_FRAME_HEAD}</head><body>${sanitizeSvg(text)}</body></html>`);
    });
    return () => {
      cancelled = true;
    };
  }, [blob]);
  if (srcDoc === null) return null;
  return (
    <div className="pv-image" data-testid="preview-image">
      <iframe title={name} sandbox="" srcDoc={srcDoc} referrerPolicy="no-referrer" />
    </div>
  );
}

function BitmapView({ blob, name }) {
  const url = useBlobUrl(blob);
  const [failed, setFailed] = useState(false);
  if (failed) {
    return <Notice title="This image could not be displayed">The browser could not decode it.</Notice>;
  }
  if (!url) return null;
  return (
    <div className="pv-image" data-testid="preview-image">
      <img src={url} alt={name} onError={() => setFailed(true)} draggable={false} />
    </div>
  );
}

export default function ImageView({ blob, name }) {
  if (blob.type === "image/svg+xml") return <SvgView blob={blob} name={name} />;
  return <BitmapView blob={blob} name={name} />;
}
