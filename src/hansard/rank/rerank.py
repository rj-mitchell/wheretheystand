"""An optional second, closer look at the best candidates (re-ranking).

Keyword and meaning-based search are fast but rough: they compare the question and each passage separately. A
cross-encoder reads the question and a passage together and judges how well the passage answers it, which is
slower, so it is only run on the best few hundred candidates. It runs on the server's own CPU (a small open model,
weights downloaded once like the embedding model), so it costs nothing per question.

The relevance used for ranking becomes a blend: ``(1 - weight) * search relevance + weight * cross-encoder score``.
Candidates beyond the re-ranked set are treated as if the cross-encoder had scored them zero, so they stay below.
Recency and the role weighting apply exactly as before. Which speaker groups are eligible for the evidence is still
decided by the search relevance (see ``select_evidence``), so re-ranking changes the order, never the balance.
Turned on with ``[ranking] reranker`` in the settings.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import replace
from pathlib import Path

from hansard.rank.composite import Scored

log = logging.getLogger(__name__)

PASSAGE_CHARS = 1500  # cross-encoders read about 512 tokens; the start of the passage is enough


def model_on_disk(cache_dir: Path, model: str) -> bool:
    return any((Path(cache_dir) / f"models--{model.replace('/', '--')}").rglob("*.onnx"))


class Reranker:
    def __init__(self, model: str, cache_dir: Path, threads: int | None = None, batch_size: int = 16):
        if model_on_disk(cache_dir, model):
            os.environ.setdefault("HF_HUB_OFFLINE", "1")  # never ask Hugging Face again once it is on disk
        from fastembed.rerank.cross_encoder import (
            TextCrossEncoder,  # optional dependency ("embed" extra)
        )

        self.name = model
        self.batch_size = batch_size
        self._model = TextCrossEncoder(model, cache_dir=str(cache_dir), threads=threads)

    def scores(self, query: str, texts: list[str]) -> list[float]:
        """Probability-like scores in 0..1 (a logistic of the model's raw score)."""
        raw = self._model.rerank(query, texts, batch_size=self.batch_size)
        return [1.0 / (1.0 + math.exp(-max(min(float(x), 30.0), -30.0))) for x in raw]


def passage_text(s: Scored) -> str:
    c = s.candidate
    p = c.data.get("passages", {}).get(c.best_passage_id or "", {})
    heading = p.get("heading") or ""
    return (f"{heading}. " if heading else "") + (p.get("text") or "")[:PASSAGE_CHARS]


def rerank(scored: list[Scored], query: str, reranker: Reranker, top: int = 150, weight: float = 0.6) -> list[Scored]:
    """Re-score the best ``top`` candidates with the cross-encoder and re-sort everything."""
    if not scored or weight <= 0:
        return scored
    head, tail = scored[:top], scored[top:]
    texts = [passage_text(s) for s in head]
    sims = reranker.scores(query, texts)
    out = []
    for s, x in zip(head, sims, strict=True):
        rel = (1 - weight) * s.relevance + weight * x
        out.append(replace(s, relevance=rel, score=rel * s.recency * s.authority, rerank=x,
                           base_relevance=s.relevance))
    for s in tail:
        rel = (1 - weight) * s.relevance
        out.append(replace(s, relevance=rel, score=rel * s.recency * s.authority, base_relevance=s.relevance))
    out.sort(key=lambda s: (-s.score, -s.relevance, s.candidate.record_id))
    return out


def get_reranker(settings) -> Reranker | None:
    name = settings.ranking.get("reranker") or ""
    if not name:
        return None
    cache = settings.data_dir / "models"
    try:
        return Reranker(name, cache, threads=settings.embedding.get("threads"))
    except Exception as exc:  # missing weights or no network: search still works without it
        log.warning("re-ranking is off: could not load %s (%s)", name, exc)
        return None


def ensure_reranker(settings) -> dict:
    """Download the re-ranking model if it is configured and not yet on disk (run by sync, before embedding)."""
    name = settings.ranking.get("reranker") or ""
    if not name:
        return {"status": "off"}
    if model_on_disk(settings.data_dir / "models", name):
        return {"status": "present", "model": name}
    return {"status": "downloaded" if get_reranker(settings) else "failed", "model": name}
