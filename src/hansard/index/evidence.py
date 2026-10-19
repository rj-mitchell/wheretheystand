"""Meaning-based search over witnesses' evidence to select committees.

Each substantial answer in an oral evidence transcript (hansard.sources.evidence) gets one embedding, made with the
same local model as the rest of the record, in a LanceDB table of its own (data/evidence_index), keyed by session and
turn. Briefings look evidence up both by keyword (the transcript store's full-text index) and by meaning here, so an
answer that makes the point in other words is found too. Filled a session at a time during each update, newest
first, within its own time allowance; free to run (no AI service).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import lancedb
import numpy as np
import pyarrow as pa

from hansard.embed.base import Embedder
from hansard.index.lance import KEEP_OLD_VERSIONS

log = logging.getLogger(__name__)

TABLE = "turns"
TEXT_CHARS = 1500  # the start of an answer: about the model's limit, and where the point is usually made
INDEX_FROM = 10_000  # below this many answers a full comparison is quick enough without an index


def _schema(dim: int) -> pa.Schema:
    return pa.schema([("session_id", pa.int64()), ("ord", pa.int32()), ("vector", pa.list_(pa.float32(), dim))])


class EvidenceVectors:
    def __init__(self, path: Path, dim: int):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.dim = dim
        self.db = lancedb.connect(str(self.path))

    def _table(self, create: bool = False):
        res = self.db.list_tables()
        if TABLE in set(getattr(res, "tables", res)):
            return self.db.open_table(TABLE)
        return self.db.create_table(TABLE, schema=_schema(self.dim)) if create else None

    def count(self) -> int:
        t = self._table()
        return t.count_rows() if t is not None else 0

    def add(self, session_ids: list[int], turns: list[dict[str, Any]], vectors: np.ndarray) -> None:
        """Replace these sessions' vectors with the given ones."""
        t = self._table(create=True)
        if session_ids:
            t.delete(f"session_id IN ({', '.join(str(int(s)) for s in session_ids)})")
        if turns:
            t.add(pa.Table.from_pydict({"session_id": [int(x["session_id"]) for x in turns],
                                        "ord": [int(x["ord"]) for x in turns],
                                        "vector": list(vectors.astype(np.float32))}, schema=_schema(self.dim)))

    def finish(self) -> None:
        """After adding: build the index once there are enough answers, then merge and tidy."""
        t = self._table()
        if t is None:
            return
        if t.count_rows() >= INDEX_FROM and not any("vector" in (ix.columns or []) for ix in t.list_indices()):
            from lancedb.index import IvfPq

            t.create_index("vector", config=IvfPq(distance_type="cosine", num_sub_vectors=max(1, self.dim // 8)),
                           replace=True)
        t.optimize(cleanup_older_than=KEEP_OLD_VERSIONS)

    def search(self, vec: np.ndarray, limit: int = 120) -> list[tuple[int, int, float]]:
        """[(session_id, ord, similarity)], most similar first."""
        t = self._table()
        if t is None or t.count_rows() == 0:
            return []
        search = t.search(vec.astype(np.float32), vector_column_name="vector").distance_type("cosine")
        if any("vector" in (ix.columns or []) for ix in t.list_indices()):
            search = search.nprobes(20).refine_factor(5)  # check the nearest candidates against the full vectors
        rows = search.select(["session_id", "ord", "_distance"]).limit(limit).to_list()
        return [(int(r["session_id"]), int(r["ord"]), 1.0 - float(r["_distance"])) for r in rows]


def embed_evidence(store, vectors: EvidenceVectors, embedder: Embedder, *, max_seconds: float | None = None,
                   batch_sessions: int = 20) -> dict[str, int]:
    """Embed the substantial answers of sessions not done yet, newest first, within ``max_seconds``."""
    started, sessions, answers = time.time(), 0, 0
    todo = store.unembedded()
    for i in range(0, len(todo), batch_sessions):
        part = todo[i:i + batch_sessions]
        turns = store.turns_for(part)
        texts = [f"{t['speaker']}: {t['text'][:TEXT_CHARS]}" for t in turns]
        vecs = embedder.embed_documents(texts) if texts else np.zeros((0, vectors.dim), dtype=np.float32)
        vectors.add(part, turns, vecs)
        store.mark_embedded(part)
        sessions += len(part)
        answers += len(turns)
        if max_seconds is not None and time.time() - started > max_seconds:
            break
    if sessions:
        vectors.finish()
        log.info("committee evidence: embedded %d answers from %d sessions", answers, sessions)
    return {"sessions": sessions, "answers": answers, "sessions_remaining": max(0, len(todo) - sessions)}
