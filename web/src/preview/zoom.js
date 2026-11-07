import { createContext, useContext } from "react";

export const ZOOM_STEPS = [80, 90, 100, 110, 125, 150, 175, 200];
export const ZOOM_DEFAULT = 100;
const ZOOM_KEY = "assistant.previewZoom";

export const ZoomContext = createContext(1);
export const useZoom = () => useContext(ZoomContext);

export function loadZoom() {
  try {
    const value = Number(localStorage.getItem(ZOOM_KEY));
    return ZOOM_STEPS.includes(value) ? value : ZOOM_DEFAULT;
  } catch {
    return ZOOM_DEFAULT;
  }
}

export function saveZoom(value) {
  try {
    localStorage.setItem(ZOOM_KEY, String(value));
  } catch {
    /* zoom preference is optional */
  }
}

export function stepZoom(current, direction) {
  const index = ZOOM_STEPS.indexOf(current);
  const next = Math.max(0, Math.min(ZOOM_STEPS.length - 1, (index === -1 ? ZOOM_STEPS.indexOf(ZOOM_DEFAULT) : index) + direction));
  return ZOOM_STEPS[next];
}
