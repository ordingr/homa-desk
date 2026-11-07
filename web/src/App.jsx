import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api.js";
import { RANGE_RE, copyText, matchMode, plainPath, quotedPath } from "./format.js";
import Header from "./components/Header.jsx";
import QueryBar, { SORTS } from "./components/QueryBar.jsx";
import SavedPanel from "./components/SavedPanel.jsx";
import ResultList from "./components/ResultList.jsx";
import PreviewPane from "./components/PreviewPane.jsx";
import ContextMenu from "./components/ContextMenu.jsx";
import Modal from "./components/Modal.jsx";
import { CloudOffIcon, LocalIcon } from "./components/Icons.jsx";

const PAGE_SIZE = 100;
const THEME_KEY = "assistant.theme";

function initialTheme() {
  try {
    return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function normaliseModified(value) {
  if (!value) return { kind: "", from: "", to: "" };
  const range = RANGE_RE.exec(value);
  if (range) return { kind: "custom", from: range[1], to: range[2] };
  if (value === "today") return { kind: "today", from: "", to: "" };
  if (value.includes("30")) return { kind: "30d", from: "", to: "" };
  if (value.includes("7")) return { kind: "7d", from: "", to: "" };
  return { kind: "", from: "", to: "" };
}

function Notice({ title, children }) {
  return (
    <main className="notice" data-testid="notice">
      <h1>{title}</h1>
      <p>{children}</p>
    </main>
  );
}

export default function App({ session }) {
  const [expired, setExpired] = useState(false);
  if (session.status === "no-token" || expired) {
    return (
      <Notice title="Open this page from Assistant">
        {expired
          ? "The session ended. Open the full page again from Assistant (F11 in the search box)."
          : "This page needs a launch link from the Assistant app. Press F11 in the search box, or choose Full page."}
      </Notice>
    );
  }
  if (session.status === "failed") {
    return (
      <Notice title="Could not start the session">
        {session.message} Open the full page again from Assistant.
      </Notice>
    );
  }
  return <Workspace initialQuery={session.q} onExpired={() => setExpired(true)} />;
}

function Workspace({ initialQuery, onExpired }) {
  const [theme, setTheme] = useState(initialTheme);
  const [text, setText] = useState(initialQuery);
  const [chips, setChips] = useState([]);
  const [modifiedKind, setModifiedKind] = useState("");
  const [rangeFrom, setRangeFrom] = useState("");
  const [rangeTo, setRangeTo] = useState("");
  const [sort, setSort] = useState("best");

  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [problem, setProblem] = useState(null);
  const [health, setHealth] = useState(null);

  const [selected, setSelected] = useState(() => new Set());
  const [activeId, setActiveId] = useState(null);
  const [anchor, setAnchor] = useState(null);
  const [paneOpen, setPaneOpen] = useState(true);

  const [saved, setSaved] = useState([]);
  const [savedOpen, setSavedOpen] = useState(false);
  const [menu, setMenu] = useState(null);
  const [modal, setModal] = useState(null);
  const [toast, setToast] = useState(null);

  const inputRef = useRef(null);
  const seq = useRef(0);
  const toastTimer = useRef(0);
  const latest = useRef({});

  const rows = result ? result.rows : [];
  const activeRow = useMemo(() => rows.find((row) => row.id === activeId) || null, [rows, activeId]);

  latest.current = {
    text, chips, modifiedKind, rangeFrom, rangeTo, sort, rows, selected, activeId, anchor,
    activeRow, menu, modal, savedOpen,
  };

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* theme preference is optional */
    }
  }, [theme]);

  const showToast = useCallback((message, kind = "ok") => {
    window.clearTimeout(toastTimer.current);
    setToast({ message, kind });
    toastTimer.current = window.setTimeout(() => setToast(null), 4500);
  }, []);

  const fail = useCallback(
    (error) => {
      if (error.status === 401) onExpired();
      return error.message;
    },
    [onExpired],
  );

  const refreshHealth = useCallback(() => {
    api
      .get("/v1/health")
      .then((body) => setHealth(body && body.index ? body.index : null))
      .catch(() => {});
  }, []);

  const loadSaved = useCallback(() => {
    api
      .get("/v1/saved")
      .then((body) => setSaved(body.searches || []))
      .catch((error) => fail(error));
  }, [fail]);

  const modifiedParam = (kind, from, to) => {
    if (kind !== "custom") return kind;
    return from && to ? `${from}..${to}` : null;
  };

  const search = useCallback(
    async (next = {}, append = false) => {
      const now = latest.current;
      const q = next.text ?? now.text;
      const chipList = next.chips ?? now.chips;
      const kind = next.modifiedKind ?? now.modifiedKind;
      const from = next.rangeFrom ?? now.rangeFrom;
      const to = next.rangeTo ?? now.rangeTo;
      const order = next.sort ?? now.sort;
      const modified = modifiedParam(kind, from, to);
      if (modified === null) {
        setProblem({ message: "Pick both dates for a custom range.", status: 0 });
        return;
      }
      const mine = ++seq.current;
      setLoading(true);
      setProblem(null);
      try {
        const body = await api.get("/v1/search", {
          q,
          chips: chipList,
          modified,
          sort: order,
          limit: PAGE_SIZE,
          offset: append ? now.rows.length : 0,
        });
        if (mine !== seq.current) return;
        setResult((previous) =>
          append && previous ? { ...body, rows: [...previous.rows, ...body.rows] } : body,
        );
        if (!append) {
          setSelected(new Set());
          setActiveId(null);
          setAnchor(null);
        }
        if (!health || (body.built_at && body.built_at !== health.built_at)) refreshHealth();
      } catch (error) {
        if (mine !== seq.current) return;
        setProblem({ message: fail(error), status: error.status });
        if (!append) setResult(null);
      } finally {
        if (mine === seq.current) setLoading(false);
      }
    },
    [fail, health, refreshHealth],
  );

  useEffect(() => {
    refreshHealth();
    loadSaved();
    if (initialQuery.trim()) search({ text: initialQuery });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refine = (patch) => {
    const now = latest.current;
    const has = result !== null || now.text.trim() !== "";
    if (has) search(patch);
  };

  const toggleChip = (key) => {
    const next = chips.includes(key) ? chips.filter((c) => c !== key) : [...chips, key];
    setChips(next);
    refine({ chips: next });
  };

  const changeModified = (kind) => {
    setModifiedKind(kind);
    if (kind !== "custom") refine({ modifiedKind: kind });
  };

  const changeRange = (from, to) => {
    setRangeFrom(from);
    setRangeTo(to);
    if (from && to) refine({ modifiedKind: "custom", rangeFrom: from, rangeTo: to });
  };

  const changeSort = (key) => {
    setSort(key);
    refine({ sort: key });
  };

  const copyPaths = useCallback(
    async (targets, quoted = false) => {
      const lines = targets.map((row) => (quoted ? quotedPath(row.path) : plainPath(row.path)));
      const ok = await copyText(lines.join("\n"));
      showToast(
        ok
          ? targets.length > 1
            ? `Copied ${targets.length} paths`
            : "Path copied"
          : "Could not copy to the clipboard",
        ok ? "ok" : "error",
      );
    },
    [showToast],
  );

  const callAction = useCallback(
    async (verb, row, extra = {}) => {
      try {
        const body = await api.post(`/v1/actions/${verb}`, { path: row.path, ...extra });
        return body || { ok: true };
      } catch (error) {
        return { ok: false, message: fail(error) };
      }
    },
    [fail],
  );

  const afterChange = useCallback(() => search({}), [search]);

  const runVerb = useCallback(
    async (verb, row) => {
      const outcome = await callAction(verb, row);
      if (outcome.needs_confirmation) {
        setModal({
          title: outcome.title || "Confirm",
          body: outcome.message || `Run ${row.name}?`,
          confirmLabel: "Run it",
          onConfirm: async () => {
            const again = await callAction(verb, row, { confirm: true });
            if (again.ok === false) return again.message || "The action failed.";
            showToast(again.message || "Done");
            return null;
          },
        });
        return;
      }
      showToast(outcome.message || (outcome.ok === false ? "The action failed." : "Done"),
        outcome.ok === false ? "error" : "ok");
    },
    [callAction, showToast],
  );

  const perform = useCallback(
    (verb, row, targets) => {
      if (!row) return;
      if (verb === "copy_path") return copyPaths(targets || [row]);
      if (verb === "copy_path_quoted") return copyPaths(targets || [row], true);
      if (verb === "rename") {
        setModal({
          title: "Rename",
          body: plainPath(row.path),
          input: true,
          initial: row.name,
          confirmLabel: "Rename",
          onConfirm: async (value) => {
            const outcome = await callAction("rename", row, { new_name: value });
            if (outcome.ok === false) return outcome.message || "The rename failed.";
            showToast(outcome.message || "Renamed");
            afterChange();
            return null;
          },
        });
        return;
      }
      if (verb === "recycle") {
        setModal({
          title: "Move to Recycle Bin",
          body: `${plainPath(row.path)} will go to the Recycle Bin. You can restore it from there.`,
          confirmLabel: "Move to Recycle Bin",
          danger: true,
          onConfirm: async () => {
            let outcome = await callAction("recycle", row);
            if (outcome.needs_confirmation) outcome = await callAction("recycle", row, { confirm: true });
            if (outcome.ok === false) return outcome.message || "The move failed.";
            showToast(outcome.message || "Moved to the Recycle Bin");
            afterChange();
            return null;
          },
        });
        return;
      }
      if (verb === "ask_claude") {
        setModal({
          title: "Ask Claude about this file",
          body: `The contents of ${plainPath(row.path)} will be sent to Claude. Nothing else leaves this PC.`,
          confirmLabel: "Send to Claude",
          cloud: true,
          onConfirm: async () => {
            const outcome = await callAction("ask_claude", row, { confirm: true });
            if (outcome.ok === false) return outcome.message || "The request failed.";
            showToast(outcome.message || "Sent to Claude");
            return null;
          },
        });
        return;
      }
      return runVerb(verb, row);
    },
    [afterChange, callAction, copyPaths, runVerb, showToast],
  );

  const targetsFor = (row) => {
    const now = latest.current;
    if (now.selected.has(row.id) && now.selected.size > 1) {
      return now.rows.filter((r) => now.selected.has(r.id));
    }
    return [row];
  };

  const selectOnly = (row, index) => {
    setSelected(new Set([row.id]));
    setActiveId(row.id);
    setAnchor(index);
    setPaneOpen(true);
  };

  const onRowClick = (event, row, index) => {
    const now = latest.current;
    if (event.shiftKey && now.anchor !== null) {
      const [lo, hi] = [Math.min(now.anchor, index), Math.max(now.anchor, index)];
      setSelected(new Set(now.rows.slice(lo, hi + 1).map((r) => r.id)));
      setActiveId(row.id);
      setPaneOpen(true);
    } else if (event.ctrlKey || event.metaKey) {
      const next = new Set(now.selected);
      if (next.has(row.id)) next.delete(row.id);
      else next.add(row.id);
      setSelected(next);
      setActiveId(next.has(row.id) ? row.id : [...next].pop() ?? null);
      setAnchor(index);
      setPaneOpen(true);
    } else {
      selectOnly(row, index);
    }
  };

  const onRowDoubleClick = (event, row) => {
    perform(event.ctrlKey || event.metaKey ? "reveal" : "open", row);
  };

  const onRowContextMenu = (event, row, index) => {
    event.preventDefault();
    if (event.ctrlKey) {
      copyPaths(targetsFor(row));
      return;
    }
    if (!latest.current.selected.has(row.id)) selectOnly(row, index);
    setMenu({ x: event.clientX, y: event.clientY, row });
  };

  const move = useCallback((delta, extend, absolute) => {
    const now = latest.current;
    if (now.rows.length === 0) return;
    const current = now.rows.findIndex((r) => r.id === now.activeId);
    let target;
    if (absolute === "home") target = 0;
    else if (absolute === "end") target = now.rows.length - 1;
    else if (current === -1) target = delta > 0 ? 0 : now.rows.length - 1;
    else target = Math.max(0, Math.min(now.rows.length - 1, current + delta));
    const row = now.rows[target];
    if (extend) {
      const base = now.anchor ?? (current === -1 ? target : current);
      const [lo, hi] = [Math.min(base, target), Math.max(base, target)];
      setSelected(new Set(now.rows.slice(lo, hi + 1).map((r) => r.id)));
      setAnchor(base);
      setActiveId(row.id);
      setPaneOpen(true);
    } else {
      selectOnly(row, target);
    }
  }, []);

  const clearAll = () => {
    setText("");
    setResult(null);
    setProblem(null);
    setSelected(new Set());
    setActiveId(null);
    setAnchor(null);
    seq.current += 1;
    setLoading(false);
  };

  useEffect(() => {
    const onKeyDown = (event) => {
      const now = latest.current;
      if (now.menu || now.modal) return;
      const tag = event.target.tagName;
      const typing = tag === "INPUT" || tag === "TEXTAREA";
      const control = event.ctrlKey || event.metaKey;
      const key = event.key;

      if (key === "F11") {
        event.preventDefault();
        api.closeFullPage();
        return;
      }
      if (control && key === "Enter") {
        event.preventDefault();
        perform("reveal", now.activeRow);
        return;
      }
      if (control && (key === "m" || key === "M")) {
        event.preventDefault();
        perform("md_vault", now.activeRow);
        return;
      }
      if (control && (key === "s" || key === "S")) {
        event.preventDefault();
        const order = SORTS.map(([k]) => k);
        const next = order[(order.indexOf(now.sort) + 1) % order.length];
        setSort(next);
        refine({ sort: next });
        return;
      }
      if (key === "Escape") {
        event.preventDefault();
        if (now.selected.size > 0) {
          setSelected(new Set());
          setActiveId(null);
          setAnchor(null);
        } else if (now.text !== "" || result !== null) clearAll();
        else api.closeFullPage();
        return;
      }
      if (tag === "SELECT" || (typing && event.target.type === "date")) return;
      if (key === "ArrowDown" || key === "ArrowUp") {
        event.preventDefault();
        move(key === "ArrowDown" ? 1 : -1, event.shiftKey);
      } else if ((key === "Home" || key === "End") && !typing) {
        event.preventDefault();
        move(0, event.shiftKey, key === "Home" ? "home" : "end");
      } else if (key === "Enter" && !typing && tag !== "BUTTON") {
        event.preventDefault();
        perform("open", now.activeRow);
      } else if (key === "ContextMenu" || (key === "F10" && event.shiftKey)) {
        if (!now.activeRow) return;
        event.preventDefault();
        const el = document.querySelector(`[data-path="${CSS.escape(now.activeRow.path)}"]`);
        const box = el ? el.getBoundingClientRect() : { left: 80, bottom: 120 };
        setMenu({ x: box.left + 80, y: box.bottom, row: now.activeRow });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [perform, move, result]);

  const saveCurrent = async (name) => {
    const modified = modifiedParam(modifiedKind, rangeFrom, rangeTo) || "";
    try {
      await api.post("/v1/saved", { name, query: text, chips, modified, sort, mode: "files" });
      showToast(`Saved "${name}"`);
      loadSaved();
    } catch (error) {
      showToast(fail(error), "error");
    }
  };

  const deleteSaved = async (item) => {
    try {
      await api.del(`/v1/saved/${encodeURIComponent(item.name)}`);
      loadSaved();
    } catch (error) {
      showToast(fail(error), "error");
    }
  };

  const runSaved = (item) => {
    const mod = normaliseModified(item.modified);
    const chipList = item.chips || [];
    const order = item.sort || "best";
    setText(item.query);
    setChips(chipList);
    setModifiedKind(mod.kind);
    setRangeFrom(mod.from);
    setRangeTo(mod.to);
    setSort(order);
    setSavedOpen(false);
    search({
      text: item.query,
      chips: chipList,
      modifiedKind: mod.kind,
      rangeFrom: mod.from,
      rangeTo: mod.to,
      sort: order,
    });
  };

  const openPanel = (name) =>
    setModal(
      name === "settings"
        ? {
            title: "Settings",
            body:
              "Search roots, excludes, hotkey and notes are set in the Assistant app on this PC " +
              "(tray icon, then Settings). This page only changes its own theme.",
            cancelLabel: "Close",
          }
        : {
            title: "About",
            body:
              "Assistant full-page search. Files mode runs on this PC; nothing leaves it except " +
              "the explicit Ask Claude action. The page talks only to the local Assistant app.",
            cancelLabel: "Close",
          },
    );

  const builtAt = (result && result.built_at) || (health && health.built_at) || 0;
  const entries = health && typeof health.entries === "number" ? health.entries : null;
  const hasMore = result && result.rows.length < result.total && result.rows.length < 100000;
  const showPane = paneOpen && activeRow;

  return (
    <div className={`app ${showPane ? "with-pane" : ""}`}>
      <Header
        entries={entries}
        builtAt={builtAt}
        theme={theme}
        onToggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
        onPanel={openPanel}
      />
      <QueryBar
        inputRef={inputRef}
        text={text}
        onText={setText}
        onSubmit={() => search({})}
        chips={chips}
        onToggleChip={toggleChip}
        modifiedKind={modifiedKind}
        onModifiedKind={changeModified}
        rangeFrom={rangeFrom}
        rangeTo={rangeTo}
        onRange={changeRange}
        sort={sort}
        onSort={changeSort}
        canSave={text.trim() !== ""}
        onSave={saveCurrent}
        savedOpen={savedOpen}
        onToggleSaved={() => setSavedOpen(!savedOpen)}
        savedCount={saved.length}
      />
      {savedOpen && <SavedPanel items={saved} onRun={runSaved} onDelete={deleteSaved} />}

      <div className="status" role="status" aria-live="polite" data-testid="status">
        <span className="badge local" title="Files search runs on this PC">
          <LocalIcon /> Files &middot; local
        </span>
        {loading && <span className="dim">Searching&hellip;</span>}
        {!loading && result && (
          <>
            <strong className="num" data-testid="count">
              {result.total.toLocaleString("en-US")}
              {result.truncated ? "+" : ""} result{result.total === 1 ? "" : "s"}
            </strong>
            <span className="num dim" data-testid="elapsed">
              {Number(result.elapsed_ms).toFixed(1)} ms
            </span>
            <span className="badge mode" data-testid="mode">
              {matchMode(text)}
            </span>
            {result.interpretation && (
              <span className="interp" title={result.interpretation} data-testid="interpretation">
                {result.interpretation}
              </span>
            )}
            {result.truncated && (
              <span className="badge warn" data-testid="truncated">
                <CloudOffIcon /> partial: refine the query
              </span>
            )}
            {(result.notes || []).map((note) => (
              <span key={note} className="badge warn" data-testid="note">
                {note}
              </span>
            ))}
          </>
        )}
        {problem && (
          <span className="problem" role="alert" data-testid="problem">
            {problem.message}
          </span>
        )}
      </div>

      <main className="split">
        <section className="listwrap" aria-label="Results">
          {!result && !problem && !loading && (
            <p className="empty">Type a query and press Enter. Try <code>design md</code>.</p>
          )}
          {problem && problem.status === 503 && (
            <p className="empty">The index is not ready yet. Try again in a moment.</p>
          )}
          {result && result.rows.length === 0 && (
            <p className="empty" data-testid="empty">
              No files match. Try fewer words, or clear the filters.
            </p>
          )}
          {result && result.rows.length > 0 && (
            <>
              <ResultList
                rows={result.rows}
                selected={selected}
                activeId={activeId}
                onRowClick={onRowClick}
                onRowDoubleClick={onRowDoubleClick}
                onRowContextMenu={onRowContextMenu}
              />
              {hasMore && (
                <button
                  type="button"
                  className="btn more"
                  onClick={() => search({}, true)}
                  data-testid="more"
                >
                  Show more
                </button>
              )}
            </>
          )}
        </section>
        {showPane && (
          <PreviewPane
            row={activeRow}
            extraSelected={Math.max(0, selected.size - 1)}
            onCopy={(row) => copyPaths([row])}
            onClose={() => setPaneOpen(false)}
          />
        )}
      </main>

      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          onClose={() => setMenu(null)}
          onPick={(verb) => {
            const row = menu.row;
            setMenu(null);
            perform(verb, row, targetsFor(row));
          }}
        />
      )}
      {modal && <Modal modal={modal} onClose={() => setModal(null)} />}
      {toast && (
        <div className={`toast ${toast.kind}`} role="status" data-testid="toast">
          {toast.message}
        </div>
      )}
    </div>
  );
}
