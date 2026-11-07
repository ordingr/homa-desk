import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { useChunks, MoreBar } from "./chunks.jsx";
import MarkmapDiagram from "./MarkmapDiagram.jsx";
import MermaidDiagram from "./MermaidDiagram.jsx";

const REMARK = [remarkGfm];
const REHYPE = [[rehypeHighlight, { detect: false, ignoreMissing: true }]];
const SAFE_LINK = /^(?:https?:|mailto:)/i;

function textOf(node) {
  if (!node) return "";
  if (node.type === "text") return node.value || "";
  return (node.children || []).map(textOf).join("");
}

const COMPONENTS = {
  a({ href, children }) {
    if (!href || !SAFE_LINK.test(href)) return <span>{children}</span>;
    return (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  },
  img({ alt }) {
    return <span className="dim">[image: {alt || "not loaded"}]</span>;
  },
  pre({ node, children }) {
    const code = node?.children?.[0];
    if (code?.tagName === "code") {
      const classes = [].concat(code.properties?.className || []);
      const language = classes.find((c) => typeof c === "string" && c.startsWith("language-"));
      const kind = language ? language.slice(9) : "";
      if (kind === "mermaid") return <MermaidDiagram code={textOf(code).trim()} />;
      if (kind === "markmap") return <MarkmapDiagram content={textOf(code).trim()} />;
    }
    return <pre>{children}</pre>;
  },
};

export function MarkdownText({ text }) {
  return useMemo(
    () => (
      <ReactMarkdown remarkPlugins={REMARK} rehypePlugins={REHYPE} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    ),
    [text],
  );
}

export default function MarkdownView({ path, initial }) {
  const chunks = useChunks(path, initial);
  const body = <MarkdownText text={chunks.text} />;
  return (
    <div className="pv-md" data-testid="preview-markdown">
      {body}
      <MoreBar chunks={chunks} />
    </div>
  );
}
