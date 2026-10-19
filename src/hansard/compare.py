"""Comparisons: two or three MPs, parties or nations side by side on the same question.

Each side gets its own search and an equal share of the sources, so one side cannot crowd out another (a bigger
party simply says more). The briefing then sets the sides out in turn and says where they agree and differ, with
cited examples from each, and no view on which is right. Sides come from the Compare page, or from a question that
asks to compare two or three parties or named members.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from hansard.engine import Filters

ENGLISH_REGIONS = ["North East", "North West", "Yorkshire and The Humber", "East Midlands", "West Midlands",
                   "East of England", "London", "South East", "South West"]
NATIONS = {"england": ("England", ENGLISH_REGIONS), "scotland": ("Scotland", ["Scotland"]),
           "wales": ("Wales", ["Wales"]), "northern-ireland": ("Northern Ireland", ["Northern Ireland"])}
COMPARE_WORDS = re.compile(r"\b(compare|compared|comparison|versus|vs\.?|differ|differences?|contrast)\b", re.I)
PERSON_PREFIX = "uk.org.publicwhip/person/"


def side_filters(base: Filters, side: dict[str, Any]) -> Filters:
    """The search filters for one side: the question's own filters, narrowed to that side."""
    kind, sid = side["kind"], side["id"]
    if kind == "party":
        return replace(base, parties=[sid])
    if kind == "person":
        return replace(base, person_ids=[sid])
    if kind == "nation":
        return replace(base, regions=NATIONS[sid][1], houses=["commons"])
    raise ValueError(f"unknown side {kind!r}")


def resolve_sides(raw: list[dict[str, Any]], parties: dict[str, str], person_name) -> list[dict[str, str]]:
    """Check and label sides given as {"kind": party|person|nation, "id": …}; persons may be given by number."""
    out = []
    for s in raw[:3]:
        kind, sid = s.get("kind"), str(s.get("id", "")).strip()
        if kind == "party" and sid in parties:
            out.append({"kind": kind, "id": sid, "label": parties[sid]})
        elif kind == "nation" and sid in NATIONS:
            out.append({"kind": kind, "id": sid, "label": f"MPs for seats in {NATIONS[sid][0]}"})
        elif kind == "person":
            pid = sid if sid.startswith(PERSON_PREFIX) else f"{PERSON_PREFIX}{sid}" if sid.isdigit() else ""
            name = person_name(pid) if pid else None
            if name:
                out.append({"kind": kind, "id": pid, "label": name})
    keys = {(s["kind"], s["id"]) for s in out}
    return out if 2 <= len(keys) == len(out) else []


def sides_from_question(question: str, filters: Filters, speakers: list[dict[str, str]],
                        parties: dict[str, str]) -> list[dict[str, str]]:
    """A question that asks to compare two or three named members or parties becomes a comparison."""
    if not COMPARE_WORDS.search(question):
        return []
    if 2 <= len(speakers) <= 3:
        return [{"kind": "person", "id": s["person_id"], "label": s["name"]} for s in speakers]
    groups = list(dict.fromkeys("labour" if p == "labourco-operative" else p for p in filters.parties))
    if 2 <= len(groups) <= 3 and all(p in parties for p in groups):
        return [{"kind": "party", "id": p, "label": parties[p]} for p in groups]
    return []


def instruction(sides: list[dict[str, str]]) -> str:
    names = " and ".join(s["label"] for s in sides)
    return (
        f"The question compares {names}. Each search result title begins with the side it belongs to. Open with "
        "the '**In brief**' summary: two to four bullets on the main differences and any common ground, each with "
        "cited examples. Then one section per side, in the order "
        "given, each with a bold label naming the side and its stance and cited examples from that side only. Then "
        "a section 'Where they agree and differ', in which every point quotes and cites an example from each side "
        "it mentions. Give each side similar space; if the records for one side are thin, say so in the closing "
        "note rather than filling the gap. Do not judge which side is right, and do not explain differences by "
        "motives.")
