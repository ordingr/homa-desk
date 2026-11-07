import DOMPurify from "dompurify";
import { memo, useEffect, useState } from "react";
import { installStyleGuard } from "./styleGuard.js";
import { useTheme } from "./theme.js";

installStyleGuard();

const LOCKED_CONFIG_KEYS = [
  "secure",
  "securityLevel",
  "startOnLoad",
  "maxTextSize",
  "suppressErrorRendering",
  "maxEdges",
  "themeCSS",
  "fontFamily",
  "altFontFamily",
  "themeVariables",
];

let mermaidPromise = null;
let counter = 0;

function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then((module) => module.default);
  }
  return mermaidPromise;
}

async function renderDiagram(code, theme) {
  const mermaid = await loadMermaid();
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: theme === "light" ? "default" : "dark",
    suppressErrorRendering: true,
    secure: LOCKED_CONFIG_KEYS,
  });
  const { svg } = await mermaid.render(`pv-mermaid-${++counter}`, code);
  return DOMPurify.sanitize(svg, {
    ADD_TAGS: ["foreignobject"],
    ADD_ATTR: ["dominant-baseline"],
    HTML_INTEGRATION_POINTS: { foreignobject: true },
    FORBID_TAGS: ["script", "a"],
  });
}

function MermaidDiagram({ code }) {
  const theme = useTheme();
  const [state, setState] = useState({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    renderDiagram(code, theme).then(
      (svg) => !cancelled && setState({ status: "ready", svg }),
      (err) => !cancelled && setState({ status: "error", message: String(err?.message || err).split("\n")[0] }),
    );
    return () => {
      cancelled = true;
    };
  }, [code, theme]);

  if (state.status === "error") {
    return (
      <div className="pv-diagram-error" role="alert">
        <strong>Diagram could not be drawn</strong>
        <p className="dim">{state.message}</p>
        <details>
          <summary>Show diagram source</summary>
          <pre className="pv-code">{code}</pre>
        </details>
      </div>
    );
  }
  if (state.status === "loading") return <p className="dim pv-note">Drawing diagram...</p>;
  return <div className="pv-diagram" data-testid="preview-mermaid" dangerouslySetInnerHTML={{ __html: state.svg }} />;
}

export default memo(MermaidDiagram);
