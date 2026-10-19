"""Registered interests that may relate to what a cited member said.

For each member quoted in a briefing, the entries in their register of interests (the Commons Register of Members'
Financial Interests, or the Register of Lords' Interests) that were registered before they spoke: ongoing interests
(jobs, directorships, shareholdings, land, unpaid roles) registered on or before the day, and one-off ones (gifts,
hospitality, visits, donations, single payments) registered in the twelve months before it. A small model then
judges, strictly, which of those concern the same subject as the contribution. It runs while the briefing is being
written, so it adds nothing to the wait.

For MPs, the archive of every edition since 2000 gives the entries as they stood on the day (registered by then and
still listed then); without it, the latest edition is used, in which an interest that has since ended is not seen.
The Lords register is current only. Matches are worded as a connection only, never as a suggestion of influence or
wrongdoing.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from hansard.people import person_number

log = logging.getLogger(__name__)

COMMONS_REGISTER = "Register of Members' Financial Interests"
LORDS_REGISTER = "Register of Lords' Interests"
DONATIONS_REGISTER = "Electoral Commission's register of donations"

# one-off entries (dated by when they were registered): relevant for a year, as the House's own rule on declaring has it
_ONE_OFF_COMMONS = re.compile(r"^(1\.1|2|3|4|5)\b")
_ONE_OFF_LORDS = re.compile(r"sponsorship|overseas visits|gifts", re.I)
_DETAILS = ("Organisation Name", "Organisation Description", "Job Title", "Employer", "Role, work or services",
            "Payer Name", "Payer Nature Of Business", "Donor Name", "Payment Description", "Purpose",
            "Property Description", "Land Use", "Miscellaneous Interest Type", "Location", "Country")


def _registered(item: dict[str, Any]) -> date | None:
    if item.get("registered_on"):
        try:
            return date.fromisoformat(item["registered_on"][:10])
        except ValueError:
            return None
    raw = (item.get("registered") or "").strip()
    for fmt in ("%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


_CEASED = re.compile(r"\binterest ceased\s+(\d{1,2}\s+\w+\s+\d{4})", re.I)


def _ended(item: dict[str, Any]) -> date | None:
    """When the interest ended, if the register says: "(interest ceased 30 April 2026)" in the Lords register, an
    end date in the Commons one."""
    m = _CEASED.search(item.get("summary") or "")
    raw = m.group(1) if m else (dict(item.get("details") or []).get("End Date") or "")
    for fmt in ("%d %B %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw.strip()[:10] if fmt != "%d %B %Y" else raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _text(item: dict[str, Any]) -> str:
    details = dict(item.get("details") or [])
    bits = [item.get("summary") or ""] + [f"{k}: {details[k]}" for k in _DETAILS if details.get(k)]
    bits += [c.get("summary") or "" for c in item.get("children") or []]
    return " ".join(" — ".join(b for b in bits if b).split())[:400]


# Posts in government, Parliament or a party are not outside interests: a minister's salary (the Lords register lists
# it under remunerated employment) says nothing about a conflict when they speak for their department.
_PUBLIC_OFFICE = re.compile(
    r"\b(secretary of state|minister\b|ministerial|financial secretary|chief secretary|economic secretary|"
    r"exchequer secretary|parliamentary (under-)?secretary|parliamentary private secretary|under-secretary of state|"
    r"(lord|baroness|lady) in waiting|whip\b|whips\b|captain of (the )?(king|queen|honourable)|"
    r"lord speaker|deputy speaker|chairman of committees|senior deputy speaker|leader of the house|"
    r"leader of the opposition|shadow\b|lord chancellor|lord privy seal|lord president|paymaster general|"
    r"chancellor of the duchy|chancellor of the exchequer|attorney general|solicitor general|advocate general|"
    r"prime minister|member of (the )?(house of lords|house of commons|parliament)|salary as|"
    r"office[- ]holder'?s? (salary|allowance))",
    re.I)


def public_office(item: dict[str, Any]) -> bool:
    """Whether a register entry is a post in government, Parliament or a party (not an outside interest)."""
    return bool(_PUBLIC_OFFICE.search(item.get("summary") or ""))


def held_on(categories: list[dict[str, Any]], on: date, lords: bool, as_listed: bool = False) -> list[dict[str, Any]]:
    """The register entries that applied on ``on``: see the module notes. ``as_listed``: the categories are already
    those on the register at the time (from the archive of editions), so one-off entries need no twelve-month rule
    (and the oldest editions number their categories differently)."""
    out = []
    for c in categories:
        label = f"{c.get('type') or ''} {c.get('name') or ''}".strip()
        one_off = False if as_listed else bool(_ONE_OFF_LORDS.search(c.get("name") or "")) if lords else \
            bool(_ONE_OFF_COMMONS.match(str(c.get("type") or "")))
        for item in c.get("items") or []:
            when, ended = _registered(item), _ended(item)
            if when is None or when > on or (one_off and when < on - timedelta(days=365)) or (ended and ended < on):
                continue
            if re.match(r"^\s*no (registrable|registered|relevant)? ?interests?", item.get("summary") or "", re.I):
                continue
            if public_office(item):
                continue
            out.append({"category": c.get("name") or label, "summary": " ".join((item.get("summary") or "").split()),
                        "text": _text(item), "registered": when.isoformat(), "kind": "register",
                        "register": LORDS_REGISTER if lords else COMMONS_REGISTER, "url": None})
    return out


class Match(BaseModel):
    contribution: int = Field(description="The contribution's number")
    interest: str = Field(description="The interest's label, e.g. 'A3'")
    subjects: str = Field(description="A few words each: what the interest concerns; what the contribution concerns")
    relevant: bool = Field(description="True only if both concern the same organisation, industry, profession, place "
                                       "or specific subject, as the instructions define it")
    connection: str = Field(description="One neutral sentence naming what links the interest to the contribution")


# a "connection" that says there is none: the model sometimes lists an entry only to explain why it does not count
_DOUBT = re.compile(r"\b(not (?:clear|direct|relevant|related|listed|a match|connected)|no (?:clear |direct )?(?:link|"
                    r"match|connection)|too weak|rather than|unrelated|should not|does not (?:relate|concern|count))\b",
                    re.I)


class Matches(BaseModel):
    matches: list[Match]


SYSTEM = """You check whether members of the UK Parliament had a registered interest that relates directly to what they
said in a particular contribution. You are given each member's register entries that applied on the day, and their
contributions.

An interest is relevant only if it concerns the same organisation, industry, profession, place or specific subject as
the contribution: for example a gift of hospitality from the Betting and Gaming Council and a speech on gambling
regulation; a paid role with an energy company and a speech on energy prices; owning farmland and a speech on
inheritance tax on farms; a directorship of a football club and a speech on football regulation; a trusteeship of a
charity and a speech on that charity's cause.

Not relevant: posts in government, Parliament or a party (a minister's or whip's salary, a committee chair, a
party role), which are the member's public job, not an outside interest; interests that share only a broad area (a theatre ticket and a speech on BBC funding); memberships,
fellowships, honorary or unpaid roles in broad professional, learned or medical bodies (a royal college, a faculty,
a professional institute, a university) unless the body exists for the specific subject of the contribution; the
member's own profession on its own (a doctor speaking about the NHS, unless they are paid by the body concerned);
donations or support for general political, campaign or constituency work; hospitality at unrelated events; family
members employed in the member's office; property that is only the member's home or rented out with no link to the
subject; and anything you are unsure about. When in doubt, leave it out: most contributions have no relevant
interest.

Donations to the member from the Electoral Commission's register are relevant only when the donor is an
organisation in the industry or cause the contribution concerns (a donation from a betting company and a speech on
gambling), or the register describes the donor that way. A donation from a private individual, a trade union on a
subject that is not about its members' work, or a business unrelated to the subject is not relevant.

List only the relevant ones, each with relevant set to true; do not list an interest in order to explain why it
does not count. Write each connection as one plain, neutral sentence describing the link only, for example "The register lists a
paid role as adviser to an energy supplier; the contribution concerned energy prices." Never suggest that the
interest influenced the member, that they should have declared it, or any wrongdoing or motive."""


def donations_on(rows: list[dict[str, Any]], on: date) -> list[dict[str, Any]]:
    """Donations to the member (Electoral Commission register) accepted in the twelve months before ``on``."""
    out = []
    for r in rows:
        try:
            when = date.fromisoformat(str(r.get("accepted") or r.get("reported") or "")[:10])
        except ValueError:
            continue
        if not (on - timedelta(days=365) <= when <= on):
            continue
        value = f" - £{r['value']:,.2f}" if r.get("value") else ""
        bits = [f"Donor: {r.get('donor') or 'unknown'}", r.get("donor_status") or "", r.get("donation_type") or "",
                r.get("nature") or "", r.get("purpose") or ""]
        out.append({"category": "Donation to the member (Electoral Commission)", "kind": "donation",
                    "register": DONATIONS_REGISTER, "url": r.get("url"),
                    "summary": f"{r.get('donor') or 'Unknown donor'}{value}",
                    "text": " — ".join(b for b in bits if b)[:400], "registered": when.isoformat()})
    return out


def merge(register: list[dict[str, Any]], given: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Register entries and donations, a donation the member also registered counted once (the register's entry,
    with a link to the Electoral Commission's record)."""
    out = list(register)
    for d in given:
        same = next((h for h in register if d["summary"].lower() == h["summary"].lower()), None)
        if same is None:
            out.append(d)
        elif not same.get("url"):
            same["url"] = d["url"]
    return out


def candidates(sources: list[Any], commons, lords, donations=None) -> dict[str, Any]:
    """Members quoted in the briefing, their contributions, and the register entries (and donations) that applied
    on each day."""
    people: dict[str, dict[str, Any]] = {}
    for s in sources:
        sp = s.meta.get("speaker") or {}
        pid, house = sp.get("person_id"), sp.get("house")
        if s.kind != "record" or not pid or house not in ("commons", "lords") or not s.meta.get("date"):
            continue
        on = date.fromisoformat(str(s.meta["date"])[:10])
        if pid not in people:
            if house == "commons":
                reg = commons.for_person(pid) if commons else None
                cats = (reg or {}).get("categories") or []
            else:
                reg = next((r for r in (lords.for_person(pid) if lords else []) if r["register"] == "lords"), None)
                cats = (reg or {}).get("categories") or []
            given = (donations.for_person(pid) or {}).get("personal", []) if donations and house == "commons" else []
            people[pid] = {"name": sp.get("name") or s.meta.get("speaker_name_raw") or "", "house": house,
                           "categories": cats, "donations": given, "contributions": []}
        # an MP's entries as they stood on the day, from the archive of past editions when there is one
        dated = commons.categories_on(pid, on) if house == "commons" and hasattr(commons, "categories_on") else None
        cats = dated if dated is not None else people[pid]["categories"]
        held = merge(held_on(cats, on, house == "lords", as_listed=dated is not None),
                     donations_on(people[pid]["donations"], on))
        if held:
            people[pid]["contributions"].append({"source": s.n, "date": on.isoformat(), "held": held,
                                                 "heading": s.meta.get("heading") or "",
                                                 "text": " ".join((s.meta.get("text") or "").split())[:900]})
    return {pid: p for pid, p in people.items() if p["contributions"]}


def prompt(question: str, people: dict[str, Any]) -> tuple[str, dict[str, dict[str, Any]]]:
    """The model's input, and each interest label's entry and member."""
    parts, labels = [f"The briefing's question: {question}"], {}
    for k, (pid, p) in enumerate(people.items()):
        letter = chr(ord("A") + k) if k < 26 else f"Z{k}"
        entries: dict[tuple[str, str], str] = {}
        for c in p["contributions"]:
            for h in c["held"]:
                key = (h["text"], h["registered"])
                if key not in entries:
                    entries[key] = f"{letter}{len(entries) + 1}"
                    labels[entries[key]] = {**h, "person_id": pid, "name": p["name"], "house": p["house"]}
        lines = [f"\nMember {letter}: {p['name']} ({'House of Lords' if p['house'] == 'lords' else 'MP'})",
                 "Register entries:"]
        lines += [f"  {label}: {text} ({'donation accepted' if labels[label]['kind'] == 'donation' else 'registered'} "
                  f"{when})" for (text, when), label in entries.items()]
        for c in p["contributions"]:
            mine = sorted({entries[(h['text'], h['registered'])] for h in c["held"]})
            lines.append(f"Contribution {c['source']} ({c['date']}, entries that applied: {', '.join(mine)}): "
                         f"{c['heading']}: {c['text']}")
        parts.append("\n".join(lines))
    return "\n".join(parts), labels


def check(client, model: str, question: str, sources: list[Any], commons, lords, donations=None,
          effort: str = "low") -> tuple[list[dict[str, Any]], Any]:
    """Relevant registered interests for the briefing's sources, one entry per source, and the model's usage."""
    people = candidates(sources, commons, lords, donations)
    if not people:
        return [], None
    text, labels = prompt(question, people)
    from hansard.rag.answer import cached_system

    resp = client.messages.parse(model=model, max_tokens=6000, system=cached_system(SYSTEM),
                                 messages=[{"role": "user", "content": text}],
                                 output_config={"effort": effort}, output_format=Matches)
    found = resp.parsed_output.matches if resp.parsed_output else []
    by_source: dict[int, dict[str, Any]] = {}
    for m in found:
        if not m.relevant or _DOUBT.search(m.connection):
            continue  # listed, but judged not to relate after all
        entry = labels.get(m.interest.strip())
        if entry is None or entry["person_id"] != pid_of(people, m.contribution):
            continue  # a label the model made up, or another member's entry
        if not any(h["text"] == entry["text"] and h["registered"] == entry["registered"]
                   for c in people[entry["person_id"]]["contributions"] if c["source"] == m.contribution
                   for h in c["held"]):
            continue  # an entry that did not apply on the day of this contribution
        number = person_number(entry["person_id"])
        flag = by_source.setdefault(m.contribution, {
            "source": m.contribution, "person_id": entry["person_id"], "name": entry["name"],
            "register": LORDS_REGISTER if entry["house"] == "lords" else COMMONS_REGISTER,
            "link": f"/people/{number}#h-interests" if number else None, "items": []})
        if entry["kind"] == "donation" and flag["register"] != DONATIONS_REGISTER and \
                all(i["kind"] == "donation" for i in flag["items"]):
            flag["register"] = DONATIONS_REGISTER
        if not any(i["summary"] == entry["summary"] and i["registered"] == entry["registered"] for i in flag["items"]):
            flag["items"].append({"category": entry["category"], "summary": entry["summary"],
                                  "registered": entry["registered"], "connection": " ".join(m.connection.split()),
                                  "kind": entry["kind"], "register": entry["register"], "url": entry["url"]})
    return sorted(by_source.values(), key=lambda f: f["source"]), resp.usage


def without_public_office(flags: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Flags saved before posts in government or Parliament were left out, with those entries removed (a flag left
    with nothing is dropped)."""
    out = []
    for f in flags or []:
        items = [i for i in f.get("items") or [] if i.get("kind") != "register" or not public_office(i)]
        if items:
            out.append({**f, "items": items})
    return out


def pid_of(people: dict[str, Any], source: int) -> str | None:
    return next((pid for pid, p in people.items() for c in p["contributions"] if c["source"] == source), None)
