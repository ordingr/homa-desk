import { fmtAgo, fmtCount, fmtDateTime } from "../format.js";
import { MoonIcon, SunIcon } from "./Icons.jsx";

export default function Header({ entries, builtAt, theme, onToggleTheme, onPanel }) {
  const indexText = builtAt
    ? `index from ${fmtDateTime(builtAt)} (${fmtAgo(builtAt)})`
    : "index not built yet";
  return (
    <header className="topbar">
      <span className="brand">Assistant</span>
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
        aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        title={theme === "dark" ? "Light theme" : "Dark theme"}
        data-testid="theme-toggle"
      >
        {theme === "dark" ? <SunIcon /> : <MoonIcon />}
      </button>
    </header>
  );
}
