"""Columnar file index (plan 4b): immutable ``IndexStore``, its ``IndexBuilder`` and the live
``IndexDelta`` overlay.

Layout: one ``str`` of all names joined (and terminated) by ``\\x00`` plus a casefolded twin, a
start-offset array per blob, and parallel arrays for parent directory, size, mtime and flags.
Directories also have a path table; a file's path is its directory's path plus its name.
Directories are entries too (``FLAG_DIR``), so they are searchable. The casefolded blob and its
offsets are shared with the original ones whenever casefolding changes nothing.

``find_substring`` runs ``str.find`` at C speed on the casefolded blob and maps each hit to an
entry id with ``bisect`` on the offsets, skipping the rest of a name after a hit. Large stores also
carry bitsets (one bit per entry) for their most common 2-character sequences, built in
``finish()``: those terms are answered by decoding the bitset instead of scanning the blob; every
other term is scanned.

Delta overlay: ``IndexDelta.apply_to(hits)`` drops store entries the delta removed or replaced and
appends the delta's adds whose casefolded name contains the search term. Added entries get ids
``entry_count + k``; the store resolves those ids through the delta that last produced them, so
``name``/``path``/``size``/... work for them like for any stored entry. Delta ids are only valid
while that delta is alive.

Persistence: one file, a pickle (protocol 5) of plain types only, loaded with an unpickler that
refuses every global, written to a sibling temp file and moved into place with ``os.replace``.
``mtime`` is stored as float seconds (``array('d')``), the same value the crawler reads from
``os.stat``.
"""
import heapq
import os
import pickle
import sys
import threading
import time
import weakref
from array import array
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from collections import Counter
from itertools import accumulate, chain, compress, islice, repeat
from typing import Iterable, Iterator, Optional

FLAG_DIR = 1
FLAG_HIDDEN = 2
FLAG_SYSTEM = 4
FLAG_PLACEHOLDER = 8
FLAG_DENIED = 16

FORMAT_MAGIC = "assistant-index"
FORMAT_VERSION = 1
CHUNK = 65536
PER_HIT_LIMIT = 2048
CACHE_TERMS = 8
CACHE_MIN_HITS = 5000
CACHE_MAX_IDS = 4_000_000
WINDOW_START = 4096
WINDOW_MIN = 256
WINDOW_MAX = 65536
WINDOW_HITS = 512
GRAM_MIN_ENTRIES = 50_000
GRAM_COUNT = 48
GRAM_SCAN_CHARS = 400_000_000
GRAM_SAMPLE_CHARS = 400_000
GRAM_SAMPLE_PIECES = 16
GRAM_MIN_SHARE = 0.02
_SEPS = ("\\", "/")

_latest: Optional[weakref.ref] = None


def _remember(store: "IndexStore") -> None:
    global _latest
    _latest = weakref.ref(store)


def _key(path: str) -> str:
    return os.path.normpath(path).casefold()


_BYTE_MASKS = [bytes((b >> k) & 1 for k in range(8)) for b in range(256)]


class Hits(array):
    """``array('I')`` of entry ids, ascending, that remembers where it came from."""
    store = None
    term = None


class IndexStore:
    def __init__(self, names: str, offs: array, cf: str, cf_offs: array, parent: array, size: array,
                 mtime: array, flags: array, dirs: list, root_paths: dict, built_at: float,
                 grams: Optional[dict] = None) -> None:
        self._grams: dict = grams or {}
        self._names, self._offs = names, offs
        self._cf, self._cf_offs = cf, cf_offs
        self._parent, self._size, self._mtime, self._flags = parent, size, mtime, flags
        self._dirs = dirs
        self._root_paths = root_paths
        self.entry_count = len(parent)
        self.built_at = built_at
        self.roots = [root_paths[i] for i in sorted(root_paths)]
        self._lock = threading.Lock()
        self._runs: Optional[dict] = None
        self._dir_ids: Optional[dict] = None
        self._overlay: Optional[weakref.ref] = None
        self._cache: OrderedDict = OrderedDict()
        _remember(self)

    @property
    def dir_count(self) -> int:
        return len(self._dirs)

    def _check(self, i: int) -> None:
        if i < 0:
            raise IndexError(i)

    def _entry(self, i: int):
        delta = self._overlay() if self._overlay is not None else None
        if delta is None:
            raise IndexError(i)
        return delta.entry(i - self.entry_count)

    def find_substring(self, term_cf: str, limit: Optional[int] = None) -> Hits:
        """Ids (ascending) of entries whose casefolded name contains ``term_cf``; ``limit`` stops early."""
        out = Hits("I")
        out.store, out.term = self, term_cf
        cap = self.entry_count if limit is None else max(0, int(limit))
        if not cap:
            return out
        if not term_cf:
            out.extend(range(min(cap, self.entry_count)))
            return out
        if "\x00" in term_cf:
            return out
        with self._lock:
            cached = self._cache.get(term_cf)
            if cached is not None:
                self._cache.move_to_end(term_cf)
        if cached is not None:
            out.frombytes(cached[:cap].tobytes())
            return out
        if not self._gram_hits(term_cf, cap, out):
            self._scan(term_cf, cap, out)
        if limit is None and len(out) >= CACHE_MIN_HITS and len(out) <= CACHE_MAX_IDS:
            self._remember_hits(term_cf, out)
        return out

    def _gram_hits(self, term: str, cap: int, out: Hits) -> bool:
        """Fills ``out`` from the bigram bitset of a 2-character ``term``; False when it has none."""
        grams = self._grams
        if not grams or len(term) < 2:
            return False
        bits = grams.get(term) if len(term) == 2 else None
        if bits is None:
            return False
        mask = b"".join(map(_BYTE_MASKS.__getitem__, bits))
        out.extend(islice(compress(range(self.entry_count), mask), cap))
        return True

    def build_grams(self) -> None:
        """Bitsets for the most common bigrams (see module doc); a no-op below ``GRAM_MIN_ENTRIES``."""
        n = self.entry_count
        self._grams = {}
        if n < GRAM_MIN_ENTRIES:
            return
        cf = self._cf
        step = max(1, len(cf) // GRAM_SAMPLE_PIECES)
        piece = GRAM_SAMPLE_CHARS // GRAM_SAMPLE_PIECES
        counts: Counter = Counter()
        sampled = 0
        for start in range(0, len(cf), step):
            chunk = cf[start:start + piece]
            counts.update(map("".join, zip(chunk, chunk[1:])))
            sampled += len(chunk)
        floor = GRAM_MIN_SHARE * sampled / max(1.0, len(cf) / n)
        wanted = [g for g, c in counts.most_common(GRAM_COUNT * 2) if "\x00" not in g and c >= floor]
        nbytes = (n + 7) // 8
        for g in wanted[:min(GRAM_COUNT, GRAM_SCAN_CHARS // max(1, len(cf)))]:
            ids = Hits("I")
            self._scan(g, n, ids)
            ba = bytearray(nbytes)
            for i in ids:
                ba[i >> 3] |= 1 << (i & 7)
            self._grams[g] = bytes(ba)

    def _remember_hits(self, term: str, hits: Hits) -> None:
        keep = array("I", hits)
        with self._lock:
            self._cache[term] = keep
            self._cache.move_to_end(term)
            while len(self._cache) > CACHE_TERMS or sum(map(len, self._cache.values())) > CACHE_MAX_IDS:
                self._cache.popitem(last=False)

    def _scan(self, term: str, cap: int, out: Hits) -> None:
        cf, offs, n = self._cf, self._cf_offs, self.entry_count
        find, append = cf.find, out.append
        pos = find(term)
        i = found = 0
        while pos >= 0 and found < PER_HIT_LIMIT:
            i = bisect_right(offs, pos, i) - 1
            append(i)
            found += 1
            if found >= cap:
                return
            pos = find(term, offs[i + 1])
        nul = "\x00"
        window = WINDOW_START
        while pos >= 0:
            s = bisect_right(offs, pos, i) - 1
            e = min(n, s + window)
            pieces = cf[offs[s]:offs[e]].split(term)
            pieces.pop()
            counts = list(map(str.count, pieces, repeat(nul)))
            ids = accumulate(counts, initial=s)
            next(ids)
            out.extend(compress(ids, chain((1,), islice(counts, 1, None))))
            if len(out) >= cap:
                del out[cap:]
                return
            window = int(min(WINDOW_MAX, max(WINDOW_MIN, WINDOW_HITS * e / len(out))))
            i = e
            pos = find(term, offs[e])

    def name(self, i: int) -> str:
        if i >= self.entry_count:
            return os.path.basename(self._entry(i)[0].rstrip("\\/")) or self._entry(i)[0]
        self._check(i)
        o = self._offs
        return self._names[o[i]:o[i + 1] - 1]

    def name_cf(self, i: int) -> str:
        if i >= self.entry_count:
            return self._entry(i)[4]
        self._check(i)
        o = self._cf_offs
        return self._cf[o[i]:o[i + 1] - 1]

    def path(self, i: int) -> str:
        if i >= self.entry_count:
            return self._entry(i)[0]
        self._check(i)
        p = self._parent[i]
        if p < 0:
            return self._root_paths[i]
        d = self._dirs[p]
        return (d + self.name(i)) if d.endswith(_SEPS) else (d + "\\" + self.name(i))

    def parent_path(self, i: int) -> str:
        if i >= self.entry_count:
            return os.path.dirname(self._entry(i)[0].rstrip("\\/"))
        self._check(i)
        p = self._parent[i]
        if p < 0:
            return os.path.dirname(self._root_paths[i].rstrip("\\/"))
        return self._dirs[p]

    def size(self, i: int) -> int:
        if i >= self.entry_count:
            return self._entry(i)[1]
        self._check(i)
        return self._size[i]

    def mtime(self, i: int) -> float:
        if i >= self.entry_count:
            return self._entry(i)[2]
        self._check(i)
        return self._mtime[i]

    def flags(self, i: int) -> int:
        if i >= self.entry_count:
            return self._entry(i)[3]
        self._check(i)
        return self._flags[i]

    name_at, path_of, parent_at, size_at, mtime_at, flags_at = name, path, parent_path, size, mtime, flags

    def rank_best_literal(self, ids, end: int, term_cf: str, show_denied: bool, deadline: Optional[float],
                          want: int):
        """``(top, total, truncated)`` for ``ids[:end]`` (store ids, all containing ``term_cf``): the best ``want``
        as ``((tier, tier, len, name), id, tier)`` in order, and how many were ranked. Same tiers and key as
        ``rank.name_tier`` / ``rank.best_key`` for a lone literal name term under ``sort=best``."""
        cf, o, flags = self._cf, self._cf_offs, self._flags
        deny = 0 if show_denied else FLAG_DENIED
        m = len(term_cf)
        flat: list = []
        append = flat.append
        step = 4096
        truncated = False
        for base in range(0, end, step):
            if deadline is not None and base and time.perf_counter() > deadline:
                truncated = True
                break
            for i in ids[base:min(end, base + step)]:
                if flags[i] & deny:
                    continue
                n = cf[o[i]:o[i + 1] - 1]
                s = n.find(term_cf)
                if s == 0:
                    size = len(n)
                    if m == size:
                        t = 0
                    else:
                        dot = n.rfind(".")
                        t = 0 if m == (dot if dot > 0 else size) else 1
                elif n[s - 1].isalnum():
                    t = 3
                    for _ in range(15):
                        s = n.find(term_cf, s + 1)
                        if s < 0:
                            break
                        if not n[s - 1].isalnum():
                            t = 2
                            break
                else:
                    t = 2
                append((t, len(n), n, i))
        top = heapq.nsmallest(want, flat)
        return [((t, t, size, n), i, t) for t, size, n, i in top], len(flat), truncated

    def newest_ids(self, n: int) -> array:
        """The ``n`` entries with the latest mtime, as ascending ids (ties at the cutoff fill up to ``n``)."""
        m = self._mtime
        if n >= len(m):
            return array("I", range(len(m)))
        cutoff = heapq.nlargest(n, m)[-1]
        ids = array("I", compress(range(len(m)), map(cutoff.__le__, m)))
        if len(ids) > n:
            newer = sum(1 for _ in compress(range(len(m)), map(cutoff.__lt__, m)))
            spare = n - newer
            keep = array("I")
            for i in ids:
                if m[i] > cutoff:
                    keep.append(i)
                elif spare > 0:
                    keep.append(i)
                    spare -= 1
            ids = keep
        return ids

    def _build_lookups(self) -> None:
        with self._lock:
            if self._runs is not None:
                return
            runs: dict = {}
            prev, start = -2, 0
            for i, p in enumerate(self._parent):
                if p != prev:
                    if prev >= 0:
                        runs.setdefault(prev, []).append((start, i))
                    prev, start = p, i
            if prev >= 0:
                runs.setdefault(prev, []).append((start, len(self._parent)))
            self._dir_ids = {_key(d): n for n, d in enumerate(self._dirs)}
            self._runs = runs

    def dir_index(self, dir_path: str) -> Optional[int]:
        self._build_lookups()
        return self._dir_ids.get(_key(dir_path))

    def iter_in_dir(self, dir_path: str) -> Iterator[int]:
        d = self.dir_index(dir_path)
        if d is None:
            return iter(())
        return (i for a, b in self._runs.get(d, ()) for i in range(a, b))

    def ids_for_path(self, path: str) -> list:
        """Entry ids whose full path equals ``path`` (normally zero or one)."""
        key = _key(path)
        found = [i for i, p in self._root_paths.items() if _key(p) == key]
        parent, leaf = os.path.split(key)
        d = self._dir_ids.get(parent) if self._runs is not None else self.dir_index(parent)
        if d is not None:
            found.extend(i for a, b in self._runs.get(d, ()) for i in range(a, b) if self.name_cf(i) == leaf)
        return found

    def _payload(self) -> dict:
        shared = self._cf is self._names
        return {
            "magic": FORMAT_MAGIC, "version": FORMAT_VERSION, "byteorder": sys.byteorder,
            "built_at": self.built_at, "count": self.entry_count,
            "names": self._names, "offs": self._offs.tobytes(),
            "cf": None if shared else self._cf,
            "cf_offs": None if self._cf_offs is self._offs else self._cf_offs.tobytes(),
            "parent": self._parent.tobytes(), "size": self._size.tobytes(),
            "mtime": self._mtime.tobytes(), "flags": self._flags.tobytes(),
            "dirs": self._dirs, "roots": sorted(self._root_paths.items()),
            "grams": sorted(self._grams.items()),
        }

    def save(self, path) -> None:
        path = os.fspath(path)
        part = path + ".part"
        try:
            with open(part, "wb") as fh:
                pickle.dump(self._payload(), fh, protocol=5)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(part, path)
        except BaseException:
            try:
                os.unlink(part)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path) -> "IndexStore":
        with open(os.fspath(path), "rb") as fh:
            data = _PlainUnpickler(fh).load()
        if (not isinstance(data, dict) or data.get("magic") != FORMAT_MAGIC
                or data.get("version") != FORMAT_VERSION or data.get("byteorder") != sys.byteorder):
            raise ValueError("not a usable index file")
        n = data["count"]

        def col(code: str, raw: bytes) -> array:
            a = array(code)
            a.frombytes(raw)
            return a

        try:
            names, offs = data["names"], col("I", data["offs"])
            cf = names if data["cf"] is None else data["cf"]
            cf_offs = offs if data["cf_offs"] is None else col("I", data["cf_offs"])
            parent, size = col("i", data["parent"]), col("q", data["size"])
            mtime, flags = col("d", data["mtime"]), col("B", data["flags"])
            dirs = list(data["dirs"])
            roots = {int(i): p for i, p in data["roots"]}
            built_at = float(data["built_at"])
            grams = {g: b for g, b in data.get("grams") or ()}
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError("index file is inconsistent") from None
        if not (isinstance(n, int) and isinstance(names, str) and isinstance(cf, str)
                and all(isinstance(d, str) for d in dirs) and all(isinstance(p, str) for p in roots.values())):
            raise ValueError("index file is inconsistent")
        if not (len(parent) == len(size) == len(mtime) == len(flags) == n
                and len(offs) == len(cf_offs) == n + 1
                and offs[-1] == len(names) and cf_offs[-1] == len(cf)):
            raise ValueError("index file is inconsistent")
        if not all(isinstance(g, str) and len(g) == 2 and isinstance(b, bytes) and len(b) == (n + 7) // 8
                   for g, b in grams.items()):
            raise ValueError("index file is inconsistent")
        if n and (max(parent) >= len(dirs) or min(parent) < -1):
            raise ValueError("index file is inconsistent")
        if any(roots.get(i) is None for i, p in enumerate(parent) if p == -1):
            raise ValueError("index file is inconsistent")
        return cls(names, offs, cf, cf_offs, parent, size, mtime, flags, dirs, roots, built_at, grams)


class _PlainUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        raise pickle.UnpicklingError("index files hold plain data only")


class IndexBuilder:
    """Single-threaded; one builder produces one store."""

    def __init__(self) -> None:
        self._buf: list = []
        self._chunks: list = []
        self._lens = array("I")
        self._parent = array("i")
        self._size = array("q")
        self._mtime = array("d")
        self._flags = array("B")
        self._dirs: list = []
        self._roots: dict = {}
        self._ascii = True
        self._cf_len: dict = {}
        self._deny = None

    def _denied(self, path: str) -> bool:
        if self._deny is None:
            try:
                from ..security import denylist
                self._deny = denylist.matches
            except Exception:
                self._deny = lambda p: False
        return self._deny(path)

    def add(self, parent_id: int, name: str, size: int, mtime: float, flags: int) -> int:
        if "\x00" in name:
            name = name.replace("\x00", "?")
        i = len(self._parent)
        if not name.isascii():
            self._ascii = False
            n = len(name.casefold())
            if n != len(name):
                self._cf_len[i] = n
        buf = self._buf
        buf.append(name)
        self._lens.append(len(name) + 1)
        self._parent.append(parent_id)
        self._size.append(size)
        self._mtime.append(mtime)
        self._flags.append(flags)
        if len(buf) >= CHUNK:
            self._chunks.append("\x00".join(buf) + "\x00")
            buf.clear()
        return i

    def add_dir(self, path: str, parent_id: int, mtime: float = 0.0, flags: int = 0) -> int:
        """Registers a directory and its entry; returns the id to pass as ``parent_id`` for its children."""
        stripped = path.rstrip("\\/")
        name = os.path.basename(stripped) or path
        flags |= FLAG_DIR
        if not flags & FLAG_DENIED and self._denied(path):
            flags |= FLAG_DENIED
        entry = self.add(parent_id if parent_id >= 0 else -1, name, 0, mtime, flags)
        if parent_id < 0:
            self._roots[entry] = path
        self._dirs.append(path)
        return len(self._dirs) - 1

    def finish(self) -> IndexStore:
        if self._buf:
            self._chunks.append("\x00".join(self._buf) + "\x00")
        names = "".join(self._chunks)
        offs = array("I", accumulate(self._lens, initial=0))
        if self._ascii:
            cf = names.casefold()
            cf_offs = offs
        else:
            cf = names.casefold()
            if len(cf) == len(names) and not self._cf_len:
                cf_offs = offs
            else:
                lens = array("I", self._lens)
                for i, n in self._cf_len.items():
                    lens[i] = n + 1
                cf_offs = array("I", accumulate(lens, initial=0))
        if cf == names:
            cf = names
        store = IndexStore(names, offs, cf, cf_offs, self._parent, self._size, self._mtime, self._flags,
                           self._dirs, self._roots, time.time())
        store.build_grams()
        self.__init__()
        return store


class IndexDelta:
    """Live adds and removals between rescans; thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list = []
        self._adds: dict = {}
        self._removed: set = set()
        self._version = 0
        self._resolved_for: Optional[weakref.ref] = None
        self._resolved: dict = {}

    def __len__(self) -> int:
        return len(self._adds) + len(self._removed)

    def add(self, path: str, size: int, mtime: float, flags: int) -> None:
        key = _key(path)
        name = os.path.basename(path.rstrip("\\/")) or path
        entry = (path, int(size), float(mtime), int(flags), name.casefold(), key)
        with self._lock:
            k = self._adds.get(key)
            if k is None:
                self._adds[key] = len(self._entries)
                self._entries.append(entry)
            else:
                self._entries[k] = entry
            self._removed.discard(key)
            self._version += 1

    def remove(self, path: str) -> None:
        key = _key(path)
        with self._lock:
            self._adds.pop(key, None)
            self._removed.add(key)
            self._version += 1

    def entry(self, k: int):
        return self._entries[k]

    def _hidden_ids(self, store: IndexStore, keys: list) -> set:
        ref = self._resolved_for
        if ref is None or ref() is not store:
            self._resolved_for, self._resolved = weakref.ref(store), {}
        hidden: set = set()
        for key in keys:
            ids = self._resolved.get(key)
            if ids is None:
                ids = self._resolved[key] = store.ids_for_path(key)
            hidden.update(ids)
        return hidden

    def apply_to(self, hits) -> Hits:
        store = getattr(hits, "store", None)
        if store is None:
            store = _latest() if _latest is not None else None
        term = getattr(hits, "term", None)
        with self._lock:
            keys = list(self._removed) + list(self._adds)
            if not keys:
                return hits
            adds = [(k, e) for k, e in enumerate(self._entries)
                    if self._adds.get(e[5]) == k and (not term or term in e[4])]
        out = Hits("I")
        out.store, out.term = store, term
        if store is None:
            out.extend(hits)
            return out
        with self._lock:
            hidden = sorted(self._hidden_ids(store, keys))
        n = len(hits)
        start = 0
        for h in hidden:
            j = bisect_left(hits, h, start)
            if j < n and hits[j] == h:
                out.extend(hits[start:j])
                start = j + 1
        out.extend(hits[start:])
        if adds:
            base = store.entry_count
            out.extend(base + k for k, _ in adds)
            store._overlay = weakref.ref(self)
        return out
