"""Timelines: what one member, or one party, has said on a topic, in date order, with their votes in between.

Free to run: a search (no model) for the most relevant contributions, with no preference for recent records or
office-holders, shown newest first; and the divisions whose titles match the topic, with how the member voted (or
how the party's members split). Nothing is said about consistency: the reader sees what was said on each date and
draws their own conclusions.
"""

from __future__ import annotations

from typing import Any

from hansard.debates import debate_path
from hansard.engine import Engine, Filters, Scope
from hansard.people import People
from hansard.vote_types import vote_types
from hansard.votes import keywords

RELEVANCE_FLOOR = 0.3  # keep contributions at least this relevant compared with the best match


def _record_item(r: dict[str, Any]) -> dict[str, Any]:
    sp = r.get("speaker") or {}
    offices = [o.get("title") or o.get("role") for o in sp.get("offices") or [] if o.get("kind") in ("gov", "shadow",
                                                                                                  "chair")]
    excerpt = " ".join((r.get("excerpt") or "").split())
    return {
        "kind": "said", "date": r["date"], "record_id": r["record_id"], "chamber": r.get("chamber"),
        "record_type": r.get("record_type"), "heading": r.get("heading"), "excerpt": excerpt[:700],
        "speaker": sp.get("name") or r.get("speaker_name_raw"), "party": sp.get("party"),
        "role": offices[0] if offices else None, "url": r.get("official_url") or r.get("twfy_url"),
        "twfy_url": r.get("twfy_url"),
        "debate_path": debate_path(r.get("source") or "", r["date"], r.get("major_heading"), r.get("minor_heading"),
                                   r.get("bill"), r["record_id"]),
    }


def _division_filter(topic: str) -> tuple[str, list[str], int]:
    kws = keywords(topic)
    heading = "lower(coalesce(d.major_heading, '') || ' ' || coalesce(d.minor_heading, ''))"
    score = " + ".join(f"(CASE WHEN {heading} LIKE ? THEN 1 ELSE 0 END)" for _ in kws) or "0"
    need = 1 if len(kws) <= 1 else 2
    return score, [f"%{k}%" for k in kws], need


# votes on the process (timetable, closure, money) rather than the substance
PROCEDURAL = {"programme", "closure", "money", "ways_and_means"}


def member_votes(people: People, person_id: str, topic: str, limit: int = 20) -> list[dict[str, Any]]:
    if not keywords(topic) or not people._ensure_votes():
        return []
    score, params, need = _division_filter(topic)
    rows = people.con.execute(f"""
        SELECT d.date, d.house, d.number, d.major_heading, d.minor_heading, d.ayes, d.noes, pv.vote, pm.majority,
               pm.n, d.official_url, d.twfy_url, d.division_id
        FROM party_votes pv JOIN divisions d ON d.division_id = pv.division_id
        LEFT JOIN party_majorities pm ON pm.division_id = pv.division_id AND pm.whip_group = pv.whip_group
        WHERE pv.person_id = ? AND ({score}) >= {need}
        ORDER BY d.date DESC, d.number DESC LIMIT {int(limit)}""", [person_id, *params]).fetchall()
    return [{"kind": "voted", "date": r[0].isoformat(), "house": r[1], "number": r[2],
             "title": " — ".join(x for x in (r[3], r[4]) if x), "ayes": r[5], "noes": r[6], "vote": r[7],
             "against_party": bool(r[8] and r[9] and r[9] >= 2 and r[7] != r[8]),
             "url": r[10] or r[11], "division_id": r[12]} for r in rows]


def party_votes(people: People, party_id: str, topic: str, limit: int = 20) -> list[dict[str, Any]]:
    if not keywords(topic) or not people._ensure_votes():
        return []
    score, params, need = _division_filter(topic)
    group = people.con.execute("SELECT coalesce(whip_group, party_id) FROM ref_parties WHERE party_id = ?",
                               [party_id]).fetchone()
    rows = people.con.execute(f"""
        SELECT d.date, d.house, d.number, d.major_heading, d.minor_heading, d.ayes, d.noes,
               count(*) FILTER (WHERE pv.vote = 'aye'), count(*) FILTER (WHERE pv.vote = 'no'),
               d.official_url, d.twfy_url, d.division_id
        FROM party_votes pv JOIN divisions d ON d.division_id = pv.division_id
        WHERE pv.whip_group = ? AND ({score}) >= {need}
        GROUP BY ALL ORDER BY d.date DESC, d.number DESC LIMIT {int(limit)}""",
                             [group[0] if group else party_id, *params]).fetchall()
    return [{"kind": "voted", "date": r[0].isoformat(), "house": r[1], "number": r[2],
             "title": " — ".join(x for x in (r[3], r[4]) if x), "ayes": r[5], "noes": r[6],
             "party_ayes": r[7], "party_noes": r[8], "url": r[9] or r[10], "division_id": r[11]} for r in rows]


def timeline(engine: Engine, people: People, topic: str, *, person_id: str | None = None,
             party_id: str | None = None, limit: int = 30) -> dict[str, Any]:
    """Contributions and votes on ``topic`` by one member or one party, newest first."""
    if bool(person_id) == bool(party_id):
        raise ValueError("give a member or a party")
    topic = " ".join(topic.split())[:200]
    filters = Filters(person_ids=[person_id]) if person_id else Filters(parties=[party_id])
    # no preference for recent records or office-holders: a timeline should reach back as far as the record does
    res = engine.search(topic, filters, Scope(), [], recency=0.0, role_weight=1.0, limit=limit,
                        diversify=bool(party_id))  # a party's timeline spreads across its members
    recs = res.records
    if recs:
        best = max(r["ranking"]["relevance"] for r in recs)
        recs = [r for r in recs if r["ranking"]["relevance"] >= best * RELEVANCE_FLOOR]
    said = [_record_item(r) for r in recs]
    votes = member_votes(people, person_id, topic) if person_id else party_votes(people, party_id, topic)
    vote_types(people.settings).annotate(votes)
    items = sorted(said + votes, key=lambda x: (x["date"], x["kind"] == "voted"), reverse=True)  # newest first
    summary = None
    if person_id and votes:  # a member's votes on the topic, in brief (procedural votes counted separately)
        summary = {"aye": sum(1 for v in votes if v.get("vote") == "aye"),
                   "no": sum(1 for v in votes if v.get("vote") == "no"),
                   "against_party": sum(1 for v in votes if v.get("against_party")),
                   "procedural": sum(1 for v in votes if (v.get("vote_type") or {}).get("kind") in PROCEDURAL)}
    return {"topic": topic, "items": items, "said": len(said), "votes": len(votes), "vote_summary": summary,
            "note": "The most relevant contributions found, and the votes whose titles match the topic, newest "
                    "first. Each is shown as recorded; read them in full before drawing conclusions."}
