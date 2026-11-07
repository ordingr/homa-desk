import { Component } from "react";
import { MarkdownText } from "../preview/MarkdownView.jsx";
import { CloseIcon, CloudSendIcon, CopyIcon } from "./Icons.jsx";
import { plainPath } from "../format.js";

class MarkdownGuard extends Component {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) return <pre className="pv-code">{this.props.text}</pre>;
    return <MarkdownText text={this.props.text} />;
  }
}

export default function AskPane({ ask, closeKey, continuing, continueMissing, onContinue, onCopy, onClose }) {
  return (
    <aside className="askpane" aria-label="Claude's answer" data-scope="sends-to-claude" data-testid="ask-pane">
      <div className="pane-head">
        <div className="pane-title">
          <strong>
            <CloudSendIcon /> Claude's answer
          </strong>
          <span className="dim" title={plainPath(ask.path)}>
            {plainPath(ask.path)}
          </span>
        </div>
        <div className="pane-actions">
          <button
            type="button"
            className="btn cloudbtn continue-btn"
            disabled={ask.status !== "done" || continuing || continueMissing}
            title={
              continueMissing
                ? "Not available in this build"
                : "Hands this answer's file to an interactive Claude Code session (sends to Claude)"
            }
            data-scope="sends-to-claude"
            onClick={onContinue}
            data-testid="ask-continue"
          >
            <CloudSendIcon /> {continueMissing ? "Continue in Claude Code: not available" : "Continue in Claude Code"}
          </button>
          <button
            type="button"
            className="icon-btn"
            aria-label="Copy answer"
            title="Copy answer"
            disabled={ask.status !== "done"}
            onClick={onCopy}
            data-testid="ask-copy"
          >
            <CopyIcon />
          </button>
          <button
            type="button"
            className="icon-btn"
            aria-label="Close answer"
            title={`Close answer (${closeKey})`}
            onClick={onClose}
            data-testid="ask-close"
          >
            <CloseIcon />
          </button>
        </div>
      </div>
      <div className="pane-body" data-testid="ask-body">
        {ask.status === "loading" && <p className="dim" data-testid="ask-loading">Waiting for Claude...</p>}
        {ask.status === "error" && (
          <div className="pv-notice pv-warn" role="alert" data-testid="ask-notice">
            <strong>No answer</strong>
            <p className="dim">{ask.message}</p>
          </div>
        )}
        {ask.status === "done" && (
          <div className="pv-md" data-testid="ask-answer">
            <MarkdownGuard text={ask.answer} />
          </div>
        )}
      </div>
    </aside>
  );
}
