const LONG_PREFIX = /^\\\\\?\\/;

export function plainPath(path) {
  return path.replace(LONG_PREFIX, "");
}

export function quotedPath(path) {
  return `"${plainPath(path)}"`;
}

export function fmtSize(bytes) {
  if (bytes === null || bytes === undefined) return "";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

const pad = (n) => String(n).padStart(2, "0");

export function fmtDate(epochSeconds) {
  if (!epochSeconds) return "";
  const d = new Date(epochSeconds * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function fmtDateTime(epochSeconds) {
  if (!epochSeconds) return "";
  const d = new Date(epochSeconds * 1000);
  return `${fmtDate(epochSeconds)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fmtAgo(epochSeconds, now = Date.now()) {
  if (!epochSeconds) return "";
  const minutes = Math.max(0, Math.round((now / 1000 - epochSeconds) / 60));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function fmtCount(n) {
  if (n === null || n === undefined) return "";
  if (n >= 100000) return `${(n / 1e6).toFixed(1)}M`;
  return n.toLocaleString("en-US");
}

const EXT_CLASSES = {
  docs: "doc docx dot dotx odt rtf txt md markdown rst tex ppt pptx pps ppsx odp",
  sheets: "xls xlsx xlsm xlsb xlt xltx ods csv tsv",
  pdf: "pdf",
  images: "png jpg jpeg gif bmp svg webp tif tiff ico heic heif raw psd avif",
  code:
    "py pyw js mjs cjs ts tsx jsx java kt c h cpp cc cxx hpp cs go rs rb php swift sh bash " +
    "ps1 psm1 bat cmd sql html htm css scss json yml yaml toml xml ini cfg lua r pl vue",
};

const EXT_LOOKUP = new Map();
for (const [cls, list] of Object.entries(EXT_CLASSES)) {
  for (const ext of list.split(" ")) EXT_LOOKUP.set(ext, cls);
}

export function extClass(row) {
  if (row.is_dir) return "folder";
  return EXT_LOOKUP.get((row.ext || "").toLowerCase()) || "other";
}

export function highlightParts(name, spans) {
  if (!spans || spans.length === 0) return [{ text: name, hit: false }];
  const parts = [];
  let cursor = 0;
  for (const [start, end] of [...spans].sort((a, b) => a[0] - b[0])) {
    if (start < cursor || end > name.length || end <= start) continue;
    if (start > cursor) parts.push({ text: name.slice(cursor, start), hit: false });
    parts.push({ text: name.slice(start, end), hit: true });
    cursor = end;
  }
  if (cursor < name.length) parts.push({ text: name.slice(cursor), hit: false });
  return parts;
}

export const RANGE_RE = /^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$/;

export function matchMode(query) {
  return /[*?]/.test(query.replace(/"[^"]*"/g, "")) ? "wildcard" : "substring";
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    area.remove();
    return ok;
  }
}
