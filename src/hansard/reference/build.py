"""Build normalised reference tables from parlparse ``members/`` and the geo lookup.

Outputs (Parquet, in ``settings.reference_dir``):

* ``persons``         one row per canonical person (redirects resolved), with gender and date of
                      birth where known (matched by UK Parliament ID; see ``sources.demographics``)
* ``person_ids``      every id that can appear in Hansard XML -> canonical person:
                      person ids (incl. redirected ones), Commons member ids and
                      Lords ids (incl. redirected memberships)
* ``names``           dated *Main* names (display only; never used for matching)
* ``memberships``     Westminster seat / peerage spells with party, constituency,
                      GSS code, region and nation
* ``offices``         government, shadow, parliamentary, whip and committee spells,
                      de-duplicated and interval-merged, with policy portfolios
* ``constituencies``  ONS constituency -> ITL1 region with validity intervals
* ``parties``         party id -> name and whip group (for rebellion analysis)
* ``representations`` the Commons seats each MP has held, by UK Parliament constituency id (Members API)
* ``election_results`` every general election and by-election result for those seats (Members API)

Identity is keyed only on ``uk.org.publicwhip/person/N``. Names, including
"Alternate" names such as "The Prime Minister", are never used to resolve who
spoke.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from hansard.config import STATIC_DIR
from hansard.dates import SENTINEL_END, parse_end, parse_start
from hansard.reference.portfolios import (
    KIND_CHAIR,
    KIND_MEMBER,
    classify_committee,
    classify_post,
)

COMMONS = "commons"
LORDS = "lords"
PERSON_PREFIX = "uk.org.publicwhip/person/"

# Parties whose members take the same whip, for rebellion analysis.
PARTY_GROUPS = {
    "labourco-operative": "labour",
    "labour": "labour",
}
NON_PARTISAN = {"speaker", "deputy-speaker", "presiding-officer", "crossbench", "non-affiliated",
                "independent", "none", "bishop", "other"}

# Committee display names that differ from the latest name parlparse stores
# under the committee id (ids carry the most recent name).
COMMITTEE_NAME_HISTORY: dict[str, list[tuple[date, date, str]]] = {
    "committee-on-the-future-relationship-with-the-european-union": [
        (date(2016, 7, 1), date(2020, 1, 26), "Exiting the European Union Committee"),
    ],
}


@dataclass
class ReferenceData:
    persons: list[dict[str, Any]] = field(default_factory=list)
    person_ids: list[dict[str, Any]] = field(default_factory=list)
    names: list[dict[str, Any]] = field(default_factory=list)
    memberships: list[dict[str, Any]] = field(default_factory=list)
    offices: list[dict[str, Any]] = field(default_factory=list)
    constituencies: list[dict[str, Any]] = field(default_factory=list)
    parties: list[dict[str, Any]] = field(default_factory=list)
    representations: list[dict[str, Any]] = field(default_factory=list)
    election_results: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- names


def normalise_place(name: str) -> str:
    """Normalise a constituency name for matching ("&" vs "and", punctuation, accents)."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    text = re.sub(r"\bst\.?\b", "st", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def format_main_name(n: dict[str, Any]) -> str:
    """Display form of a parlparse Main name (Commons or Lords style)."""
    prefix = (n.get("honorific_prefix") or "").strip()
    lordname = (n.get("lordname") or "").strip()
    lordofname = (n.get("lordofname") or "").strip()
    if lordname or lordofname:
        parts = [prefix] if prefix else []
        if lordname:
            parts.append(lordname)
        text = " ".join(parts)
        if lordofname:
            text = f"{text} of {lordofname}" if text else lordofname
        return text.strip()
    given = (n.get("given_name") or "").strip()
    family = (n.get("family_name") or "").strip()
    base = " ".join(p for p in (given, family) if p) or (n.get("name") or "").strip()
    if prefix in {"Sir", "Dame"}:
        return f"{prefix} {base}"
    return base


# Members recorded with a middle name they are not known by (some full names, such as "Michael Jabez Foster", tell
# two members apart, and "Anne Marie Morris" is the name she uses, so middle names are only dropped here or when
# they are initials)
KNOWN_AS = {"John Martin McDonnell": "John McDonnell"}


def short_name(n: dict[str, Any], alternates: set[str]) -> str:
    """The Main name as the member is known: without a middle initial ("Diana R. Johnson") when the short form is
    also on record, or as listed in KNOWN_AS."""
    full = format_main_name(n)
    if full in KNOWN_AS:
        return KNOWN_AS[full]
    given, family = (n.get("given_name") or "").split(), (n.get("family_name") or "").strip()
    if len(given) < 2 or not family or n.get("lordname") or n.get("lordofname"):
        return full
    if not all(re.fullmatch(r"[A-Z]\.?", g) for g in given[1:]):
        return full
    short = f"{given[0]} {family}"
    if short not in alternates:
        return full
    prefix = (n.get("honorific_prefix") or "").strip()
    return f"{prefix} {short}" if prefix in {"Sir", "Dame"} else short


# --------------------------------------------------------------------------- loaders


def _resolve_chain(mapping: dict[str, str], key: str) -> str:
    seen = set()
    while key in mapping and key not in seen:
        seen.add(key)
        key = mapping[key]
    return key


def election_tables(persons: list[dict[str, Any]], elections: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    by_mnis = {p["datadotparl_id"]: p["person_id"] for p in persons if p.get("datadotparl_id")}
    reps = []
    for mnis, seats in (elections.get("representations") or {}).items():
        person = by_mnis.get(str(mnis))
        if person is None:
            continue
        for r in seats:
            reps.append({"person_id": person, "constituency_id": int(r["id"]), "constituency_name": r["name"],
                         "start": date.fromisoformat(r["start"]),
                         "end": date.fromisoformat(r["end"]) if r.get("end") else SENTINEL_END})
    results = []
    for cid, rs in (elections.get("results") or {}).items():
        for r in rs:
            results.append({"constituency_id": int(cid), "election_date": date.fromisoformat(r["date"]),
                            "title": r.get("title"), "is_general": bool(r.get("general")),
                            "majority": r.get("majority"), "votes": r.get("votes"), "electorate": r.get("electorate"),
                            "result": r.get("result"), "party": r.get("party")})
    return reps, results


def load_constituencies(csv_path: Path) -> list[dict[str, Any]]:
    rows = []
    with open(csv_path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append({
                "gss_code": r["gss_code"],
                "name": r["name"],
                "region": r["region"],
                "nation": r["nation"],
                "boundary_set": r["boundary_set"],
                "valid_from": date.fromisoformat(r["valid_from"]),
                "valid_to": date.fromisoformat(r["valid_to"]),
                "settlement": r.get("settlement") or None,
                "main_area": r.get("main_area") or None,
                "urban_area": r.get("urban_area") or None,
                "urban_area_pop": int(r["urban_area_pop"]) if r.get("urban_area_pop") else None,
            })
    return rows


def load_overrides(path: Path | None) -> dict[str, str]:
    """``config/overrides/constituency_names.csv``: parlparse name -> ONS name."""
    if not path or not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return {normalise_place(r["parlparse_name"]): normalise_place(r["ons_name"])
                for r in csv.DictReader(fh) if r.get("parlparse_name")}


# Raised whenever the way the reference is built changes, so the next sync rebuilds it even if the member data has not.
REFERENCE_VERSION = "2026-10-19d"  # by-elections of 1997-2001
EARLIEST_SEATS = date(1997, 5, 1)  # seats are resolved for memberships from the 1997 general election on
PRE2010_CSV = STATIC_DIR / "geo" / "pre2010_regions.csv"
PARLIAMENT_IDS = STATIC_DIR / "parliament_ids.json"


def parliament_id_for(person_id: str, idents: dict[str, str], saved: dict[str, str], by_qid: dict[str, str],
                      claimed: set[str]) -> str | None:
    """A person's UK Parliament ID: from parlparse, else the saved list, else Wikidata. parlparse sometimes has one
    member twice (Willie Rennie as an MP and as an MSP) with the same Wikidata item, so an ID found through Wikidata
    goes only to a person when no one else has it yet (``claimed`` holds those given out)."""
    mnis = idents.get("datadotparl_id") or saved.get(person_id)
    if mnis:
        return mnis
    mnis = by_qid.get(idents.get("wikidata", ""))
    if not mnis or mnis in claimed:
        return None
    claimed.add(mnis)
    return mnis


def saved_parliament_ids(path: Path | None = None) -> dict[str, str]:
    """{parlparse person ID: UK Parliament ID} for MPs parlparse has no Parliament ID for."""
    try:
        return json.loads(Path(path or PARLIAMENT_IDS).read_text()).get("ids") or {}
    except (OSError, ValueError):
        return {}


def load_pre2010_regions(path: Path | None) -> dict[str, dict[str, Any]]:
    """{normalised name: {"name", "nation", "region"}} for pre-2010 seats with no namesake in later sets."""
    path = Path(path or PRE2010_CSV)
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {normalise_place(r["name"]): {"name": r["name"], "nation": r["nation"], "region": r["region"]}
                for r in csv.DictReader(f)}


def build_reference(
    members_dir: Path,
    geo_csv: Path,
    overrides_csv: Path | None = None,
    demographics: dict[str, dict[str, Any]] | None = None,
    elections: dict[str, Any] | None = None,
    pre2010_csv: Path | None = None,
) -> ReferenceData:
    genders = (demographics or {}).get("genders", {})
    births = (demographics or {}).get("births", {})
    # members with a Wikidata item but no UK Parliament ID in ParlParse: Wikidata records the ID (P10428)
    mnis_by_qid = {b["qid"]: m for m, b in births.items() if b.get("qid")}
    # and those found in the Members API by surname, seat and dates (scripts/snapshot_parliament_ids.py)
    mnis_saved = saved_parliament_ids()
    people = json.loads((members_dir / "people.json").read_text(encoding="utf-8"))
    claimed = set(mnis_saved.values()) | {str(i["identifier"]) for p in people["persons"]
                                          for i in p.get("identifiers", []) if i.get("scheme") == "datadotparl_id"}
    ref = ReferenceData()
    ref.constituencies = load_constituencies(geo_csv)

    # ---- persons, redirects and dated names
    person_redirect = {p["id"]: p["redirect"] for p in people["persons"] if "redirect" in p}
    for p in people["persons"]:
        if "redirect" in p:
            continue
        idents = {i["scheme"]: str(i["identifier"]) for i in p.get("identifiers", [])}
        mains = []
        alternates = {(n.get("name") or "").strip() for n in p.get("other_names", []) if n.get("note") == "Alternate"}
        for n in p.get("other_names", []):
            if n.get("note") != "Main":
                continue
            mains.append({
                "person_id": p["id"],
                "name": short_name(n, alternates),
                "start": parse_start(n.get("start_date")),
                "end": parse_end(n.get("end_date")),
            })
        mains.sort(key=lambda r: r["start"])
        ref.names.extend(mains)
        mnis = parliament_id_for(p["id"], idents, mnis_saved, mnis_by_qid, claimed)
        # age and gender also for the second record of a member parlparse has twice (same Wikidata item)
        facts = mnis or mnis_by_qid.get(idents.get("wikidata", ""))
        birth = births.get(facts) if facts else None
        ref.persons.append({
            "person_id": p["id"],
            "latest_name": mains[-1]["name"] if mains else p["id"],
            "datadotparl_id": mnis,
            "wikidata": idents.get("wikidata"),
            "gender": genders.get(facts) if facts else None,
            "birth_date": date.fromisoformat(birth["date"]) if birth else None,
            "birth_precision": birth["precision"] if birth else None,
        })
    known = {p["person_id"] for p in ref.persons}

    # ---- parties
    for org in people["organizations"]:
        if org.get("classification") == "party":
            ref.parties.append({
                "party_id": org["id"],
                "party_name": org.get("name", org["id"]),
                "whip_group": None if org["id"] in NON_PARTISAN else PARTY_GROUPS.get(org["id"], org["id"]),
            })
    party_names = {p["party_id"]: p["party_name"] for p in ref.parties}

    # ---- posts and seat -> GSS matching
    posts = {p["id"]: p for p in people["posts"]}
    overrides = load_overrides(overrides_csv)
    by_set: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    # A few unchanged seats (e.g. Orkney and Shetland) keep their GSS code across
    # boundary sets, so codes map to every entry and the one valid on the date wins.
    by_gss: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in ref.constituencies:
        by_set[c["boundary_set"]][normalise_place(c["name"])] = c
        by_gss[c["gss_code"]].append(c)

    # Seats before the 2010 general election (the 1997 boundaries, and Scotland's from 2005) have no GSS code in
    # the ONS lookups: they take their region, nation and seat type from the seat of the same name in the 2010 or
    # 2024 set, or (for seats that were abolished or renamed) their nation and region from a small curated table.
    pre2010 = load_pre2010_regions(pre2010_csv)

    def seat_before_2010(post: dict[str, Any]) -> dict[str, Any] | None:
        for raw in [post["area"]["name"], *post["area"].get("other_names", [])]:
            key = normalise_place(raw)
            key = overrides.get(key, key)
            same = by_set["2010"].get(key) or by_set["2024"].get(key)
            if same:
                return {**same, "gss_code": None}
            if key in pre2010:
                return {**pre2010[key], "gss_code": None, "settlement": None, "urban_area": None,
                        "urban_area_pop": None}
        return None

    def seat_for(post: dict[str, Any], on: date) -> dict[str, Any] | None:
        if on < date(2010, 5, 6):
            return seat_before_2010(post)
        for ident in post.get("identifiers", []):
            if ident["scheme"] == "gss" and ident["identifier"] in by_gss:
                entries = by_gss[ident["identifier"]]
                return next((c for c in entries if c["valid_from"] <= on <= c["valid_to"]), entries[-1])
        bset = "2024" if on >= date(2024, 7, 4) else "2010" if on >= date(2010, 5, 6) else None
        if bset is None:
            return None
        names = [post["area"]["name"], *post["area"].get("other_names", [])]
        for raw in names:
            key = normalise_place(raw)
            key = overrides.get(key, key)
            if key in by_set[bset]:
                return by_set[bset][key]
        return None

    # ---- memberships
    member_redirect: dict[str, str] = {}
    member_person: dict[str, str] = {}
    unmatched_seats: set[str] = set()
    for m in people["memberships"]:
        if "redirect" in m:
            member_redirect[m["id"]] = m["redirect"]
            continue
        pid = _resolve_chain(person_redirect, m.get("person_id", ""))
        if pid not in known:
            continue
        member_person[m["id"]] = pid
        start, end = parse_start(m.get("start_date")), parse_end(m.get("end_date"))
        post = posts.get(m.get("post_id", ""))
        if m.get("organization_id") == "house-of-lords":
            house, constituency, seat = LORDS, None, None
        elif post and post.get("organization_id") == "house-of-commons":
            house, constituency = COMMONS, post["area"]["name"]
            # Seat set is decided by where the membership falls; memberships that
            # straddle the earliest year covered resolve against the set in force then.
            on = max(start, EARLIEST_SEATS)
            seat = seat_for(post, on) if end >= EARLIEST_SEATS else None
            if seat is None and end >= EARLIEST_SEATS:
                unmatched_seats.add(constituency)
        else:
            continue  # devolved legislatures etc. are out of scope
        party = m.get("on_behalf_of_id")
        ref.memberships.append({
            "membership_id": m["id"],
            "person_id": pid,
            "house": house,
            "constituency": constituency,
            "gss_code": seat["gss_code"] if seat else None,
            "region": seat["region"] if seat else None,
            "nation": seat["nation"] if seat else None,
            "settlement": seat.get("settlement") if seat else None,
            "urban_area": seat.get("urban_area") if seat else None,
            "urban_area_pop": seat.get("urban_area_pop") if seat else None,
            "party_id": party,
            "party_name": party_names.get(party, party),
            "start": start,
            "end": end,
            "start_reason": m.get("start_reason"),
            "end_reason": m.get("end_reason"),
        })

    # Memberships before 1997 often have no party recorded; the member's next spell in the same House says it
    by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in ref.memberships:
        by_person[m["person_id"]].append(m)
    for spells in by_person.values():
        spells.sort(key=lambda m: m["start"])
        for k, m in enumerate(spells):
            if m["party_id"] is None and m["end"] < EARLIEST_SEATS:
                nxt = next((n for n in spells[k + 1:] if n["house"] == m["house"] and n["party_id"]), None)
                if nxt:
                    m["party_id"], m["party_name"] = nxt["party_id"], nxt["party_name"]

    # ---- every id that may appear in XML -> canonical person
    for old, new in person_redirect.items():
        ref.person_ids.append({"xml_id": old, "person_id": _resolve_chain(person_redirect, new)})
    for pid in known:
        ref.person_ids.append({"xml_id": pid, "person_id": pid})
    for mid, pid in member_person.items():
        ref.person_ids.append({"xml_id": mid, "person_id": pid})
    for old in member_redirect:
        target = _resolve_chain(member_redirect, old)
        if target in member_person:
            ref.person_ids.append({"xml_id": old, "person_id": member_person[target]})

    # ---- offices
    ref.offices, office_stats = load_offices(members_dir, person_redirect, known)

    # ---- seats and election results (matched by UK Parliament ID)
    ref.representations, ref.election_results = election_tables(ref.persons, elections or {})

    recent = {m["person_id"] for m in ref.memberships if m["end"] >= date(2010, 5, 6)}
    recent_persons = [p for p in ref.persons if p["person_id"] in recent]
    ref.stats = {
        "persons": len(ref.persons),
        "members_since_2010": len(recent_persons),
        "with_gender_since_2010": sum(1 for p in recent_persons if p["gender"]),
        "with_birth_date_since_2010": sum(1 for p in recent_persons if p["birth_date"]),
        "mps_with_election_results": len({r["person_id"] for r in ref.representations}),
        "election_results": len(ref.election_results),
        "memberships": len(ref.memberships),
        "names": len(ref.names),
        "unmatched_seats": sorted(unmatched_seats),
        **office_stats,
    }
    return ref


LEGACY_FROM, LEGACY_TO = "1997-05-01", "2010-05-11"
_OPPOSITION_WORDS = re.compile(r"shadow|spokes|opposition|parliamentary party|parliamentary leader", re.I)
_PARLIAMENTARY = re.compile(r"\bspeaker\b|ways and means|chairman of committees|deputy chairman of committees", re.I)


def legacy_ministers(path: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """parlparse's pre-2010 ``ministers.json`` (1997 to May 2010), turned into the shape of ``ministers-2010.json``.

    That file names the department in ``organization_id`` and gives a bare role ("Minister of State"); opposition
    portfolios are organisations with short names ("Home Affairs") holding shadow or spokesperson roles. The role is
    written out with its department ("Minister of State (Home Office)") so it is classified like later posts."""
    if not path.exists():
        return {}, []
    d = json.loads(path.read_text(encoding="utf-8"))
    names = {o["id"]: o.get("name", o["id"]) for o in d.get("organizations", [])}
    rows = [m for m in d.get("memberships", [])
            if (m.get("end_date") or "9999") >= LEGACY_FROM and (m.get("start_date") or "") < "2010-05-06"]
    by_org: dict[str, list[str]] = defaultdict(list)
    for m in rows:
        by_org[m.get("organization_id", "")].append(m.get("role") or "")
    mixed = {"house-of-commons", "house-of-lords", "parliament"}  # government and opposition roles side by side
    opposition_orgs = {o for o, roles in by_org.items() if o not in mixed
                       and sum(bool(_OPPOSITION_WORDS.search(r)) for r in roles) * 2 >= len(roles)}
    out = []
    for m in rows:
        org = m.get("organization_id", "")
        name = names.get(org, org)
        role = " ".join((m.get("role") or "").strip(" -").split())
        if "ommittee" in name or "Panel" in name:
            source, full = "parlparse/committee", role
        elif _OPPOSITION_WORDS.search(role) or org in opposition_orgs or "party" in name.lower():
            source, full = "parlparse/oppositionpost", role if name.lower() in role.lower() else f"{role} ({name})"
        elif org in ("house-of-commons", "house-of-lords", "parliament") and _PARLIAMENTARY.search(role):
            source, full = "parlparse/parliamentarypost", role
        else:
            # the department in brackets, except for offices that stand on their own (the Prime Minister's department
            # in this file is HM Treasury, as First Lord of the Treasury)
            alone = name.lower() in role.lower() or _STANDALONE.match(role)
            source, full = "parlparse/governmentpost", role if alone else f"{role} ({name})"
        end = m.get("end_date")
        out.append({**m, "role": full, "source": source,
                    "end_date": min(end, LEGACY_TO) if end else end})
    return names, out


def load_offices(
    members_dir: Path, person_redirect: dict[str, str], known: set[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    files = [members_dir / "ministers-2010.json", members_dir / "posts" / "westminster-parliament-posts.json"]
    raw_rows: list[dict[str, Any]] = []
    org_names: dict[str, str] = {}
    for f in files:
        if not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        for org in d.get("organizations", []):
            # later files (the posts scraper) carry fresher committee names
            org_names[org["id"]] = org.get("name", org["id"])
        raw_rows.extend(d.get("memberships", []))
    # Members who sat after 2010 have their whole careers in Parliament's own data above; the older parlparse file
    # (1997 to 2010) is used only for people it does not cover, so no post is listed twice.
    covered = {_resolve_chain(person_redirect, m.get("person_id", "")) for m in raw_rows}
    legacy_names, legacy_rows = legacy_ministers(members_dir / "ministers.json")
    for name_id, name in legacy_names.items():
        org_names.setdefault(name_id, name)
    raw_rows.extend(m for m in legacy_rows if _resolve_chain(person_redirect, m.get("person_id", "")) not in covered)
    # select committees of MPs who left before 2010 (not in Parliament's data above), saved with the code from the
    # Members API (scripts/snapshot_committees_before_2010.py)
    before = STATIC_DIR / "committees_before_2010.json"
    if before.exists():
        d = json.loads(before.read_text(encoding="utf-8"))
        for org in d.get("organizations", []):
            org_names.setdefault(org["id"], org.get("name", org["id"]))

        def same(name: str) -> str:
            return re.sub(r"\s*\((commons|lords)\)\s*$", "", (name or "").lower()).strip()

        # parlparse's 1997-2010 file has some of these posts already (under its own committee IDs): those are kept
        listed: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for m in raw_rows:
            if m.get("source", "").endswith("/committee") or m.get("organization_id") in org_names:
                pid = _resolve_chain(person_redirect, m.get("person_id", ""))
                listed.setdefault((pid, same(org_names.get(m.get("organization_id"), ""))), []).append(
                    ((m.get("start_date") or "0000")[:10], (m.get("end_date") or "9999")[:10]))
        for m in d.get("memberships", []):
            pid = _resolve_chain(person_redirect, m.get("person_id", ""))
            if pid in covered:
                continue
            start, end = (m.get("start_date") or "0000")[:10], (m.get("end_date") or "9999")[:10]
            overlap = [s for s, e in listed.get((pid, same(org_names.get(m["organization_id"], ""))), [])
                       if s <= end and start <= e]
            if overlap:  # keep only the part before the post as parlparse lists it, if any
                first = min(overlap)
                if first <= start:
                    continue
                m = {**m, "end_date": (date.fromisoformat(first) - timedelta(days=1)).isoformat()}
            raw_rows.append(m)

    rows_in = len(raw_rows)
    exact: dict[tuple, dict[str, Any]] = {}
    impossible = 0
    for m in raw_rows:
        pid = _resolve_chain(person_redirect, m.get("person_id", ""))
        if pid not in known:
            continue
        if parse_end(m.get("end_date")) < parse_start(m.get("start_date")):
            impossible += 1  # ends before it starts (a few of Parliament's committee rows): cannot be placed in time
            continue
        source = m.get("source", "")
        role = (m.get("role") or "").strip()
        if source.endswith("/committee"):
            committee_id = m["organization_id"]
            committee_name = org_names.get(committee_id, committee_id)
            rc = classify_committee(committee_name, role)
        else:
            committee_id = committee_name = None
            rc = classify_post(source, role)
        key = (pid, rc.kind, role if rc.kind not in (KIND_CHAIR, KIND_MEMBER) else rc.kind, committee_id,
               parse_start(m.get("start_date")), parse_end(m.get("end_date")))
        if key in exact:
            exact[key]["source_ids"].append(m["id"])
            continue
        exact[key] = {
            "person_id": pid,
            "kind": rc.kind,
            "role": role or (rc.kind.replace("_", " ").title() if committee_id else ""),
            "portfolios": list(rc.portfolios),
            "committee_id": committee_id,
            "committee_name": committee_name,
            "source": source,
            "start": key[4],
            "end": key[5],
            "source_ids": [m["id"]],
        }
    deduped = list(exact.values())

    # Merge overlapping or adjacent spells of the same office.
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for r in deduped:
        groups[(r["person_id"], r["kind"], r["role"], r["committee_id"])].append(r)
    merged: list[dict[str, Any]] = []
    for spells in groups.values():
        spells.sort(key=lambda r: (r["start"], r["end"]))
        cur = dict(spells[0], source_ids=list(spells[0]["source_ids"]))
        for nxt in spells[1:]:
            if cur["end"] == SENTINEL_END or nxt["start"] <= cur["end"] + timedelta(days=1):
                cur["end"] = max(cur["end"], nxt["end"])
                cur["source_ids"].extend(nxt["source_ids"])
            else:
                merged.append(cur)
                cur = dict(nxt, source_ids=list(nxt["source_ids"]))
        merged.append(cur)
    for r in merged:
        r["display_name"] = office_display(r)
    stats = {"office_rows_in": rows_in, "office_rows_exact_dupes": rows_in - len(deduped) - impossible,
             "office_rows_impossible": impossible, "office_rows_after_merge": len(merged)}
    return merged, stats


_STANDALONE = re.compile(r"^(?:the )?(?:deputy )?prime minister\b", re.I)


def office_display(office: dict[str, Any], on: date | None = None) -> str:
    """Human-readable office title, using the committee name in force on ``on``."""
    if office.get("committee_id"):
        name = office["committee_name"]
        for start, end, old in COMMITTEE_NAME_HISTORY.get(office["committee_id"], []):
            if on is not None and start <= on <= end:
                name = old
        prefix = "Chair, " if office["kind"] == KIND_CHAIR else "Member, "
        return prefix + name
    return office["role"]


# --------------------------------------------------------------------------- output

_SCHEMAS = {
    "persons": pa.schema([("person_id", pa.string()), ("latest_name", pa.string()),
                          ("datadotparl_id", pa.string()), ("wikidata", pa.string()),
                          ("gender", pa.string()), ("birth_date", pa.date32()), ("birth_precision", pa.string())]),
    "person_ids": pa.schema([("xml_id", pa.string()), ("person_id", pa.string())]),
    "names": pa.schema([("person_id", pa.string()), ("name", pa.string()),
                        ("start", pa.date32()), ("end", pa.date32())]),
    "memberships": pa.schema([
        ("membership_id", pa.string()), ("person_id", pa.string()), ("house", pa.string()),
        ("constituency", pa.string()), ("gss_code", pa.string()), ("region", pa.string()),
        ("nation", pa.string()), ("settlement", pa.string()), ("urban_area", pa.string()),
        ("urban_area_pop", pa.int32()), ("party_id", pa.string()), ("party_name", pa.string()),
        ("start", pa.date32()), ("end", pa.date32()), ("start_reason", pa.string()),
        ("end_reason", pa.string())]),
    "offices": pa.schema([
        ("person_id", pa.string()), ("kind", pa.string()), ("role", pa.string()),
        ("portfolios", pa.list_(pa.string())), ("committee_id", pa.string()),
        ("committee_name", pa.string()), ("source", pa.string()), ("start", pa.date32()),
        ("end", pa.date32()), ("source_ids", pa.list_(pa.string())), ("display_name", pa.string())]),
    "constituencies": pa.schema([
        ("gss_code", pa.string()), ("name", pa.string()), ("region", pa.string()),
        ("nation", pa.string()), ("boundary_set", pa.string()), ("valid_from", pa.date32()),
        ("valid_to", pa.date32()), ("settlement", pa.string()), ("main_area", pa.string()),
        ("urban_area", pa.string()), ("urban_area_pop", pa.int32())]),
    "parties": pa.schema([("party_id", pa.string()), ("party_name", pa.string()),
                          ("whip_group", pa.string())]),
    "representations": pa.schema([("person_id", pa.string()), ("constituency_id", pa.int32()),
                                  ("constituency_name", pa.string()), ("start", pa.date32()), ("end", pa.date32())]),
    "election_results": pa.schema([("constituency_id", pa.int32()), ("election_date", pa.date32()),
                                   ("title", pa.string()), ("is_general", pa.bool_()), ("majority", pa.int32()),
                                   ("votes", pa.int32()), ("electorate", pa.int32()), ("result", pa.string()),
                                   ("party", pa.string())]),
}


def write_reference(ref: ReferenceData, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, schema in _SCHEMAS.items():
        rows = getattr(ref, name)
        table = pa.Table.from_pylist(rows, schema=schema)
        tmp = out_dir / f".{name}.parquet.tmp"
        pq.write_table(table, tmp, compression="zstd")
        tmp.replace(out_dir / f"{name}.parquet")
    (out_dir / "stats.json").write_text(json.dumps(ref.stats, indent=2, default=str))


__all__ = [
    "COMMONS",
    "LORDS",
    "SENTINEL_END",
    "ReferenceData",
    "build_reference",
    "format_main_name",
    "normalise_place",
    "office_display",
    "write_reference",
]
