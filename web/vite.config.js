import { readdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const devTarget = process.env.HOMA_DEV_TARGET;

const PDFJS_ASSETS = [
  ["standard_fonts", /\.(ttf|pfb)$|^LICENSE/],
  ["wasm", /^(jbig2|openjpeg|qcms_bg)\.wasm$|^LICENSE/],
  ["iccs", /\.icc$|^LICENSE/],
];

function pdfjsAssets() {
  const root = path.dirname(createRequire(import.meta.url).resolve("pdfjs-dist/package.json"));
  return {
    name: "homa-pdfjs-assets",
    generateBundle() {
      for (const [folder, pattern] of PDFJS_ASSETS) {
        for (const file of readdirSync(path.join(root, folder)).sort()) {
          if (!pattern.test(file)) continue;
          this.emitFile({
            type: "asset",
            fileName: `pdfjs/${folder}/${file}`,
            source: readFileSync(path.join(root, folder, file)),
          });
        }
      }
    },
  };
}

function noCdn() {
  return {
    name: "homa-no-cdn",
    enforce: "pre",
    transform(code, id) {
      if (!/\/node_modules\/(npm2url|markmap-common)\//.test(id)) return null;
      return code.replace(/https:\/\/(cdn\.jsdelivr\.net\/npm|unpkg\.com)\//g, "/app/no-cdn/");
    },
  };
}

export default defineConfig({
  base: "/app/",
  plugins: [noCdn(), react(), pdfjsAssets()],
  server: devTarget ? { proxy: { "/v1": devTarget } } : {},
  build: {
    target: "es2022",
    sourcemap: false,
    ...(process.env.HOMA_WEB_OUT
      ? { outDir: process.env.HOMA_WEB_OUT, emptyOutDir: true }
      : {}),
  },
});
