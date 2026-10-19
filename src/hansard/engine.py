"""Deterministic retrieval: filters -> BM25 + vector candidates -> composite rank -> records.

Given the same index, query text, filters and slider values, results are
identical every time. The only non-deterministic step in the product, turning
a plain-English question into filters, happens before this (``rag.query_parse``)
and its output is shown to the user as editable chips.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from hansard.config import Settings
from hansard.embed.base import Embedder, get_embedder
from hansard.index.lance import SearchIndex, date_lit, sql_str
from hansard.rank.composite import Candidate, RankParams, Scored, rank
from hansard.rank.evidence import evidence_summary, select_evidence
from hansard.rank.places import places_for
from hansard.rank.rerank import Reranker, get_reranker, rerank
from hansard.reference.profiles import ProfileResolver
from hansard.sources.demographics import AGE_BANDS, age_band, age_on, assumed_birth, band_dates
from hansard.store.parquet import read_records
from hansard.store.shared import shared

log = logging.getLogger(__name__)

WRITTEN_SOURCES = {"wrans", "lordswrans", "wms", "lordswms"}
PBC_SOURCES = {"standing"}
ORAL_SOURCES = {"debates", "westminhall", "lordspages"}
GROUP_BY = ("role", "gender", "age", "tenure", "majority")


@dataclass
class Filters:
    date_from: date | None = None
    date_to: date | None = None
    houses: list[str] = field(default_factory=list)          # "commons", "lords"
    regions: list[str] = field(default_factory=list)         # ITL1 names
    parties: list[str] = field(default_factory=list)         # party ids, e.g. "labour"
    person_ids: list[str] = field(default_factory=list)
    offices: list[str] = field(default_factory=list)         # "gov", "shadow", "chair", "backbench"
    record_types: list[str] = field(default_factory=list)    # speech/question/answer/statement
    genders: list[str] = field(default_factory=list)         # "F", "M" (as recorded by Parliament)
    age_bands: list[str] = field(default_factory=list)       # see sources.demographics.AGE_BANDS; age on the day
    tenure: list[str] = field(default_factory=list)          # "first-term", "returning" (MPs, on the day)
    majority_bands: list[str] = field(default_factory=list)  # "marginal", "middle", "safe" (MPs, on the day)
    seat_types: list[str] = field(default_factory=list)      # "city", "town", "rural" (MPs' seats, on the day)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("date_from", "date_to"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


@dataclass
class Scope:
    """Corpus scope toggles from the UI."""

    written: bool = True   # written answers and written ministerial statements
    pbc: bool = True       # Public Bill Committee debates
    votes: bool = True     # division (voting) records
    edms: bool = True      # early day motions and who signed them
    devolved: bool = True   # the Scottish Parliament, the Senedd and the NI Assembly (reported in their own section)
    evidence: bool = True   # witnesses' oral evidence to select committees (reported in their own section)

    def sources(self) -> list[str]:
        s = set(ORAL_SOURCES)
        if self.written:
            s |= WRITTEN_SOURCES
        if self.pbc:
            s |= PBC_SOURCES
        return sorted(s)


@dataclass
class SearchResult:
    scored: list[Scored]
    records: list[dict[str, Any]]
    no_records: bool
    candidates: int
    semantic_available: bool
    where: str | None
    evidence: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)  # seconds per stage, for the Server-Timing header


def build_where(filters: Filters, scope: Scope) -> str:
    clauses = [f"source IN ({', '.join(sql_str(s) for s in scope.sources())})"]
    if filters.date_from:
        clauses.append(f"date >= {date_lit(filters.date_from)}")
    if filters.date_to:
        clauses.append(f"date <= {date_lit(filters.date_to)}")
    if filters.houses:
        clauses.append(f"house IN ({', '.join(sql_str(h) for h in filters.houses)})")
    if filters.regions:
        clauses.append(f"region IN ({', '.join(sql_str(r) for r in filters.regions)})")
    if filters.parties:
        clauses.append(f"party_id IN ({', '.join(sql_str(p) for p in filters.parties)})")
    if filters.person_ids:
        clauses.append(f"person_id IN ({', '.join(sql_str(p) for p in filters.person_ids)})")
    if filters.record_types:
        clauses.append(f"record_type IN ({', '.join(sql_str(t) for t in filters.record_types)})")
    office = []
    for o in filters.offices:
        if o == "gov":
            office.append("is_gov = true")
        elif o == "shadow":
            office.append("is_shadow = true")
        elif o == "chair":
            office.append("is_chair = true")
        elif o == "backbench":
            office.append("(is_gov = false AND is_shadow = false AND is_chair = false)")
    if office:
        clauses.append("(" + " OR ".join(office) + ")")
    return " AND ".join(clauses)


class Engine:
    def __init__(self, settings: Settings, embedder: Embedder | None = None, today: date | None = None,
                 reranker: Reranker | None | bool = True):
        self.settings = settings
        # True: load the model named in the settings when first needed; None or False: no re-ranking
        self._reranker: Reranker | None | bool = reranker
        dim = int(settings.embedding.get("dim", 384))
        self.index = SearchIndex(settings.index_dir, dim)
        self.resolver = ProfileResolver(settings.reference_dir)
        self._embedder = embedder
        self.today = today
        self.con = shared()
        self.con.execute("SET enable_object_cache = true")  # cache Parquet footers between queries
        self._has_vectors = False

    def has_vectors(self) -> bool:
        """Whether meaning-based search has anything to search yet (once true, it stays true: vectors only grow)."""
        if not self._has_vectors:
            self._has_vectors = self.index.stats()["vectors"] > 0
        return self._has_vectors

    @property
    def embedder(self) -> Embedder | None:
        if self._embedder is None:
            try:
                self._embedder = get_embedder(self.settings)
            except Exception as exc:  # semantic search degrades to keyword-only
                log.warning("embedder unavailable (%s); using keyword search only", exc)
                return None
        return self._embedder

    @property
    def reranker(self) -> Reranker | None:
        if self._reranker is True:
            self._reranker = get_reranker(self.settings)  # None if switched off or the model cannot be loaded
        return self._reranker or None

    # the same designation recorded two ways
    PARTY_ALIASES = {"independent-conservative": ["conservative-independent"]}

    def expand_parties(self, parties: list[str]) -> list[str]:
        """'labour' also matches Labour/Co-operative members (same whip); a party recorded two ways matches both."""
        if not parties:
            return []
        parties = sorted(set(parties) | {a for p in parties for a in self.PARTY_ALIASES.get(p, [])})
        rows = self.resolver.con.execute(
            """SELECT party_id FROM ref_parties WHERE list_contains(?, party_id) OR whip_group IN
               (SELECT whip_group FROM ref_parties WHERE list_contains(?, party_id) AND whip_group IS NOT NULL)""",
            [parties, parties]).fetchall()
        return sorted({r[0] for r in rows} | set(parties))

    def demographic_people(self, filters: Filters) -> tuple[list[str] | None, dict[str, date]]:
        """People who can match the gender and age filters, and their birth dates (for the exact check by date).

        Age is a property of a person on a date, so the index cannot hold it. Instead the search is limited to
        people who were in an age band at some point in the date range, and each record is then checked against
        the speaker's age on the day it was spoken.
        """
        if not (filters.genders or filters.age_bands or filters.tenure or filters.majority_bands):
            return None, {}
        clauses, params = ["m.\"end\" >= ?"], [filters.date_from or self.settings.archive_start]
        if filters.tenure or filters.majority_bands:
            clauses.append("m.house = 'commons'")  # first terms and majorities apply to MPs
        if filters.date_to:
            clauses.append("m.start <= ?")
            params.append(filters.date_to)
        if filters.genders:
            clauses.append("list_contains(?, p.gender)")
            params.append(filters.genders)
        if filters.age_bands:
            clauses.append("p.birth_date IS NOT NULL")
        rows = self.resolver.con.execute(
            f"""SELECT DISTINCT p.person_id, p.birth_date, p.birth_precision FROM ref_persons p
                JOIN ref_memberships m ON m.person_id = p.person_id WHERE {' AND '.join(clauses)}""",
            params).fetchall()
        lo, hi = filters.date_from or self.settings.archive_start, filters.date_to or (self.today or date.today())
        bands = [b for b in filters.age_bands if b in {k for k, *_ in AGE_BANDS}]
        people: list[str] = []
        births: dict[str, date] = {}
        for pid, born, precision in rows:
            birth = assumed_birth(born, precision)
            if bands:
                spans = [band_dates(birth, b) for b in bands]
                if not any(a <= hi and b >= lo for a, b in spans):
                    continue
                births[pid] = birth
            people.append(pid)
        if filters.person_ids:
            people = [p for p in people if p in set(filters.person_ids)]
        return sorted(people), births

    def seat_type_spans(self, filters: Filters) -> dict[str, list[tuple[date, date, str]]]:
        """For each MP who sat for a seat of the asked-for types (city, town, rural) in the date range: when."""
        params: list[Any] = [filters.seat_types, filters.date_from or self.settings.archive_start]
        clause = ""
        if filters.date_to:
            clause = "AND start <= ?"
            params.append(filters.date_to)
        rows = self.resolver.con.execute(
            f"""SELECT person_id, start, "end", settlement FROM ref_memberships
                WHERE house = 'commons' AND list_contains(?, settlement) AND "end" >= ? {clause}""",
            params).fetchall()
        out: dict[str, list[tuple[date, date, str]]] = {}
        for pid, start, end, kind in rows:
            out.setdefault(pid, []).append((start, end, kind))
        return out

    def search(self, query: str, filters: Filters | None = None, scope: Scope | None = None,
               portfolios: list[str] | None = None, recency: float | None = None,
               role_weight: float | None = None, limit: int = 12, diversify: bool = False,
               group_by: str = "role", rerank_query: str | None = None,
               local_question: bool | None = None) -> SearchResult:
        """``local_question``: whether the question is about a place, so local contributions are not
        down-weighted (by default: when the search words name a place, or regions or members are filtered)."""
        filters, scope = filters or Filters(), scope or Scope()
        places = places_for(self.settings.reference_dir)
        if local_question is None:
            local_question = bool(filters.regions or filters.person_ids or places.mentioned(query))
        r = self.settings.ranking
        if filters.parties:
            filters = Filters(**{**filters.__dict__, "parties": self.expand_parties(filters.parties)})
        post_filters = bool(filters.age_bands or filters.tenure or filters.majority_bands or filters.seat_types)
        if (filters.tenure or filters.majority_bands) and not (filters.genders or filters.age_bands):
            # First terms and majorities depend on the day, so each candidate is checked below; listing every MP
            # in the search itself would only slow it down. Limiting it to the Commons is enough.
            people, births = None, {}
            if not filters.houses:
                filters = Filters(**{**filters.__dict__, "houses": ["commons"]})
        else:
            people, births = self.demographic_people(filters)
        seat_spans: dict[str, list[tuple[date, date, str]]] = {}
        if filters.seat_types:
            # MPs who held a seat of these types during the dates asked about; each record is then checked
            # against the seat its speaker held on the day
            seat_spans = self.seat_type_spans(filters)
            people = sorted(set(seat_spans) & set(people)) if people is not None else sorted(seat_spans)
            if filters.person_ids:
                people = [p for p in people if p in set(filters.person_ids)]
            if not filters.houses:
                filters = Filters(**{**filters.__dict__, "houses": ["commons"]})
        if people is not None:
            # an impossible id keeps the search empty when nobody matches
            filters = Filters(**{**filters.__dict__, "person_ids": people or ["-none-"]})
        bands = set(filters.age_bands)
        tenure, majority = set(filters.tenure), set(filters.majority_bands)
        facts: dict[tuple[str, date], dict[str, Any]] = {}

        def seat(pid: str, on: date) -> dict[str, Any]:
            if (pid, on) not in facts:
                facts[(pid, on)] = self.resolver.seat_facts(pid, on)
            return facts[(pid, on)]

        def age_ok(hit: dict[str, Any]) -> bool:
            """The checks that depend on the date of the record: age, first term, majority at the time."""
            pid = hit.get("person_id")
            if bands:
                birth = births.get(pid)
                if birth is None or age_band(age_on(birth, hit["date"])) not in bands:
                    return False
            if seat_spans:
                on = hit["date"]
                if not any(a <= on <= b for a, b, _ in seat_spans.get(pid, [])):
                    return False
            if tenure or majority:
                f = seat(pid, hit["date"]) if pid else {}
                if tenure and {True: "first-term", False: "returning"}.get(f.get("first_term")) not in tenure:
                    return False
                if majority and f.get("majority_band") not in majority:
                    return False
            return True

        where = build_where(filters, scope)
        timings: dict[str, float] = {}
        clock = time.perf_counter()

        def lap(stage: str) -> None:
            nonlocal clock
            now = time.perf_counter()
            timings[stage] = timings.get(stage, 0.0) + now - clock
            clock = now

        # checks made after the search discard some candidates, so fetch more to start with
        widen = 2 if post_filters else 1

        def keyword() -> tuple[list[dict[str, Any]], float]:
            start = time.perf_counter()
            return self.index.bm25(query, where, int(r.get("candidates_bm25", 300)) * widen), time.perf_counter() - start

        # the keyword search runs on its own thread while the question is embedded and searched by meaning
        with ThreadPoolExecutor(max_workers=1) as pool:
            by_keyword = pool.submit(keyword)
            vec_hits: list[dict[str, Any]] = []
            semantic = self.has_vectors() and self.embedder is not None
            if semantic:
                vec = self.embedder.embed_query(query)
                lap("embed")
                vec_hits = self.index.vector(vec, where, int(r.get("candidates_vector", 300)) * widen,
                                             nprobes=r.get("vector_nprobes"),
                                             refine_factor=r.get("vector_refine_factor"))
                lap("meaning")
            bm, timings["keyword"] = by_keyword.result()
            lap("keyword_wait")

        cands: dict[str, Candidate] = {}

        def cand_for(hit: dict[str, Any]) -> Candidate:
            c = cands.get(hit["record_id"])
            if c is None:
                hit = dict(hit)
                c = Candidate(
                    record_id=hit["record_id"], date=hit["date"],
                    is_gov=bool(hit["is_gov"]), is_shadow=bool(hit["is_shadow"]), is_chair=bool(hit["is_chair"]),
                    gov_portfolios=list(hit["gov_portfolios"] or []),
                    shadow_portfolios=list(hit["shadow_portfolios"] or []),
                    chair_portfolios=list(hit["chair_portfolios"] or []),
                    data={"source": hit["source"], "source_key": hit["source_key"], "passages": {},
                          "person_id": hit.get("person_id")},
                )
                cands[hit["record_id"]] = c
            return c

        bm = [h for h in bm if age_ok(h)]
        vec_hits = [h for h in vec_hits if age_ok(h)]
        for h in bm:
            c = cand_for(h)
            c.data["passages"][h["passage_id"]] = {"text": h["text"], "heading": h["heading"]}
            if c.bm25 is None or h["_score"] > c.bm25:
                c.bm25 = float(h["_score"])
                c.best_passage_id = h["passage_id"]
        for h in vec_hits:
            c = cand_for(h)
            if h.get("text"):
                c.data["passages"].setdefault(h["passage_id"], {"text": h["text"], "heading": h.get("heading")})
            sim = 1.0 - float(h["_distance"])
            if c.cosine is None or sim > c.cosine:
                c.cosine = sim
                if c.bm25 is None:
                    c.best_passage_id = h["passage_id"]

        if not local_question:
            for c in cands.values():
                c.local = any(places.is_local(x.get("heading"), x.get("text")) for x in c.data["passages"].values())
        lap("places")

        floor, ceiling = getattr(self.embedder, "similarity_range", (0.5, 0.85)) if semantic else (0.5, 0.85)
        params = RankParams.from_settings(
            r, recency=recency, role_weight=role_weight,
            semantic_floor=r.get("semantic_floor", floor), semantic_ceiling=r.get("semantic_ceiling", ceiling),
            **({"today": self.today} if self.today else {}))
        if portfolios and cands:  # ordinary committee members on the subject (not held on the passages themselves)
            subjects = self.resolver.committee_subjects([(c.data.get("person_id"), c.date) for c in cands.values()])
            for c in cands.values():
                c.member_portfolios = subjects.get((c.data.get("person_id"), c.date), [])
        scored = rank(list(cands.values()), set(portfolios or []), params)
        lap("rank")
        if self.reranker is not None and scored:
            scored = rerank(scored, rerank_query or query, self.reranker, top=int(r.get("rerank_top", 150)),
                            weight=float(r.get("rerank_weight", 0.6)))
            lap("rerank")
        best_cos = max((c.cosine or 0.0 for c in cands.values()), default=0.0)
        no_records = not scored or (semantic and best_cos < float(r.get("no_records_cosine", 0.0))
                                    and not any(c.bm25 for c in cands.values()))
        if diversify and scored:
            pool = scored[:max(limit * 8, 150)]
            profiles = self.resolver.profiles([(s.candidate.record_id, s.candidate.data.get("person_id"),
                                                s.candidate.date) for s in pool])
            top = select_evidence(pool, profiles, limit, group_by=group_by if group_by in GROUP_BY else "role",
                                  max_per_speaker=int(r.get("max_per_speaker", 2)),
                                  min_relevance_ratio=float(r.get("min_relevance_ratio", 0.25)),
                                  min_occasions=int(r.get("min_occasions", 3)),
                                  max_persistent=int(r.get("max_persistent", 4)))
        else:
            top = scored[:limit]
        lap("select")
        records = self.records_for(top) if top else []
        lap("records")
        return SearchResult(top, records, no_records, len(cands), semantic, where, evidence_summary(records), timings)

    # ---- full records for display / grounding ---------------------------------
    def records_for(self, scored: list[Scored]) -> list[dict[str, Any]]:
        by_part: dict[tuple[str, int], list[Scored]] = {}
        for s in scored:
            by_part.setdefault((s.candidate.data["source"], s.candidate.date.year), []).append(s)
        found: dict[tuple[str, str], dict[str, Any]] = {}

        def read(part: tuple[str, int]) -> list[dict[str, Any]]:
            items = by_part[part]
            return read_records(self.settings.parquet_dir, part[0], part[1],
                                {s.candidate.record_id for s in items}, {s.candidate.date for s in items},
                                {s.candidate.data["source_key"] for s in items})

        # each source and year is read on its own thread (reading and unpacking Parquet releases the GIL)
        with ThreadPoolExecutor(max_workers=min(4, len(by_part) or 1)) as pool:
            for recs in pool.map(read, sorted(by_part)):
                for rec in recs:
                    # the indexed copy: other files may hold older copies, and an ID can repeat within a file
                    found.setdefault((rec["record_id"], rec["source_key"]), rec)
        rows = {s.candidate.record_id: found[key] for s in scored
                if (key := (s.candidate.record_id, s.candidate.data["source_key"])) in found}
        passages = {pid: p for s in scored for pid, p in s.candidate.data.get("passages", {}).items()}
        missing = [s.candidate.best_passage_id for s in scored
                   if s.candidate.best_passage_id and s.candidate.best_passage_id not in passages]
        passages.update(self.index.passages_for(missing))
        profiles = self.resolver.profiles([(rid, rows[rid]["xml_person_id"], rows[rid]["date"]) for rid in rows])
        out = []
        for s in scored:
            rec = rows.get(s.candidate.record_id)
            if rec is None:
                continue
            prof = profiles.get(s.candidate.record_id)
            passage = passages.get(s.candidate.best_passage_id or "", {})
            out.append({
                **{k: rec[k] for k in ("record_id", "source", "house", "chamber", "record_type", "time", "colnum",
                                       "uin", "major_heading", "minor_heading", "oral_heading", "bill", "text",
                                       "context", "official_url", "url_kind", "twfy_url", "speaker_name_raw")},
                "date": rec["date"].isoformat(),
                "heading": passage.get("heading") or rec.get("minor_heading") or rec.get("major_heading"),
                "excerpt": passage.get("text") or rec["text"][:1500],
                "speaker": prof.to_dict() if prof else None,
                "ranking": s.breakdown(),
                "occasions": s.candidate.data.get("occasions"),
            })
        return out
