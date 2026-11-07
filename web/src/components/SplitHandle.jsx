import { useRef } from "react";

export const SPLIT_DEFAULT = 0.5;
export const SPLIT_MIN_PX = 320;
const SPLIT_KEY = "assistant.split";
const KEY_STEP = 0.02;

export function loadSplit() {
  try {
    const value = Number(localStorage.getItem(SPLIT_KEY));
    return value >= 0.15 && value <= 0.85 ? value : SPLIT_DEFAULT;
  } catch {
    return SPLIT_DEFAULT;
  }
}

export function saveSplit(value) {
  try {
    localStorage.setItem(SPLIT_KEY, String(value));
  } catch {
    /* split position is optional */
  }
}

export function clampSplit(frac, width) {
  if (!(width > 0)) return frac;
  const min = SPLIT_MIN_PX / width;
  const max = 1 - min;
  if (min >= max) return SPLIT_DEFAULT;
  return Math.max(min, Math.min(max, frac));
}

export default function SplitHandle({ containerRef, frac, onChange }) {
  const dragging = useRef(false);

  const fromPointer = (event) => {
    const box = containerRef.current.getBoundingClientRect();
    return clampSplit((box.right - event.clientX) / box.width, box.width);
  };

  const onPointerDown = (event) => {
    if (event.button !== 0) return;
    dragging.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  };

  const onPointerMove = (event) => {
    if (dragging.current) onChange(fromPointer(event), false);
  };

  const onPointerUp = (event) => {
    if (!dragging.current) return;
    dragging.current = false;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    onChange(fromPointer(event), true);
  };

  const onKeyDown = (event) => {
    const width = containerRef.current.getBoundingClientRect().width;
    if (event.key === "ArrowLeft") onChange(clampSplit(frac + KEY_STEP, width), true);
    else if (event.key === "ArrowRight") onChange(clampSplit(frac - KEY_STEP, width), true);
    else if (event.key === "Home" || event.key === "Enter") onChange(SPLIT_DEFAULT, true);
    else return;
    event.preventDefault();
    event.stopPropagation();
  };

  return (
    <div
      className="split-handle"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize preview"
      aria-valuemin={15}
      aria-valuemax={85}
      aria-valuenow={Math.round(frac * 100)}
      title="Drag to resize the preview. Double-click to reset."
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDoubleClick={() => onChange(SPLIT_DEFAULT, true)}
      onKeyDown={onKeyDown}
      data-testid="split-handle"
    />
  );
}
