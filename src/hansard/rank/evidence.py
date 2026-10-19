"""Choose a balanced evidence set for synthesis, and summarise it.

A briefing that says "ministers argued X, city MPs Y, rural MPs Z" needs
evidence from each of those groups, not the twelve highest-scoring speeches
(which are often six speeches by one minister). Selection is deterministic:

1. drop candidates far weaker than the best match (``min_relevance_ratio``);
2. cap contributions per speaker (``max_per_speaker``);
3. members who raised the subject on several separate days (``min_occasions``; not counting what they
   said as ministers) come first, up to
   ``max_persistent`` of them, most persistent first: a backbencher who keeps returning to a subject is
   who a researcher wants to find, and would otherwise lose their group's few places to members who
   spoke on it once;
4. take the best remaining record from each speaker group in turn (groups
   ordered by their best score), then fill any remaining slots by score.

The number of separate days is counted among the records found for the question, and kept on each
chosen record of those members (``candidate.data["occasions"]``) so the briefing can say so.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from hansard.rank.composite import Scored

GROUPS = {
    "gov": "Government ministers",
    "shadow": "Opposition frontbench",
    "chair": "Select committee chairs",
    "city": "Backbench MPs, city seats",
    "town": "Backbench MPs, town seats",
    "rural": "Backbench MPs, rural seats",
    "commons": "Backbench MPs",
    "lords": "Backbench peers",
    "other": "Other / unattributed",
}

SETTLEMENT_LABEL = {"city": "city seat", "town": "town seat", "rural": "rural seat"}


def people(n: int) -> str:
    return f"{n / 1e6:.1f}m" if n >= 1_000_000 else f"{round(n, -3):,}"


def seat_type_label(sp: dict[str, Any]) -> str | None:
    """e.g. 'city seat (Greater Manchester urban area, 2.8m people)' or 'town seat (urban area of 172,000)'."""
    kind = sp.get("settlement")
    if not kind:
        return None
    label = SETTLEMENT_LABEL.get(kind, kind)
    pop, area = sp.get("urban_area_pop"), sp.get("urban_area")
    if kind == "city":
        detail = ", ".join(x for x in (f"{area} urban area" if area else None,
                                       f"{people(pop)} people" if pop else None) if x)
        return f"{label} ({detail})" if detail else label
    if kind == "town" and pop:
        return f"{label} (urban area of {people(pop)})"
    return label


def speaker_group(profile: Any) -> str:
    """Group key for a speaker profile (``SpeakerProfile`` or its dict form)."""
    p = profile.to_dict() if hasattr(profile, "to_dict") else (profile or {})
    kinds = {o.get("kind") for o in p.get("offices") or []}
    if "gov" in kinds:
        return "gov"
    if "shadow" in kinds:
        return "shadow"
    if "chair" in kinds:
        return "chair"
    if p.get("house") == "lords":
        return "lords"
    if p.get("house") == "commons":
        return p.get("settlement") or "commons"
    return "other"


def demographic_group(profile: Any, group_by: str) -> str:
    """The group key for gender, age on the date, first term or majority at the time ('unknown' when not known)."""
    p = profile.to_dict() if hasattr(profile, "to_dict") else (profile or {})
    if group_by == "gender":
        return p.get("gender") or "unknown"
    if group_by == "tenure":
        return {True: "first-term", False: "returning"}.get(p.get("first_term"), "unknown")
    if group_by == "majority":
        return p.get("majority_band") or "unknown"
    return p.get("age_band") or "unknown"


def _person(profile: Any) -> str | None:
    if isinstance(profile, dict):
        return profile.get("person_id")
    return getattr(profile, "person_id", None)


def select_evidence(scored: list[Scored], profiles: dict[str, Any], k: int, *, max_per_speaker: int = 2,
                    min_relevance_ratio: float = 0.25, group_by: str = "role", min_occasions: int = 3,
                    max_persistent: int = 4) -> list[Scored]:
    if not scored:
        return []
    def rel(s: Scored) -> float:  # search relevance: re-ranking reorders the evidence but must not narrow it
        return s.relevance if s.base_relevance is None else s.base_relevance

    best_rel = max(rel(s) for s in scored)
    pool = [s for s in scored if rel(s) >= best_rel * min_relevance_ratio]
    per_speaker: Counter[str] = Counter()
    groups: dict[str, list[Scored]] = defaultdict(list)
    for s in pool:  # already in score order
        prof = profiles.get(s.candidate.record_id)
        groups[speaker_group(prof) if group_by == "role" else demographic_group(prof, group_by)].append(s)
    order = sorted(groups, key=lambda g: -groups[g][0].score)
    chosen: list[Scored] = []
    taken: set[str] = set()

    def speaker(s: Scored) -> str:
        return _person(profiles.get(s.candidate.record_id)) or s.candidate.record_id

    def take(s: Scored) -> bool:
        if s.candidate.record_id in taken or per_speaker[speaker(s)] >= max_per_speaker:
            return False
        chosen.append(s)
        taken.add(s.candidate.record_id)
        per_speaker[speaker(s)] += 1
        return True

    # members who keep raising the subject: separate days among the records found (not as a minister, whose
    # answers on their own subject come daily with the job, and who already have a group and a role weight)
    days: dict[str, set[Any]] = defaultdict(set)
    best: dict[str, Scored] = {}
    for s in pool:
        p = profiles.get(s.candidate.record_id)
        pid = _person(p)
        if pid and speaker_group(p) != "gov":
            days[pid].add(s.candidate.date)
            best.setdefault(pid, s)
    persistent = sorted((pid for pid, d in days.items() if len(d) >= min_occasions),
                        key=lambda pid: (-len(days[pid]), -best[pid].score, pid))[:max_persistent]
    for pid in persistent:
        if len(chosen) < k:
            take(best[pid])

    progress = True
    while len(chosen) < k and progress:
        progress = False
        for g in order:
            for s in groups[g]:
                if take(s):
                    progress = True
                    break
            if len(chosen) >= k:
                break
    for s in pool:  # fill by score if groups ran dry
        if len(chosen) >= k:
            break
        take(s)
    for s in chosen:
        pid = speaker(s)
        if len(days.get(pid, ())) >= min_occasions:
            s.candidate.data["occasions"] = len(days[pid])
    chosen.sort(key=lambda s: (-s.score, s.candidate.record_id))
    return chosen


def evidence_summary(records: list[dict[str, Any]], divisions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Deterministic description of the evidence base (shown in the UI, not model-written)."""
    groups: Counter[str] = Counter()
    parties: Counter[str] = Counter()
    settlements: Counter[str] = Counter()
    genders: Counter[str] = Counter()
    ages: Counter[str] = Counter()
    tenures: Counter[str] = Counter()
    majorities: Counter[str] = Counter()
    speakers: set[str] = set()
    persistent: dict[str, int] = {}
    dates = []
    for r in records:
        if r.get("occasions") and (r.get("speaker") or {}).get("name"):
            persistent[r["speaker"]["name"]] = r["occasions"]
        sp = r.get("speaker") or {}
        groups[GROUPS[speaker_group(sp)]] += 1
        if sp.get("party"):
            parties[sp["party"]] += 1
        if sp.get("settlement"):
            settlements[sp["settlement"]] += 1
        if sp.get("person_id"):
            genders[sp.get("gender") or "unknown"] += 1
            ages[sp.get("age_band") or "unknown"] += 1
            if sp.get("house") == "commons":
                tenures[demographic_group(sp, "tenure")] += 1
                majorities[demographic_group(sp, "majority")] += 1
        speakers.add(sp.get("person_id") or r.get("speaker_name_raw") or r["record_id"])
        dates.append(r["date"])
    return {
        "records": len(records),
        "speakers": len(speakers),
        "divisions": len(divisions or []),
        "date_from": min(dates) if dates else None,
        "date_to": max(dates) if dates else None,
        "groups": dict(groups.most_common()),
        "parties": dict(parties.most_common()),
        "seat_types": dict(settlements.most_common()),
        "genders": dict(genders.most_common()),       # contributions by speakers' gender (F, M, unknown)
        "age_bands": dict(ages.most_common()),        # contributions by speakers' age on the day
        "tenure": dict(tenures.most_common()),         # MPs' contributions: first term or re-elected, on the day
        "majority_bands": dict(majorities.most_common()),  # MPs' contributions by majority at the time
        "imbalance": imbalance(parties, len(records)),
        # members who raised the subject on several separate days (not as ministers), most often first
        "raised_most": [{"name": n, "days": d} for n, d in sorted(persistent.items(), key=lambda kv: (-kv[1], kv[0]))],
    }


def imbalance(parties: Counter[str], total: int, threshold: float = 0.7, minimum: int = 6) -> dict[str, Any] | None:
    """When most of the evidence comes from one party, say so (the record may simply hold less from others)."""
    if total < minimum or not parties:
        return None
    party, n = parties.most_common(1)[0]
    if n / total < threshold:
        return None
    return {"party": party, "count": n, "total": total}
