"""Query latency (PRD 12: substring over 2M names <= 50 ms; plan 7.3 M3a-7 Done: p95 of 50 random 2-6 char queries).

Tech-lead decision (M3a-1): the 50 ms gate applies to SELECTIVE needles, cold. A dense needle (tens of thousands of hits)
is reported cold and warm (the store keeps a small result cache) and must be < 5 ms with ``limit=500``.

Everything prints p50 / p95 (run with ``-s``, or read the captured output of a failure):

* synthetic: the 2,000,000-name conftest store at the store level, through ``search.run``, and 50 random 2-6 character
  queries split into selective / dense by hit count.
* real: set ``HOMA_PERF_ROOT`` (';'-separated roots); a private app indexes them and 50 random 2-6 character
  substrings of real file names are timed through ``GET /v1/search``.
"""
import os
import random
import statistics
import time

import pytest

from conftest import Api, _free_port, launch_app, percentile, wait_index

ALPHABET = "abcdefghijklmnopqrstuvwxyz"
DENSE_AT = 5000  # hits; matches the store's result-cache threshold


def report(label, samples):
    p50, p95 = statistics.median(samples), percentile(samples, 95)
    print(f"\n[perf] {label}: n={len(samples)} p50 {p50:.1f} ms  p95 {p95:.1f} ms  max {max(samples):.1f} ms")
    return p50, p95


def time_it(fn, *args, **kw):
    t0 = time.perf_counter()
    result = fn(*args, **kw)
    return (time.perf_counter() - t0) * 1000, result


def test_synthetic_2m_selective_needles_cold(synthetic_store_2m):
    store, _, _ = synthetic_store_2m
    samples = []
    for n in range(50):
        ms, hits = time_it(store.find_substring, f"_{1_500_011 + n * 41}.")
        samples.append(ms)
        assert len(hits) <= 1
    p50, p95 = report("2M find_substring, selective needle, cold (50 distinct)", samples)
    assert p50 <= 50, "PRD 12 target: substring over 2M names <= 50 ms"
    assert p95 <= 100, "a miss by more than 2x triggers the P7 fallbacks"


def test_synthetic_2m_dense_needles_cold_warm_and_limited(synthetic_store_2m):
    store, _, _ = synthetic_store_2m
    terms = ["bud", "mee", "pho", "bac", "sum", "ske"]
    cold, warm, limited = [], [], []
    for term in terms:
        ms, hits = time_it(store.find_substring, term)
        assert len(hits) > DENSE_AT
        cold.append(ms)
        warm.extend(time_it(store.find_substring, term)[0] for _ in range(10))
        limited.extend(time_it(store.find_substring, term, limit=500)[0] for _ in range(10))
    report("2M find_substring, dense needle, cold (first call per term)", cold)
    report("2M find_substring, dense needle, warm", warm)
    _, lim95 = report("2M find_substring, dense needle, limit=500", limited)
    assert statistics.median(limited) < 5, "dense needle with limit=500 must be < 5 ms"
    assert lim95 < 25


def test_synthetic_2m_fifty_random_queries_split_by_density(synthetic_store_2m):
    store, _, _ = synthetic_store_2m
    rng = random.Random(2024)
    selective, dense = [], []
    for _ in range(50):
        q = "".join(rng.choice(ALPHABET) for _ in range(rng.randint(2, 6)))
        ms, hits = time_it(store.find_substring, q)
        (dense if len(hits) > DENSE_AT else selective).append(ms)
    allms = selective + dense
    report("2M find_substring, 50 random 2-6 char queries (all)", allms)
    if selective:
        s50, s95 = report(f"  of which selective ({len(selective)})", selective)
        assert s50 <= 50 and s95 <= 100
    if dense:
        report(f"  of which dense ({len(dense)}; cold, first sight of each term)", dense)


def test_synthetic_2m_through_search_run_selective_and_dense(synthetic_store_2m):
    query = pytest.importorskip("homa.index.query", reason="query.py (M3a-3) not present")
    search = pytest.importorskip("homa.index.search", reason="search.py (M3a-3) not present")
    store, _, _ = synthetic_store_2m

    def run(text, limit=50):
        return search.run(query.parse(text, {"md", "txt", "py"}), store, limit=limit)

    selective = [f"report_999_{n}" for n in (101, 2001, 30001, 400001, 500009, 600011, 700013, 800017)]
    dense = ["invoice", "design", "notes", "readme", "photo", "backup", "client", "server"]
    sel_ms = [time_it(run, q)[0] for q in selective * 4]
    dense_cold = [time_it(run, q)[0] for q in dense]
    dense_warm = [time_it(run, q)[0] for q in dense * 3]
    s50, _ = report("2M search.run, selective", sel_ms)
    report("2M search.run, dense (first call; result-cache cold)", dense_cold)
    _, d95 = report("2M search.run, dense (repeat; warm)", dense_warm)
    print(f"[perf] SearchResult.elapsed_ms, selective: {[round(run(q).elapsed_ms, 1) for q in selective[:3]]}; "
          f"dense: {[round(run(q).elapsed_ms, 1) for q in dense[:3]]}")
    assert s50 <= 50, "selective queries must meet the 50 ms target through the executor"
    assert d95 < 5000, "dense queries through the executor are reported; only a runaway fails"


@pytest.mark.timeout(1500)
def test_real_index_random_queries_via_http():
    roots = os.environ.get("HOMA_PERF_ROOT")
    if not roots:
        pytest.skip("set HOMA_PERF_ROOT (';'-separated) to time the real index through GET /v1/search")
    import json
    import tempfile
    import urllib.parse
    from pathlib import Path
    rootlist = [r for r in roots.split(";") if r]
    rng = random.Random(99)
    names = []
    for root in rootlist:
        for dirpath, dirnames, filenames in os.walk(root):
            names.extend(f for f in filenames if len(f) > 6)
            if len(names) > 20000:
                break
        if len(names) > 20000:
            break
    assert names, "no sample file names found under the perf roots"
    queries = []
    for _ in range(50):
        name = os.path.splitext(rng.choice(names))[0]
        length = min(len(name), rng.randint(2, 6))
        start = rng.randint(0, len(name) - length)
        queries.append(name[start:start + length])
    home = Path(tempfile.mkdtemp(prefix="perf_home_"))
    with launch_app(home, _free_port(), {"index": {"roots": rootlist}}) as (base_url, token):
        index = wait_index(base_url, token, minimum=1000, timeout=900)
        if not isinstance(index, dict) or index.get("entries", 0) < 1000:
            pytest.skip(f"index not ready: {index!r} (M3a-7 wiring?)")
        api = Api(base_url, token)
        wall, server, totals = [], [], []
        for q in queries:
            t0 = time.perf_counter()
            status, body = api.get("/v1/search?" + urllib.parse.urlencode({"q": q, "limit": 50}))
            wall.append((time.perf_counter() - t0) * 1000)
            assert status == 200, (q, status, body)
            server.append(body["elapsed_ms"])
            totals.append(body["total"])
        print(f"\n[perf] real index: {index['entries']} entries; sample queries {queries[:8]}")
        w50, w95 = report("real index, GET /v1/search wall time", wall)
        s50, s95 = report("real index, server elapsed_ms", server)
        sel = [ms for ms, t in zip(server, totals) if t <= DENSE_AT]
        if sel:
            report(f"real index, server elapsed_ms for selective queries (total <= {DENSE_AT}; {len(sel)} of 50)", sel)
        print(json.dumps({"entries": index["entries"], "wall_p50": round(w50, 1), "wall_p95": round(w95, 1),
                          "server_p50": round(s50, 1), "server_p95": round(s95, 1)}))
        assert sel and statistics.median(sel) <= 50, "PRD 12: median selective query <= 50 ms on the real index"
