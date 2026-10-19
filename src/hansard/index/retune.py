"""Rebuild the meaning-search index for the number of vectors there are now, checking it finds as much as before."""

from __future__ import annotations

import time
from typing import Any

from hansard.index.lance import SearchIndex

# A spread of subjects, with and without filters, as briefings search them.
SAMPLE_QUERIES: list[tuple[str, str | None]] = [
    ("cost of school uniforms", None),
    ("future of BBC funding and the licence fee", None),
    ("rail services in the north of England", None),
    ("NHS waiting lists for elective surgery", None),
    ("artificial intelligence regulation and copyright", None),
    ("small boats crossing the Channel", None),
    ("farmers and inheritance tax on agricultural land", None),
    ("potholes and local road maintenance", None),
    ("defence spending as a share of GDP", None),
    ("special educational needs funding for councils", None),
    ("onshore wind planning rules", "house = 'commons'"),
    ("assisted dying", "house = 'lords'"),
    ("water companies and sewage discharges", "year >= 2020"),
    ("housing targets and the green belt", "is_gov = true"),
]


def top_ids(idx: SearchIndex, vectors: list[Any], limit: int, nprobes: int | None = None,
            refine_factor: int | None = None, exact: bool = False) -> tuple[list[list[str]], float]:
    """The passage IDs each sample query finds, and the median search time in milliseconds."""
    found, times = [], []
    for vec, (_, where) in zip(vectors, SAMPLE_QUERIES, strict=True):
        start = time.perf_counter()
        hits = idx.vector(vec, where, limit, nprobes=nprobes, refine_factor=refine_factor, columns=["passage_id"],
                          exact=exact)
        times.append((time.perf_counter() - start) * 1000)
        found.append([h["passage_id"] for h in hits])
    times.sort()
    return found, round(times[len(times) // 2], 1)


def recall(truth: list[list[str]], found: list[list[str]]) -> float:
    """Share of the true nearest passages that were found (averaged over the queries)."""
    shares = [len(set(f) & set(t)) / len(t) for t, f in zip(truth, found, strict=True) if t]
    return round(sum(shares) / len(shares), 3) if shares else 1.0


def rebuild(idx: SearchIndex, embedder, rows_per_partition: int = 8192, probes: tuple[int, ...] = (20, 40, 80),
            refines: tuple[int | None, ...] = (None, 5, 10), current: dict[str, Any] | None = None,
            limit: int = 300, tolerance: float = 0.02, force: bool = False) -> dict[str, Any]:
    """Rebuild the vector index, then try each ``nprobes`` and ``refine_factor``, measuring how many of the true
    nearest passages (from a search that compares every vector) each finds.

    The new index is kept if some setting finds at least as many as the old index with its ``current`` settings did
    (within ``tolerance``); the fastest setting within ``tolerance`` of the best is recommended. Otherwise the old
    index is put back (unless ``force``). The caller holds the update lock, so nothing else writes meanwhile."""
    current = current or {}
    vt = idx.vectors()
    rows = vt.count_rows()
    vectors = [embedder.embed_query(q) for q, _ in SAMPLE_QUERIES]
    truth, exact_ms = top_ids(idx, vectors, limit, exact=True)
    settings = {"nprobes": current.get("nprobes"), "refine_factor": current.get("refine_factor")}
    top_ids(idx, vectors, limit, **settings)  # warm the cache, so the timing is not of a cold read
    before, before_ms = top_ids(idx, vectors, limit, **settings)
    before_recall = recall(truth, before)
    version = vt.version
    start = time.time()
    idx.rebuild_vector_index(rows_per_partition)
    built = round(time.time() - start)
    trials = []
    for n in probes:
        for rf in refines:
            top_ids(idx, vectors, limit, n, rf)
            after, ms = top_ids(idx, vectors, limit, n, rf)
            trials.append({"nprobes": n, "refine_factor": rf, "median_ms": ms, "recall": recall(truth, after)})
    best = max(t["recall"] for t in trials)
    out: dict[str, Any] = {"vectors": rows, "rows_per_partition": rows_per_partition,
                           "partitions": max(1, rows // rows_per_partition), "build_seconds": built,
                           "exact_median_ms": exact_ms,
                           "before": {**settings, "median_ms": before_ms, "recall": before_recall}, "trials": trials}
    if best >= before_recall - tolerance:
        pick = min((t for t in trials if t["recall"] >= best - tolerance), key=lambda t: t["median_ms"])
        out["recommended"] = {"nprobes": pick["nprobes"], "refine_factor": pick["refine_factor"]}
        out["kept"] = True
    elif force:
        out["kept"] = True
    else:
        idx.vectors().restore(version)
        out["kept"] = False
        out["note"] = "the new index found fewer of the nearest passages at every setting; the old one was put back"
    return out
