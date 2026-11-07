import hljs from "highlight.js/lib/common";
import { useMemo } from "react";
import { useChunks, MoreBar } from "./chunks.jsx";

export const HIGHLIGHT_LIMIT = 1_000_000;

const ALIASES = { ipynb: "json", ps1: "powershell", log: "", txt: "", csv: "", tsv: "" };

function languageFor(name) {
  const ext = name.includes(".") ? name.split(".").pop().toLowerCase() : "";
  const lang = ext in ALIASES ? ALIASES[ext] : ext;
  return lang && hljs.getLanguage(lang) ? lang : "";
}

export default function TextView({ path, name, initial }) {
  const chunks = useChunks(path, initial);
  const language = useMemo(() => languageFor(name), [name]);
  const plain = !language || chunks.text.length > HIGHLIGHT_LIMIT;
  const html = useMemo(
    () => (plain ? null : hljs.highlight(chunks.text, { language, ignoreIllegals: true }).value),
    [plain, language, chunks.text],
  );
  return (
    <div className="pv-text" data-testid="preview-text">
      {language && chunks.text.length > HIGHLIGHT_LIMIT && (
        <p className="dim pv-note">Syntax highlighting is off above 1 MB.</p>
      )}
      {html === null ? (
        <pre className="pv-code" data-testid="preview-code">
          {chunks.text}
        </pre>
      ) : (
        <pre className="pv-code hljs" data-testid="preview-code" dangerouslySetInnerHTML={{ __html: html }} />
      )}
      <MoreBar chunks={chunks} />
    </div>
  );
}
