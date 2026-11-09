import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import { CloudSendIcon, LocalIcon } from "./Icons.jsx";
import { chordLabel } from "../shortcuts.js";

export const MENU_ITEMS = [
  { verb: "open", label: "Open", shortcut: "open" },
  { verb: "open_with", label: "Open with…" },
  { verb: "reveal", label: "Reveal in Explorer", shortcut: "reveal" },
  { verb: "copy_files", label: "Copy", shortcut: "copy_files", many: "Copy {n} files" },
  { verb: "cut_files", label: "Cut", shortcut: "cut_files", many: "Cut {n} files" },
  { verb: "copy_path", label: "Copy path", shortcut: "copy_path" },
  { verb: "copy_path_quoted", label: "Copy path (quoted)" },
  { separator: true },
  { verb: "rename", label: "Rename…", shortcut: "rename" },
  { verb: "properties", label: "Properties", shortcut: "properties" },
  { verb: "md_vault", label: "Open in MD Vault", shortcut: "md_vault" },
  { verb: "recycle", label: "Move to Recycle Bin", shortcut: "recycle" },
  { separator: true },
  { verb: "ask_claude", label: "Ask Claude about this file", cloud: true },
];

export default function ContextMenu({ x, y, claude, shortcuts, count = 1, onPick, onClose }) {
  const ref = useRef(null);
  const [pos, setPos] = useState({ left: x, top: y });

  useLayoutEffect(() => {
    const box = ref.current.getBoundingClientRect();
    setPos({
      left: Math.max(4, Math.min(x, window.innerWidth - box.width - 4)),
      top: Math.max(4, Math.min(y, window.innerHeight - box.height - 4)),
    });
  }, [x, y]);

  useEffect(() => {
    ref.current.querySelector("button:not(:disabled)")?.focus();
    const away = (event) => {
      if (!ref.current.contains(event.target)) onClose();
    };
    window.addEventListener("mousedown", away, true);
    window.addEventListener("blur", onClose);
    window.addEventListener("resize", onClose);
    return () => {
      window.removeEventListener("mousedown", away, true);
      window.removeEventListener("blur", onClose);
      window.removeEventListener("resize", onClose);
    };
  }, [onClose]);

  const onKeyDown = (event) => {
    const buttons = [...ref.current.querySelectorAll("button:not(:disabled)")];
    const at = buttons.indexOf(document.activeElement);
    if (event.key === "ArrowDown") buttons[(at + 1) % buttons.length].focus();
    else if (event.key === "ArrowUp") buttons[(at - 1 + buttons.length) % buttons.length].focus();
    else if (event.key === "Escape") onClose();
    else return;
    event.preventDefault();
    event.stopPropagation();
  };

  return (
    <div
      ref={ref}
      className="menu"
      role="menu"
      style={pos}
      onKeyDown={onKeyDown}
      onContextMenu={(event) => event.preventDefault()}
      data-testid="context-menu"
    >
      {MENU_ITEMS.map((item, index) => {
        if (item.separator) return <hr key={index} />;
        const blocked = item.cloud && !claude.available;
        return (
          <Fragment key={item.verb}>
          <button
            type="button"
            role="menuitem"
            disabled={blocked}
            title={blocked ? claude.reason : undefined}
            className={`menuitem ${item.cloud ? "cloud" : "local"}`}
            data-verb={item.verb}
            data-scope={item.cloud ? "sends-to-claude" : "local"}
            onClick={() => onPick(item.verb)}
          >
            <span className="marker" title={item.cloud ? "Sends to Claude" : "Stays on this PC"}>
              {item.cloud ? <CloudSendIcon /> : <LocalIcon />}
            </span>
            <span className="menulabel">
              {item.many && count > 1 ? item.many.replace("{n}", count) : item.label}
            </span>
            {item.cloud ? (
              <span className="menutag">sends to Claude</span>
            ) : (
              item.shortcut && shortcuts[item.shortcut] && <kbd>{chordLabel(shortcuts[item.shortcut])}</kbd>
            )}
          </button>
          {blocked && (
            <div className="menu-note" data-testid="ask-unavailable">
              {claude.reason}
            </div>
          )}
          </Fragment>
        );
      })}
      <div className="menu-legend">
        <span className="legend local">
          <LocalIcon /> local
        </span>
        <span className="legend cloud">
          <CloudSendIcon /> sends to Claude
        </span>
      </div>
    </div>
  );
}
