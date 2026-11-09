import { fmtAgo, fmtCount, fmtDateTime } from "../format.js";
import { MonitorIcon, MoonIcon, SunIcon } from "./Icons.jsx";

export const NEXT = { dark: "light", light: "system", system: "dark" };

export default function Header({ entries, builtAt, theme, onToggleTheme, onPanel }) {
  const indexText = builtAt
    ? `index from ${fmtDateTime(builtAt)} (${fmtAgo(builtAt)})`
    : "index not built yet";
  return (
    <header className="topbar">
      <span className="brand">Homa</span>
      <span className="topbar-stat" data-testid="entries">
        {entries === null ? "" : `${fmtCount(entries)} entries`}
      </span>
      <span className="topbar-stat dim" data-testid="index-age">
        {indexText}
      </span>
      <span className="spacer" />
      <button type="button" className="link-btn" onClick={() => onPanel("settings")}>
        Settings
      </button>
      <button type="button" className="link-btn" onClick={() => onPanel("about")}>
        About
      </button>
      <button
        type="button"
        className="icon-btn"
        onClick={onToggleTheme}
        aria-label={`Theme: ${theme}. Switch to ${NEXT[theme]} theme`}
        title={`Theme: ${theme} (click for ${NEXT[theme]})`}
        data-testid="theme-toggle"
      >
        {theme === "dark" ? <MoonIcon /> : theme === "light" ? <SunIcon /> : <MonitorIcon />}
      </button>
    </header>
  );
}
