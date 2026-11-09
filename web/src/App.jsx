import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api.js";
import { RANGE_RE, copyText, fmtSize, matchMode, plainPath, quotedPath } from "./format.js";
import AskPane from "./components/AskPane.jsx";
import FolderListing from "./components/FolderListing.jsx";
import Header, { NEXT } from "./components/Header.jsx";
import QueryBar, { SORTS } from "./components/QueryBar.jsx";
import SavedPanel from "./components/SavedPanel.jsx";
import ResultList from "./components/ResultList.jsx";
import PreviewPane, { EXPAND_HINT } from "./components/PreviewPane.jsx";
import SplitHandle, { loadSplit, saveSplit } from "./components/SplitHandle.jsx";
import { loadZoom, saveZoom } from "./preview/zoom.js";
import ContextMenu from "./components/ContextMenu.jsx";
import { buildShortcuts, chordLabel, defaultShortcuts, matches, parseChord } from "./shortcuts.js";
import Modal from "./components/Modal.jsx";
import { CloudOffIcon, LocalIcon } from "./components/Icons.jsx";

const PAGE_SIZE = 100;
const SEARCH_DELAY_MS = 150;
const THEME_KEY = "homa.theme";
const LEGACY_THEME_KEY = "assistant.theme";

const THEME_PREFS = ["dark", "light", "system"];
const DARK_QUERY = "(prefers-color-scheme: dark)";
const CLAUDE_MISSING = "Claude Code not found on this machine";
const MODIFIED_CYCLE = ["", "today", "7d", "30d"];
const EXPAND_CHORD = parseChord(EXPAND_HINT);

function storedTheme() {
  try {
    const value = localStorage.getItem(THEME_KEY) ?? localStorage.getItem(LEGACY_THEME_KEY);
    return THEME_PREFS.includes(value) ? value : null;
  } catch {
    return null;
  }
}

function systemPrefersDark() {
  return window.matchMedia ? window.matchMedia(DARK_QUERY).matches : true;
}

const bytes = (n) => Number(n).toLocaleString("en-US");

function sendsWhole(summary) {
  return summary.mode ? summary.mode === "whole_file" : summary.whole_file === true;
}

function sendDetails(summary) {
  const whole = sendsWhole(summary);
  const details = [
    ["File", plainPath(summary.path)],
    ["Sends", `${bytes(summary.bytes_to_send)} of ${bytes(summary.file_bytes)} bytes (${fmtSize(summary.bytes_to_send)})`],
    ["Form", whole ? "Whole file" : "Preview text only (the first part of the file)"],
  ];
  if (summary.what) details.push(["What", summary.what]);
  if ("office_text" in summary) {
    details.push(["Office text", summary.office_text ? "Yes, text extracted from an Office file" : "No"]);
  }
  if ("account" in summary) details.push(["Account", summary.account || "Not known"]);
  return details;
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
      <Notice title="Open this page from Homa">
        {expired
          ? "The session ended. Open the full page again from Homa (F11 in the search box)."
          : "This page needs a launch link from the Homa app. Press F11 in the search box, or choose Full page."}
      </Notice>
    );
  }
  if (session.status === "failed") {
    return (
      <Notice title="Could not start the session">
        {session.message} Open the full page again from Homa.
      </Notice>
    );
  }
  return <Workspace initialQuery={session.q} onExpired={() => setExpired(true)} />;
}

function completion(word, name) {
  const lower = name.toLowerCase();
  const want = word.toLowerCase();
  for (let at = lower.indexOf(want); at !== -1; at = lower.indexOf(want, at + 1)) {
    if (at > 0 && /[a-z0-9]/.test(lower[at - 1])) continue;
    const from = at + want.length;
    const rest = name.slice(from);
    const stop = rest.search(/[\s._-]/);
    const tail = stop === -1 ? rest : rest.slice(0, stop);
    return word === want ? tail.toLowerCase() : tail;
  }
  return "";
}

function Workspace({ initialQuery, onExpired }) {
  const [themePref, setThemePref] = useState(storedTheme);
  const [appTheme, setAppTheme] = useState(null);
  const [systemDark, setSystemDark] = useState(systemPrefersDark);
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
  const [expanded, setExpanded] = useState(false);
  const [splitFrac, setSplitFrac] = useState(loadSplit);
  const [zoom, setZoom] = useState(loadZoom);

  const [saved, setSaved] = useState([]);
  const [savedOpen, setSavedOpen] = useState(false);
  const [menu, setMenu] = useState(null);
  const [modal, setModal] = useState(null);
  const [toast, setToast] = useState(null);
  const [claude, setClaude] = useState({ available: false, reason: CLAUDE_MISSING });
  const [ask, setAsk] = useState(null);
  const [listing, setListing] = useState(null);
  const [searchedText, setSearchedText] = useState("");
  const [shortcuts, setShortcuts] = useState(defaultShortcuts);
  const [continuing, setContinuing] = useState(false);
  const [continueMissing, setContinueMissing] = useState(false);

  const inputRef = useRef(null);
  const splitRef = useRef(null);
  const seq = useRef(0);
  const inflight = useRef(null);
  const debounce = useRef(0);
  const lastRun = useRef("");
  const toastTimer = useRef(0);
  const askSeq = useRef(0);
  const latest = useRef({});

  const [ghostOff, setGhostOff] = useState(null);
  const rows = result ? result.rows : [];
  const activeRow = useMemo(() => rows.find((row) => row.id === activeId) || null, [rows, activeId]);
  const showPane = paneOpen && activeRow;
  const expandedNow = expanded && Boolean(showPane);

  latest.current = {
    text, chips, modifiedKind, rangeFrom, rangeTo, sort, rows, selected, activeId, anchor,
    activeRow, menu, modal, savedOpen, ask, listing, searchedText, shortcuts, expanded: expandedNow,
    paneOpen,
  };

  const ghost = useMemo(() => {
    if (text === ghostOff) return "";
    const typed = /\S+$/.exec(text);
    if (!typed || /\s$/.test(text)) return "";
    const candidates = [];
    if (result && result.rows.length > 0 && text === searchedText) candidates.push(result.rows[0].name);
    saved.forEach((item) => candidates.push(item.name));
    for (const name of candidates) {
      const suffix = completion(typed[0], name || "");
      if (suffix) return suffix;
    }
    return "";
  }, [text, ghostOff, result, searchedText, saved]);

  const themeChoice = themePref ?? (THEME_PREFS.includes(appTheme) ? appTheme : "dark");
  const theme = themeChoice === "system" ? (systemDark ? "dark" : "light") : themeChoice;

  useEffect(() => {
    if (expanded && !showPane) setExpanded(false);
  }, [expanded, showPane]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const query = window.matchMedia(DARK_QUERY);
    const onChange = (event) => setSystemDark(event.matches);
    setSystemDark(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  const chooseTheme = async () => {
    const next = NEXT[themeChoice];
    const previousPref = themePref;
    const previousTheme = theme;
    setThemePref(next);
    try {
      const body = await api.post("/v1/settings/theme", { theme: next });
      const saved = body && (body.theme ?? (body.settings && body.settings.theme));
      setAppTheme(THEME_PREFS.includes(saved) ? saved : next);
      setThemePref(null);
      try {
        localStorage.removeItem(THEME_KEY);
        localStorage.removeItem(LEGACY_THEME_KEY);
      } catch {
        /* theme preference is optional */
      }
    } catch (error) {
      if (error.status === 404 || error.code === "route_missing") {
        try {
          localStorage.setItem(THEME_KEY, next);
        } catch {
          /* theme preference is optional */
        }
        return;
      }
      setThemePref(previousPref);
      if (error.status === 401) {
        document.documentElement.dataset.theme = previousTheme;
        onExpired();
        return;
      }
      showToast(error.message || "Could not save the theme.", "error");
    }
  };

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
      window.clearTimeout(debounce.current);
      if (inflight.current) inflight.current.abort();
      const controller = new AbortController();
      inflight.current = controller;
      if (!append) lastRun.current = q.trim();
      const mine = ++seq.current;
      setLoading(true);
      setProblem(null);
      try {
        const body = await api.get(
          "/v1/search",
          {
            q,
            chips: chipList,
            modified,
            sort: order,
            limit: PAGE_SIZE,
            offset: append ? now.rows.length : 0,
          },
          controller.signal,
        );
        if (mine !== seq.current) return;
        setResult((previous) =>
          append && previous ? { ...body, rows: [...previous.rows, ...body.rows] } : body,
        );
        if (!append) {
          setSearchedText(q);
          const keep = new Set(body.rows.map((r) => r.id));
          if (now.activeId !== null && keep.has(now.activeId)) {
            setSelected((previous) => new Set([...previous].filter((id) => keep.has(id))));
          } else {
            setSelected(new Set());
            setActiveId(null);
            setAnchor(null);
          }
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
    api
      .get("/v1/settings")
      .then((body) => {
        setAppTheme((body && (body.theme ?? (body.settings && body.settings.theme))) || null);
        setShortcuts(buildShortcuts(body && body.shortcuts));
      })
      .catch(() => {});
    api
      .get("/v1/actions/ask_claude/status")
      .then((body) =>
        setClaude({
          available: body.available === true,
          reason: body.available === true ? null : body.reason || CLAUDE_MISSING,
        }),
      )
      .catch(() => setClaude({ available: false, reason: CLAUDE_MISSING }));
    if (initialQuery.trim()) search({ text: initialQuery });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const trimmed = text.trim();
    if (trimmed === lastRun.current) return undefined;
    if (trimmed === "") {
      clearAll();
      return undefined;
    }
    debounce.current = window.setTimeout(() => search({ text }), SEARCH_DELAY_MS);
    return () => window.clearTimeout(debounce.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  useEffect(() => {
    const trimmed = text.trim();
    const url = new URL(window.location.href);
    if (trimmed) url.searchParams.set("q", text);
    else url.searchParams.delete("q");
    const rest = url.searchParams.toString();
    window.history.replaceState(null, "", url.pathname + (rest ? "?" + rest : "") + url.hash);
  }, [text]);

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

  const sendToClaude = useCallback(
    async (row, wholeFile = false) => {
      const mine = ++askSeq.current;
      setAsk({ status: "loading", path: row.path });
      const outcome = await callAction("ask_claude", row, wholeFile ? { confirm: true, whole_file: true } : { confirm: true });
      if (mine !== askSeq.current) return;
      if (outcome.ok === false || typeof outcome.answer !== "string") {
        setAsk({
          status: "error",
          path: row.path,
          message: outcome.message || "Claude did not return an answer.",
        });
      } else {
        setAsk({ status: "done", path: row.path, answer: outcome.answer });
      }
    },
    [callAction],
  );

  const askModal = useCallback(
    (row, outcome, wholeFile, reload) => {
      const summary = outcome.summary;
      const blocked = summary.available === false;
      return {
        title: "Ask Claude about this file",
        body: outcome.message,
        details: sendDetails(summary),
        warning: blocked ? summary.reason || CLAUDE_MISSING : null,
        note:
          wholeFile && !sendsWhole(summary)
            ? `Only ${bytes(summary.bytes_to_send)} bytes fit in one request, so the file is cut off.`
            : null,
        option:
          wholeFile || !sendsWhole(summary)
            ? {
                label: `Send the whole file (${bytes(summary.file_bytes)} bytes)`,
                checked: wholeFile,
                onChange: reload,
              }
            : null,
        confirmLabel: "Send to Claude",
        confirmDisabled: blocked,
        cloud: true,
        onConfirm: () => {
          sendToClaude(row, wholeFile);
          return null;
        },
      };
    },
    [sendToClaude],
  );

  const askClaude = useCallback(
    async (row) => {
      const request = (wholeFile) =>
        callAction("ask_claude", row, wholeFile ? { confirm: false, whole_file: true } : { confirm: false });
      const reload = async (wholeFile) => {
        const again = await request(wholeFile);
        if (!again.needs_confirmation || !again.summary) {
          showToast(again.message || "Could not refresh what will be sent.", "error");
          return false;
        }
        setModal(askModal(row, again, wholeFile, reload));
        return true;
      };
      const outcome = await request(false);
      if (!outcome.needs_confirmation || !outcome.summary) {
        showToast(outcome.message || "Ask Claude is not available for this file.", "error");
        return;
      }
      setModal(askModal(row, outcome, false, reload));
    },
    [askModal, callAction, showToast],
  );

  const continueInClaudeCode = async () => {
    if (!ask || continuing) return;
    setContinuing(true);
    try {
      await api.post("/v1/actions/ask_claude/continue", { path: ask.path });
      showToast("Opened in Claude Code");
    } catch (error) {
      if (error.code === "route_missing") {
        setContinueMissing(true);
      } else {
        showToast(fail(error), "error");
      }
    } finally {
      setContinuing(false);
    }
  };

  const closeAsk = () => {
    askSeq.current += 1;
    setAsk(null);
  };

  const loadListing = useCallback(
    async (path, stack) => {
      const outcome = await callAction("folder_listing", { path });
      if (outcome.ok === false || !Array.isArray(outcome.rows)) {
        showToast(outcome.message || "The folder could not be listed.", "error");
        return;
      }
      setListing({ ...outcome, stack });
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
      if (verb === "ask_claude") return askClaude(row);
      if (verb === "folder_listing") return loadListing(row.path, []);
      return runVerb(verb, row);
    },
    [afterChange, askClaude, callAction, copyPaths, loadListing, runVerb, showToast],
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

  const changeSplit = (frac, commit) => {
    setSplitFrac(frac);
    if (commit) saveSplit(frac);
  };

  const changeZoom = (value) => {
    setZoom(value);
    saveZoom(value);
  };

  const clearAll = () => {
    window.clearTimeout(debounce.current);
    if (inflight.current) inflight.current.abort();
    lastRun.current = "";
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
      if (now.menu || now.modal || now.listing) return;
      const tag = event.target.tagName;
      const typing = tag === "INPUT" || tag === "TEXTAREA";
      const key = event.key;
      const chord = (action) => matches(event, now.shortcuts[action]);

      if (chord("full_page")) {
        event.preventDefault();
        api.closeFullPage();
        return;
      }
      if (now.ask) {
        if (chord("close")) {
          event.preventDefault();
          closeAsk();
        }
        return;
      }
      const inQuery = event.target === inputRef.current;
      const otherField = tag === "SELECT" || (typing && !inQuery);
      const hasSelection = window.getSelection().toString() !== "";
      const rowLive =
        now.activeRow && !otherField && !hasSelection && (!inQuery || now.text === now.searchedText);
      if (rowLive && chord("copy_path")) {
        event.preventDefault();
        copyPaths(targetsFor(now.activeRow));
        return;
      }
      if (rowLive && chord("recycle")) {
        event.preventDefault();
        perform("recycle", now.activeRow);
        return;
      }
      if (now.activeRow && !otherField && chord("rename")) {
        event.preventDefault();
        perform("rename", now.activeRow);
        return;
      }
      if (now.activeRow && !otherField && chord("properties")) {
        event.preventDefault();
        perform("properties", now.activeRow);
        return;
      }
      if (now.activeRow && !otherField && chord("folder_listing")) {
        event.preventDefault();
        loadListing(now.activeRow.path, []);
        return;
      }
      if (chord("cycle_date")) {
        event.preventDefault();
        changeModified(MODIFIED_CYCLE[(MODIFIED_CYCLE.indexOf(now.modifiedKind) + 1) % MODIFIED_CYCLE.length]);
        return;
      }
      if (chord("reveal")) {
        event.preventDefault();
        perform("reveal", now.activeRow);
        return;
      }
      if (chord("md_vault")) {
        event.preventDefault();
        perform("md_vault", now.activeRow);
        return;
      }
      if (chord("save_search")) {
        event.preventDefault();
        if (now.text.trim()) saveCurrent(now.text.trim().slice(0, 80));
        else showToast("Type a query to save.", "error");
        return;
      }
      if (chord("cycle_sort")) {
        event.preventDefault();
        const order = SORTS.map(([k]) => k);
        const next = order[(order.indexOf(now.sort) + 1) % order.length];
        setSort(next);
        refine({ sort: next });
        return;
      }
      if (chord("close") && now.expanded) {
        event.preventDefault();
        setExpanded(false);
        return;
      }
      if (now.activeRow && now.paneOpen && matches(event, EXPAND_CHORD)) {
        event.preventDefault();
        setExpanded(!now.expanded);
        return;
      }
      if (chord("close")) {
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
      } else if (chord("open") && !typing && tag !== "BUTTON") {
        event.preventDefault();
        perform("open", now.activeRow);
      } else if (key === "ContextMenu" || chord("context_menu")) {
        if (!now.activeRow) return;
        event.preventDefault();
        const el = document.querySelector(`[data-path="${CSS.escape(now.activeRow.path)}"]`);
        const box = el ? el.getBoundingClientRect() : { left: 80, bottom: 120 };
        setMenu({ x: box.left + 80, y: box.bottom, row: now.activeRow });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

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
              "Search roots, excludes, hotkey and notes are set in the Homa app on this PC " +
              "(tray icon, then Settings). This page only changes its own theme.",
            cancelLabel: "Close",
          }
        : {
            title: "About",
            body:
              "Homa full-page search. Files mode runs on this PC; nothing leaves it except " +
              "the explicit Ask Claude action. The page talks only to the local Homa app.",
            cancelLabel: "Close",
          },
    );

  const builtAt = (result && result.built_at) || (health && health.built_at) || 0;
  const entries = health && typeof health.entries === "number" ? health.entries : null;
  const hasMore = result && result.rows.length < result.total && result.rows.length < 100000;
  return (
    <div className={`app ${showPane ? "with-pane" : ""}`}>
      <Header
        entries={entries}
        builtAt={builtAt}
        theme={themeChoice}
        onToggleTheme={chooseTheme}
        onPanel={openPanel}
      />
      <QueryBar
        inputRef={inputRef}
        text={text}
        onText={setText}
        onSubmit={() => search({})}
        ghost={ghost}
        onAcceptGhost={() => setText(text + ghost)}
        onDismissGhost={() => setGhostOff(text)}
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

      <main
        ref={splitRef}
        className={`split ${expandedNow ? "expanded" : ""}`}
        style={{ "--pane-basis": `${(splitFrac * 100).toFixed(2)}%` }}
      >
        <section className="listwrap" aria-label="Results" aria-hidden={expandedNow || undefined}>
          {!result && !problem && !loading && (
            <p className="empty">Start typing to search. Try <code>design md</code>.</p>
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
        {showPane && !expandedNow && (
          <SplitHandle containerRef={splitRef} frac={splitFrac} onChange={changeSplit} />
        )}
        {showPane && (
          <PreviewPane
            row={activeRow}
            extraSelected={Math.max(0, selected.size - 1)}
            expanded={expandedNow}
            zoom={zoom}
            onZoom={changeZoom}
            onToggleExpand={() => setExpanded(!expandedNow)}
            onCopy={(row) => copyPaths([row])}
            onClose={() => setPaneOpen(false)}
          />
        )}
      </main>

      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          claude={claude}
          shortcuts={shortcuts}
          onClose={() => setMenu(null)}
          onPick={(verb) => {
            const row = menu.row;
            setMenu(null);
            perform(verb, row, targetsFor(row));
          }}
        />
      )}
      {modal && <Modal modal={modal} onClose={() => setModal(null)} />}
      {ask && (
        <AskPane
          ask={ask}
          closeKey={chordLabel(shortcuts.close)}
          continuing={continuing}
          continueMissing={continueMissing}
          onContinue={continueInClaudeCode}
          onCopy={async () => {
            const ok = await copyText(ask.answer);
            showToast(ok ? "Answer copied" : "Could not copy to the clipboard", ok ? "ok" : "error");
          }}
          onClose={closeAsk}
        />
      )}
      {listing && (
        <FolderListing
          listing={listing}
          canGoBack={listing.stack.length > 0}
          onBack={() => loadListing(listing.stack[listing.stack.length - 1], listing.stack.slice(0, -1))}
          onOpenFolder={(item) => loadListing(item.path, [...listing.stack, listing.folder])}
          onOpenFile={(item) => perform("open", item)}
          onClose={() => setListing(null)}
        />
      )}
      {toast && (
        <div className={`toast ${toast.kind}`} role="status" data-testid="toast">
          {toast.message}
        </div>
      )}
    </div>
  );
}
