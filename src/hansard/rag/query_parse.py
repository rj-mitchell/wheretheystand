"""Turn a plain-English question into structured, user-editable search filters.

This is the only model-driven step before retrieval. Its output is shown in
the UI as filter chips the user can remove or change; retrieval itself is
deterministic. Without an API key, :func:`parse_offline` applies simple rules.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from hansard.engine import Filters
from hansard.reference.portfolios import PORTFOLIOS, portfolios_for_query
from hansard.sources.demographics import AGE_BANDS

AGE_BAND_KEYS = [k for k, *_ in AGE_BANDS]
GROUP_BY_KEYS = ("role", "gender", "age", "tenure", "majority")

log = logging.getLogger(__name__)

ITL1_REGIONS = [
    "North East", "North West", "Yorkshire and The Humber", "East Midlands", "West Midlands",
    "East of England", "London", "South East", "South West", "Scotland", "Wales", "Northern Ireland",
]
Region = Literal["North East", "North West", "Yorkshire and The Humber", "East Midlands", "West Midlands",
                 "East of England", "London", "South East", "South West", "Scotland", "Wales", "Northern Ireland"]
Office = Literal["gov", "shadow", "chair", "backbench"]
House = Literal["commons", "lords"]
Gender = Literal["F", "M"]
AgeBand = Literal["under-40", "40-54", "55-69", "70-plus"]
GroupBy = Literal["role", "gender", "age", "tenure", "majority"]
Tenure = Literal["first-term", "returning"]
MajorityBand = Literal["marginal", "middle", "safe"]
SeatType = Literal["city", "town", "rural"]
SEAT_TYPES = ("city", "town", "rural")


class ParsedQuery(BaseModel):
    """Schema the model fills in (structured output)."""

    search_text: str = Field(description="Keywords for retrieval in UK parliamentary vocabulary")
    date_from: str | None = Field(description="YYYY-MM-DD or null")
    date_to: str | None = Field(description="YYYY-MM-DD or null")
    houses: list[House]
    regions: list[Region]
    parties: list[str]
    speakers: list[str]
    offices: list[Office]
    genders: list[Gender]
    age_bands: list[AgeBand]
    tenure: list[Tenure]
    majority_bands: list[MajorityBand]
    seat_types: list[SeatType] = Field(default_factory=list)
    group_by: GroupBy
    portfolios: list[str]
    wants_votes: bool
    interpretation: str = Field(description="One sentence: how the question was interpreted")


@dataclass
class QueryPlan:
    question: str
    search_text: str
    filters: Filters
    portfolios: list[str] = field(default_factory=list)
    wants_votes: bool = False
    speakers: list[dict[str, str]] = field(default_factory=list)  # resolved {"person_id", "name"}
    unresolved_speakers: list[str] = field(default_factory=list)
    interpretation: str = ""
    parser: str = "offline"
    group_by: str = "role"   # how the briefing groups speakers: role (default), gender or age on the day
    parse_error: str | None = None  # set when the model could not be used and offline rules were (not shown to readers)
    compare: list[dict[str, str]] = field(default_factory=list)  # sides compared: {"kind", "id", "label"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "search_text": self.search_text,
            "filters": self.filters.as_dict(),
            "portfolios": self.portfolios,
            "wants_votes": self.wants_votes,
            "speakers": self.speakers,
            "unresolved_speakers": self.unresolved_speakers,
            "interpretation": self.interpretation,
            "parser": self.parser,
            "group_by": self.group_by,
            "compare": self.compare,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QueryPlan:
        f = d.get("filters") or {}
        filters = Filters(
            date_from=_iso(f.get("date_from")), date_to=_iso(f.get("date_to")),
            houses=list(f.get("houses") or []), regions=list(f.get("regions") or []),
            parties=list(f.get("parties") or []), person_ids=list(f.get("person_ids") or []),
            offices=list(f.get("offices") or []), record_types=list(f.get("record_types") or []),
            genders=[g for g in f.get("genders") or [] if g in ("F", "M")],
            age_bands=[b for b in f.get("age_bands") or [] if b in AGE_BAND_KEYS],
            tenure=[t for t in f.get("tenure") or [] if t in ("first-term", "returning")],
            majority_bands=[b for b in f.get("majority_bands") or [] if b in ("marginal", "middle", "safe")],
            seat_types=[t for t in f.get("seat_types") or [] if t in SEAT_TYPES],
        )
        return cls(question=d.get("question", ""), search_text=d.get("search_text") or d.get("question", ""),
                   filters=filters, portfolios=list(d.get("portfolios") or []),
                   wants_votes=bool(d.get("wants_votes")), speakers=list(d.get("speakers") or []),
                   interpretation=d.get("interpretation", ""), parser=d.get("parser", "edited"),
                   group_by=d.get("group_by") if d.get("group_by") in GROUP_BY_KEYS else "role",
                   compare=[{k: str(x.get(k, "")) for k in ("kind", "id", "label")} for x in d.get("compare") or []
                            if isinstance(x, dict)][:3])


def _iso(v: Any) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------- offline rules

_YEAR = r"(19\d\d|20\d\d)"


def _dates(q: str, today: date) -> tuple[date | None, date | None]:
    ql = q.lower()
    if m := re.search(rf"between {_YEAR} and {_YEAR}", ql) or re.search(rf"{_YEAR}\s*(?:-|–|to|until)\s*{_YEAR}", ql):
        a, b = sorted(int(x) for x in m.groups())
        return date(a, 1, 1), date(b, 12, 31)
    if m := re.search(rf"(?:since|after|from) {_YEAR}", ql):
        return date(int(m.group(1)), 1, 1), None
    if m := re.search(rf"before {_YEAR}", ql):
        return None, date(int(m.group(1)) - 1, 12, 31)
    if m := re.search(rf"\bin {_YEAR}\b", ql):
        y = int(m.group(1))
        return date(y, 1, 1), date(y, 12, 31)
    if "last year" in ql:
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if "this year" in ql:
        return date(today.year, 1, 1), None
    return None, None


_FILTER_WORDS = set(["how", "have", "has", "had", "did", "do", "does", "what", "which", "who", "whom", "whose", "when", "where", "why", "is", "are", "was", "were", "be", "been", "being", "the", "a", "an", "of", "to", "in", "on", "for", "by", "with", "about", "from", "at", "as", "and", "or", "not", "any", "all", "this", "that", "these", "those", "there", "their", "they", "them", "it", "its", "into", "over", "under", "between", "since", "before", "after", "during", "until", "than", "then", "so", "such", "said", "say", "says", "saying", "stated", "mps", "mp", "members", "member", "ministers", "minister", "government", "governments", "lords", "lord", "peers", "peer", "commons", "house", "parliament", "parliamentary", "shadow", "backbench", "backbenchers", "backbencher", "chairs", "chair", "committee", "committees", "northern", "southern", "midlands", "north", "south", "east", "west", "party", "parties", "labour", "conservative", "conservatives", "tory", "tories", "liberal", "democrat", "democrats", "snp", "year", "years", "last", "this"])


_PARTY_WORDS = [
    ("labour", r"\blabour\b"),
    ("conservative", r"\bconservatives?\b|\btor(y|ies)\b"),
    ("liberal-democrat", r"\blib(eral)? ?dem(ocrat)?s?\b"),
    ("scottish-national-party", r"\bsnp\b|scottish national"),
    ("reform", r"\breform uk\b|\breform (mps|party)\b"),
    ("plaid-cymru", r"\bplaid\b"),
    ("dup", r"\bdup\b"),
]


_WHO = r"(?:mps|members|peers|ministers|politicians|parliamentarians|backbenchers|speakers)"


def demographic_rules(ql: str) -> tuple[list[str], list[str], str]:
    """Gender and age filters, and grouping, from plain words. A topic ("women's health") is not a filter."""
    genders: list[str] = []
    if re.search(rf"\b(women|female) {_WHO}\b", ql):
        genders.append("F")
    if re.search(rf"\b(men|male) {_WHO}\b", ql) and not re.search(r"\b(women|female) and (men|male)\b", ql):
        genders.append("M")
    bands: list[str] = []
    if re.search(rf"\b(younger|young) {_WHO}\b|\bunder (the age of )?40\b", ql):
        bands.append("under-40")
    if re.search(rf"\b(older|oldest) {_WHO}\b|\b(over|aged) 70\b", ql):
        bands.append("70-plus")
    group_by = "role"
    if re.search(r"\bby (gender|sex)\b|\b(women|female) and (men|male)\b|\b(men|male) and (women|female)\b|"
                 r"\bgender (split|divide|gap|differences?)\b", ql):
        group_by, genders = "gender", []
    elif re.search(r"\bby age\b|\b(younger|young) and older\b|\bolder and younger\b|\bage (gap|groups?|divide)\b",
                   ql):
        group_by, bands = "age", []
    return genders, bands, group_by


def seat_rules(ql: str) -> tuple[list[str], list[str], str | None]:
    """First-term / marginal-seat filters and grouping from plain words."""
    tenure: list[str] = []
    if re.search(rf"\b(new|newly[- ]elected|first[- ]term|new intake of) {_WHO}\b|\b20\d\d intake\b", ql):
        tenure.append("first-term")
    majority: list[str] = []
    if re.search(r"\bmarginal (seats?|constituenc)", ql):
        majority.append("marginal")
    if re.search(r"\bsafe (seats?|constituenc)", ql):
        majority.append("safe")
    group = None
    if re.search(r"\bmarginal and safe\b|\bsafe and marginal\b|\bby majority\b", ql):
        group, majority = "majority", []
    elif re.search(rf"\b(new|first[- ]term) and (long[- ]serving|experienced|returning) {_WHO}\b", ql):
        group, tenure = "tenure", []
    return tenure, majority, group


def seat_type_rules(ql: str) -> list[str]:
    """City, town or rural seats, when the question restricts to them (not when it compares them)."""
    kinds = {k for k, pat in (("city", r"\b(city|cities|urban)\b"), ("town", r"\btowns?\b"),
                              ("rural", r"\b(rural|countryside|village)\b"))
             if re.search(rf"{pat}\W+(?:\w+\W+)?(?:seats?|constituenc\w*|mps)\b|\bmps for {pat}", ql)}
    mentioned = sum(1 for pat in (r"\b(city|cities|urban)\b", r"\btowns?\b", r"\b(rural|countryside|village)\b")
                    if re.search(pat, ql))
    if mentioned > 1 and re.search(r"\b(differ\w*|compar\w*|versus|vs|between)\b", ql):
        return []  # a comparison between kinds of seat: search them all
    if "urban" in ql and "city" in kinds:
        kinds.add("town")
    return [k for k in SEAT_TYPES if k in kinds]


_DEMOGRAPHIC_PHRASES = re.compile(
    rf"\b(?:(?:women|female|men|male|younger|young|older|oldest|new|newly[- ]elected|first[- ]term)"
    rf"(?: and (?:women|female|men|male|older|younger|long[- ]serving|experienced|returning))? "
    rf"{_WHO}|under (?:the age of )?40|(?:over|aged) 70|by (?:gender|sex|age)|"
    rf"(?:gender|age) (?:split|divide|gap|groups?|differences?)|(?:marginal|safe)(?: and (?:marginal|safe))? "
    rf"(?:seats?|constituencies)|20\d\d intake|by majority|(?:mps for )?(?:city|town|rural|urban|countryside) (?:seats?|constituencies|mps))\b", re.I)


def topic_words(question: str) -> str:
    """Retrieval words: drop question words, filter words and years (they become filters)."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", question)
    keep = [w for w in words if w.lower() not in _FILTER_WORDS and not re.fullmatch(r"(19|20)\d\d", w)]
    return " ".join(keep)


def parse_offline(question: str, region_groups: dict[str, list[str]], today: date | None = None) -> QueryPlan:
    today = today or date.today()
    ql = question.lower()
    date_from, date_to = _dates(question, today)
    regions: list[str] = []
    for group, members in region_groups.items():
        stem = group.rstrip("s").replace("_", " ")
        if re.search(rf"\b{re.escape(stem)}", ql):
            regions.extend(members)
    for r in ITL1_REGIONS:
        if r.lower() in ql and r not in regions:
            regions.append(r)
    houses: list[str] = []
    if re.search(r"\blords\b|\bpeers?\b", ql):
        houses.append("lords")
    if re.search(r"\bmps\b|\bcommons\b", ql) or regions:
        houses.append("commons")
    offices: list[str] = []
    if re.search(r"\bshadow\b", ql):
        offices.append("shadow")
    elif re.search(r"\bministers?\b|\bgovernment\b", ql):
        offices.append("gov")
    if re.search(r"committee chairs?|chairs? of", ql):
        offices.append("chair")
    if re.search(r"backbench", ql):
        offices.append("backbench")
    wants_votes = bool(re.search(r"\bvot(e|ed|es|ing)\b|dissent|rebel|divisions?\b", ql))
    genders, age_bands, group_by = demographic_rules(ql)
    tenure, majority_bands, seat_group = seat_rules(ql)
    group_by = seat_group or group_by
    seat_types = seat_type_rules(ql)
    if tenure or majority_bands or seat_group or seat_types:
        houses.append("commons")
    parties = [pid for pid, pattern in _PARTY_WORDS if re.search(pattern, ql)]
    return QueryPlan(
        question=question,
        search_text=topic_words(_DEMOGRAPHIC_PHRASES.sub(" ", question)) or question,
        filters=Filters(date_from=date_from, date_to=date_to, houses=sorted(set(houses)) if len(set(houses)) == 1 else [],
                        regions=regions, offices=offices, parties=parties, genders=genders, age_bands=age_bands,
                        tenure=tenure, majority_bands=majority_bands, seat_types=seat_types),
        portfolios=list(portfolios_for_query(_DEMOGRAPHIC_PHRASES.sub(" ", question))),
        wants_votes=wants_votes,
        interpretation="Interpreted with offline rules (no language model).",
        parser="offline",
        group_by=group_by,
    )


# --------------------------------------------------------------------------- model-based


def system_prompt(region_groups: dict[str, list[str]], parties: list[tuple[str, str]], today: date,
                  start: date = date(1997, 5, 1)) -> str:
    groups = "\n".join(f"- {k}: {', '.join(v)}" for k, v in region_groups.items())
    ports = "\n".join(f"- {k}: {v}" for k, v in PORTFOLIOS.items())
    party_list = ", ".join(f"{pid} ({name})" for pid, name in parties)
    return f"""You convert research questions about the UK Parliament into search filters for a Hansard search engine.
Today's date is {today.isoformat()}. The archive covers {start:%B %Y} to today.

Rules:
- Only set a filter when the question clearly asks for it. Never invent constraints.
- search_text: the topic, rewritten in the vocabulary used in Hansard. Translate non-UK or informal terms
  (e.g. "zoning" -> "planning, land use", "cops" -> "police") and add close UK synonyms. Leave out words that
  only express filters (dates, regions, party, "ministers").
- date_from/date_to: ISO dates for explicit or clearly implied periods ("between 2018 and 2022" ->
  2018-01-01 to 2022-12-31; "since the 2024 election" -> 2024-07-04). A bare year is the calendar year: "since
  2024" and "from 2024" -> 2024-01-01; use an election date only when the question mentions the election.
- houses: "commons" for MPs/House of Commons/constituencies, "lords" for peers/House of Lords.
- regions: ITL1 regions, only for questions about where MPs' seats are, listed individually by name. A named group
  stands for all its regions, so list each of them:
{groups}
  Choose exactly the places asked about: "Scottish MPs" -> ["Scotland"], "Welsh MPs" -> ["Wales"].
  Region filters apply to MPs only (peers have no constituency), so also set houses to ["commons"].
- parties: party ids from this list: {party_list}
- speakers: full names of specific people the question asks about, as written.
- offices: "gov" for government ministers, "shadow" for opposition frontbenchers, "chair" for select committee
  chairs, "backbench" for backbenchers - only when the question restricts who is speaking.
- genders: "F" (women) and/or "M" (men), only when the question restricts speakers by gender ("female MPs").
  A topic about women or men ("women's health", "men's mental health") is never a gender filter.
- age_bands: speakers' age on the date they spoke: "under-40", "40-54", "55-69", "70-plus". Only when the
  question restricts speakers by age ("younger MPs" -> ["under-40"], "MPs over 70" -> ["70-plus"]).
- tenure: MPs' length of service on the date they spoke: "first-term" (newly elected: still in the Parliament they
  were first elected to, e.g. "new MPs", "the 2024 intake") or "returning" (re-elected at least once). Only when the
  question restricts speakers this way; it also implies houses ["commons"].
- majority_bands: the MP's majority at the time they spoke, as a share of votes cast: "marginal" (under 10%),
  "middle" (10% to 20%), "safe" (over 20%). Only when the question restricts to marginal or safe seats; it also
  implies houses ["commons"].
- seat_types: the kind of place an MP's seat is, by the size of its main urban area (not official city status):
  "city" (an urban area of about 200,000 people or more), "town" (a smaller one), "rural" (mostly outside towns of
  10,000+). Only when the question restricts speakers to one or two kinds ("MPs for town seats" -> ["town"],
  "rural MPs" -> ["rural"], "urban MPs" -> ["city", "town"]); it also implies houses ["commons"]. When the question
  compares kinds of seat ("where do rural and city MPs differ"), leave it empty so every kind is searched.
- group_by: "gender" if the question asks how women and men differ, "age" if it asks how views differ by age,
  "tenure" if it compares new and long-serving MPs, "majority" if it compares MPs in marginal and safe seats;
  otherwise "role" (the default: ministers, opposition, backbenchers by seat type). When grouping by one of these,
  leave the matching filter empty so every group is searched.
- portfolios: the policy areas the question is about (used to boost relevant ministers, not to filter):
{ports}
- wants_votes: true if the question is about how members voted, rebellions, dissent or divisions.
- Follow-up questions: when earlier questions are given and the new one builds on the last of them ("what about
  the Lords?", "and since 2024?", "how did they vote?", "which Labour MPs?"), keep the earlier topic and filters
  and change only what the follow-up changes: search_text keeps the earlier topic, and a new filter replaces the
  one it contradicts ("what about the Lords?" after a question about MPs sets houses to ["lords"] and drops
  MP-only filters such as regions). A question on a new subject starts afresh.
- interpretation: one plain sentence describing the filters you chose, in British English (British spelling,
  dates written as 9 October 2026)."""


def expand_regions(names: list[str], groups: dict[str, list[str]]) -> list[str]:
    """ITL1 region names, in the standard order; a named group ("northern") becomes its regions."""
    lower = {r.lower(): r for r in ITL1_REGIONS}
    out: set[str] = set()
    for n in names:
        key = n.strip()
        group = groups.get(key) or groups.get(key.lower().replace(" ", "_"))
        out.update(group or ([lower[key.lower()]] if key.lower() in lower else []))
    return [r for r in ITL1_REGIONS if r in out]


def _open_end(d: date | None, today: date) -> date | None:
    """An end date of today or later is the same as no end date (and reads better as a filter)."""
    return None if d is not None and d >= today else d


def parse_model(settings) -> str:
    return settings.answer.get("query_parse_model") or settings.answer.get("model", "claude-sonnet-5-5")


def parse_with_model(client, settings, question: str, history: list[dict[str, str]] | None,
                     parties: list[tuple[str, str]], today: date | None = None) -> tuple[QueryPlan, Any]:
    from hansard.rag.answer import cached_system

    today = today or date.today()
    convo = ""
    if history:
        convo = ("Earlier questions in this conversation, oldest first, with what each searched for (for resolving "
                 "follow-ups like 'what about the Lords?'):\n")
        convo += "\n".join(f"- {h['question']}" + (f" [searched for: {h['topic']}" + (f"; {h['scope']}" if h.get("scope") else "")
                                                   + "]" if h.get("topic") else "")
                           for h in history[-4:]) + "\n\n"
    resp = client.messages.parse(
        model=parse_model(settings),
        max_tokens=2000,
        system=cached_system(system_prompt(settings.regions, parties, today, settings.archive_start)),
        messages=[{"role": "user", "content": f"{convo}Question: {question}"}],
        output_config={"effort": settings.answer.get("query_parse_effort", "low")},
        output_format=ParsedQuery,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"query parser returned no output (stop_reason={resp.stop_reason})")
    p: ParsedQuery = resp.parsed_output
    known_ports = set(PORTFOLIOS)
    known_parties = {pid for pid, _ in parties}
    plan = QueryPlan(
        question=question,
        search_text=p.search_text.strip() or question,
        filters=Filters(
            date_from=_iso(p.date_from), date_to=_open_end(_iso(p.date_to), today),
            houses=sorted(set(p.houses)), regions=expand_regions(p.regions, settings.regions),
            parties=[x for x in p.parties if x in known_parties], offices=sorted(set(p.offices)),
            genders=sorted(set(p.genders)) if p.group_by != "gender" else [],
            age_bands=[b for b in AGE_BAND_KEYS if b in p.age_bands] if p.group_by != "age" else [],
            tenure=sorted(set(p.tenure)) if p.group_by != "tenure" else [],
            majority_bands=[b for b in ("marginal", "middle", "safe") if b in p.majority_bands]
            if p.group_by != "majority" else [],
            seat_types=[t for t in SEAT_TYPES if t in p.seat_types],
        ),
        portfolios=[x for x in p.portfolios if x in known_ports],
        wants_votes=p.wants_votes,
        interpretation=p.interpretation,
        parser="model",
        group_by=p.group_by,
    )
    plan.unresolved_speakers = list(p.speakers)
    plan.filters.date_from = year_start(question, plan.filters.date_from)
    return plan, resp.usage


def year_start(question: str, date_from: date | None) -> date | None:
    """"Since 2024" means from 1 January 2024, not from the general election that year, unless the question
    mentions an election."""
    m = re.search(r"\b(?:since|from|after|starting in|beginning in)\s+(?:the\s+(?:start|beginning)\s+of\s+)?((?:19|20)\d\d)\b",
                  question, re.I)
    if not m or re.search(r"\belection\b", question, re.I):
        return date_from
    year = int(m.group(1))
    if date_from is None or date_from.year == year:
        return date(year, 1, 1)
    return date_from


def resolve_speakers(resolver_con, names: list[str], f: Filters) -> tuple[list[dict[str, str]], list[str]]:
    """Map names the user mentioned to person ids. Every word of the name must be a word of the member's name (a
    first name may be a short form: "Ed Davey" is Edward Davey; "Benn" is Hilary Benn, not Andrew Bennett). Among
    several, a full-name match comes first, then people who sat in the dates asked about (or lately), then the
    better known (government posts count most, then shadow posts, then committee chairs)."""
    from hansard.sources.donations import _same_first

    found, missing = [], []
    lo, hi = f.date_from or date(1997, 5, 1), f.date_to or date.today()
    for name in names:
        want = re.findall(r"[\w'’-]+", name.lower())
        if not want:
            missing.append(name)
            continue
        rows = resolver_con.execute(
            """SELECT n.person_id, n.name,
                      (SELECT max(m."end") FROM ref_memberships m WHERE m.person_id = n.person_id) AS last_sat,
                      EXISTS (SELECT 1 FROM ref_memberships m WHERE m.person_id = n.person_id
                              AND m.start <= ? AND m."end" >= ?) AS sat_then,
                      (SELECT coalesce(sum(CASE o.kind WHEN 'gov' THEN 3 WHEN 'shadow' THEN 2 WHEN 'chair' THEN 1
                                            ELSE 0 END), 0)
                       FROM ref_offices o WHERE o.person_id = n.person_id) AS posts
               FROM ref_names n WHERE lower(n.name) LIKE '%' || ? || '%'
               GROUP BY n.person_id, n.name LIMIT 500""", [hi, lo, want[-1]]).fetchall()

        def matches(row: tuple, want: list[str] = want) -> bool:
            words = re.findall(r"[\w'’-]+", row[1].lower())
            if want[-1] not in words:
                return False
            return all(any(w == x or _same_first(w, x) for x in words) for w in want[:-1])

        rows = [r for r in rows if matches(r)]
        if not rows:
            missing.append(name)
            continue
        best = max(rows, key=lambda r: (r[1].lower() == name.lower(), r[3], r[4], r[2] or date.min))
        found.append({"person_id": best[0], "name": best[1], "query": name})
    return found, missing


def plan_json(plan: QueryPlan) -> str:
    return json.dumps(plan.to_dict(), default=str)
