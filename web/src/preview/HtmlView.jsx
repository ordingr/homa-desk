import createDOMPurify from "dompurify";
import { useMemo } from "react";
import { useChunks, MoreBar } from "./chunks.jsx";

const SANITIZE = {
  FORBID_TAGS: [
    "script", "form", "input", "button", "textarea", "select", "option", "iframe", "frame", "frameset",
    "object", "embed", "applet", "meta", "link", "base", "template", "audio", "video", "source", "track",
  ],
  FORBID_ATTR: ["srcset", "ping", "formaction", "action"],
  ALLOW_DATA_ATTR: false,
};

const FRAME_HEAD =
  "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:\">" +
  "<base target=\"_blank\">" +
  "<style>body{font:14px/1.5 system-ui,sans-serif;margin:12px;color:#111;background:#fff}img{max-width:100%}</style>";

const purify = createDOMPurify(window);
purify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "IMG" && !/^data:image\//i.test(node.getAttribute("src") || "")) {
    node.removeAttribute("src");
  }
});

export function sanitizeHtml(source) {
  return purify.sanitize(source, SANITIZE);
}

export default function HtmlView({ path, initial }) {
  const chunks = useChunks(path, initial);
  const srcDoc = useMemo(
    () => `<!doctype html><html><head>${FRAME_HEAD}</head><body>${sanitizeHtml(chunks.text)}</body></html>`,
    [chunks.text],
  );
  return (
    <div className="pv-html" data-testid="preview-html">
      <p className="dim pv-note">Sanitised HTML: scripts, forms and external content are removed.</p>
      <iframe title="HTML preview" sandbox="" srcDoc={srcDoc} referrerPolicy="no-referrer" />
      <MoreBar chunks={chunks} />
    </div>
  );
}
