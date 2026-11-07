import DOMPurify from "dompurify";

const HOSTILE_CSS = /url\(\s*(?!["']?\s*#)|@import|expression\(|image-set\(|(?<![\w-])src\(/i;

const guarded = new WeakSet();

export function guardStyles(purify) {
  if (guarded.has(purify)) return purify;
  guarded.add(purify);
  purify.addHook("uponSanitizeElement", (node, data) => {
    if (data.tagName === "style" && HOSTILE_CSS.test(node.textContent || "")) {
      node.parentNode?.removeChild(node);
    }
  });
  purify.addHook("afterSanitizeAttributes", (node) => {
    if (node.hasAttribute?.("style") && HOSTILE_CSS.test(node.getAttribute("style"))) {
      node.removeAttribute("style");
    }
  });
  return purify;
}

export function installStyleGuard() {
  guardStyles(DOMPurify);
}
