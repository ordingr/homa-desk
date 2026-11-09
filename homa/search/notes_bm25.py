"""Ask notes: BM25 over the markdown files under the user's notes folder.

Everything is local. The index is a gzip JSON under the Homa home.
Files carrying a cloud-placeholder attribute are never opened (reading one
would make OneDrive download it); they are only counted.

Row shape handed to the popup (documented for search.modes):
    Row(path: str, title: str, passage: str, score: float,
        highlights: list[tuple[int, int]])
`passage` is markdown; `highlights` are (start, end) character offsets into
`passage` covering words that match the query.
"""
import gzip
import heapq
import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .. import paths

log = logging.getLogger("homa.notes")

ENV_NOTES_DIR = "HOMA_NOTES_DIR"
UNSET_MESSAGE = "Set your notes folder in Settings"
BUILDING_MESSAGE = "building index…"
FOOTER = "search over your notes; nothing left this PC"

MAX_FILE_BYTES = 2 * 1024 * 1024
SWEEP_SECONDS = 60.0
PASSAGE_CHARS = 900
MAX_PER_FILE = 3
INDEX_VERSION = 1

_ATTR_OFFLINE = 0x1000
_ATTR_RECALL_ON_OPEN = 0x40000
_ATTR_RECALL_ON_DATA_ACCESS = 0x400000
_ATTR_REPARSE = 0x400
_PLACEHOLDER_MASK = _ATTR_OFFLINE | _ATTR_RECALL_ON_OPEN | _ATTR_RECALL_ON_DATA_ACCESS

_SKIP_DIRS = {"node_modules", "$recycle.bin", "system volume information"}
_WORD = re.compile(r"\w+", re.UNICODE)
_STOP = frozenset("a an and are as at be by for from in is it of on or that the this to was with".split())
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+\S")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_K1 = 1.5
_B = 0.75


@dataclass
class Row:
    path: str
    title: str
    passage: str
    score: float
    highlights: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


def stem(word: str) -> str:
    n = len(word)
    if n > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if n > 5 and word.endswith("ing"):
        return word[:-3]
    if n > 4 and word.endswith("ed"):
        return word[:-2]
    if n > 4 and word.endswith("sses"):
        return word[:-2]
    if n > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokenise(text: str) -> list:
    out = []
    for m in _WORD.finditer(text.lower()):
        w = m.group()
        if w in _STOP or (len(w) < 2 and not w.isdigit()):
            continue
        out.append(stem(w))
    return out


def default_index_path() -> Path:
    return paths.home() / "index" / "notes.json.gz"


def resolve_notes_dir(settings: Any = None) -> str:
    env = (paths.env("NOTES_DIR") or "").strip()
    if env:
        return env
    if settings is not None:
        try:
            cfg = settings.get("notes") or {}
            value = cfg.get("dir", "") if isinstance(cfg, dict) else ""
            return value.strip() if isinstance(value, str) else ""
        except Exception:
            return ""
    return ""


def _split_front_matter(text: str) -> tuple:
    if text.startswith("---"):
        lines = text.split("\n", 200)
        if lines[0].strip() == "---":
            for i in range(1, min(len(lines), 200)):
                if lines[i].strip() in ("---", "..."):
                    meta = "\n".join(lines[1:i])
                    rest = "\n".join(lines[i + 1:]) if i + 1 < len(lines) else ""
                    return meta, rest
    return "", text


def split_passages(text: str) -> tuple:
    """Return (title or '', [passage markdown, ...])."""
    meta, body = _split_front_matter(text.replace("\r\n", "\n").replace("\r", "\n"))
    title = ""
    m = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", meta, re.MULTILINE | re.IGNORECASE)
    if m:
        title = m.group(1).strip()

    sections: list = []
    heading = ""
    blocks: list = []
    cur: list = []
    in_fence = False

    def end_block() -> None:
        if cur:
            blocks.append("\n".join(cur))
            cur.clear()

    def end_section() -> None:
        end_block()
        if blocks:
            sections.append((heading, list(blocks)))
        blocks.clear()

    for line in body.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            cur.append(line)
            continue
        if in_fence:
            cur.append(line)
            continue
        if _HEADING.match(line):
            end_section()
            heading = line.strip()
            if not title and line.lstrip().startswith("# "):
                title = line.lstrip()[2:].strip().strip("#").strip()
            continue
        if not line.strip():
            end_block()
        else:
            cur.append(line)
    end_section()

    passages: list = []
    for head, blks in sections:
        acc: list = []
        size = 0

        def flush() -> None:
            nonlocal acc, size
            if acc:
                body_text = "\n\n".join(acc)
                passages.append(f"{head}\n\n{body_text}" if head else body_text)
            acc, size = [], 0

        for blk in blks:
            pieces = [blk]
            if len(blk) > PASSAGE_CHARS * 1.6:
                pieces, part, plen = [], [], 0
                for ln in blk.split("\n"):
                    if plen + len(ln) > PASSAGE_CHARS and part:
                        pieces.append("\n".join(part))
                        part, plen = [], 0
                    part.append(ln)
                    plen += len(ln) + 1
                if part:
                    pieces.append("\n".join(part))
            for piece in pieces:
                if size and size + len(piece) > PASSAGE_CHARS:
                    flush()
                acc.append(piece)
                size += len(piece)
        flush()
        if not blks and head:
            passages.append(head)
    return title, passages


def _read_note(path: str) -> Optional[str]:
    with open(path, "rb") as fh:
        raw = fh.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        return None
    return raw.decode("utf-8-sig", errors="replace")


class NotesIndex:
    def __init__(self, notes_dir: str, index_path: Optional[Any] = None, scheduler: Any = None) -> None:
        self.dir = os.path.abspath(notes_dir)
        self.index_path = Path(index_path) if index_path else default_index_path()
        self.scheduler = scheduler
        self._lock = threading.RLock()
        self._sync_lock = threading.Lock()
        self._files: dict = {}
        self._pids: dict = {}
        self._passages: dict = {}
        self._postings: dict = {}
        self._next_pid = 0
        self._total_len = 0
        self._thread: Optional[threading.Thread] = None
        self._handle: Any = None
        self._ready = False
        self._error = ""
        self.skipped_placeholder = 0
        self.skipped_large = 0
        self.built_at: Optional[float] = None
        self.last_build_s: Optional[float] = None

    @property
    def building(self) -> bool:
        return not self._ready and not self._error

    @property
    def status_text(self) -> str:
        if self._error:
            return self._error
        if not self._ready:
            return BUILDING_MESSAGE
        return f"{len(self._files)} notes, {len(self._passages)} passages"

    @property
    def note_count(self) -> int:
        return len(self._files)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._initial, name="notes-index", daemon=True)
        self._thread.start()
        if self.scheduler is not None:
            self._handle = self.scheduler.every(SWEEP_SECONDS, self.refresh)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    def wait_ready(self, timeout: float = 60.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._ready or self._error:
                return self._ready
            time.sleep(0.01)
        return self._ready

    def hint(self) -> None:
        """A watcher saw something change; resync off the caller's thread."""
        threading.Thread(target=self.refresh, name="notes-hint", daemon=True).start()

    def _initial(self) -> None:
        try:
            loaded = self._load()
            if loaded:
                self._ready = True
            self._sync(save=True)
            self._ready = True
        except Exception as exc:
            log.exception("notes index build failed")
            self._error = f"Could not read the notes folder ({type(exc).__name__})"

    def refresh(self) -> None:
        if not self._ready:
            return
        try:
            self._sync(save=True)
        except Exception:
            log.exception("notes refresh failed")

    def _walk(self):
        stack = [self.dir]
        while stack:
            current = stack.pop()
            try:
                it = os.scandir(current)
            except OSError:
                continue
            with it:
                for entry in it:
                    try:
                        st = entry.stat(follow_symlinks=False)
                        attrs = getattr(st, "st_file_attributes", 0)
                        if entry.is_symlink() or attrs & _ATTR_REPARSE and entry.is_dir(follow_symlinks=False):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            name = entry.name.lower()
                            if not name.startswith(".") and name not in _SKIP_DIRS:
                                stack.append(entry.path)
                        elif entry.name.lower().endswith(".md"):
                            yield entry.path, st.st_mtime_ns, st.st_size, attrs
                    except OSError:
                        continue

    def _sync(self, save: bool) -> None:
        if not self._sync_lock.acquire(blocking=False):
            return
        started = time.time()
        changed = False
        try:
            if not os.path.isdir(self.dir):
                raise FileNotFoundError(self.dir)
            seen = set()
            placeholders = large = 0
            for path, mtime, size, attrs in self._walk():
                rel = os.path.relpath(path, self.dir)
                if attrs & _PLACEHOLDER_MASK or _denied(path):
                    placeholders += 1
                    continue
                if size > MAX_FILE_BYTES:
                    large += 1
                    continue
                seen.add(rel)
                known = self._files.get(rel)
                if known and known["m"] == mtime and known["s"] == size:
                    continue
                try:
                    text = _read_note(path)
                except OSError:
                    continue
                if text is None:
                    large += 1
                    seen.discard(rel)
                    continue
                title, passages = split_passages(text)
                parsed = []
                for p in passages:
                    toks = tokenise(p)
                    if not toks:
                        continue
                    tf: dict = {}
                    for t in toks:
                        tf[t] = tf.get(t, 0) + 1
                    parsed.append((p, tf, len(toks)))
                self._put(rel, {"m": mtime, "s": size, "t": title or Path(rel).stem}, parsed)
                changed = True
            with self._lock:
                self.skipped_placeholder = placeholders
                self.skipped_large = large
            for rel in [r for r in list(self._files) if r not in seen]:
                self._drop(rel)
                changed = True
            self.built_at = time.time()
            self.last_build_s = self.built_at - started
            if changed and save:
                self._save()
        finally:
            self._sync_lock.release()

    def _put(self, rel: str, meta: dict, parsed: list) -> None:
        with self._lock:
            self._drop(rel)
            pids = []
            for text, tf, length in parsed:
                pid = self._next_pid
                self._next_pid += 1
                self._passages[pid] = (rel, text, length, tf)
                self._total_len += length
                for term, n in tf.items():
                    self._postings.setdefault(term, {})[pid] = n
                pids.append(pid)
            self._files[rel] = meta
            self._pids[rel] = pids

    def _drop(self, rel: str) -> None:
        with self._lock:
            for pid in self._pids.pop(rel, ()):
                _, _, length, tf = self._passages.pop(pid)
                self._total_len -= length
                for term in tf:
                    post = self._postings.get(term)
                    if post is not None:
                        post.pop(pid, None)
                        if not post:
                            del self._postings[term]
            self._files.pop(rel, None)

    def _load(self) -> bool:
        try:
            with gzip.open(self.index_path, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("v") != INDEX_VERSION or data.get("dir") != self.dir:
                return False
            for rel, rec in data["files"].items():
                parsed = [(p[0], p[1], p[2]) for p in rec["p"]]
                self._put(rel, {"m": rec["m"], "s": rec["s"], "t": rec["t"]}, parsed)
            return True
        except (OSError, ValueError, KeyError, TypeError, IndexError, EOFError):
            with self._lock:
                for rel in list(self._files):
                    self._drop(rel)
            return False

    def _save(self) -> None:
        with self._lock:
            files = {}
            for rel, meta in self._files.items():
                files[rel] = {**meta, "p": [[self._passages[pid][1], self._passages[pid][3], self._passages[pid][2]]
                                            for pid in self._pids[rel]]}
        payload = {"v": INDEX_VERSION, "dir": self.dir, "files": files}
        tmp = self.index_path.with_name(self.index_path.name + ".tmp")
        try:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=3) as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.index_path)
        except OSError:
            log.warning("could not save the notes index")

    def search(self, text: str, limit: int = 20) -> list:
        qterms = list(dict.fromkeys(tokenise(text)))
        if not qterms or not self._ready:
            return []
        with self._lock:
            n_docs = len(self._passages)
            if not n_docs:
                return []
            avg = self._total_len / n_docs
            scores: dict = {}
            for term in qterms:
                post = self._postings.get(term)
                if not post:
                    continue
                df = len(post)
                idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
                for pid, tf in post.items():
                    length = self._passages[pid][2]
                    scores[pid] = scores.get(pid, 0.0) + idf * tf * (_K1 + 1) / (
                        tf + _K1 * (1 - _B + _B * length / avg))
            if not scores:
                return []
            best = heapq.nlargest(max(limit * 4, 40), scores.items(), key=lambda kv: kv[1])
            qset = set(qterms)
            rows: list = []
            per_file: dict = {}
            for pid, score in best:
                rel, ptext, _, _ = self._passages[pid]
                if per_file.get(rel, 0) >= MAX_PER_FILE:
                    continue
                per_file[rel] = per_file.get(rel, 0) + 1
                path = os.path.join(self.dir, rel)
                if _denied(path):
                    continue
                rows.append(Row(path, self._files[rel]["t"], ptext, round(score, 4),
                                highlight_spans(ptext, qset)))
                if len(rows) >= limit:
                    break
            return rows


def highlight_spans(text: str, stems: set) -> list:
    spans = []
    for m in _WORD.finditer(text):
        w = m.group().lower()
        if w not in _STOP and stem(w) in stems:
            spans.append((m.start(), m.end()))
    return spans


def _denied(path: str) -> bool:
    try:
        from ..security import denylist
        return bool(denylist.matches(path))
    except ImportError:
        return False
    except Exception:
        return True


class AskNotesMode:
    keyword = "?"
    label = "Ask notes"
    local = True
    noun = "passage(s)"
    footer = FOOTER

    def __init__(self, settings: Any = None, scheduler: Any = None, index_path: Optional[Any] = None) -> None:
        self.settings = settings
        self.scheduler = scheduler
        self.index_path = index_path
        self._index: Optional[NotesIndex] = None
        self._lock = threading.Lock()

    def notes_dir(self) -> str:
        return resolve_notes_dir(self.settings)

    def index(self) -> Optional[NotesIndex]:
        """The index for the current folder, started on first use; None when unset."""
        folder = self.notes_dir()
        with self._lock:
            if not folder:
                if self._index is not None:
                    self._index.close()
                    self._index = None
                return None
            if self._index is None or self._index.dir != os.path.abspath(folder):
                if self._index is not None:
                    self._index.close()
                self._index = NotesIndex(folder, self.index_path, self.scheduler)
                self._index.start()
            return self._index

    @property
    def message(self) -> Optional[str]:
        """Text to show instead of results, or None when results are meaningful."""
        idx = self.index()
        if idx is None:
            return UNSET_MESSAGE
        if idx.building or idx._error:
            return idx.status_text
        return None

    @property
    def skipped_placeholder(self) -> int:
        return self._index.skipped_placeholder if self._index else 0

    def search(self, text: str) -> list:
        idx = self.index()
        if idx is None:
            return []
        return idx.search(text)

    def close(self) -> None:
        with self._lock:
            if self._index is not None:
                self._index.close()
                self._index = None
