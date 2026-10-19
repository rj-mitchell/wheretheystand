"""Composite soft ranking: relevance x recency x authority.

Nothing here excludes a record. Recency and role only re-weight relevance, so
a much stronger textual match from 2011 still outranks a weak 2025 one. Hard
exclusions happen only through filters the user sets explicitly.

relevance  = w * bm25 / max(bm25) + (1 - w) * calibrated(cosine)
             calibrated(c) = clamp((c - floor) / (ceiling - floor), 0, 1), using the
             embedding model's similarity range (unrelated text scores ~floor), so a
             weak semantic match is not inflated just because it was the best of a
             weak set. A retriever that missed a record adds 0.
recency    = floor + (1 - floor) * 0.5 ** (age_years / half_life)
             slider s in [0, 1]: s = 0 -> recency = 1 for every record;
             otherwise half_life = min_half_life_years / s (s = 1 -> 2 years)
authority  = 1 + (W - 1) * m        W = role slider in [1.0, 2.0]
             m = strongest office the speaker held on the record date:
             government minister or select committee chair whose portfolio
             matches the question 1.0, in another portfolio 0.5; shadow
             minister matching 0.6, other 0.3; an ordinary member of a
             select committee on the question's subject 0.3; backbencher 0.
locality   = local_factor (default 0.7) for a contribution about one place (a town's bus route, a
             constituency's station: see rank.places), unless the question is itself local; otherwise 1
score      = relevance * recency * authority * locality
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class RankParams:
    recency: float = 0.5
    role_weight: float = 1.5
    bm25_weight: float = 0.5
    recency_floor: float = 0.35
    min_half_life_years: float = 2.0
    m_gov_match: float = 1.0
    m_gov_other: float = 0.5
    m_shadow_match: float = 0.6
    m_shadow_other: float = 0.3
    m_chair_match: float = 1.0
    m_chair_other: float = 0.5
    m_member_match: float = 0.3  # an ordinary member of a committee on the question's subject
    semantic_floor: float = 0.5
    semantic_ceiling: float = 0.85
    local_factor: float = 0.7
    today: date = field(default_factory=date.today)

    @classmethod
    def from_settings(cls, ranking: dict[str, Any], **overrides: Any) -> RankParams:
        keys = {f for f in cls.__dataclass_fields__ if f != "today"}
        values = {k: ranking[k] for k in keys if k in ranking}
        if "default_recency" in ranking:
            values["recency"] = ranking["default_recency"]
        if "default_role_weight" in ranking:
            values["role_weight"] = ranking["default_role_weight"]
        values.update({k: v for k, v in overrides.items() if v is not None})
        p = cls(**values)
        p.recency = min(max(float(p.recency), 0.0), 1.0)
        p.role_weight = min(max(float(p.role_weight), 1.0), 2.0)
        return p


@dataclass
class Candidate:
    record_id: str
    date: date
    bm25: float | None = None
    cosine: float | None = None
    is_gov: bool = False
    is_shadow: bool = False
    is_chair: bool = False
    gov_portfolios: list[str] = field(default_factory=list)
    shadow_portfolios: list[str] = field(default_factory=list)
    chair_portfolios: list[str] = field(default_factory=list)
    member_portfolios: list[str] = field(default_factory=list)  # subjects of committees sat on (set by the engine)
    best_passage_id: str | None = None
    local: bool = False  # mainly about one place, for a question that is not (set by the engine)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scored:
    candidate: Candidate
    relevance: float
    recency: float
    authority: float
    authority_m: float
    authority_reason: str
    score: float
    rerank: float | None = None  # the cross-encoder's score, when re-ranking is on
    base_relevance: float | None = None  # search relevance before re-ranking (decides which groups are in the pool)

    def breakdown(self) -> dict[str, Any]:
        out = {
            "score": round(self.score, 4),
            "relevance": round(self.relevance, 4),
            "keyword": None if self.candidate.bm25 is None else round(self.candidate.bm25, 4),
            "semantic": None if self.candidate.cosine is None else round(self.candidate.cosine, 4),
            "recency": round(self.recency, 4),
            "authority": round(self.authority, 4),
            "authority_reason": self.authority_reason,
        }
        if self.candidate.local:
            out["local"] = True
        if self.rerank is not None:
            out["reranker"] = round(self.rerank, 4)
        return out


def recency_factor(d: date, p: RankParams) -> float:
    if p.recency <= 0:
        return 1.0
    half_life = p.min_half_life_years / p.recency
    age_years = max((p.today - d).days, 0) / 365.25
    return p.recency_floor + (1 - p.recency_floor) * 0.5 ** (age_years / half_life)


def authority_m(c: Candidate, query_portfolios: set[str], p: RankParams) -> tuple[float, str]:
    options: list[tuple[float, str]] = [(0.0, "no ministerial or committee-chair office")]
    if c.is_gov:
        hit = query_portfolios & set(c.gov_portfolios)
        options.append((p.m_gov_match, f"government minister ({', '.join(sorted(hit))})") if hit
                       else (p.m_gov_other, "government minister (other portfolio)"))
    if c.is_chair:
        hit = query_portfolios & set(c.chair_portfolios)
        options.append((p.m_chair_match, f"select committee chair ({', '.join(sorted(hit))})") if hit
                       else (p.m_chair_other, "select committee chair (other subject)"))
    if c.is_shadow:
        hit = query_portfolios & set(c.shadow_portfolios)
        options.append((p.m_shadow_match, f"shadow minister ({', '.join(sorted(hit))})") if hit
                       else (p.m_shadow_other, "shadow minister (other portfolio)"))
    hit = query_portfolios & set(c.member_portfolios)
    if hit:
        options.append((p.m_member_match, f"select committee member ({', '.join(sorted(hit))})"))
    return max(options, key=lambda o: o[0])


def rank(candidates: list[Candidate], query_portfolios: set[str] | list[str], p: RankParams) -> list[Scored]:
    if not candidates:
        return []
    qp = set(query_portfolios)
    bm_max = max((c.bm25 for c in candidates if c.bm25), default=0.0)
    has_sem = any(c.cosine is not None for c in candidates)
    w = p.bm25_weight if (bm_max and has_sem) else (1.0 if bm_max else 0.0)
    span = max(p.semantic_ceiling - p.semantic_floor, 1e-6)
    out: list[Scored] = []
    for c in candidates:
        kw = (c.bm25 or 0.0) / bm_max if bm_max else 0.0
        sem = 0.0 if c.cosine is None else min(max((c.cosine - p.semantic_floor) / span, 0.0), 1.0)
        rel = w * kw + (1 - w) * sem
        rec = recency_factor(c.date, p)
        m, why = authority_m(c, qp, p)
        auth = 1 + (p.role_weight - 1) * m
        local = p.local_factor if c.local else 1.0
        out.append(Scored(c, rel, rec, auth, m, why, rel * rec * auth * local))
    out.sort(key=lambda s: (-s.score, -s.relevance, s.candidate.record_id))
    return out
