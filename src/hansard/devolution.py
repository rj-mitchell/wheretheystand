"""Devolution notes: when a question is about a devolved subject, say which nations Westminster decides it for.

Many subjects MPs debate (health, schools, housing and so on) are decided separately in Scotland, Wales and Northern
Ireland. A briefing on them mostly describes policy for England, and members for seats elsewhere may be speaking
about their own nation's system or about UK-wide money. The note says so, from a hand-kept table: no AI, and it is
shown beside the briefing rather than written into it.

The table is a simplification of the devolution settlements, checked against the UK government's guidance
(https://www.gov.uk/guidance/devolution-of-powers-to-scotland-wales-and-northern-ireland). Keep it short and
cautious, and update it when powers change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GUIDANCE = "https://www.gov.uk/guidance/devolution-of-powers-to-scotland-wales-and-northern-ireland"
NATIONS = ("Scotland", "Wales", "Northern Ireland")


@dataclass(frozen=True)
class Subject:
    key: str
    name: str
    pattern: re.Pattern[str]
    devolved: tuple[str, ...]  # nations that decide this themselves
    detail: str = ""           # a caveat, where the split is not clean


def _p(words: str) -> re.Pattern[str]:
    return re.compile(rf"\b(?:{words})\b", re.IGNORECASE)


SUBJECTS: list[Subject] = [
    Subject("health", "Health and social care",
            _p(r"nhs|health(?:care)?(?! and safety)|hospitals?|gps?|social care|care homes?|mental health|waiting (?:lists?|times)|"
               r"nurses?|doctors?|dentists?|dentistry|ambulances?|prescriptions?|a&e"),
            NATIONS),
    Subject("education", "Education and skills",
            _p(r"schools?|teachers?|pupils?|education|universit(?:y|ies)|tuition fees?|students?|nurser(?:y|ies)|"
               r"childcare|apprenticeships?|further education|ofsted|curriculum|exams?"),
            NATIONS),
    Subject("housing", "Housing, planning and local government",
            _p(r"housing|housebuilding|planning (?:permission|system|rules|reforms?|applications?|law)|renters?|renting|landlords?|tenants?|homelessness|social housing|"
               r"council tax|local government|local councils?|business rates"),
            NATIONS),
    Subject("justice", "Policing, courts and prisons",
            _p(r"police|policing|prisons?|(?<!international )(?<!international criminal )courts?|sentencing|probation|criminal justice|legal aid"),
            ("Scotland", "Northern Ireland"),
            "England and Wales share one legal system, so Westminster decides these for Wales too."),
    Subject("transport", "Roads, buses and local transport",
            _p(r"buses|bus services?|roads?|potholes?|local transport|trams?|railways?|rail services?|trains?"),
            NATIONS,
            "Rail is split: some powers are devolved and some, such as most rail infrastructure in Wales, are not. "
            "Aviation and shipping safety are decided for the whole UK."),
    Subject("farming", "Farming, fishing and the environment",
            _p(r"farm(?:ing|ers?)|agricultur(?:e|al)|fisheries|fishing|flood(?:ing|s)?|water companies|sewage|"
               r"national parks?|rural payments?|animal welfare"),
            NATIONS),
    Subject("culture", "Culture, sport and tourism",
            _p(r"museums?|libraries|tourism|sport(?:s)?(?: funding| facilities)?|arts funding"),
            NATIONS,
            "Broadcasting, including the BBC, is decided for the whole UK."),
    Subject("welfare", "Benefits and social security",
            _p(r"benefit (?:claimants?|cap|cuts?|system|rates?)|benefits system|out-of-work benefits|universal credit|"
               r"(?<!animal )welfare|social security|pip|personal independence payment|disability benefits?|"
               r"child benefit|two-child (?:limit|benefit cap)|carers? allowance"),
            ("Northern Ireland",),
            "Scotland now runs some disability, carer and family benefits itself; most other benefits in Great "
            "Britain are decided at Westminster. Northern Ireland decides its own but usually matches Great Britain."),
    Subject("income_tax", "Income tax",
            _p(r"income tax(?: rates?| bands?)?"),
            (),
            "Scotland sets its own rates and bands of income tax on earnings, and Wales sets part of its rate. Other "
            "taxes are mostly decided for the whole UK."),
    Subject("employment", "Employment rights",
            _p(r"employment rights|workers'? rights|minimum wage|zero[- ]hours?|trade unions?|"
               r"fire and rehire|industrial action"),
            ("Northern Ireland",),
            "The minimum wage is set for the whole UK."),
    Subject("energy", "Energy",
            _p(r"energy (?:policy|bills?|prices?|security|supply)|electricity|gas (?:prices|supply)|"
               r"onshore wind|nuclear power"),
            ("Northern Ireland",),
            "Planning consent for some energy projects is decided in Scotland and Wales."),
]


def notes_for(question: str, limit: int = 2) -> list[dict[str, str]]:
    """Up to ``limit`` notes for the devolved subjects a question is about (the earliest mentioned first)."""
    found = []
    for s in SUBJECTS:
        m = s.pattern.search(question or "")
        if m:
            found.append((m.start(), s))
    out = []
    for _, s in sorted(found, key=lambda x: x[0])[:limit]:
        westminster = ["England", *[n for n in NATIONS if n not in s.devolved]]
        subject = s.name[0].lower() + s.name[1:]
        if s.devolved == NATIONS:
            text = (f"Decisions on {subject} are devolved. What Westminster decides mostly applies to England; "
                    "Scotland, Wales and Northern Ireland decide their own policy.")
        elif not s.devolved:
            text = f"Decisions on {subject} are mostly made at Westminster for the whole UK, with some powers devolved."
        else:
            text = (f"Westminster decides {subject} for {_join(westminster)}; {_join(list(s.devolved))} "
                    f"{'decides its' if len(s.devolved) == 1 else 'decide their'} own.")
        if s.detail:
            text += " " + s.detail
        out.append({"subject": s.key, "name": s.name, "text": text,
                    "westminster_for": westminster, "devolved_in": list(s.devolved)})
    return out


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
