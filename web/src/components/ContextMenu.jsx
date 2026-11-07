import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { CloudSendIcon, LocalIcon } from "./Icons.jsx";

export const MENU_ITEMS = [
  { verb: "open", label: "Open", key: "Enter" },
  { verb: "open_with", label: "Open with…" },
  { verb: "reveal", label: "Reveal in Explorer", key: "Ctrl+Enter" },
  { verb: "copy_path", label: "Copy path" },
  { verb: "copy_path_quoted", label: "Copy path (quoted)" },
  { separator: true },
  { verb: "rename", label: "Rename…" },
  { verb: "properties", label: "Properties" },
  { verb: "md_vault", label: "Open in MD Vault", key: "Ctrl+M" },
  { verb: "recycle", label: "Move to Recycle Bin" },
  { separator: true },
  { verb: "ask_claude", label: "Ask Claude about this file", cloud: true },
];

export default function ContextMenu({ x, y, onPick, onClose }) {
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
    ref.current.querySelector("button")?.focus();
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
    const buttons = [...ref.current.querySelectorAll("button")];
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
      {MENU_ITEMS.map((item, index) =>
        item.separator ? (
          <hr key={index} />
        ) : (
          <button
            key={item.verb}
            type="button"
            role="menuitem"
            className={`menuitem ${item.cloud ? "cloud" : "local"}`}
            data-verb={item.verb}
            data-scope={item.cloud ? "sends-to-claude" : "local"}
            onClick={() => onPick(item.verb)}
          >
            <span className="marker" title={item.cloud ? "Sends to Claude" : "Stays on this PC"}>
              {item.cloud ? <CloudSendIcon /> : <LocalIcon />}
            </span>
            <span className="menulabel">{item.label}</span>
            {item.cloud ? (
              <span className="menutag">sends to Claude</span>
            ) : (
              item.key && <kbd>{item.key}</kbd>
            )}
          </button>
        ),
      )}
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
