import { useEffect, useRef, useState } from "react";
import { CloudSendIcon } from "./Icons.jsx";

export default function Modal({ modal, onClose }) {
  const [value, setValue] = useState(modal.initial || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [checked, setChecked] = useState(modal.option ? modal.option.checked : false);
  const [reloading, setReloading] = useState(false);
  const inputRef = useRef(null);
  const okRef = useRef(null);

  useEffect(() => {
    (inputRef.current || okRef.current)?.focus();
    inputRef.current?.select();
  }, []);

  const toggleOption = async (next) => {
    setChecked(next);
    setReloading(true);
    const refreshed = await modal.option.onChange(next);
    if (refreshed === false) setChecked(!next);
    setReloading(false);
  };

  const confirm = async () => {
    setBusy(true);
    setError("");
    try {
      const failure = modal.onConfirm ? await modal.onConfirm(value) : null;
      if (failure) {
        setError(failure);
        setBusy(false);
      } else {
        onClose();
      }
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  const confirmRef = useRef(confirm);
  confirmRef.current = confirm;
  const busyRef = useRef(busy);
  busyRef.current = busy;

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onClose();
      } else if (event.key === "Enter" && event.target.tagName !== "BUTTON" && event.target.type !== "checkbox") {
        event.preventDefault();
        event.stopPropagation();
        if (!busyRef.current && modal.confirmLabel && !modal.confirmDisabled) confirmRef.current();
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [modal, onClose]);

  return (
    <div className="scrim" onMouseDown={onClose}>
      <div
        className={`dialog ${modal.cloud ? "cloud" : ""}`}
        data-scope={modal.cloud ? "sends-to-claude" : undefined}
        role="dialog"
        aria-modal="true"
        aria-label={modal.title}
        onMouseDown={(event) => event.stopPropagation()}
        data-testid="dialog"
      >
        <h2>
          {modal.cloud && <CloudSendIcon />} {modal.title}
        </h2>
        {modal.cloud && <p className="cloudnote">Sends to Claude. This file leaves your PC.</p>}
        {modal.body && <p>{modal.body}</p>}
        {modal.details && (
          <dl className="sendbox" data-testid="send-details">
            {modal.details.map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        )}
        {modal.option && (
          <label className="sendoption">
            <input
              type="checkbox"
              checked={checked}
              disabled={busy || reloading}
              onChange={(event) => toggleOption(event.target.checked)}
              data-testid="dialog-option"
            />
            {modal.option.label}
          </label>
        )}
        {modal.note && (
          <p className="dim" data-testid="dialog-note">
            {modal.note}
          </p>
        )}
        {modal.warning && (
          <p className="error" role="alert" data-testid="dialog-warning">
            {modal.warning}
          </p>
        )}
        {modal.input && (
          <input
            ref={inputRef}
            type="text"
            value={value}
            spellCheck={false}
            onChange={(event) => setValue(event.target.value)}
            data-testid="dialog-input"
          />
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>
            {modal.cancelLabel || "Cancel"}
          </button>
          {modal.confirmLabel && (
            <button
              ref={okRef}
              type="button"
              className={`btn ${modal.cloud ? "cloudbtn" : modal.danger ? "danger" : "primary"}`}
              disabled={busy || reloading || modal.confirmDisabled}
              onClick={confirm}
              data-testid="dialog-ok"
            >
              {modal.confirmLabel}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
