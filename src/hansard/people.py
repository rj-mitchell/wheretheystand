"""MP and peer pages: who someone is, the seats and roles they have held, their majorities, recent contributions
and voting record, and "Your MP" by postcode.

Everything is computed from the same reference tables and records as the briefings. Voting summaries use the
rebellion definition in ``votes.py``: voting against the majority of one's own party in that division.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import date, timedelta
from typing import Any

import httpx

from hansard.config import Settings
from hansard.debates import debate_path
from hansard.reference.build import office_display
from hansard.reference.profiles import ProfileResolver, attach_reference
from hansard.sources.demographics import USER_AGENT, age_on, assumed_birth
from hansard.sources.elections import election_label, majority_band
from hansard.store.parquet import attach_store
from hansard.store.shared import shared
from hansard.vote_types import vote_types

log = logging.getLogger(__name__)

PREFIX = "uk.org.publicwhip/person/"
POSTCODE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$")
OPEN_END = date(9999, 12, 31)


def person_number(person_id: str) -> int | None:
    return int(person_id.rsplit("/", 1)[1]) if person_id and person_id.startswith(PREFIX) else None


def members_by_parliament_id(con: Any, pairs: list[tuple[int, str | None]]
                             ) -> dict[tuple[int, str | None], dict[str, Any]]:
    """Members by UK Parliament ID, each with their party on a given date (or latest): for early day motions.
    ``con`` is a DuckDB connection with the reference tables attached."""
    if not pairs:
        return {}
    rows = con.execute("""
        WITH p(mnis, on_date) AS (SELECT * FROM (VALUES {values}))
        SELECT p.mnis, p.on_date, r.person_id, r.latest_name,
               (SELECT arg_max(m.party_name, m.start) FROM ref_memberships m WHERE m.person_id = r.person_id
                  AND coalesce(try_cast(p.on_date AS DATE), current_date) BETWEEN m.start AND m."end") AS party
        FROM p JOIN ref_persons r ON r.datadotparl_id = CAST(p.mnis AS VARCHAR)""".format(
        values=", ".join("(?, ?)" for _ in pairs)), [x for pair in pairs for x in pair]).fetchall()
    return {(mnis, on): {"number": person_number(pid), "slug": slug(name), "name": name, "party": party}
            for mnis, on, pid, name, party in rows}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _iso(d: date | None) -> str | None:
    return None if d is None or d == OPEN_END else d.isoformat()


# Contributions too slight to list as something a member "said": a request to intervene, a point of order on its own,
# a formal motion. Short only: an intervention that goes on to ask a real question is kept.
TRIVIAL_PATTERN = (r"give way|giving way|point of order|i beg to (move|present)|hear,? hear|indicated (assent|dissent)|"
                   r"rose\s*[-—]|^order[.!]|^(yes|no|thank you)[.!]?$|^i (will|shall) give way")
TRIVIAL_SQL = "length(trim(text)) < 40 OR (length(trim(text)) < 110 AND regexp_matches(lower(trim(text)), ?))"
# subjects that say nothing about what a member cares about: the routine of the House
ROUTINE_SUBJECTS = (r"^(engagements|topical questions|business of the house|points? of order|prime minister|"
                    r"oral answers|speaker'?s statement|bills? presented|deferred divisions?|business without debate|"
                    r"royal assent|petitions?|observations|arrangement of business|business|"
                    r"(the )?(lord|deputy) speaker|introduction.*|oaths and affirmations|members sworn|"
                    r"(urgent )?question|written statements?|house of lords|members:? correspondence|"
                    r"members'? (correspondence|enquiries))$")


class People:
    def __init__(self, settings: Settings, resolver: ProfileResolver, today: date | None = None):
        self.settings = settings
        self.resolver = resolver
        self.today = today
        self.con = shared()
        attach_reference(self.con, settings.reference_dir)
        self.stores = set(attach_store(self.con, settings.parquet_dir))
        self._lock = threading.Lock()
        self._votes_version: Any = None

    # ---- identity ----------------------------------------------------------------------------------------------
    def resolve(self, number: int) -> str | None:
        """Canonical person id for a parlparse person number (following redirects)."""
        row = self.con.execute("SELECT person_id FROM ref_person_ids WHERE xml_id = ?", [f"{PREFIX}{number}"]).fetchone()
        return row[0] if row else None

    def _xml_ids(self, person_id: str) -> list[str]:
        return [r[0] for r in self.con.execute("SELECT xml_id FROM ref_person_ids WHERE person_id = ?",
                                               [person_id]).fetchall()]

    def brief(self, person_id: str, on: str | None = None) -> dict[str, Any] | None:
        """Name, page link and party (on ``on``, or latest) for a list entry."""
        with self._lock:
            row = self.con.execute("""
                SELECT r.latest_name,
                       (SELECT arg_max(m.party_name, m.start) FROM ref_memberships m WHERE m.person_id = r.person_id
                          AND coalesce(try_cast(? AS DATE), current_date) BETWEEN m.start AND m."end") AS party,
                       (SELECT arg_max(m.constituency, m.start) FROM ref_memberships m WHERE m.person_id = r.person_id
                          AND m.house = 'commons' AND coalesce(try_cast(? AS DATE), current_date) >= m.start)
                FROM ref_persons r WHERE r.person_id = ?""", [on, on, person_id]).fetchone()
        if row is None:
            return None
        return {"name": row[0], "number": person_number(person_id), "slug": slug(row[0]), "party": row[1],
                "constituency": row[2]}

    # ---- the page ----------------------------------------------------------------------------------------------
    def by_parliament_id(self, pairs: list[tuple[int, str | None]]) -> dict[tuple[int, str | None], dict[str, Any]]:
        return members_by_parliament_id(self.con, pairs)

    def page(self, person_id: str) -> dict[str, Any] | None:
        person = self.con.execute("SELECT latest_name, gender, birth_date, birth_precision, datadotparl_id "
                                  "FROM ref_persons WHERE person_id = ?", [person_id]).fetchone()
        if person is None:
            return None
        name, gender, born, precision, mnis = person
        today = self.today or date.today()
        names = [r[0] for r in self.con.execute(
            "SELECT name FROM ref_names WHERE person_id = ? GROUP BY name ORDER BY min(start)", [person_id]).fetchall()]
        memberships = [
            {"house": h, "constituency": c, "region": reg, "party": party, "start": _iso(s), "end": _iso(e)}
            for h, c, reg, party, s, e in self.con.execute(
                """SELECT house, constituency, region, party_name, start, "end" FROM ref_memberships
                   WHERE person_id = ? ORDER BY start""", [person_id]).fetchall()]
        offices = []
        for kind, role, cid, cname, s, e in self.con.execute(
                """SELECT kind, role, committee_id, committee_name, start, "end" FROM ref_offices
                   WHERE person_id = ? AND kind IN ('gov', 'shadow', 'chair', 'whip', 'parliamentary')
                   ORDER BY start DESC""", [person_id]).fetchall():
            title = office_display({"kind": kind, "role": role, "committee_id": cid, "committee_name": cname}, s)
            offices.append({"kind": kind, "title": title, "start": _iso(s), "end": _iso(e)})
        current = [m for m in memberships if m["end"] is None]
        seats = self._seats(person_id)
        birth = assumed_birth(born, precision)
        return {
            "person_id": person_id,
            "number": person_number(person_id),
            "name": names[-1] if names else name,
            "other_names": names[:-1],
            "slug": slug(names[-1] if names else name),
            "current": current[-1] if current else None,
            "status": self._status(memberships, current),
            "gender": gender,
            "age": age_on(birth, today) if birth else None,
            "age_approximate": precision == "year" if birth else None,
            "parliament_id": mnis,
            "memberships": memberships,
            "offices": offices,
            "seats": seats,
            "first_elected": self.resolver._seat_index()["first"].get(person_id).isoformat()
            if self.resolver._seat_index()["first"].get(person_id) else None,
        }

    @staticmethod
    def _status(memberships: list[dict[str, Any]], current: list[dict[str, Any]]) -> str:
        if current:
            m = current[-1]
            if m["house"] == "commons":
                return f"MP for {m['constituency']}" + (f" ({m['party']})" if m.get("party") else "")
            return "Member of the House of Lords" + (f" ({m['party']})" if m.get("party") else "")
        if memberships:
            last = memberships[-1]
            until = date.fromisoformat(last["end"]) if last.get("end") else None
            when = f" (until {until.day} {until.strftime('%B %Y')})" if until else ""
            return f"Former {'MP' if last['house'] == 'commons' else 'member of the House of Lords'}{when}"
        return "Not a current member"

    def _seats(self, person_id: str) -> list[dict[str, Any]]:
        """Each seat held, with the results of the elections they won there (newest first)."""
        idx = self.resolver._seat_index()
        out = []
        for start, end, cid, cname in reversed(idx["reps"].get(person_id, [])):
            results = []
            first_day, last_day = start.isoformat(), _iso(end) or "9999-12-31"
            for r in reversed(idx["results"].get(cid, [])):
                # the elections they won while holding this seat: a seat's last day is the day of the election that
                # ended it, whose result belongs to whoever held the seat next
                if first_day <= r["date"] < last_day:
                    votes = r.get("votes") or 0
                    pct = round(100 * r["majority"] / votes, 1) if votes and r.get("majority") is not None else None
                    results.append({"election": election_label(r), "date": r["date"], "majority": r["majority"],
                                    "pct": pct, "band": majority_band(pct), "party": r.get("party")})
            out.append({"constituency": cname, "start": start.isoformat(), "end": _iso(end), "results": results})
        return out

    # ---- recent contributions ----------------------------------------------------------------------------------
    def contributions(self, person_id: str, limit: int = 20) -> list[dict[str, Any]]:
        if "records" not in self.stores:
            return []
        xml = self._xml_ids(person_id)
        today = self.today or date.today()
        first, last = self.con.execute(
            'SELECT min(start), max("end") FROM ref_memberships WHERE person_id = ?', [person_id]).fetchone()
        if first is None:
            return []
        # only the years they sat (the record has nothing else of theirs): the last year or two first, then the
        # last five, then all of them. Reading years they were not in Parliament would scan millions of records.
        end_year = min(last, today).year
        start_year = max(first.year, self.settings.archive_start.year)
        rows: list[tuple] = []
        for since in (end_year - 1, end_year - 5, start_year):
            since = max(since, start_year)
            rows = self.con.execute(
                f"""SELECT DISTINCT ON (record_id) record_id, date, chamber, record_type, major_heading, minor_heading,
                           left(text, 320) AS excerpt, twfy_url, official_url, source, bill
                    FROM records WHERE year BETWEEN {int(since)} AND {int(end_year)} AND list_contains(?, xml_person_id)
                      AND NOT ({TRIVIAL_SQL})
                    ORDER BY record_id, date DESC""", [xml, TRIVIAL_PATTERN]).fetchall()
            if len(rows) >= limit or since == start_year:
                break
        rows.sort(key=lambda r: (r[1], r[0]), reverse=True)
        cols = ["record_id", "date", "chamber", "record_type", "major_heading", "minor_heading", "excerpt",
                "twfy_url", "official_url", "source", "bill"]
        out = []
        for r in rows[:limit]:
            d = dict(zip(cols, r, strict=True))
            d["date"] = d["date"].isoformat()
            d["debate_path"] = debate_path(d.pop("source"), d["date"], d["major_heading"], d["minor_heading"],
                                           d.pop("bill"), d["record_id"])
            d["heading"] = " — ".join(x for x in (d.pop("major_heading"), d.pop("minor_heading")) if x)
            out.append(d)
        return out

    def topics(self, person_id: str, limit: int = 6) -> dict[str, Any]:
        """What they have spoken or asked about most in their last two years in Parliament: the subjects (a debate's
        own title, or a question's topic) on the most separate days, leaving out the routine ones (Engagements,
        Business of the House and the like). Counted from the record; no AI."""
        if "records" not in self.stores:
            return {"topics": [], "since": None, "until": None}
        xml = self._xml_ids(person_id)
        today = self.today or date.today()
        first, last = self.con.execute(
            'SELECT min(start), max("end") FROM ref_memberships WHERE person_id = ?', [person_id]).fetchone()
        if first is None:
            return {"topics": [], "since": None, "until": None}
        until = min(last, today)
        since = max(until - timedelta(days=730), first, self.settings.archive_start)
        rows = self.con.execute(
            f"""SELECT DISTINCT subject, date FROM (
                    SELECT date, trim(coalesce(nullif(minor_heading, ''), major_heading)) AS subject
                    FROM records WHERE year BETWEEN {int(since.year)} AND {int(until.year)} AND date >= ?
                      AND list_contains(?, xml_person_id) AND NOT ({TRIVIAL_SQL}))
                WHERE subject IS NOT NULL AND subject <> '' AND NOT regexp_matches(lower(subject), ?)""",
            [since, xml, TRIVIAL_PATTERN, ROUTINE_SUBJECTS]).fetchall()
        # "Air Pollution: Yeovil" from the MP for Yeovil is the same subject as "Air Pollution"
        seats = {c.lower() for (c,) in self.con.execute(
            "SELECT DISTINCT constituency FROM ref_memberships WHERE person_id = ? AND constituency IS NOT NULL",
            [person_id]).fetchall()}
        days: dict[str, set] = {}
        for subject, day in rows:
            head, _, tail = subject.rpartition(": ")
            days.setdefault(head if head and tail.lower() in seats else subject, set()).add(day)
        ranked = sorted(((s, len(d), max(d)) for s, d in days.items() if len(d) >= 2), key=lambda x: (-x[1], -x[2].toordinal()))
        return {"topics": [{"subject": s, "days": n} for s, n, _ in ranked[:limit]],
                "since": since.isoformat(), "until": until.isoformat()}

    # ---- voting record -----------------------------------------------------------------------------------------
    def _ensure_votes(self) -> bool:
        if not {"votes", "divisions"} <= self.stores:
            return False
        version = self.con.execute("SELECT count(*), max(date) FROM divisions").fetchone()
        with self._lock:
            if version != self._votes_version:
                # every member's vote with their whip group on the day, and each group's majority (about 2-3 s)
                self.con.execute("""
                    CREATE OR REPLACE TABLE party_votes AS
                    WITH v AS (
                      SELECT v.division_id, v.date, v.vote, coalesce(pid.person_id, v.xml_person_id) AS person_id
                      FROM votes v LEFT JOIN ref_person_ids pid ON pid.xml_id = v.xml_person_id
                      WHERE v.vote IN ('aye', 'no')),
                    m AS (
                      SELECT v.division_id, v.date, v.person_id, v.vote, arg_max(mm.party_id, mm.start) AS party_id
                      FROM v LEFT JOIN ref_memberships mm
                        ON mm.person_id = v.person_id AND v.date BETWEEN mm.start AND mm."end"
                      GROUP BY ALL)
                    SELECT m.*, p.whip_group FROM m LEFT JOIN ref_parties p ON p.party_id = m.party_id""")
                self.con.execute("""
                    CREATE OR REPLACE TABLE party_majorities AS
                    SELECT division_id, whip_group, mode(vote) AS majority, count(*) AS n
                    FROM party_votes WHERE whip_group IS NOT NULL GROUP BY ALL""")
                self._votes_version = version
        return True

    def voting(self, person_id: str, recent: int = 15) -> dict[str, Any] | None:
        if not self._ensure_votes():
            return None
        total, first, last = self.con.execute("""
            SELECT count(*), min(pv.date), max(pv.date) FROM party_votes pv WHERE pv.person_id = ?""",
                                              [person_id]).fetchone()
        rows = self.con.execute("""
            SELECT d.date, d.house, d.number, d.major_heading, d.minor_heading, pv.vote, pm.majority, d.twfy_url,
                   d.official_url, d.division_id, pv.whip_group
            FROM party_votes pv JOIN party_majorities pm USING (division_id, whip_group)
            JOIN divisions d ON d.division_id = pv.division_id
            WHERE pv.person_id = ? AND pm.n >= 2 AND pv.vote <> pm.majority
            ORDER BY d.date DESC, d.number DESC""", [person_id]).fetchall()
        all_rebellions = [{
            "date": r[0].isoformat(), "house": r[1], "number": r[2],
            "title": " — ".join(x for x in (r[3], r[4]) if x), "vote": r[5], "party_majority": r[6],
            "twfy_url": r[7], "official_url": r[8], "division_id": r[9], "whip_group": r[10]} for r in rows]
        vote_types(self.settings).annotate(all_rebellions)
        # voting differently from colleagues in a reported free vote breaks no whip: not a rebellion
        from hansard.votes import free_parties, party_key

        real = [r for r in all_rebellions if party_key(r.pop("whip_group")) not in free_parties(r.get("vote_type"))]
        free_votes = len(all_rebellions) - len(real)
        rebellions, recent_rebellions = len(real), real[:recent]
        return {"votes": total, "rebellions": rebellions, "free_vote_differences": free_votes,
                "since": first.isoformat() if first else None,
                "until": last.isoformat() if last else None, "recent_rebellions": recent_rebellions,
                "method": "A rebellion is a vote against the majority of the member's own party in that division, "
                          "using their party on the day. Some divisions are free votes, where parties set no line, so "
                          "voting differently from colleagues there breaks no whip. Members without a party whip are "
                          "not counted, and absences are not recorded."}

    # ---- finding people ----------------------------------------------------------------------------------------
    def search(self, text: str = "", current_only: bool = True, limit: int = 2000) -> list[dict[str, Any]]:
        """Members for the people index: current MPs and peers (or anyone since the archive starts), optionally by
        name or seat. Every word typed must appear in a name they have used or a seat they have held ("ed balls"
        finds Edward Balls)."""
        where = ['m."end" >= ?' if not current_only else 'm."end" = DATE \'9999-12-31\'']
        params: list[Any] = [] if current_only else [self.settings.archive_start]
        for word in text.lower().split()[:6]:
            where.append("(lower(n.name) LIKE ? OR lower(coalesce(m.constituency, '')) LIKE ?)")
            params += [f"%{word}%", f"%{word}%"]
        rows = self.con.execute(f"""
            SELECT m.person_id, arg_max(m.house, m.start), arg_max(m.constituency, m.start), arg_max(m.party_name, m.start),
                   (SELECT arg_max(n2.name, n2.start) FROM ref_names n2 WHERE n2.person_id = m.person_id) AS name,
                   (SELECT max(m2."end") FROM ref_memberships m2 WHERE m2.person_id = m.person_id) AS last_end
            FROM ref_memberships m JOIN ref_names n ON n.person_id = m.person_id
            WHERE {' AND '.join(where)} GROUP BY m.person_id ORDER BY name LIMIT {int(limit)}""", params).fetchall()
        return [{"number": person_number(pid), "name": name, "slug": slug(name), "house": house,
                 "constituency": cons, "party": party, "current": last_end == OPEN_END,
                 "until": None if last_end == OPEN_END else last_end.isoformat()}
                for pid, house, cons, party, name, last_end in rows if person_number(pid)]

    def by_postcode(self, postcode: str, client: httpx.Client | None = None) -> dict[str, Any] | None:
        """The current MP for a postcode, via the UK Parliament Members API."""
        compact = re.sub(r"\s+", "", postcode.upper())
        pc = f"{compact[:-3]} {compact[-3:]}" if len(compact) >= 5 else compact
        if not POSTCODE.match(pc):
            raise ValueError("That does not look like a UK postcode.")
        own = client is None
        client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=15)
        try:
            body = client.get("https://members-api.parliament.uk/api/Location/Constituency/Search",
                              params={"searchText": pc, "take": 1}).json()
        finally:
            if own:
                client.close()
        items = body.get("items") or []
        if not items:
            return None
        v = items[0]["value"]
        member = ((v.get("currentRepresentation") or {}).get("member") or {}).get("value") or {}
        row = self.con.execute("SELECT person_id FROM ref_persons WHERE datadotparl_id = ?",
                               [str(member.get("id"))]).fetchone() if member.get("id") else None
        pid = row[0] if row else None
        name = member.get("nameDisplayAs")
        return {"postcode": pc, "constituency": v.get("name"), "name": name,
                "number": person_number(pid) if pid else None, "slug": slug(name or "")}
