"""End-to-end question answering: plan -> retrieve -> votes -> cite -> verify.

Events (all JSON-serialisable) are yielded so the API can stream them:

* ``plan``     the filters used (editable chips in the UI)
* ``sources``  numbered source cards (verbatim text, speaker on the date, links)
* ``text``     answer text as it streams
* ``final``    verified answer: segments with [Source n] markers, grounding report
* ``error``    something failed; nothing unsupported is shown
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from hansard.config import Settings
from hansard.engine import Engine, Filters, Scope
from hansard.rag.british import britishise_segments, to_british
from hansard.rag.neutral import flag_loaded
from hansard.rag.prompts import NO_RECORDS
from hansard.rag.query_parse import (
    QueryPlan,
    parse_model,
    parse_offline,
    parse_with_model,
    resolve_speakers,
)
from hansard.rag.sources import build_sources
from hansard.rag.verify import check_quotes, drop_preambles, verify
from hansard.sources.demographics import AGE_BAND_LABEL, GENDER_LABEL
from hansard.sources.elections import MAJORITY_BAND_LABEL, TENURE_LABEL
from hansard.votes import VoteFinder

log = logging.getLogger(__name__)

# not offered as choices: the Chair's roles and catch-alls, and second spellings of a party offered under another
HIDDEN_PARTIES = {"speaker", "deputy-speaker", "judge", "other", "labourco-operative", "conservative-independent"}


@dataclass
class AskRequest:
    question: str
    mode: str = "summary"                     # summary | comprehensive
    recency: float | None = None              # 0..1
    role_weight: float | None = None          # 1..2
    scope: Scope = field(default_factory=Scope)
    plan: dict[str, Any] | None = None        # edited plan from the UI (skips re-parsing)
    history: list[dict[str, str]] = field(default_factory=list)
    compare: list[dict[str, Any]] = field(default_factory=list)  # sides to compare, from the Compare page


def plan_note(plan: QueryPlan, scope: Scope) -> str:
    f = plan.filters
    bits = []
    if f.date_from or f.date_to:
        bits.append(f"dates {f.date_from or 'start'} to {f.date_to or 'today'}")
    if f.houses:
        bits.append("houses: " + ", ".join(f.houses))
    if f.regions:
        bits.append("MPs for seats in: " + ", ".join(f.regions))
    if f.parties:
        bits.append("parties: " + ", ".join(f.parties))
    if plan.speakers:
        bits.append("speakers: " + ", ".join(s["name"] for s in plan.speakers))
    if f.offices:
        bits.append("speaker roles: " + ", ".join(f.offices))
    if f.genders:
        bits.append("speakers: " + " and ".join(GENDER_LABEL[g] for g in f.genders))
    if f.age_bands:
        bits.append("speakers aged " + ", ".join(AGE_BAND_LABEL[b] for b in f.age_bands) + " on the day")
    if f.tenure:
        bits.append("speakers: " + " and ".join(TENURE_LABEL[t] for t in f.tenure) + " (on the day)")
    if f.majority_bands:
        bits.append("MPs in seats that were " + ", ".join(MAJORITY_BAND_LABEL[b] for b in f.majority_bands)
                    + " at the time")
    if f.seat_types:
        bits.append("MPs for " + " and ".join(f.seat_types) + " seats (on the day they spoke)")
    if not scope.written:
        bits.append("written answers/statements excluded")
    if not scope.pbc:
        bits.append("Public Bill Committees excluded")
    if plan.compare:
        bits.append("comparing " + " and ".join(s["label"] for s in plan.compare) + ", each searched separately")
    return "; ".join(bits)


def plan_facets(plan: QueryPlan) -> set[str]:
    """Speaker facts the question is about (gender, age, first term, majority): only these are given to the model."""
    f = plan.filters
    out = {plan.group_by} - {"role"}
    for facet, active in (("gender", f.genders), ("age", f.age_bands), ("tenure", f.tenure),
                          ("majority", f.majority_bands)):
        if active:
            out.add(facet)
    return out


def rerank_query(plan: QueryPlan, how: str | None) -> str:
    """What the re-ranker compares passages with: the question as asked, the search keywords, or both
    (the keywords carry the topic of a follow-up such as "what about the Lords?")."""
    if how == "keywords":
        return plan.search_text
    if how == "question":
        return plan.question
    return f"{plan.question} ({plan.search_text})"


# the start of a witness's opening statement
_INTRODUCTION = re.compile(r"\b(my name is|by way of introduction|thank you (very much )?(for|,)[^.]{0,60}"
                           r"(opportunity|inviting|having me)|i am the [\w -]{0,60}(of|at) )", re.I)
# an answer that declines to comment, or only promises to follow up
_NOTHING_SAID = re.compile(r"\b((would|could) not (want to |wish to |be able to )?comment|(not|unable) (be )?(able |in a "
                           r"position )?to comment|can(no|')t comment|no comment|(i am|i'm) (always )?happy to (meet|write"
                           r"|follow up|come back)|(i will|i'll|we will|we'll) (have to )?(write to|come back to) (you|the "
                           r"committee))\b", re.I)
# Without an embedding model to compare them, the first few found of each (summary, comprehensive)
FEW_EDMS, FEW_EVIDENCE, FEW_DEVOLVED = (2, 3), (3, 5), (2, 3)
# how many may be chosen, at most, of each kind (devolved: of each parliament)
SIDE_CAPS = {False: {"edms": 3, "evidence": 6, "devolved": 8}, True: {"edms": 4, "evidence": 8, "devolved": 12}}


def first_few(side: dict[str, list], comprehensive: bool) -> dict[str, list]:
    """The first few found of each kind (of each devolved parliament), as their own searches ranked them."""
    kept = {"edms": side["edms"][:FEW_EDMS[comprehensive]], "evidence": side["evidence"][:FEW_EVIDENCE[comprehensive]],
            "devolved": []}
    per_body: dict[str, int] = {}
    for d in side["devolved"]:
        per_body[d.get("legislature")] = per_body.get(d.get("legislature"), 0) + 1
        if per_body[d.get("legislature")] <= FEW_DEVOLVED[comprehensive]:
            kept["devolved"].append(d)
    return kept


class Pipeline:
    def __init__(self, settings: Settings, engine: Engine | None = None, client=None, today: date | None = None):
        self.settings = settings
        self.engine = engine or Engine(settings, today=today)
        self.votes = VoteFinder(settings, self.engine.con)
        self._client = client
        self.today = today
        self._edms = None
        self._devolved = None
        self._evidence = None
        self._evidence_vectors = None
        self._size: tuple[float, int] | None = None
        self._registers = None

    @property
    def client(self):
        if self._client is None:
            from hansard.rag.answer import make_client

            self._client = make_client()
        return self._client

    def has_model(self) -> bool:
        if self._client is not None:
            return True
        import os

        from hansard.rag.answer import api_key

        return bool(api_key() or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def parties(self) -> list[tuple[str, str]]:
        rows = self.engine.resolver.con.execute(
            """SELECT DISTINCT p.party_id, p.party_name FROM ref_parties p
               JOIN ref_memberships m ON m.party_id = p.party_id WHERE m."end" >= ?
               ORDER BY 1""", [self.settings.archive_start]).fetchall()
        return [(r[0], r[1]) for r in rows]

    def party_choices(self) -> list[tuple[str, str]]:
        """The parties a reader can choose from: not the Chair's roles or other non-parties, and one entry for
        names recorded two ways (Labour includes Labour/Co-operative members)."""
        return [(i, n) for i, n in self.parties() if i not in HIDDEN_PARTIES]

    def make_plan(self, req: AskRequest) -> tuple[QueryPlan, dict[str, int] | None]:
        if req.plan:
            return QueryPlan.from_dict({"question": req.question, **req.plan}), None
        usage = None
        if self.has_model():
            try:
                plan, u = parse_with_model(self.client, self.settings, req.question, req.history, self.parties(),
                                           self.today)
                plan.interpretation = to_british(plan.interpretation)
                from hansard.rag.answer import usage_dict

                usage = usage_dict(u)
            except Exception as exc:
                log.warning("model query parsing failed (%s); using offline rules", exc)
                plan = parse_offline(req.question, self.settings.regions, self.today)
                plan.parse_error = f"{type(exc).__name__}: {exc}"
        else:
            plan = parse_offline(req.question, self.settings.regions, self.today)
        if plan.unresolved_speakers:
            plan.speakers, plan.unresolved_speakers = resolve_speakers(
                self.engine.resolver.con, plan.unresolved_speakers, plan.filters)
            plan.filters.person_ids = [s["person_id"] for s in plan.speakers]
        self._set_comparison(plan, req)
        return plan, usage

    def person_name(self, person_id: str) -> str | None:
        row = self.engine.resolver.con.execute("SELECT latest_name FROM ref_persons WHERE person_id = ?",
                                               [person_id]).fetchone()
        return row[0] if row else None

    def _set_comparison(self, plan: QueryPlan, req: AskRequest) -> None:
        """Sides from the Compare page, or from a question that asks to compare named members or parties."""
        from hansard.compare import resolve_sides, sides_from_question

        names = dict(self.parties())
        sides = (resolve_sides(req.compare, names, self.person_name) if req.compare
                 else sides_from_question(plan.question, plan.filters, plan.speakers, names))
        if not sides:
            return
        plan.compare = sides
        kinds = {s["kind"] for s in sides}
        # each side brings its own filter; the question's other filters (dates, House, roles) still apply to both
        if "party" in kinds:
            plan.filters.parties = []
        if "person" in kinds:
            plan.filters.person_ids = []
        if "nation" in kinds:
            plan.filters.regions = []
        plan.group_by = "role"
        plan.interpretation = (plan.interpretation + " " if plan.interpretation else "") + \
            "Comparing " + " and ".join(s["label"] for s in sides) + ", each searched separately."

    def find(self, plan: QueryPlan, req: AskRequest):
        """The evidence for a plan: (search result, divisions, numbered sources). Free: no model call."""
        limit = int(self.settings.answer.get(
            "comprehensive_sources" if req.mode == "comprehensive" else "summary_sources", 12))
        rq = rerank_query(plan, self.settings.ranking.get("rerank_query"))
        # votes, motions, the devolved parliaments and committee evidence are looked up alongside the main search
        side = {"votes": req.scope.votes and plan.wants_votes, "edms": getattr(req.scope, "edms", True),
                "devolved": getattr(req.scope, "devolved", False), "evidence": getattr(req.scope, "evidence", False)}
        lookups = {"votes": lambda: self.votes.find(plan.search_text, plan.filters, limit=4),
                   "edms": lambda: self.compared("edms", self.find_edms(plan, req), plan),
                   "devolved": lambda: self.compared("devolved", self.find_devolved(plan, req), plan),
                   "evidence": lambda: self.compared("evidence", self.find_evidence(plan, req), plan)}
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = {k: pool.submit(lookups[k]) for k, wanted in side.items() if wanted}
            if plan.compare:
                result = self._compare_search(plan, req, limit, rq)
            else:
                result = self.engine.search(plan.search_text, plan.filters, req.scope, plan.portfolios,
                                            req.recency, req.role_weight, limit, diversify=True,
                                            group_by=plan.group_by, rerank_query=rq,
                                            local_question=self.local_question(plan))
            found = {k: f.result() for k, f in pending.items()}
        divisions = found.get("votes", [])
        if req.scope.votes and not plan.wants_votes and not result.records:
            divisions = self.votes.find(plan.search_text, plan.filters, limit=4)
        side_found = {k: found.get(k, []) for k in ("edms", "devolved", "evidence")}
        result, side_found = self.make_room(plan, req, result, side_found, limit)
        return result, divisions, build_sources(result.records, divisions, plan_facets(plan), plan.group_by,
                                                side_found["edms"], side_found["devolved"], side_found["evidence"])

    def compared(self, kind: str, items: list[dict[str, Any]], plan: QueryPlan) -> list[dict[str, Any]]:
        """Each item with its closeness in meaning to the question (``_sim``), worked out while the main search runs.
        Left out if there is no embedding model or it fails; ``make_room`` then keeps the first few."""
        from hansard.rag.blend import side_text, similarities

        embedder = self.engine.embedder
        if embedder is None or not items:
            return items
        try:
            for item, sim in zip(items, similarities(embedder, plan.search_text, [side_text(kind, i) for i in items]),
                                 strict=True):
                item["_sim"] = sim
        except Exception:  # never let this step break a briefing
            log.exception("could not compare %s with the question", kind)
        return items

    def make_room(self, plan: QueryPlan, req: AskRequest, result, side: dict[str, list], limit: int):
        """Choose among the motions, devolved contributions and witnesses' answers found, and how many Westminster
        records they may replace, by how close each is in meaning to the question (see ``hansard.rag.blend``).
        Without an embedding model, the first few of each are kept, as their own searches ranked them."""
        from dataclasses import replace

        from hansard.rag.blend import blend, similarities
        from hansard.rank.evidence import evidence_summary

        comprehensive = req.mode == "comprehensive"
        pairs = [(k, item) for k in ("edms", "devolved", "evidence") for item in side[k]]
        embedder = self.engine.embedder
        if not pairs:
            return result, side
        side_sims = [item.pop("_sim", None) for _, item in pairs]
        if embedder is None or None in side_sims:
            return result, first_few(side, comprehensive)
        a = self.settings.answer
        lo, hi = getattr(embedder, "similarity_range", (0.5, 0.85))
        floor = float(a.get("side_min_similarity", lo + 0.4 * (hi - lo)))
        try:
            record_sims: list[float | None] = [(r.get("ranking") or {}).get("semantic") for r in result.records]
            missing = [i for i, s in enumerate(record_sims) if s is None]
            for i, s in zip(missing, similarities(embedder, plan.search_text,
                                                  [result.records[i].get("excerpt") or "" for i in missing]),
                            strict=True):
                record_sims[i] = s
        except Exception:  # never let this step break a briefing: keep the first few of each
            log.exception("could not compare the other records with the question")
            return result, first_few(side, comprehensive)
        records, kept = blend(
            result.records, record_sims, pairs, side_sims, floor=floor,
            budget=int(a.get("comprehensive_side_sources" if comprehensive else "side_sources",
                             15 if comprehensive else 10)),
            keep=max(limit // 2, 1), caps=SIDE_CAPS[comprehensive],
            persistent=int(self.settings.ranking.get("min_occasions", 3)))
        if len(records) != len(result.records):
            result = replace(result, records=records, evidence=evidence_summary(records))
        return result, kept

    def local_question(self, plan: QueryPlan) -> bool:
        """A question about a place, a region, a kind of seat or particular members: their local matters belong."""
        from hansard.rank.places import places_for

        f = plan.filters
        if f.regions or f.person_ids or plan.speakers or f.seat_types:
            return True
        if re.search(r"\b(local|constituenc\w*|my area|town|village|county|council)\b", plan.question, re.I):
            return True
        return bool(places_for(self.settings.reference_dir).mentioned(f"{plan.question} {plan.search_text}"))

    def evidence_by_meaning(self, store, plan: QueryPlan) -> list[dict[str, Any]]:
        """Witnesses' answers close in meaning to the question, from the evidence's own search index (empty until
        the answers have been embedded, or when there is no embedding model)."""
        from hansard.index.evidence import EvidenceVectors
        from hansard.sync import evidence_index_path

        path = evidence_index_path(self.settings)
        embedder = self.engine.embedder
        if embedder is None or not path.exists():
            return []
        if self._evidence_vectors is None:
            self._evidence_vectors = EvidenceVectors(path, int(getattr(embedder, "dim", 384)))
        floor, ceiling = getattr(embedder, "similarity_range", (0.5, 0.85))
        # well inside the range the model gives related text, so that loosely related answers stay out
        keep = float(self.settings.section("committees").get("evidence_min_similarity",
                                                             floor + 0.4 * (ceiling - floor)))
        hits = [(s, o) for s, o, sim in self._evidence_vectors.search(embedder.embed_query(plan.search_text))
                if sim >= keep]
        rows = store.turns_by_key(hits)
        return [rows[k] for k in hits if k in rows]

    def find_evidence(self, plan: QueryPlan, req: AskRequest) -> list[dict[str, Any]]:
        """A few witnesses' answers to committees on the subject (when the reader asks for them): one per session
        and per organisation, so one inquiry or one body does not fill the evidence."""
        if plan.speakers or plan.filters.person_ids or plan.compare:
            return []
        from hansard.sources.committees import Committees
        from hansard.sources.evidence import EvidenceStore, header_witnesses, witness_for
        from hansard.sync import committees_path, evidence_path
        from hansard.votes import keywords

        if not evidence_path(self.settings).exists() or not committees_path(self.settings).exists():
            return []
        wanted = 12 if req.mode == "comprehensive" else 8  # a pool to choose from (see make_room)
        try:
            if self._evidence is None:
                self._evidence = (EvidenceStore(evidence_path(self.settings)), Committees(committees_path(self.settings)))
            store, committees = self._evidence
            words = keywords(plan.search_text)
            turns = merge_ranked(store.relevant(words), self.evidence_by_meaning(store, plan))
            # an answer found by meaning alone must still name something particular to the question ("Palantir"),
            # not only its general words ("company", "NHS"); and one that declines to comment says nothing to quote
            rare = store.distinctive(words)
            if rare:
                named = re.compile(r"\b(" + "|".join(re.escape(w) for w in rare) + ")", re.I)
                turns = [t for t in turns if named.search(t["text"])]
            turns = [t for t in turns if not _NOTHING_SAID.search(t["text"][:300])]
            # a witness's opening statement (who they are, what their organisation does) only if nothing else they
            # said matched: their answers to the committee's questions carry their views
            turns.sort(key=lambda t: bool(_INTRODUCTION.search(t["text"][:400])))
            sessions = committees.sessions_meta({t["session_id"] for t in turns})
            # Parliament lists no witnesses for some sessions; the transcript's own header names them
            unlisted = {sid for sid, s in sessions.items() if not any((w.get("name") or "").strip()
                                                                      for w in s["witnesses"])}
            for sid, header in store.headers(unlisted).items():
                sessions[sid]["witnesses"] = header_witnesses(header)
        except Exception:  # never let the committee record break a briefing
            log.exception("committee evidence unavailable")
            return []
        lo = plan.filters.date_from and plan.filters.date_from.isoformat()
        hi = plan.filters.date_to and plan.filters.date_to.isoformat()
        from hansard.sources.committees import org_key

        out: list[dict[str, Any]] = []
        used_sessions: set[int] = set()
        used_bodies: set[str] = set()
        used_inquiries: set[int] = set()
        for t in turns:
            s = sessions.get(t["session_id"])
            if not s or not s.get("date") or t["session_id"] in used_sessions:
                continue
            if (lo and s["date"] < lo) or (hi and s["date"] > hi):
                continue
            w = witness_for(t["speaker"], s["witnesses"])
            body = org_key((w or {}).get("organisation")) or ((w or {}).get("name") or "").lower()
            if not w or body in used_bodies or (s.get("inquiry_id") and s["inquiry_id"] in used_inquiries):
                continue  # one answer per body (however its name is written), per session and per inquiry
            used_sessions.add(t["session_id"])
            used_bodies.add(body)
            if s.get("inquiry_id"):
                used_inquiries.add(s["inquiry_id"])
            text, kept = "", []
            for para in t["text"].split("\n\n"):  # whole paragraphs, to about 2,500 characters
                if kept and len(text) + len(para) > 2500:
                    break
                kept.append(para)
                text = "\n\n".join(kept)
            out.append({**t, "text": text, "witness": w, "date": s["date"], "committees": s.get("committees"),
                        "inquiry": s.get("inquiry"), "inquiry_id": s.get("inquiry_id"), "url": s.get("url")})
            if len(out) >= wanted:
                break
        return out

    def find_devolved(self, plan: QueryPlan, req: AskRequest) -> list[dict[str, Any]]:
        """A few contributions from each devolved parliament on the subject (when the reader asks for them)."""
        if plan.speakers or plan.filters.person_ids or plan.compare:
            return []
        from hansard.devolved import LEGISLATURES, DevolvedStore, Members
        from hansard.people import person_number
        from hansard.sync import devolved_path
        from hansard.votes import keywords

        path = devolved_path(self.settings)
        if not path.exists():
            return []
        try:
            if self._devolved is None:
                self._devolved = (DevolvedStore(path), Members(self.settings.parlparse_dir / "members" / "people.json"))
            store, members = self._devolved
            found = store.relevant(keywords(plan.search_text),
                                   date_from=plan.filters.date_from and plan.filters.date_from.isoformat(),
                                   date_to=plan.filters.date_to and plan.filters.date_to.isoformat(),
                                   per_body=12 if req.mode == "comprehensive" else 8)  # a pool (see make_room)
        except Exception:  # never let the devolved record break a briefing
            log.exception("devolved parliaments unavailable")
            return []
        for d in found:
            cfg = LEGISLATURES[d["legislature"]]
            on = members.on(d["person_id"], cfg["org"], d["date"])
            number = person_number(d["person_id"]) if d["person_id"] else None
            name = on.get("name") or d["speaker"]
            d["body"] = cfg["name"]
            d["member"] = {**on, "name": name, "number": number,
                           "slug": re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") if number else None}
        return found

    def find_edms(self, plan: QueryPlan, req: AskRequest) -> list[dict[str, Any]]:
        """A few early day motions on the subject, with signatures counted by party on the day (when collected)."""
        if plan.speakers or plan.filters.person_ids or plan.compare:
            return []  # questions about particular members or sides: their own words and votes come first
        from hansard.people import members_by_parliament_id
        from hansard.sources.edms import EdmStore
        from hansard.sync import edms_path
        from hansard.votes import keywords

        path = edms_path(self.settings)
        if not path.exists():
            return []
        try:
            if self._edms is None:
                self._edms = EdmStore(path)
            found = self._edms.relevant(keywords(plan.search_text),
                                        date_from=plan.filters.date_from and plan.filters.date_from.isoformat(),
                                        date_to=plan.filters.date_to and plan.filters.date_to.isoformat(),
                                        limit=6 if req.mode == "comprehensive" else 4)  # a pool (see make_room)
            pairs = [(s["mnis"], s["signed"]) for e in found for s in e["sponsors"]]
            pairs += [(e["primary_mnis"], e["date_tabled"]) for e in found if e["primary_mnis"]]
            members = members_by_parliament_id(self.engine.con, pairs)
        except Exception:  # never let motions break a briefing
            log.exception("early day motions unavailable")
            return []
        for e in found:
            counts: dict[str, int] = {}
            for s in e["sponsors"]:
                if not s["withdrawn"]:
                    party = (members.get((s["mnis"], s["signed"])) or {}).get("party") or s["party"] or "Unknown"
                    counts[party] = counts.get(party, 0) + 1
            e["party_counts"] = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
            m = members.get((e["primary_mnis"], e["date_tabled"])) or {}
            e["sponsor"] = {"name": m.get("name") or e["primary_name"], "party": m.get("party") or e["primary_party"],
                            "number": m.get("number"), "slug": m.get("slug")}
        return found

    def _compare_search(self, plan: QueryPlan, req: AskRequest, limit: int, rq: str):
        """One search per side, each with an equal share of the sources."""
        from dataclasses import replace

        from hansard.compare import side_filters

        share = max(4, -(-limit // len(plan.compare)))
        results = []
        for side in plan.compare:
            res = self.engine.search(plan.search_text, side_filters(plan.filters, side), req.scope, plan.portfolios,
                                     req.recency, req.role_weight, share, diversify=True, group_by="role",
                                     rerank_query=rq)
            for r in res.records:
                r["compare_side"] = side["label"]
            results.append(res)
        records = [r for res in results for r in res.records]
        from hansard.rank.evidence import evidence_summary

        return replace(results[0], records=records, scored=[x for res in results for x in res.scored],
                       no_records=all(res.no_records for res in results),
                       candidates=sum(res.candidates for res in results), evidence=evidence_summary(records))

    def checks_interests(self) -> bool:
        return bool(self.settings.answer.get("interests_check", True)) and self.has_model()

    def find_interests(self, question: str, sources: list[Any]) -> tuple[list[dict[str, Any]], Any]:
        """Registered interests that may relate to what the quoted members said (never stops a briefing)."""
        from hansard.rag.interests import check
        from hansard.sources.donations import Donations
        from hansard.sources.registers import Registers
        from hansard.sources.regmem import Register
        from hansard.sync import donations_path, regmem_path

        try:
            if self._registers is None:
                self._registers = (Register(regmem_path(self.settings)), Registers(self.settings.data_dir),
                                   Donations(donations_path(self.settings)))
            commons, lords, donations = self._registers
            return check(self.client, parse_model(self.settings), question, sources, commons, lords, donations,
                         effort=self.settings.answer.get("query_parse_effort", "low"))
        except Exception:
            log.exception("interests check failed")
            return [], None

    def archive_size(self) -> int:
        """Passages in the search index (counted at most every ten minutes: it changes twice a day)."""
        import time

        now = time.time()
        if self._size is None or now - self._size[0] > 600:
            try:
                self._size = (now, int(self.engine.index.stats()["passages"]))
            except Exception:  # an empty or missing index: the count is only for the progress message
                self._size = (now, 0)
        return self._size[1]

    def run(self, req: AskRequest) -> Iterator[dict[str, Any]]:
        # straight away, before the question is read: the reader sees work begin, and how much will be searched
        yield {"type": "progress", "step": "reading", "passages": self.archive_size()}
        plan, parse_usage = self.make_plan(req)
        ev: dict[str, Any] = {"type": "plan", "plan": plan.to_dict(), "usage": parse_usage}
        if plan.parse_error:
            ev["parse_error"] = plan.parse_error  # for the operator's failure log; removed before reaching readers
        yield ev

        result, divisions, sources = self.find(plan, req)
        from hansard.devolution import notes_for
        from hansard.rank.evidence import evidence_summary

        yield {"type": "sources", "sources": [s.card() for s in sources], "candidates": result.candidates,
               "semantic": result.semantic_available,
               "evidence": {**evidence_summary(result.records, divisions), "devolution": notes_for(plan.question),
                            "edms": sum(1 for s in sources if s.kind == "edm"),
                            "devolved": sum(1 for s in sources if s.kind == "devolved"),
                            "committee_evidence": sum(1 for s in sources if s.kind == "evidence")}}

        if not sources or (result.no_records and not divisions):
            yield {"type": "final", "answer": {"segments": [{"text": NO_RECORDS + " The archive contains no "
                   "records matching this question and these filters.", "sources": [], "cited": [],
                   "status": "connective"}], "markdown": NO_RECORDS, "no_records": True, "citations_total": 0,
                   "citations_invalid": 0, "grounded_ratio": 1.0, "unsupported_segments": 0}, "usage": None}
            return
        if not self.has_model():
            yield {"type": "final", "answer": None, "usage": None,
                   "note": "Showing the evidence set only (no Anthropic API key on the server)."}
            return

        from hansard.rag.answer import Answerer

        # members' registered interests that may relate to what they said: checked while the briefing is written
        pool = ThreadPoolExecutor(max_workers=1)
        interests = pool.submit(self.find_interests, req.question, sources) if self.checks_interests() else None
        flags: tuple[list[dict[str, Any]], Any] | None = None
        final = None
        try:
            for ev in Answerer(self.settings, self.client).stream(
                    req.question, sources, req.mode, plan_note(plan, req.scope), req.history, self.today,
                    group_by=plan.group_by, compare=plan.compare):
                if ev["type"] == "final":
                    final = ev
                else:
                    yield ev
                if interests is not None and flags is None and interests.done():
                    flags = interests.result()
                    if flags[0]:
                        yield {"type": "interests", "interests": flags[0]}
            if final is None or final["stop_reason"] == "refusal":
                yield {"type": "error", "message": "The model declined to answer this question."}
                return
            yield {"type": "progress", "step": "checking"}
            if interests is not None and flags is None:
                flags = interests.result()
                if flags[0]:
                    yield {"type": "interests", "interests": flags[0]}
        finally:
            pool.shutdown(wait=False)
        report = verify(final["blocks"], sources)
        drop_preambles(report.segments)       # "Across the records retrieved, ..." openers
        britishise_segments(report.segments)  # the model's own words only; quotations stay verbatim
        flag_loaded(report.segments)          # judgemental words in its own voice are highlighted, not hidden
        check_quotes(report, sources)         # quotations not found word for word in their sources are marked
        from hansard.rag.answer import usage_dict

        yield {"type": "final", "answer": report.to_dict(), "usage": final["usage"], "model": final["model"],
               "stop_reason": final["stop_reason"], "interests": (flags or ([], None))[0],
               "interests_usage": usage_dict(flags[1]) if flags and flags[1] is not None else None}


def answer_once(pipeline: Pipeline, req: AskRequest) -> dict[str, Any]:
    """Run the pipeline to completion (CLI / tests)."""
    out: dict[str, Any] = {"text": ""}
    for ev in pipeline.run(req):
        if ev["type"] == "text":
            out["text"] += ev["text"]
        elif ev["type"] in ("plan", "sources", "final", "error"):
            out[ev["type"]] = ev
    return out


__all__ = ["AskRequest", "Filters", "Pipeline", "answer_once"]


def merge_ranked(*lists: list[dict[str, Any]], k: int = 60) -> list[dict[str, Any]]:
    """Several best-first lists of turns as one (reciprocal rank fusion): a turn high in either list comes early,
    and one found both ways comes earliest."""
    score: dict[tuple[int, int], float] = {}
    first: dict[tuple[int, int], dict[str, Any]] = {}
    for items in lists:
        for rank, t in enumerate(items):
            key = (t["session_id"], t["ord"])
            score[key] = score.get(key, 0.0) + 1.0 / (k + rank + 1)
            first.setdefault(key, t)
    return [first[key] for key in sorted(score, key=lambda x: -score[x])]
