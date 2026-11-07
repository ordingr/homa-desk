export const DEFAULT_SHORTCUTS = {
  open: "Enter",
  reveal: "Ctrl+Enter",
  copy_path: "Ctrl+C",
  rename: "F2",
  properties: "Alt+Enter",
  recycle: "Delete",
  md_vault: "Ctrl+M",
  folder_listing: "Ctrl+F",
  cycle_sort: "Ctrl+S",
  save_search: "Ctrl+Shift+S",
  cycle_date: "Ctrl+D",
  cycle_mode: "Tab",
  context_menu: "Shift+F10",
  full_page: "F11",
  close: "Escape",
};

const NAMED_KEYS = {
  enter: "Enter",
  return: "Enter",
  escape: "Escape",
  esc: "Escape",
  delete: "Delete",
  del: "Delete",
  tab: "Tab",
  space: " ",
  backspace: "Backspace",
  home: "Home",
  end: "End",
  pageup: "PageUp",
  pagedown: "PageDown",
  insert: "Insert",
  up: "ArrowUp",
  down: "ArrowDown",
  left: "ArrowLeft",
  right: "ArrowRight",
  contextmenu: "ContextMenu",
};

const MODIFIERS = { ctrl: "ctrl", control: "ctrl", cmd: "ctrl", alt: "alt", shift: "shift" };

export function parseChord(text) {
  if (typeof text !== "string") return null;
  const parts = text.split("+").map((part) => part.trim());
  if (parts.length === 0 || parts.some((part) => part === "")) return null;
  const chord = { ctrl: false, alt: false, shift: false, key: "" };
  const keyPart = parts.pop().toLowerCase();
  for (const part of parts) {
    const modifier = MODIFIERS[part.toLowerCase()];
    if (!modifier || chord[modifier]) return null;
    chord[modifier] = true;
  }
  if (/^f([1-9]|1\d|2[0-4])$/.test(keyPart)) chord.key = keyPart.toUpperCase();
  else if (NAMED_KEYS[keyPart]) chord.key = NAMED_KEYS[keyPart];
  else if (keyPart.length === 1) chord.key = keyPart;
  else return null;
  return chord;
}

export function chordLabel(chord) {
  const key = chord.key === " " ? "Space" : chord.key.length === 1 ? chord.key.toUpperCase() : chord.key;
  return [chord.ctrl && "Ctrl", chord.alt && "Alt", chord.shift && "Shift", key].filter(Boolean).join("+");
}

export function matches(event, chord) {
  if (!chord) return false;
  if ((event.ctrlKey || event.metaKey) !== chord.ctrl) return false;
  if (event.altKey !== chord.alt) return false;
  if (event.shiftKey !== chord.shift) return false;
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  return key === chord.key;
}

export function buildShortcuts(source) {
  const given = source && typeof source === "object" && !Array.isArray(source) ? source : {};
  const chords = {};
  for (const [action, fallback] of Object.entries(DEFAULT_SHORTCUTS)) {
    chords[action] = parseChord(given[action]) || parseChord(fallback);
  }
  return chords;
}

export const defaultShortcuts = () => buildShortcuts(null);
