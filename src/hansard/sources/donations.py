"""Political donations to MPs, from the Electoral Commission's register of donations.

Two kinds are linked to MPs:

* **Donations to the MP.** The Commission lists the recipient by name and type ("MP - Member of Parliament",
  "Leadership Candidate", "Member of Registered Political Party"), with its own ID for the recipient but no
  Parliament ID. Each recipient is matched to a member by name, only among the MPs sitting on the date of each
  donation (with the member's names as they were then), and only when exactly one MP fits. Every donation to that
  recipient must point to the same member, or none is linked. A recipient registered as an MP may also be matched
  by surname alone, when only one sitting MP had that surname. Leadership candidates and party members need their
  first name too.
* **Donations to the MP's local party.** A party's local accounting unit is linked when its name is exactly one
  constituency's name (party words such as "CLP" or "Conservative Association" aside), and the seat's MP on that
  date was from the same party. These are shown as donations to the local party, never as donations to the MP.

Donors' addresses and postcodes are left out. The register is published by the Electoral Commission; everything
here can be checked against its own page for each donation, which every item links to.

Kept in data/donations.sqlite, rebuilt in full when the register is refreshed (weekly).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import sqlite3
import threading
import unicodedata
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from hansard.sources.demographics import USER_AGENT

log = logging.getLogger(__name__)

CSV_URL = "https://search.electoralcommission.org.uk/api/csv/Donations"
DONATION_URL = "https://search.electoralcommission.org.uk/English/Donations/{ref}"
SOURCE = "Electoral Commission, register of donations"
REFRESH = timedelta(days=7)
OPT_COLS = ["Register", "RegulatedDoneeType", "CompanyRegistrationNumber", "NatureOfDonation", "PurposeOfVisit",
            "DonationAction", "ReportedDate", "IsBequest", "IsAggregation", "AccountingUnitsAsCentralParty",
            "IsSponsorship", "ReportingPeriodName"]
SLACK = timedelta(days=60)  # a donation reported around an election, or just before a member took their seat

# Recipient types that can be a sitting MP, and how strict the name match must be
KINDS = {"MP - Member of Parliament": "mp", "Leadership Candidate": "leadership",
         "Member of Registered Political Party": "party_member"}

_TITLES = {"rt", "hon", "the", "right", "honourable", "mp", "sir", "dame", "dr", "mr", "mrs", "ms", "miss", "mx",
           "professor", "prof", "cbe", "obe", "mbe", "kbe", "dbe", "cb", "kc", "qc", "pc", "frs", "lord", "baroness",
           "lady", "rev", "revd", "reverend", "cllr", "councillor", "msp", "jp", "dl", "phd", "frcs", "rn", "lt",
           "col", "major", "captain", "general"}
# common short forms of first names (both directions are checked)
_NICK = {
    "alex": {"alexander", "alexandra", "alexandria"}, "andy": {"andrew"}, "tony": {"anthony", "antony"},
    "tom": {"thomas"}, "bob": {"robert"}, "rob": {"robert"}, "robbie": {"robert"}, "bill": {"william"},
    "will": {"william"}, "jim": {"james"}, "jamie": {"james"}, "nick": {"nicholas", "nicolas"},
    "chris": {"christopher", "christine", "christina"}, "dave": {"david"}, "mike": {"michael"},
    "steve": {"stephen", "steven"}, "pat": {"patrick", "patricia"}, "liz": {"elizabeth"}, "beth": {"elizabeth"},
    "kate": {"katherine", "catherine", "kathryn"}, "kath": {"katherine", "catherine", "kathryn"},
    "jo": {"joanne", "joanna", "josephine", "joseph", "jonathan"}, "sam": {"samuel", "samantha"}, "matt": {"matthew"},
    "ed": {"edward"}, "ted": {"edward"}, "dan": {"daniel"}, "danny": {"daniel"}, "ben": {"benjamin"},
    "jon": {"jonathan"}, "jack": {"john"}, "johnny": {"john"}, "tim": {"timothy"}, "jeff": {"geoffrey"},
    "geoff": {"geoffrey"}, "greg": {"gregory"}, "ken": {"kenneth"}, "ron": {"ronald"}, "phil": {"philip", "phillip"},
    "nicky": {"nicola", "nicholas"}, "becky": {"rebecca"}, "maggie": {"margaret"}, "meg": {"margaret"},
    "sue": {"susan"}, "debbie": {"deborah"}, "jenny": {"jennifer"}, "vicky": {"victoria"}, "di": {"diana", "diane"},
    "toby": {"tobias"}, "charlie": {"charles"}, "olly": {"oliver"}, "ollie": {"oliver"}, "fred": {"frederick"},
    "harry": {"henry", "harold"}, "hal": {"henry"}, "jules": {"julian", "julia"},
    "les": {"leslie"}, "ray": {"raymond"}, "rosie": {"rosemary", "rosalind"}, "rick": {"richard"},
    "rich": {"richard"}, "dick": {"richard"}, "stu": {"stuart", "stewart"}, "wes": {"wesley"},
    "pete": {"peter"}, "gerry": {"gerald", "gerard"}, "angie": {"angela"}, "cat": {"catherine"},
    "kev": {"kevin"}, "mick": {"michael"}, "sandy": {"alexander", "sandra"}, "josh": {"joshua"},
    "zac": {"zachary"}, "abi": {"abigail"}, "ali": {"alison", "alistair", "alasdair"}, "al": {"alan", "alistair"},
}

# EC party names -> parlparse party ids
_PARTIES = [
    (re.compile(r"^conservative"), {"conservative"}),
    (re.compile(r"^labour party$"), {"labour", "labourco-operative"}),
    (re.compile(r"^co-operative party$"), {"labourco-operative"}),
    (re.compile(r"^liberal democrat"), {"liberal-democrat"}),
    (re.compile(r"^scottish national"), {"scottish-national-party"}),
    (re.compile(r"^plaid"), {"plaid-cymru"}),
    (re.compile(r"^green party$"), {"green"}),
    (re.compile(r"^reform uk"), {"reform"}),
    (re.compile(r"^democratic unionist"), {"dup"}),
    (re.compile(r"^sinn f"), {"sinn-fein"}),
    (re.compile(r"^sdlp|^social democratic"), {"social-democratic-and-labour-party"}),
    (re.compile(r"^alliance - alliance party"), {"alliance"}),
    (re.compile(r"^ulster unionist"), {"uup"}),
    (re.compile(r"^uk independence party"), {"ukip"}),
    (re.compile(r"^alba"), {"alba"}),
    (re.compile(r"^traditional unionist"), {"traditional-unionist-voice"}),
    (re.compile(r"^respect"), {"respect"}),
]
# words around a constituency's name in the name of a local party
_UNIT_WORDS = {"clp", "constituency", "labour", "party", "conservative", "conservatives", "association", "unionist",
               "liberal", "democrats", "democrat", "lib", "dems", "local", "green", "greens", "snp", "branch",
               "co-operative", "cooperative", "plaid", "cymru", "reform", "uk", "ukip", "federation", "group",
               "and", "the", "of", "&"}

_SCHEMA = """
CREATE TABLE donations (
  ec_ref TEXT PRIMARY KEY, kind TEXT, person_id TEXT, match TEXT, recipient TEXT, recipient_id INTEGER,
  recipient_type TEXT, unit TEXT, constituency TEXT, donor TEXT, donor_status TEXT, company_number TEXT,
  value REAL, donation_type TEXT, nature TEXT, purpose TEXT, accepted TEXT, reported TEXT, period TEXT,
  sponsorship INTEGER, action TEXT, register TEXT);
CREATE INDEX donations_person ON donations (person_id, accepted);
CREATE INDEX donations_donor ON donations (donor);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


# --------------------------------------------------------------------------- reading the register


def _money(v: str | None) -> float | None:
    v = re.sub(r"[£,\s]", "", v or "")
    try:
        return float(v)
    except ValueError:
        return None


def _day(v: str | None) -> str | None:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", v or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _flag(v: str | None) -> bool:
    return (v or "").strip().lower() == "true"


def parse_csv(text: str) -> list[dict[str, Any]]:
    """The Commission's CSV export -> plain rows (donors' addresses and postcodes left out)."""
    out = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        if not r.get("ECRef"):
            continue
        out.append({
            "ec_ref": r["ECRef"], "recipient": " ".join((r.get("RegulatedEntityName") or "").split()),
            "recipient_id": int(r["RegulatedEntityId"]) if (r.get("RegulatedEntityId") or "").isdigit() else None,
            "entity_type": r.get("RegulatedEntityType") or "", "donee_type": r.get("RegulatedDoneeType") or "",
            "unit": " ".join((r.get("AccountingUnitName") or "").split()) or None,
            "central": _flag(r.get("AccountingUnitsAsCentralParty")),
            "donor": " ".join((r.get("DonorName") or "").split()) or None, "donor_status": r.get("DonorStatus") or None,
            "company_number": r.get("CompanyRegistrationNumber") or None, "value": _money(r.get("Value")),
            "donation_type": r.get("DonationType") or None, "nature": r.get("NatureOfDonation") or None,
            "purpose": r.get("PurposeOfVisit") or None,
            "accepted": _day(r.get("AcceptedDate")) or _day(r.get("ReceivedDate")) or _day(r.get("ReportedDate")),
            "reported": _day(r.get("ReportedDate")), "period": r.get("ReportingPeriodName") or None,
            "sponsorship": _flag(r.get("IsSponsorship")), "action": r.get("DonationAction") or None,
            "register": r.get("RegisterName") or None,
        })
    return out


def fetch(client: httpx.Client, entity_type: str) -> list[dict[str, Any]]:
    """Every donation to one kind of recipient ("rd": named people and groups, "pp": parties), in one download."""
    params: list[tuple[str, str]] = [("start", "0"), ("rows", "50"), ("query", ""), ("sort", "AcceptedDate"),
                                     ("order", "desc"), ("et", entity_type), ("register", "gb"),
                                     ("register", "ni"), ("regStatus", "all")]
    params += [("optCols", c) for c in OPT_COLS]
    resp = client.get(CSV_URL, params=params)
    resp.raise_for_status()
    return parse_csv(resp.text)


# --------------------------------------------------------------------------- matching


def name_tokens(name: str | None) -> list[str]:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("’", "'")
    s = re.sub(r"\([^)]*\)", " ", s)
    words = [w.strip("'.") for w in re.findall(r"[a-z][a-z'.]*", s.replace("-", " "))]  # "Long-Bailey" = "Long Bailey"
    return [w for w in words if w and w.replace(".", "") not in _TITLES]


def _one_slip(a: str, b: str) -> bool:
    """Spelt the same but for one letter added, dropped or changed ("Ottoway" for "Ottaway"); long names only."""
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _same_first(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    return b in _NICK.get(a, set()) or a in _NICK.get(b, set())


def _place_key(name: str | None) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", s.replace("-", " ")))


def unit_key(unit: str | None) -> str:
    """'Stoke-On-Trent North CLP' -> 'stoke on trent north' (party words at either end removed)."""
    words = _place_key(unit).split()
    while words and words[-1] in _UNIT_WORDS:
        words.pop()
    while words and words[0] in _UNIT_WORDS:
        words.pop(0)
    return " ".join(words)


def party_ids(ec_party: str | None) -> set[str]:
    p = (ec_party or "").lower().strip()
    for pattern, ids in _PARTIES:
        if pattern.search(p):
            return ids
    return set()


@dataclass
class Spell:
    """One MP's time in one seat for one party, with the names they used then."""
    person_id: str
    start: date
    end: date
    party_id: str | None
    constituency: str | None
    names: list[list[str]] = field(default_factory=list)


def mp_spells(con: Any) -> list[Spell]:
    """Commons memberships since 2001 with each member's names in that time (DuckDB, reference attached)."""
    rows = con.execute("""
        SELECT m.person_id, m.start, m."end", m.party_id, m.constituency, list(DISTINCT n.name)
        FROM ref_memberships m LEFT JOIN ref_names n ON n.person_id = m.person_id
             AND n.start <= m."end" AND n."end" >= m.start
        WHERE m.house = 'commons' AND m."end" >= DATE '2001-01-01'
        GROUP BY ALL""").fetchall()
    return [Spell(pid, start, end, party, seat, [name_tokens(n) for n in names or [] if n])
            for pid, start, end, party, seat, names in rows]


class Matcher:
    def __init__(self, spells: list[Spell]):
        self.spells = spells
        self._by_year: dict[int, list[Spell]] = defaultdict(list)
        for s in spells:
            lo = max(s.start - SLACK, date(2000, 1, 1)).year
            hi = min(s.end, date.today()).year + 1
            for y in range(lo, hi + 1):
                self._by_year[y].append(s)
        self._seats: dict[tuple[str, int], list[Spell]] = defaultdict(list)
        for s in spells:
            for y in range(max(s.start.year, 2000), min(s.end, date.today()).year + 1):
                self._seats[(_place_key(s.constituency), y)].append(s)

    def sitting(self, on: date, slack: timedelta = SLACK) -> list[Spell]:
        return [s for s in self._by_year.get(on.year, []) if s.start - slack <= on and (s.end.year > 9000 or on <= s.end + slack)]

    def person(self, recipient: str, on: date, kind: str) -> tuple[str, str] | None:
        """(person_id, how) for the one MP sitting on ``on`` whose name fits; None if none or several do."""
        want = name_tokens(recipient)
        if not want:
            return None
        levels: list[tuple[str, Any]] = [
            ("full name", lambda n: n == want),
            ("first and last name", lambda n: len(n) >= 2 and len(want) >= 2 and n[0] == want[0] and n[-1] == want[-1]),
            ("first and last name (short form)", lambda n: len(n) >= 2 and len(want) >= 2 and n[-1] == want[-1]
             and _same_first(n[0], want[0])),
        ]
        if kind == "mp":  # the Commission says the recipient is an MP
            # a one-letter slip in either name, the other matching
            levels.append(("first and last name, one letter different", lambda n: len(n) >= 2 and len(want) >= 2 and (
                (n[-1] == want[-1] and _one_slip(n[0], want[0])) or (_same_first(n[0], want[0])
                                                                       and _one_slip(n[-1], want[-1])))))
            # a surname only one sitting MP had, when the first name (if given) does not say otherwise
            levels.append(("surname and first initial, unique among sitting MPs", lambda n: n[-1] == want[-1] and (
                len(want) == 1 or n[0][0] == want[0][0])))
        sitting = self.sitting(on)
        for how, fits in levels:
            people = {s.person_id for s in sitting if any(fits(n) for n in s.names if n)}
            if len(people) == 1:
                return people.pop(), how
            if len(people) > 1:
                return None  # several MPs fit equally well: leave it out
        return None

    def seat_mp(self, unit: str | None, ec_party: str | None, on: date) -> Spell | None:
        """The MP of the constituency a local party is named after, when they were of that party on that date."""
        key, ids = unit_key(unit), party_ids(ec_party)
        if not key or not ids:
            return None
        spells = [s for s in self._seats.get((key, on.year), []) if s.start <= on <= s.end]
        people = {s.person_id for s in spells}
        if len(people) != 1 or spells[0].party_id not in ids:
            return None
        return spells[0]


def match_all(rd: list[dict[str, Any]], pp: list[dict[str, Any]], matcher: Matcher
              ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows to store (each with kind, person_id and how it was matched) and counts for the methodology."""
    out: list[dict[str, Any]] = []
    stats: dict[str, Any] = defaultdict(int)
    by_recipient: dict[tuple[int | None, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rd:
        kind = KINDS.get(r["donee_type"])
        if kind and r["accepted"]:
            by_recipient[(r["recipient_id"], r["recipient"], kind)].append(r)
    for (_, recipient, kind), rows in by_recipient.items():
        found = [matcher.person(recipient, date.fromisoformat(r["accepted"]), kind) for r in rows]
        people = {f[0] for f in found if f}
        stats[f"{kind}_donations"] += len(rows)
        if len(people) == 1:
            pid = people.pop()
            for r, f in zip(rows, found, strict=True):
                if f:  # donations from outside the member's time as an MP are left out
                    out.append({**r, "kind": kind, "person_id": pid, "match": f[1]})
                    stats[f"{kind}_matched"] += 1
                elif kind == "mp":
                    out.append({**r, "kind": kind, "person_id": None, "match": None})
        else:
            stats[f"{kind}_conflicting" if people else f"{kind}_unmatched"] += len(rows)
            if kind == "mp":  # kept, unlinked, so the methodology can count them
                out += [{**r, "kind": kind, "person_id": None, "match": None} for r in rows]
    for r in pp:
        if not r["unit"] or r["central"] or not r["accepted"]:
            continue
        stats["local_party_donations"] += 1
        mp = matcher.seat_mp(r["unit"], r["recipient"], date.fromisoformat(r["accepted"]))
        if mp:
            stats["local_party_matched"] += 1
            out.append({**r, "kind": "local_party", "person_id": mp.person_id, "constituency": mp.constituency,
                        "match": "local party named after the MP's constituency, same party"})
    return out, dict(stats)


# --------------------------------------------------------------------------- storing and reading


def write(path: Path, rows: list[dict[str, Any]], stats: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(_SCHEMA)
        con.executemany("INSERT OR REPLACE INTO donations VALUES "
                        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                            (r["ec_ref"], r["kind"], r["person_id"], r["match"], r["recipient"], r["recipient_id"],
                             r["donee_type"] or r["entity_type"], r["unit"], r.get("constituency"), r["donor"],
                             r["donor_status"], r["company_number"], r["value"], r["donation_type"], r["nature"],
                             r["purpose"], r["accepted"], r["reported"], r["period"], int(r["sponsorship"]),
                             r["action"], r["register"]) for r in rows])
        con.executemany("INSERT INTO meta VALUES (?, ?)", [
            ("built", datetime.now(UTC).isoformat(timespec="seconds")), ("stats", json.dumps(stats))])
        con.commit()
    finally:
        con.close()
    tmp.replace(path)


def refresh(path: Path, con: Any, *, client: httpx.Client | None = None, force: bool = False) -> dict[str, Any]:
    """Download the register (two files, about 25 MB) and rebuild the matches; weekly unless ``force``."""
    path = Path(path)
    if not force and path.exists():
        built = Donations(path).meta().get("built")
        if built and datetime.now(UTC) - datetime.fromisoformat(built) < REFRESH:
            return {"updated": False, "built": built}
    own = client is None
    client = client or httpx.Client(timeout=600, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    try:
        rd, pp = fetch(client, "rd"), fetch(client, "pp")
    finally:
        if own:
            client.close()
    if not rd or not pp:
        raise ValueError("the Electoral Commission returned no donations")
    rows, stats = match_all(rd, pp, Matcher(mp_spells(con)))
    write(path, rows, stats)
    return {"updated": True, **stats}


class Donations:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()

    @contextmanager
    def connect(self):
        if not self.path.exists():
            yield None
            return
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()

    def meta(self) -> dict[str, Any]:
        with self.connect() as con:
            if con is None:
                return {}
            out = dict(con.execute("SELECT key, value FROM meta").fetchall())
        out["stats"] = json.loads(out.get("stats") or "{}")
        return out

    @staticmethod
    def _item(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["sponsorship"] = bool(d["sponsorship"])
        d["url"] = DONATION_URL.format(ref=d["ec_ref"])
        return d

    def for_person(self, person_id: str, limit: int = 200) -> dict[str, Any] | None:
        with self.connect() as con:
            if con is None:
                return None
            rows = con.execute("""SELECT * FROM donations WHERE person_id = ? ORDER BY accepted DESC""",
                               (person_id,)).fetchall()
        if not rows:
            return {"personal": [], "local": [], "totals": {}, "source": SOURCE}
        personal = [self._item(r) for r in rows if r["kind"] != "local_party"]
        local = [self._item(r) for r in rows if r["kind"] == "local_party"]
        totals = {"personal": round(sum(r["value"] or 0 for r in personal), 2), "personal_count": len(personal),
                  "local": round(sum(r["value"] or 0 for r in local), 2), "local_count": len(local)}
        donors: dict[str, float] = defaultdict(float)
        for r in personal:
            donors[r["donor"] or "Unknown"] += r["value"] or 0
        top = sorted(donors.items(), key=lambda x: -x[1])[:5]
        return {"personal": personal[:limit], "local": local[:limit], "totals": totals, "source": SOURCE,
                "top_donors": [{"donor": d, "value": round(v, 2)} for d, v in top]}

    def index(self, limit: int = 40) -> dict[str, Any]:
        """Biggest donors to MPs (personally) and the latest donations, for the donations page."""
        with self.connect() as con:
            if con is None:
                return {"available": False}
            top = con.execute("""
                SELECT donor, donor_status, round(sum(value), 2) AS total, count(*) AS n,
                       count(DISTINCT person_id) AS mps
                FROM donations WHERE person_id IS NOT NULL AND kind != 'local_party' AND accepted >= ?
                GROUP BY donor, donor_status ORDER BY total DESC LIMIT ?""",
                              ((date.today() - timedelta(days=365 * 5)).isoformat(), limit)).fetchall()
            recent = con.execute("""SELECT * FROM donations WHERE person_id IS NOT NULL AND kind != 'local_party'
                                    ORDER BY accepted DESC, ec_ref LIMIT ?""", (limit,)).fetchall()
        return {"available": True, "top_donors": [dict(r) for r in top], "recent": [self._item(r) for r in recent],
                **self.meta()}

    def donor(self, name: str, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as con:
            if con is None:
                return []
            rows = con.execute("""SELECT * FROM donations WHERE donor = ? AND person_id IS NOT NULL
                                  ORDER BY accepted DESC LIMIT ?""", (name, limit)).fetchall()
        return [self._item(r) for r in rows]

    def donors(self, q: str, limit: int = 50) -> list[dict[str, Any]]:
        """Donors to MPs (personally or their local party) whose name contains every word of ``q``."""
        words = [w for w in re.findall(r"[\w'&-]+", q.lower()) if len(w) > 1][:6]
        if not words:
            return []
        with self.connect() as con:
            if con is None:
                return []
            rows = con.execute(f"""
                SELECT donor, donor_status, round(sum(value), 2) AS total, count(*) AS n,
                       count(DISTINCT person_id) AS mps, max(accepted) AS last
                FROM donations WHERE person_id IS NOT NULL AND {' AND '.join('lower(donor) LIKE ?' for _ in words)}
                GROUP BY donor, donor_status ORDER BY total DESC LIMIT ?""",
                               [*(f"%{w}%" for w in words), limit]).fetchall()
        return [dict(r) for r in rows]
