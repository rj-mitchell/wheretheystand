"""Turn ranked records and division summaries into numbered, citable sources."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from hansard.debates import debate_path
from hansard.rank.evidence import GROUPS, demographic_group, seat_type_label, speaker_group
from hansard.sources.demographics import AGE_BAND_LABEL
from hansard.sources.elections import MAJORITY_BAND_LABEL
from hansard.votes import division_text


@dataclass
class Source:
    n: int                      # 1-based number shown as [Source n]
    kind: str                   # "record" | "division"
    title: str
    url: str
    blocks: list[str]           # citable text, verbatim from the record
    meta: dict[str, Any] = field(default_factory=dict)

    def search_result(self) -> dict[str, Any]:
        return {
            "type": "search_result",
            "source": self.url,
            "title": self.title,
            "content": [{"type": "text", "text": b} for b in self.blocks],
            "citations": {"enabled": True},
        }

    def card(self) -> dict[str, Any]:
        return {"n": self.n, "kind": self.kind, "title": self.title, "url": self.url, **self.meta}


def _fmt_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"


def age_label(sp: dict[str, Any]) -> str | None:
    """'aged 54' or 'aged about 54' (only the year of birth is known), on the date of the record."""
    if sp.get("age") is None:
        return None
    return f"aged {'about ' if sp.get('age_approximate') else ''}{sp['age']}"


def _facets(facets: set[str] | bool) -> set[str]:
    return {"gender", "age"} if facets is True else (set(facets) if facets else set())


def majority_label(m: dict[str, Any] | None) -> str | None:
    """e.g. 'majority 4,937 (11.8%) at the 2019 general election'."""
    if not m:
        return None
    pct = f" ({m['pct']}%)" if m.get("pct") is not None else ""
    return f"majority {m['majority']:,}{pct} at the {m['election']}"


def speaker_label(r: dict[str, Any], facets: set[str] | bool = False) -> str:
    """e.g. 'Hilary Benn (Labour; MP for Leeds Central, city seat (West Yorkshire urban area, 1.9m people),
    Yorkshire and The Humber; role on this date: Chair, Exiting the European Union Committee)'.

    ``facets`` adds, only when the question is about them: gender, age on the date, first term, majority then.
    """
    facets = _facets(facets)
    sp = r.get("speaker") or {}
    name = sp.get("name") or r.get("speaker_name_raw") or "Unattributed"
    bits = []
    if sp.get("person_id"):
        if "gender" in facets:
            bits.append({"F": "woman", "M": "man"}.get(sp.get("gender") or "", "gender not known"))
        if "age" in facets:
            bits.append(age_label(sp) or "age not known")
        if "tenure" in facets and sp.get("house") == "commons":
            bits.append({True: "first-term MP", False: "re-elected MP"}.get(sp.get("first_term"),
                                                                            "first election not known"))
        if "majority" in facets and sp.get("house") == "commons":
            bits.append(majority_label(sp.get("majority_then")) or "majority not known")
    if sp.get("party"):
        bits.append(sp["party"])
    if sp.get("constituency"):
        seat = [f"MP for {sp['constituency']}"]
        if sp.get("settlement"):
            seat.append(seat_type_label(sp))
        if sp.get("region"):
            seat.append(sp["region"])
        bits.append(", ".join(seat))
    elif sp.get("house") == "lords":
        bits.append("member of the House of Lords")
    if sp.get("person_id"):
        roles = [o["title"] for o in sp.get("offices", []) if o.get("kind") in ("gov", "shadow", "chair")]
        bits.append("role on this date: " + ("; ".join(roles) if roles else "backbencher"))
    return f"{name} ({'; '.join(bits)})" if bits else name


def group_label(speaker: dict[str, Any] | None, group_by: str = "role") -> str:
    """The heading a source is listed under: the speaker's role group, or their gender, age, tenure or majority."""
    if group_by == "tenure":
        return {"first-term": "First-term MPs", "returning": "Re-elected MPs"}.get(
            demographic_group(speaker, "tenure"), "Peers and others")
    if group_by == "majority":
        band = demographic_group(speaker, "majority")
        return (MAJORITY_BAND_LABEL[band][0].upper() + MAJORITY_BAND_LABEL[band][1:] if band in MAJORITY_BAND_LABEL
                else "Majority not known")
    if group_by == "gender":
        return {"F": "Women", "M": "Men"}.get(demographic_group(speaker, "gender"), "Gender not known")
    if group_by == "age":
        band = demographic_group(speaker, "age")
        return f"Aged {AGE_BAND_LABEL[band]} on the day" if band in AGE_BAND_LABEL else "Age not known"
    return GROUPS[speaker_group(speaker)]


# A speaker's own declaration in the record, e.g. "I refer the House to my entry in the Register of Members'
# Financial Interests" or "I declare an interest as a member of Unite": first person only, never about someone else.
_DECLARED = re.compile(
    r"(?:^|(?<=[.?!]\s))[^.?!]*?\b(?:I|We)\b[^.?!]{0,60}?(?:"
    r"\b(?:declare|declaring)\b[^.?!]{0,20}?\b(?:an?|my|our)\b[^.?!]{0,80}?\b(?:interests?|[Rr]egister)\b"
    r"|\b(?:refer|referring|draw|drawing|remind|direct)\b[^.?!]{0,80}?\b(?:my|our)\b[^.?!]{0,60}?"
    r"\b(?:entry|entries|interests?|[Rr]egister)\b)[^.?!]*[.?!]?")


def declared_interest(text: str) -> str | None:
    """The sentence in which the speaker declared an interest, as they said it (None if they did not)."""
    m = _DECLARED.search(text or "")
    return " ".join(m.group(0).split()) if m else None


def record_source(n: int, r: dict[str, Any], facets: set[str] | bool = False, group_by: str = "role") -> Source:
    ref = f"col. {r['colnum']}" if r.get("colnum") else (f"UIN {r['uin']}" if r.get("uin") else "")
    heading = r.get("heading") or r.get("minor_heading") or r.get("major_heading") or ""
    kind_label = {"question": "Written question", "answer": "Written answer", "statement": "Written statement"}.get(
        r["record_type"], r["chamber"])
    # how many days depends on which records a search finds, so the briefing is told only that it was repeatedly
    raised = "has raised this subject repeatedly (on several separate days)" if r.get("occasions") else None
    title = " — ".join(x for x in (r.get("compare_side"), speaker_label(r, facets), raised,
                                   f"{kind_label}, {r['chamber']}" if kind_label != r["chamber"] else r["chamber"],
                                   _fmt_date(r["date"]), ref, heading) if x)
    blocks = [p for p in r["text"].split("\n\n") if p.strip()]
    if r.get("context"):
        blocks = [f"Question asked: {r['context']}", *blocks]
    url = r.get("official_url") or r["twfy_url"]
    meta = {
        "record_id": r["record_id"],
        "debate_path": debate_path(r.get("source") or "", r["date"], r.get("major_heading"), r.get("minor_heading"),
                                   r.get("bill"), r["record_id"]),
        "date": r["date"],
        "chamber": r["chamber"],
        "record_type": r["record_type"],
        "heading": heading,
        "colnum": r.get("colnum"),
        "uin": r.get("uin"),
        "time": r.get("time"),
        "official_url": r.get("official_url"),
        "url_kind": r.get("url_kind"),
        "twfy_url": r["twfy_url"],
        "speaker": r.get("speaker"),
        "speaker_name_raw": r.get("speaker_name_raw"),
        "group": r.get("compare_side") or group_label(r.get("speaker"), group_by),
        "text": r["text"],
        "context": r.get("context"),
        "ranking": r.get("ranking"),
        "occasions": r.get("occasions"),
        "declared": declared_interest(r["text"]),
    }
    return Source(n, "record", title, url, blocks, meta)


def division_source(n: int, d: dict[str, Any]) -> Source:
    house = "House of Commons" if d["house"] == "commons" else "House of Lords"
    title = f"Division {d['number']}, {house}, {_fmt_date(d['date'])} — " + " — ".join(
        x for x in (d.get("major_heading"), d.get("minor_heading")) if x)
    blocks = division_text(d)
    url = d.get("official_url") or d["twfy_url"]
    meta = {**d, "chamber": house, "text": "\n\n".join(blocks)}
    return Source(n, "division", title, url, blocks, meta)


def edm_source(n: int, e: dict[str, Any]) -> Source:
    """An early day motion as a citable source: its words, who tabled it and who signed, by party on the day."""
    sponsor = e.get("sponsor") or {}
    who = sponsor.get("name") or e.get("primary_name") or "an MP"
    party = sponsor.get("party") or e.get("primary_party")
    tabled = _fmt_date(e["date_tabled"]) if e.get("date_tabled") else "an unknown date"
    ref = f"EDM {e['uin']}" if e.get("uin") else "Early day motion"
    title = f"{ref}, tabled {tabled} by {who}{' (' + party + ')' if party else ''} — {e['title']}"
    by = f"{who} ({party})" if party else who
    blocks = [f"{ref}: {e['title']}. Tabled on {tabled} by {by}."]
    if e.get("text"):
        blocks.append(e["text"])
    counts = e.get("party_counts") or []
    signed = sum(n for _, n in counts)
    if counts:
        blocks.append(f"Signed by {signed} member{'s' if signed != 1 else ''} (party on the day they signed): "
                      + "; ".join(f"{p} {k}" for p, k in counts) + ".")
    elif e.get("sponsors_count") is not None:
        blocks.append(f"Signatures recorded: {e['sponsors_count']}.")
    blocks.append("An early day motion records the view of the MPs who sign it. Few are debated, and ministers, "
                  "whips and the Speaker do not sign them, so the absence of a signature says nothing about a "
                  "member's view.")
    meta = {"edm_id": e["id"], "uin": e.get("uin"), "date": e.get("date_tabled"), "edm_title": e["title"],
            "motion_text": e.get("text"),
            "chamber": "Early day motions", "sponsor": sponsor or {"name": who, "party": party},
            "party_counts": counts, "sponsors_count": e.get("sponsors_count"), "text": "\n\n".join(blocks),
            "official_url": f"https://edm.parliament.uk/early-day-motion/{e['id']}", "group": "Early day motions"}
    return Source(n, "edm", title, f"/edms/{e['id']}", blocks, meta)


def devolved_source(n: int, d: dict[str, Any]) -> Source:
    """A contribution in a devolved parliament: kept apart from Westminster, labelled with its parliament."""
    sp = d.get("member") or {}
    who = sp.get("name") or d.get("speaker") or "A member"
    about = ", ".join(x for x in (sp.get("party"), sp.get("seat")) if x)
    heading = " — ".join(x for x in (d.get("major"), d.get("minor")) if x)
    title = " — ".join(x for x in (f"{who} ({about})" if about else who, d["body"], _fmt_date(d["date"]), heading) if x)
    blocks = [f"{d['body']}, {_fmt_date(d['date'])}{f' — {heading}' if heading else ''}. {who}"
              f"{f' ({about})' if about else ''}, a member of the {d['body']} (not of the UK Parliament):",
              *[p for p in d["text"].split("\n\n") if p.strip()]]
    meta = {"date": d["date"], "chamber": d["body"], "legislature": d["legislature"], "heading": heading,
            "member": {"name": who, "party": sp.get("party"), "seat": sp.get("seat"),
                       "number": sp.get("number"), "slug": sp.get("slug")},
            "text": "\n\n".join(blocks), "group": d["body"], "official_url": d.get("url"),
            "speaker_name_raw": d.get("speaker")}
    return Source(n, "devolved", title, f"/devolved/{d['legislature']}/{d['key']}#s{d['ord']}", blocks, meta)


def evidence_source(n: int, e: dict[str, Any]) -> Source:
    """A witness's answer in a committee evidence session: labelled as evidence to a committee, not a view of
    Parliament."""
    w = e["witness"]
    about = ", ".join(x for x in (w.get("role"), w.get("organisation")) if x)
    who = f"{w['name']} ({about})" if about else w["name"]
    committee = e.get("committees") or "a parliamentary committee"
    where = f"oral evidence to the {committee}" if not committee.lower().startswith("the ") else \
        f"oral evidence to {committee}"
    title = " — ".join(x for x in (who, where[0].upper() + where[1:], _fmt_date(e["date"]), e.get("inquiry")) if x)
    blocks = [f"{where[0].upper() + where[1:]}, {_fmt_date(e['date'])}"
              f"{', inquiry: ' + e['inquiry'] if e.get('inquiry') else ''}. {who}, a witness answering the "
              "committee's questions (evidence to a committee, not a view of Parliament):",
              *[p for p in e["text"].split("\n\n") if p.strip()]]
    meta = {"date": e["date"], "chamber": "Committee evidence", "committee": committee, "inquiry": e.get("inquiry"),
            "inquiry_id": e.get("inquiry_id"), "session_id": e["session_id"], "witness": w,
            "text": "\n\n".join(blocks), "group": "Committee evidence", "official_url": e.get("url"),
            "speaker_name_raw": e.get("speaker")}
    return Source(n, "evidence", title, f"/evidence/{e['session_id']}#t{e['ord']}", blocks, meta)


def build_sources(records: list[dict[str, Any]], divisions: list[dict[str, Any]],
                  facets: set[str] | bool = False, group_by: str = "role",
                  edms: list[dict[str, Any]] | None = None,
                  devolved: list[dict[str, Any]] | None = None,
                  evidence: list[dict[str, Any]] | None = None) -> list[Source]:
    out = [record_source(i, r, facets, group_by) for i, r in enumerate(records, 1)]
    out += [division_source(len(out) + i, d) for i, d in enumerate(divisions, 1)]
    out += [edm_source(len(out) + i, e) for i, e in enumerate(edms or [], 1)]
    out += [devolved_source(len(out) + i, d) for i, d in enumerate(devolved or [], 1)]
    out += [evidence_source(len(out) + i, e) for i, e in enumerate(evidence or [], 1)]
    return out
