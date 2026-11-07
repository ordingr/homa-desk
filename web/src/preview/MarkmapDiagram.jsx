import DOMPurify from "dompurify";
import { memo, useEffect, useRef, useState } from "react";
import { installStyleGuard } from "./styleGuard.js";
import { useTheme } from "./theme.js";

installStyleGuard();

const NODE_SANITIZE = {
  ALLOWED_TAGS: ["a", "b", "strong", "i", "em", "u", "s", "del", "mark", "code", "kbd", "sub", "sup", "br", "span"],
  ALLOWED_ATTR: ["href"],
  ALLOWED_URI_REGEXP: /^(?:https?|mailto):/i,
};

const FRONT_MATTER = /^﻿?---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/;

const PALETTE = {
  light: ["#2a78d6", "#1baf7a", "#eda100", "#4a3aa7", "#e34948", "#e87ba4"],
  dark: ["#3987e5", "#199e70", "#c98500", "#9085e9", "#e66767", "#d55181"],
};

let libsPromise = null;
function loadLibs() {
  if (!libsPromise) {
    libsPromise = Promise.all([import("markmap-lib/no-plugins"), import("markmap-view")]).catch(
      (err) => {
        libsPromise = null;
        throw err;
      },
    );
  }
  return libsPromise;
}

function sanitizeTree(node, purify) {
  node.content = purify.sanitize(node.content || "", NODE_SANITIZE);
  (node.children || []).forEach((child) => sanitizeTree(child, purify));
}

function MarkmapDiagram({ content }) {
  const svgRef = useRef(null);
  const mapRef = useRef(null);
  const theme = useTheme();
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [lib, view] = await loadLibs();
        if (cancelled || !svgRef.current) return;
        const transformer = new lib.Transformer([]);
        transformer.md.set({ html: false });
        const { root } = transformer.transform(content.replace(FRONT_MATTER, "") + "\n");
        sanitizeTree(root, DOMPurify);
        const options = {
          ...view.deriveOptions({
            color: PALETTE[theme === "light" ? "light" : "dark"],
            colorFreezeLevel: 2,
            maxWidth: 280,
            initialExpandLevel: -1,
          }),
          duration: 0,
          maxInitialScale: 1,
          paddingX: 10,
          spacingVertical: 8,
        };
        if (mapRef.current) mapRef.current.destroy();
        const map = new view.Markmap(svgRef.current, options);
        mapRef.current = map;
        await map.setData(root);
        if (!cancelled) {
          map.fit();
          setError("");
        }
      } catch (err) {
        if (!cancelled) setError(String(err?.message || err).split("\n")[0]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [content, theme]);

  useEffect(
    () => () => {
      mapRef.current?.destroy();
      mapRef.current = null;
    },
    [],
  );

  return (
    <div className="pv-markmap" data-testid="preview-markmap">
      {error && (
        <div className="pv-diagram-error" role="alert">
          <strong>Mind map could not be drawn</strong>
          <p className="dim">{error}</p>
        </div>
      )}
      <svg ref={svgRef} className={error ? "pv-hidden" : ""} />
      {!error && (
        <button type="button" className="pv-btn" onClick={() => mapRef.current?.fit()}>
          Fit
        </button>
      )}
    </div>
  );
}

export default memo(MarkmapDiagram);
