"""Select committees: their inquiries, reports, the government's responses, and who gave oral evidence.

From the UK Parliament Committees API (Open Parliament Licence). Rebuilt about once a week into a small SQLite file
(data/committees.sqlite) with a full-text index of witnesses and their organisations, so the site can show each
committee's work and answer "which organisations does Parliament hear from?". About 150 requests, one at a time.
If the API cannot be reached the previous copy is kept.
"""

from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from hansard.sources.demographics import USER_AGENT

log = logging.getLogger(__name__)

API = "https://committees-api.parliament.uk/api"
SITE = "https://committees.parliament.uk"
REFRESH = timedelta(days=6)
PAGE = 500
OWNER_PAGE = 100  # the per-committee lists fail at 500
PAUSE = 0.25
FIRST_YEAR = 2010  # the start of the site's archive
REPORT_TYPES = {1: "Report", 2: "Government response", 12: "Special report"}

_SCHEMA = """
CREATE TABLE committees (id INTEGER PRIMARY KEY, name TEXT, house TEXT, category TEXT, start TEXT, end TEXT,
                         parent_id INTEGER, departments TEXT, purpose TEXT);
CREATE TABLE inquiries (id INTEGER PRIMARY KEY, title TEXT, type TEXT, open TEXT, close TEXT);
CREATE TABLE inquiry_committees (inquiry_id INTEGER, committee_id INTEGER, PRIMARY KEY (inquiry_id, committee_id));
CREATE TABLE publications (id INTEGER PRIMARY KEY, type TEXT, title TEXT, date TEXT, committee_id INTEGER,
                           hc TEXT, hl TEXT, response_to INTEGER, document_id INTEGER);
CREATE TABLE publication_inquiries (publication_id INTEGER, inquiry_id INTEGER,
                                    PRIMARY KEY (publication_id, inquiry_id));
CREATE TABLE sessions (id INTEGER PRIMARY KEY, date TEXT);
CREATE TABLE session_committees (session_id INTEGER, committee_id INTEGER, PRIMARY KEY (session_id, committee_id));
CREATE TABLE session_inquiries (session_id INTEGER, inquiry_id INTEGER, PRIMARY KEY (session_id, inquiry_id));
CREATE TABLE witnesses (session_id INTEGER, name TEXT, organisation TEXT, role TEXT, mnis INTEGER);
CREATE INDEX witnesses_session ON witnesses (session_id);
CREATE INDEX witnesses_org ON witnesses (organisation COLLATE NOCASE);
CREATE INDEX publications_committee ON publications (committee_id, date);
CREATE INDEX inquiry_committees_committee ON inquiry_committees (committee_id);
CREATE INDEX session_committees_committee ON session_committees (committee_id);
CREATE VIRTUAL TABLE witness_text USING fts5(name, organisation, role, content='witnesses');
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE committee_business (committee_id INTEGER, inquiry_id INTEGER, PRIMARY KEY (committee_id, inquiry_id));
"""


def _day(v: Any) -> str | None:
    return str(v)[:10] if v else None


def _plain(text: str | None, limit: int = 600) -> str | None:
    """The first paragraph of an HTML description, as plain text."""
    if not text:
        return None
    first = re.split(r"</p>", text, maxsplit=1, flags=re.I)[0]
    out = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", first)).split())
    out = re.sub(r" ([.,;:!?)])", r"\1", out)  # the space a closing tag left before punctuation
    return out[:limit] if out else None


def _pages(client: httpx.Client, path: str, params: dict[str, Any], sleep: Callable[[float], None],
           take: int = PAGE) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    skip = 0
    while True:
        body = _get(client, path, {**params, "Skip": skip, "Take": take}, sleep)
        items = (body or {}).get("items") or []
        out += items
        skip += take
        total = (body or {}).get("totalResults")
        if len(items) < take or (total is not None and skip >= total) or skip > 100000:
            return out


def _get(client: httpx.Client, path: str, params: dict[str, Any] | None, sleep: Callable[[float], None],
         tries: int = 4) -> Any:
    for attempt in range(tries):
        try:
            resp = client.get(f"{API}{path}", params=params)
            if resp.status_code == 429:
                if attempt == tries - 1:
                    resp.raise_for_status()
                retry = resp.headers.get("Retry-After", "")
                sleep(min(int(retry) if retry.isdigit() else 30 * (attempt + 1), 300))
                continue
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            body = resp.json()
            sleep(PAUSE)
            return body
        except (httpx.HTTPError, ValueError):
            if attempt == tries - 1:
                raise
            sleep(2 + attempt * 3)
    return None


def _previous_purposes(path: Path) -> dict[int, str | None]:
    """Committee remits from the last copy, so a weekly rebuild need not fetch each committee again."""
    if not path.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return {cid: purpose for cid, purpose in con.execute("SELECT id, purpose FROM committees") if purpose}
        finally:
            con.close()
    except sqlite3.Error:
        return {}


def _previous_owners(path: Path) -> tuple[dict[int, set[int]], set[int], str | None]:
    """Which committee each inquiry belongs to, as looked up for the last copy; the committees whose whole list
    was fetched; and when that copy was built."""
    if not path.exists():
        return {}, set(), None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            owners: dict[int, set[int]] = {}
            for cid, iid in con.execute("SELECT committee_id, inquiry_id FROM committee_business"):
                owners.setdefault(cid, set()).add(iid)
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
            return owners, set(json.loads(meta.get("owners_done") or "[]")), meta.get("built")
        finally:
            con.close()
    except (sqlite3.Error, ValueError):
        return {}, set(), None


def _owners(client: httpx.Client, committees: list[dict[str, Any]], path: Path, sleep: Callable[[float], None]
            ) -> tuple[dict[int, set[int]], set[int]]:
    """The inquiry list does not say whose inquiry each is, so ask committee by committee (those sitting since
    2010). A committee's whole list is fetched once; after that, closed committees are not asked again and
    current ones only for recent work. The service is slow and fails on large pages, so pages are small, and a
    committee that fails keeps what was known and is tried again next time."""
    owners, done, built = _previous_owners(path)
    since = (datetime.fromisoformat(built) - timedelta(days=120)).date().isoformat() if built else None
    for c in committees:
        cid = c.get("id")
        if not cid or (_day(c.get("endDate")) or "9999") < f"{FIRST_YEAR}":
            continue
        if c.get("endDate") and cid in done:
            continue
        recent = cid in done and since is not None
        try:
            found = _pages(client, "/CommitteeBusiness", {"CommitteeId": cid, **({"DateFrom": since} if recent else {})},
                           sleep, take=OWNER_PAGE)
        except httpx.HTTPError as e:
            log.warning("committee %s: its inquiries could not be listed (%s); trying again next time", cid, e)
            continue
        owners[cid] = (owners.get(cid, set()) if recent else set()) | {b["id"] for b in found if b.get("id")}
        done.add(cid)
    return owners, done


def build(path: Path, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
          details: bool = True, raw_cache: Path | None = None) -> dict[str, Any]:
    """Fetch everything and write a fresh database (swapped in only when complete).

    ``raw_cache``: keep (and reuse) what was fetched, for working on the code without fetching again."""
    if raw_cache and Path(raw_cache).exists():
        raw = json.loads(Path(raw_cache).read_text())
        return _write(Path(path), raw["committees"], raw["businesses"], raw["pubs"], raw["sessions"],
                      {int(k): v for k, v in raw["purposes"].items()},
                      {int(k): set(v) for k, v in (raw.get("owners") or {}).items()}, set(raw.get("owners_done") or []))
    own = client is None
    client = client or httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    try:
        committees = _pages(client, "/Committees", {"CommitteeStatus": "All"}, sleep)
        businesses = _pages(client, "/CommitteeBusiness", {}, sleep)
        # a year at a time: the service fails when asked to page thousands deep
        pubs, sessions = [], []
        for year in range(FIRST_YEAR, datetime.now(UTC).year + 1):
            window = {"StartDate": f"{year}-01-01", "EndDate": f"{year}-12-31"}
            pubs += _pages(client, "/Publications", {"PublicationTypeIds": list(REPORT_TYPES), **window}, sleep)
            sessions += _pages(client, "/OralEvidence", window, sleep)
        purposes = _previous_purposes(Path(path))
        if details:  # the remit of each current committee, from its own record (only those not known already)
            for c in committees:
                if not c.get("endDate") and c.get("id") and not purposes.get(c["id"]):
                    one = _get(client, f"/Committees/{c['id']}", None, sleep) or {}
                    purposes[c["id"]] = _plain(one.get("purpose"))
        owners, owners_done = _owners(client, committees, Path(path), sleep)
    finally:
        if own:
            client.close()
    if not committees or not sessions:
        raise ValueError("the committees API returned no committees or no evidence sessions")
    if raw_cache:
        Path(raw_cache).write_text(json.dumps({"committees": committees, "businesses": businesses, "pubs": pubs,
                                               "sessions": sessions, "purposes": purposes,
                                               "owners": {k: sorted(v) for k, v in owners.items()},
                                               "owners_done": sorted(owners_done)}))
    return _write(Path(path), committees, businesses, pubs, sessions, purposes, owners, owners_done)


def _write(path: Path, committees: list, businesses: list, pubs: list, sessions: list,
           purposes: dict[int, str | None], owners: dict[int, set[int]] | None = None,
           owners_done: set[int] | None = None) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(_SCHEMA)
        con.executemany("INSERT OR REPLACE INTO committees VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            (c["id"], c.get("name"), c.get("house"), (c.get("category") or {}).get("name"), _day(c.get("startDate")),
             _day(c.get("endDate")), (c.get("parentCommittee") or {}).get("id"),
             "; ".join(d.get("name", "") for d in c.get("scrutinisingDepartments") or []) or None,
             purposes.get(c["id"])) for c in committees if c.get("id")])
        con.executemany("INSERT OR REPLACE INTO inquiries VALUES (?, ?, ?, ?, ?)", [
            (b["id"], b.get("title"), (b.get("type") or {}).get("name"), _day(b.get("openDate")),
             _day(b.get("closeDate"))) for b in businesses if b.get("id")])
        for p in pubs:
            ptype = REPORT_TYPES.get((p.get("type") or {}).get("id"))
            if not ptype or not p.get("id"):
                continue
            docs = p.get("documents") or []
            con.execute("INSERT OR REPLACE INTO publications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                p["id"], ptype, p.get("description"), _day(p.get("publicationStartDate")),
                (p.get("committee") or {}).get("id"), _paper(p.get("hcNumber")), _paper(p.get("hlPaper")),
                p.get("responseToPublicationId"), docs[0].get("documentId") if docs else None))
            for b in p.get("businesses") or []:
                _business(con, b)
                con.execute("INSERT OR IGNORE INTO publication_inquiries VALUES (?, ?)", (p["id"], b["id"]))
                if (p.get("committee") or {}).get("id"):
                    con.execute("INSERT OR IGNORE INTO inquiry_committees VALUES (?, ?)", (b["id"], p["committee"]["id"]))
        known = {c["id"] for c in committees if c.get("id")}
        inquiries = {b["id"] for b in businesses if b.get("id")}
        for cid, iids in (owners or {}).items():
            for iid in iids:
                con.execute("INSERT OR IGNORE INTO committee_business VALUES (?, ?)", (cid, iid))
                if cid in known and iid in inquiries:
                    con.execute("INSERT OR IGNORE INTO inquiry_committees VALUES (?, ?)", (iid, cid))
        n_witnesses = 0
        seen: set[int] = set()
        for s in sessions:
            if not s.get("id") or s["id"] in seen:
                continue
            seen.add(s["id"])
            con.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)", (s["id"], _day(s.get("meetingDate"))))
            cids = [c["id"] for c in s.get("committees") or [] if c.get("id")]
            for cid in cids:
                con.execute("INSERT OR IGNORE INTO session_committees VALUES (?, ?)", (s["id"], cid))
            for b in s.get("committeeBusinesses") or []:
                _business(con, b)
                con.execute("INSERT OR IGNORE INTO session_inquiries VALUES (?, ?)", (s["id"], b["id"]))
                for cid in cids:
                    con.execute("INSERT OR IGNORE INTO inquiry_committees VALUES (?, ?)", (b["id"], cid))
            for w in s.get("witnesses") or []:
                member = w.get("memberInfo") or {}
                orgs = w.get("organisations") or [{}]
                for o in orgs:  # one row per organisation a witness spoke for
                    con.execute("INSERT INTO witnesses VALUES (?, ?, ?, ?, ?)", (
                        s["id"], " ".join((w.get("name") or "").split()) or None,
                        " ".join((o.get("name") or "").split()) or None, o.get("role"), member.get("mnisId")))
                    n_witnesses += 1
        con.execute("INSERT INTO witness_text (witness_text) VALUES ('rebuild')")
        built = datetime.now(UTC).isoformat(timespec="seconds")
        done = sorted(owners_done or set())
        con.executemany("INSERT INTO meta VALUES (?, ?)",
                        [("built", built), ("source", API), ("owners_done", json.dumps(done))])
        con.commit()
    finally:
        con.close()
    tmp.replace(path)
    return {"committees": len(committees), "inquiries": len(businesses), "publications": len(pubs),
            "sessions": len(seen), "witnesses": n_witnesses, "built": built}


def _paper(v: Any) -> str | None:
    """'HC 61' from the API's {"number": "HC 61", "sessionId": …} (or a plain value in older records)."""
    if isinstance(v, dict):
        v = v.get("number")
    return str(v) if v not in (None, "") else None


def _business(con: sqlite3.Connection, b: dict[str, Any]) -> None:
    if b.get("id"):
        con.execute("INSERT OR IGNORE INTO inquiries VALUES (?, ?, ?, ?, ?)", (
            b["id"], b.get("title"), (b.get("type") or {}).get("name"), _day(b.get("openDate")),
            _day(b.get("closeDate"))))


def refresh(path: Path, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
            force: bool = False) -> dict[str, Any]:
    """Rebuild if the copy on disk is more than about a week old."""
    path = Path(path)
    if not force and path.exists():
        age = datetime.now(UTC) - datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if age < REFRESH:
            return {"updated": False, "age_days": age.days}
    return {"updated": True, **build(path, client, sleep)}



# ---- organisations: one name however it is written ---------------------------------------------------------------
_PARTS = re.compile(r"\s*(?:,|;|\bat\b|\band the\b| / )\s*", re.I)


_ABBREVIATION = re.compile(r"[A-Z0-9&.\-]{2,10}")


def _canon(text: str) -> str:
    # an abbreviation in brackets ("British Medical Association (BMA)") is dropped; other words in brackets, such as
    # a country ("Ministry of Defence (The Netherlands)"), tell bodies apart and are kept
    s = re.sub(r"\(\s*[A-Z0-9][A-Za-z0-9&.\- ]{0,11}\)", lambda m: " " if _ABBREVIATION.fullmatch(m.group(0)[1:-1].strip())
               else m.group(0), text)
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\b(?:the|ltd|limited|plc|llp|inc)\b", " ", s)
    s = " ".join(s.split())
    m = re.fullmatch(r"((?:\w+ ){0,2}\w+) university", s)  # "Oxford University" -> "university of oxford"
    if m and " of " not in f" {m.group(1)} ":
        s = f"university of {m.group(1)}"
    return s


def org_key(name: str | None) -> str:
    """The same body however it is written: case, "the", "&" or "and", punctuation and an abbreviation in brackets
    are ignored, "Oxford University" is "University of Oxford", and a department or college given with its
    university ("Nuffield College, University of Oxford") counts as the university."""
    if not name or len(name) > 140 or name.count(",") >= 4:
        return ""  # a whole panel of witnesses written as one "organisation" (older records): not one body
    for part in _PARTS.split(name):
        c = _canon(part)
        if c.startswith("university of ") and len(c.split()) <= 5:
            return c
    return _canon(name)


def group_organisations(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Rows of (organisation, session_id, date) grouped by organisation, shown under its most common spelling."""
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = org_key(r["organisation"])
        if not key:
            continue
        g = groups.setdefault(key, {"names": Counter(), "sessions": set(), "first": None, "last": None})
        g["names"][r["organisation"]] += 1
        g["sessions"].add(r["session_id"])
        if r["date"]:
            g["first"] = min(g["first"] or r["date"], r["date"])
            g["last"] = max(g["last"] or r["date"], r["date"])
    out = []
    for key, g in groups.items():
        # the most used spelling; for a university, its own name rather than one of its departments
        names = [n for n in g["names"] if _canon(n) == key] or list(g["names"])
        best = max(names, key=lambda n: (g["names"][n], not n.lower().startswith("the ")))
        out.append({"organisation": best, "key": key, "sessions": len(g["sessions"]), "first": g["first"],
                    "last": g["last"], "also_written": sorted(n for n in g["names"] if n != best)[:8]})
    out.sort(key=lambda o: (-o["sessions"], o["organisation"].lower()))
    return out[:limit]

# ---- reading, for the pages ---------------------------------------------------------------------------------
def links(kind: str, ident: int, extra: int | None = None) -> str:
    """Parliament's own page for a committee, inquiry, evidence session or publication."""
    if kind == "committee":
        return f"{SITE}/committee/{ident}/"
    if kind == "inquiry":
        return f"{SITE}/work/{ident}/"
    if kind == "session":
        return f"{SITE}/oralevidence/{ident}/html/"
    if extra:
        return f"{SITE}/publications/{ident}/documents/{extra}/default/"
    return f"{SITE}/publications/{ident}/"


class Committees:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()

    @contextmanager
    def connect(self):
        mtime = self.path.stat().st_mtime if self.path.exists() else None
        con, seen = getattr(self._local, "con", None), getattr(self._local, "mtime", None)
        if con is None or seen != mtime:  # a weekly rebuild swaps the file: reopen
            if con is not None:
                con.close()
            con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            con.row_factory = sqlite3.Row
            self._local.con, self._local.mtime = con, mtime
        yield con

    def available(self) -> bool:
        return self.path.exists()

    def index(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("""
                SELECT c.id, c.name, c.house, c.category, c.departments,
                       (SELECT count(*) FROM inquiry_committees ic WHERE ic.committee_id = c.id) AS inquiries,
                       (SELECT count(*) FROM publications p WHERE p.committee_id = c.id AND p.type = 'Report') AS reports,
                       (SELECT max(s.date) FROM session_committees sc JOIN sessions s ON s.id = sc.session_id
                         WHERE sc.committee_id = c.id) AS last_session,
                       (SELECT max(p.date) FROM publications p WHERE p.committee_id = c.id) AS last_publication
                FROM committees c WHERE c.end IS NULL AND c.parent_id IS NULL""").fetchall()
        out = []
        for r in rows:
            d = dict(r) | {"url": links("committee", r["id"])}
            d["last_active"] = max(filter(None, [r["last_session"], r["last_publication"]]), default=None)
            out.append(d)
        # most recently active first: the last evidence session or publication
        out.sort(key=lambda d: (d["last_active"] or "", d["name"]), reverse=True)
        return out

    def committee(self, cid: int) -> dict[str, Any] | None:
        with self.connect() as con:
            c = con.execute("SELECT * FROM committees WHERE id = ?", (cid,)).fetchone()
            if c is None:
                return None
            inquiries = con.execute("""
                SELECT i.id, i.title, i.type, i.open, i.close,
                       (SELECT count(*) FROM session_inquiries si WHERE si.inquiry_id = i.id) AS sessions,
                       (SELECT count(*) FROM publication_inquiries pi JOIN publications p ON p.id = pi.publication_id
                         WHERE pi.inquiry_id = i.id AND p.type = 'Report') AS reports
                FROM inquiries i JOIN inquiry_committees ic ON ic.inquiry_id = i.id
                WHERE ic.committee_id = ? ORDER BY coalesce(i.open, '') DESC, i.id DESC LIMIT 200""", (cid,)).fetchall()
            subs = con.execute("SELECT id, name FROM committees WHERE parent_id = ? ORDER BY name", (cid,)).fetchall()
            reports = con.execute("SELECT count(*) FROM publications WHERE committee_id = ? AND type = 'Report'",
                                  (cid,)).fetchone()[0]
            rows = con.execute("""
                SELECT w.organisation, w.session_id, s.date FROM witnesses w
                JOIN session_committees sc ON sc.session_id = w.session_id JOIN sessions s ON s.id = w.session_id
                WHERE sc.committee_id = ? AND w.organisation IS NOT NULL""", (cid,)).fetchall()
        orgs = [{"organisation": o["organisation"], "n": o["sessions"]}
                for o in group_organisations([dict(r) for r in rows], 15)]
        return {**dict(c), "url": links("committee", cid), "inquiries": [dict(i) for i in inquiries],
                "subcommittees": [dict(s) for s in subs], "top_organisations": orgs, "reports": reports}

    def inquiry(self, iid: int) -> dict[str, Any] | None:
        with self.connect() as con:
            i = con.execute("SELECT * FROM inquiries WHERE id = ?", (iid,)).fetchone()
            if i is None:
                return None
            committees = con.execute("""SELECT c.id, c.name, c.house FROM committees c JOIN inquiry_committees ic
                                        ON ic.committee_id = c.id WHERE ic.inquiry_id = ?""", (iid,)).fetchall()
            pubs = con.execute("""SELECT p.* FROM publications p JOIN publication_inquiries pi ON pi.publication_id = p.id
                                  WHERE pi.inquiry_id = ? ORDER BY p.date""", (iid,)).fetchall()
            sessions = con.execute("""SELECT s.id, s.date FROM sessions s JOIN session_inquiries si ON si.session_id = s.id
                                      WHERE si.inquiry_id = ? ORDER BY s.date""", (iid,)).fetchall()
            witnesses = con.execute(f"""SELECT session_id, name, organisation, role, mnis FROM witnesses
                                       WHERE session_id IN ({','.join('?' for _ in sessions) or 'NULL'})""",
                                    [s["id"] for s in sessions]).fetchall()
        by_session: dict[int, list[dict[str, Any]]] = {}
        for w in witnesses:
            by_session.setdefault(w["session_id"], []).append(dict(w))
        reports = {p["id"]: p for p in pubs}
        out_pubs = []
        for p in pubs:
            d = dict(p) | {"url": links("publication", p["id"], p["document_id"])}
            parent = reports.get(p["response_to"]) if p["response_to"] else None
            if parent and parent["date"] and p["date"]:
                d["days_after_report"] = (datetime.fromisoformat(p["date"]) - datetime.fromisoformat(parent["date"])).days
            out_pubs.append(d)
        return {**dict(i), "url": links("inquiry", iid), "committees": [dict(c) for c in committees],
                "publications": out_pubs,
                "sessions": [{"id": s["id"], "date": s["date"], "url": links("session", s["id"]),
                              "witnesses": by_session.get(s["id"], [])} for s in sessions]}

    def witnesses(self, q: str, limit: int = 50) -> dict[str, Any]:
        """Organisations (and people) matching the words, with how often and by whom they were heard. Each body
        appears once however its name was written, and the recent sessions show each body once per inquiry."""
        words = re.findall(r"[\w'’&-]+", q)[:8]
        if not words:
            return self.top_organisations()
        match = " ".join(f'"{w}"' for w in words)
        with self.connect() as con:
            hits = [dict(r) for r in con.execute("""
                SELECT coalesce(w.organisation, w.name) AS organisation, w.session_id, s.date
                FROM witnesses w JOIN sessions s ON s.id = w.session_id
                WHERE w.rowid IN (SELECT rowid FROM witness_text WHERE witness_text MATCH ?)""", (match,))]
            orgs = group_organisations(hits, limit)
            for o in orgs:  # which committees heard each organisation, under any of its spellings
                names = [o["organisation"], *o["also_written"]]
                o["committees"] = [c[0] for c in con.execute(f"""
                    SELECT DISTINCT c.name FROM witnesses w JOIN session_committees sc ON sc.session_id = w.session_id
                    JOIN committees c ON c.id = sc.committee_id
                    WHERE w.organisation COLLATE NOCASE IN ({','.join('?' for _ in names)}) ORDER BY c.name""", names)]
            recent = con.execute("""
                SELECT s.id, s.date, w.name, w.organisation, w.role,
                       (SELECT group_concat(c.name, '; ') FROM session_committees sc JOIN committees c ON c.id = sc.committee_id
                         WHERE sc.session_id = s.id) AS committees,
                       (SELECT i.id FROM session_inquiries si JOIN inquiries i ON i.id = si.inquiry_id
                         WHERE si.session_id = s.id LIMIT 1) AS inquiry_id,
                       (SELECT i.title FROM session_inquiries si JOIN inquiries i ON i.id = si.inquiry_id
                         WHERE si.session_id = s.id LIMIT 1) AS inquiry
                FROM witnesses w JOIN sessions s ON s.id = w.session_id
                WHERE w.rowid IN (SELECT rowid FROM witness_text WHERE witness_text MATCH ?)
                ORDER BY s.date DESC LIMIT 400""", (match,)).fetchall()
        sessions, seen, per_body = [], set(), Counter()
        for r in recent:  # one row per body and inquiry, and at most three per body, so one does not fill the list
            body = org_key(r["organisation"]) or (r["name"] or "").lower()
            if (body, r["inquiry_id"] or r["id"]) in seen or per_body[body] >= 3:
                continue
            seen.add((body, r["inquiry_id"] or r["id"]))
            per_body[body] += 1
            sessions.append(dict(r) | {"url": links("session", r["id"])})
            if len(sessions) >= 40:
                break
        return {"query": q, "organisations": orgs, "sessions": sessions}

    def top_organisations(self, since: str | None = None, limit: int = 30) -> dict[str, Any]:
        since = since or (datetime.now(UTC) - timedelta(days=365 * 2)).date().isoformat()
        with self.connect() as con:
            rows = [dict(r) for r in con.execute("""
                SELECT w.organisation, w.session_id, s.date FROM witnesses w JOIN sessions s ON s.id = w.session_id
                WHERE w.organisation IS NOT NULL AND s.date >= ?""", (since,))]
        return {"query": "", "since": since, "organisations": group_organisations(rows, limit), "sessions": []}

    def for_member(self, mnis: int, limit: int = 10) -> list[dict[str, Any]]:
        """Evidence sessions where a member of either House appeared as a witness (ministers, mostly)."""
        with self.connect() as con:
            rows = con.execute("""
                SELECT s.id, s.date, w.role,
                       (SELECT group_concat(c.name, '; ') FROM session_committees sc JOIN committees c ON c.id = sc.committee_id
                         WHERE sc.session_id = s.id) AS committees,
                       (SELECT i.title FROM session_inquiries si JOIN inquiries i ON i.id = si.inquiry_id
                         WHERE si.session_id = s.id LIMIT 1) AS inquiry
                FROM witnesses w JOIN sessions s ON s.id = w.session_id WHERE w.mnis = ?
                GROUP BY s.id ORDER BY s.date DESC LIMIT ?""", (mnis, limit)).fetchall()
        return [dict(r) | {"url": links("session", r["id"])} for r in rows]

    def sitemap_ids(self) -> tuple[list[int], list[int]]:
        """(committees, inquiries) with something to show: current committees, inquiries with sessions or reports."""
        with self.connect() as con:
            cs = [r[0] for r in con.execute("SELECT id FROM committees WHERE end IS NULL")]
            iq = [r[0] for r in con.execute("""SELECT DISTINCT inquiry_id FROM session_inquiries
                                               UNION SELECT DISTINCT inquiry_id FROM publication_inquiries""")]
        return cs, iq

    def recent_publications(self, limit: int = 40) -> list[dict[str, Any]]:
        """The newest reports, special reports and government responses, with their committee and inquiry."""
        with self.connect() as con:
            rows = con.execute("""
                SELECT p.id, p.type, p.title, p.date, p.document_id, c.id AS committee_id, c.name AS committee,
                       (SELECT i.id FROM publication_inquiries pi JOIN inquiries i ON i.id = pi.inquiry_id
                         WHERE pi.publication_id = p.id LIMIT 1) AS inquiry_id
                FROM publications p LEFT JOIN committees c ON c.id = p.committee_id
                WHERE p.date IS NOT NULL ORDER BY p.date DESC, p.id DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) | {"url": links("publication", r["id"], r["document_id"])} for r in rows]

    def sessions_meta(self, ids: set[int]) -> dict[int, dict[str, Any]]:
        """Date, committees, inquiry and witnesses of evidence sessions, for transcripts and search results."""
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        with self.connect() as con:
            rows = con.execute(f"""
                SELECT s.id, s.date,
                       (SELECT group_concat(c.name, '; ') FROM session_committees sc JOIN committees c
                          ON c.id = sc.committee_id WHERE sc.session_id = s.id) AS committees,
                       (SELECT sc.committee_id FROM session_committees sc WHERE sc.session_id = s.id LIMIT 1)
                          AS committee_id,
                       (SELECT i.id FROM session_inquiries si JOIN inquiries i ON i.id = si.inquiry_id
                         WHERE si.session_id = s.id LIMIT 1) AS inquiry_id,
                       (SELECT i.title FROM session_inquiries si JOIN inquiries i ON i.id = si.inquiry_id
                         WHERE si.session_id = s.id LIMIT 1) AS inquiry
                FROM sessions s WHERE s.id IN ({marks})""", list(ids)).fetchall()
            wit = con.execute(f"SELECT session_id, name, organisation, role FROM witnesses WHERE session_id IN ({marks})",
                              list(ids)).fetchall()
        out = {r["id"]: dict(r) | {"url": links("session", r["id"]), "witnesses": []} for r in rows}
        for w in wit:
            out[w["session_id"]]["witnesses"].append({"name": w["name"], "organisation": w["organisation"],
                                                      "role": w["role"]})
        return out

    def stats(self) -> dict[str, Any]:
        with self.connect() as con:
            built = con.execute("SELECT value FROM meta WHERE key = 'built'").fetchone()
            n = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608 (fixed table names)
                 for t in ("committees", "inquiries", "publications", "sessions")}
        return {"built": built[0] if built else None, **n}
