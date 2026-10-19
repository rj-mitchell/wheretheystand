"""Early day motions, from the UK Parliament oral questions and motions API (Open Parliament Licence).

An early day motion (EDM) is a motion put down by MPs, usually never debated, used to put a view on record and to
gather signatures. Each sync fetches the motions changed recently, works back through older years a little at a
time (since 1997), and fetches who signed each motion. Members are matched to parlparse people by their UK
Parliament ID, never by name.

Kept in their own SQLite file (data/edms.sqlite) with a full-text index. If the API cannot be reached, or answers
in a shape this code does not recognise, the sync logs it and carries on: the rest of the site never depends on it.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from hansard.sources.demographics import USER_AGENT

log = logging.getLogger(__name__)

API = "https://oralquestionsandmotions-api.parliament.uk"
START = date(1997, 5, 1)  # the start of the site's record (Parliament's list goes back to 1989)
PAGE = 100
SOURCE = "UK Parliament oral questions and motions API (early day motions)"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS edms (
  id INTEGER PRIMARY KEY, uin TEXT, title TEXT NOT NULL, text TEXT, date_tabled TEXT, status TEXT,
  status_date TEXT, sponsors_count INTEGER, primary_mnis INTEGER, primary_name TEXT, primary_party TEXT,
  amendment_to INTEGER, prayer INTEGER DEFAULT 0, raw TEXT, sponsors_fetched TEXT
);
CREATE INDEX IF NOT EXISTS edms_tabled ON edms (date_tabled);
CREATE TABLE IF NOT EXISTS edm_sponsors (
  edm_id INTEGER NOT NULL, mnis INTEGER NOT NULL, name TEXT, party TEXT, sponsoring_order INTEGER,
  signed TEXT, withdrawn TEXT, PRIMARY KEY (edm_id, mnis)
);
CREATE INDEX IF NOT EXISTS edm_sponsors_mnis ON edm_sponsors (mnis);
CREATE TABLE IF NOT EXISTS edm_months (month TEXT PRIMARY KEY, fetched TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS edm_text USING fts5(title, text, content='edms', content_rowid='id');
"""


# ---- reading the API's answers, tolerantly ---------------------------------------------------------------
def _get_key(d: Any, *names: str) -> Any:
    """The first of ``names`` present in ``d``, ignoring case (the API's casing has varied)."""
    if not isinstance(d, dict):
        return None
    lower = {k.lower(): v for k, v in d.items()}
    for n in names:
        v = lower.get(n.lower())
        if v not in (None, ""):
            return v
    return None


def _day(v: Any) -> str | None:
    return str(v)[:10] if v else None


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _items(body: Any) -> list[dict[str, Any]]:
    resp = _get_key(body, "Response", "Items", "Results") if isinstance(body, dict) else body
    if isinstance(resp, dict):
        resp = [resp]
    return [x for x in resp or [] if isinstance(x, dict)]


def _member(d: Any) -> dict[str, Any]:
    m = _get_key(d, "Member", "PrimarySponsor") or d
    return {"mnis": _int(_get_key(m, "MnisId", "MemberId", "Id")), "name": _get_key(m, "Name", "ListAs", "DisplayAs"),
            "party": _get_key(m, "Party", "PartyName")}


def normalise_edm(raw: dict[str, Any]) -> dict[str, Any] | None:
    eid, title = _int(_get_key(raw, "Id", "EdmId")), _get_key(raw, "Title")
    if eid is None or not title:
        return None
    sponsor = _member(_get_key(raw, "PrimarySponsor") or {})
    if sponsor["mnis"] is None:
        sponsor["mnis"] = _int(_get_key(raw, "MemberId"))
    return {"id": eid, "uin": str(_get_key(raw, "UINWithAmendmentSuffix", "UIN") or "") or None,
            "title": " ".join(str(title).split()), "text": _get_key(raw, "MotionText", "Text"),
            "date_tabled": _day(_get_key(raw, "DateTabled", "TabledDate")), "status": _get_key(raw, "Status"),
            "status_date": _day(_get_key(raw, "StatusDate")),
            # older motions give 0 here although they list their signatures: unknown until the names are fetched
            "sponsors_count": _int(_get_key(raw, "SponsorsCount", "SignaturesCount")) or None,
            "primary_mnis": sponsor["mnis"], "primary_name": sponsor["name"], "primary_party": sponsor["party"],
            "amendment_to": _int(_get_key(raw, "AmendmentToMotionId")),
            "prayer": 1 if _get_key(raw, "PrayingAgainstNegativeStatutoryInstrumentId", "StatutoryInstrumentTitle")
            else 0}


def normalise_sponsors(raw: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Who signed, from a single motion's record (None if the record has no list of sponsors)."""
    sponsors = _get_key(raw, "Sponsors", "Signatures")
    if not isinstance(sponsors, list):
        return None
    out = []
    for s in sponsors:
        m = _member(s)
        if m["mnis"] is None:
            continue
        out.append({**m, "sponsoring_order": _int(_get_key(s, "SponsoringOrder", "Order")),
                    "signed": _day(_get_key(s, "CreatedWhen", "DateSigned", "SignedDate")),
                    "withdrawn": _day(_get_key(s, "WithdrawnDate")) if _get_key(s, "IsWithdrawn", "WithdrawnDate")
                    else None})
    return out


# ---- the local copy -------------------------------------------------------------------------------------
class EdmStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.connect() as con:
            con.executescript(_SCHEMA)

    @contextmanager
    def connect(self):
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            self._local.con = con
        with con:
            yield con

    def upsert(self, edm: dict[str, Any], raw: dict[str, Any] | None = None) -> None:
        with self.connect() as con:
            old = con.execute("SELECT title, text FROM edms WHERE id = ?", (edm["id"],)).fetchone()
            if old:  # keep the full-text index in step (external-content table)
                con.execute("INSERT INTO edm_text (edm_text, rowid, title, text) VALUES ('delete', ?, ?, ?)",
                            (edm["id"], old["title"], old["text"] or ""))
            cols = [*edm.keys(), "raw"]
            con.execute(f"INSERT INTO edms ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
                        f"ON CONFLICT (id) DO UPDATE SET "
                        + ", ".join(f"{c} = excluded.{c}" if c != "sponsors_count" else
                                    # a count taken from the list of names stands until the names are fetched again
                                    "sponsors_count = CASE WHEN edms.sponsors_fetched IS NOT NULL "
                                    "THEN edms.sponsors_count ELSE excluded.sponsors_count END" for c in cols),
                        [*edm.values(), json.dumps(raw) if raw is not None else None])
            con.execute("INSERT INTO edm_text (rowid, title, text) VALUES (?, ?, ?)",
                        (edm["id"], edm["title"], edm["text"] or ""))

    def set_sponsors(self, edm_id: int, sponsors: list[dict[str, Any]]) -> None:
        with self.connect() as con:
            con.execute("DELETE FROM edm_sponsors WHERE edm_id = ?", (edm_id,))
            con.executemany("INSERT OR REPLACE INTO edm_sponsors VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [(edm_id, s["mnis"], s["name"], s["party"], s["sponsoring_order"], s["signed"],
                              s["withdrawn"]) for s in sponsors])
            # the motion's own count can lag; the list of names is the record
            con.execute("UPDATE edms SET sponsors_fetched = date('now'), sponsors_count = ? WHERE id = ?",
                        (sum(1 for s in sponsors if not s["withdrawn"]), edm_id))

    def count(self) -> int:
        with self.connect() as con:
            return con.execute("SELECT count(*) FROM edms").fetchone()[0]

    def latest_status(self) -> str | None:
        with self.connect() as con:
            return con.execute("SELECT max(status_date) FROM edms").fetchone()[0]

    def needing_sponsors(self, limit: int) -> list[int]:
        """Motions whose signatures are missing or may have changed (newest first)."""
        with self.connect() as con:
            rows = con.execute("""
                SELECT id FROM edms
                WHERE sponsors_fetched IS NULL OR (sponsors_fetched < status_date)
                   OR (date_tabled >= date('now', '-60 days') AND sponsors_fetched < date('now'))
                ORDER BY date_tabled DESC LIMIT ?""", (limit,)).fetchall()
        return [r[0] for r in rows]

    def months_done(self) -> set[str]:
        with self.connect() as con:
            return {r[0] for r in con.execute("SELECT month FROM edm_months")}

    def month_done(self, month: str) -> None:
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO edm_months VALUES (?, date('now'))", (month,))

    # ---- reading, for the pages --------------------------------------------------------------------------
    def search(self, q: str = "", *, mnis: list[int] | None = None, limit: int = 20, offset: int = 0
               ) -> tuple[int, list[dict[str, Any]]]:
        where, params = ["1=1"], []
        if q.strip():
            terms = " ".join(f'"{w}"' for w in re.findall(r"[\w'’-]+", q)[:8])
            if terms:
                where.append("e.id IN (SELECT rowid FROM edm_text WHERE edm_text MATCH ?)")
                params.append(terms)
        if mnis:
            marks = ", ".join("?" for _ in mnis)
            where.append(f"(e.primary_mnis IN ({marks}) OR e.id IN (SELECT edm_id FROM edm_sponsors "
                         f"WHERE mnis IN ({marks}) AND withdrawn IS NULL))")
            params += [*mnis, *mnis]
        sql = " AND ".join(where)
        with self.connect() as con:
            total = con.execute(f"SELECT count(*) FROM edms e WHERE {sql}", params).fetchone()[0]
            rows = con.execute(f"""SELECT e.id, e.uin, e.title, e.date_tabled, e.status, e.sponsors_count,
                                          e.primary_mnis, e.primary_name, e.primary_party, e.prayer
                                   FROM edms e WHERE {sql} ORDER BY e.date_tabled DESC, e.id DESC
                                   LIMIT ? OFFSET ?""", [*params, limit, offset]).fetchall()
        return total, [dict(r) for r in rows]

    def relevant(self, keywords: list[str], *, date_from: str | None = None, date_to: str | None = None,
                 limit: int = 3) -> list[dict[str, Any]]:
        """Motions matching all the keywords (the best few by relevance, then support), with their signatures."""
        words = [w for w in keywords if re.fullmatch(r"[\w'’-]+", w)][:5]
        if not words or self.count() == 0:
            return []
        where, params = ["edm_text MATCH ?", "e.amendment_to IS NULL"], [" ".join(f'"{w}"' for w in words)]
        if date_from:
            where.append("e.date_tabled >= ?")
            params.append(str(date_from))
        if date_to:
            where.append("e.date_tabled <= ?")
            params.append(str(date_to))
        with self.connect() as con:
            ids = [r[0] for r in con.execute(f"""
                SELECT e.id FROM edm_text JOIN edms e ON e.id = edm_text.rowid WHERE {' AND '.join(where)}
                ORDER BY bm25(edm_text, 5.0, 1.0), e.sponsors_count DESC LIMIT ?""", [*params, limit])]
        return [e for i in ids if (e := self.get(i))]

    def get(self, edm_id: int) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM edms WHERE id = ?", (edm_id,)).fetchone()
            if row is None:
                return None
            sponsors = con.execute("SELECT mnis, name, party, sponsoring_order, signed, withdrawn FROM edm_sponsors "
                                   "WHERE edm_id = ? ORDER BY sponsoring_order IS NULL, sponsoring_order, signed, "
                                   "name", (edm_id,)).fetchall()
            amendments = con.execute("SELECT id, uin, title, sponsors_count FROM edms WHERE amendment_to = ? "
                                     "ORDER BY id", (edm_id,)).fetchall()
        out = dict(row)
        out.pop("raw", None)
        out["sponsors"] = [dict(s) for s in sponsors]
        out["amendments"] = [dict(a) for a in amendments]
        return out

    def member_summary(self, mnis: int, recent: int = 10) -> dict[str, Any]:
        with self.connect() as con:
            tabled = con.execute("SELECT count(*) FROM edms WHERE primary_mnis = ?", (mnis,)).fetchone()[0]
            signed = con.execute("SELECT count(*) FROM edm_sponsors WHERE mnis = ? AND withdrawn IS NULL",
                                 (mnis,)).fetchone()[0]
        _, rows = self.search(mnis=[mnis], limit=recent)
        return {"tabled": tabled, "signed": signed, "recent": rows}


# ---- fetching -------------------------------------------------------------------------------------------
def _client() -> httpx.Client:
    return httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                        follow_redirects=True)


class RateLimited(Exception):
    """Parliament asked us to slow down and kept asking: stop for now and carry on at the next update."""


PAUSE = 0.25  # seconds between requests: one at a time, about four a second at most


def _get(client: httpx.Client, path: str, params: dict[str, Any] | None = None, tries: int = 3,
         sleep=time.sleep) -> Any:
    for attempt in range(tries):
        try:
            resp = client.get(f"{API}{path}", params=params)
            if resp.status_code == 429:  # too many requests: wait as asked (or a while), then try again
                wait = _int(resp.headers.get("Retry-After")) or 30 * (attempt + 1)
                if attempt == tries - 1:
                    raise RateLimited(f"{path}: still rate limited after {tries} tries")
                sleep(min(wait, 300))
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
            sleep(1 + attempt * 2)
    return None


def _list(client: httpx.Client, params: dict[str, Any], sleep=time.sleep) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    skip = 0
    while True:
        body = _get(client, "/EarlyDayMotions/list", {**params, "parameters.skip": skip, "parameters.take": PAGE},
                    sleep=sleep)
        items = _items(body)
        out += items
        total = _int(_get_key(_get_key(body, "PagingInfo") or {}, "Total")) if isinstance(body, dict) else None
        skip += PAGE
        if len(items) < PAGE or (total is not None and skip >= total) or skip > 20000:
            return out


def _save(store: EdmStore, items: list[dict[str, Any]]) -> int:
    n = 0
    for raw in items:
        edm = normalise_edm(raw)
        if edm:
            store.upsert(edm, raw)
            n += 1
    return n


def sync_edms(store: EdmStore, *, today: date | None = None, months_per_run: int = 36, sponsors_per_run: int = 6000,
              sponsors_seconds: float = 900, client: httpx.Client | None = None, sleep=time.sleep) -> dict[str, Any]:
    """Recent changes, then some older months, then signatures (up to ``sponsors_seconds`` of them). Returns counts
    for the sync log.

    Requests go one at a time with a short pause. If Parliament keeps asking us to slow down, the run stops early
    and the next update carries on where it left off."""
    today = today or date.today()
    stats: dict[str, Any] = {"recent": 0, "backfilled": 0, "months": 0, "signatures": 0, "motions_signatures": 0,
                             "unrecognised": 0}
    own = client is None
    client = client or _client()
    try:
        since = store.latest_status()
        since_day = (date.fromisoformat(since) - timedelta(days=7)) if since else today - timedelta(days=30)
        stats["recent"] = _save(store, _list(client, {"parameters.currentStatusDateStart": since_day.isoformat()},
                                             sleep))
        done = store.months_done()
        month = date(today.year, today.month, 1)
        todo = []
        while month >= date(START.year, START.month, 1) and len(todo) < months_per_run:
            key = month.strftime("%Y-%m")
            if key not in done:
                todo.append(month)
            month = (month - timedelta(days=1)).replace(day=1)
        for m in todo:
            end = (m + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            items = _list(client, {"parameters.tabledStartDate": m.isoformat(),
                                   "parameters.tabledEndDate": end.isoformat()}, sleep)
            stats["backfilled"] += _save(store, items)
            stats["unrecognised"] += sum(1 for i in items if normalise_edm(i) is None)
            if end < today - timedelta(days=60):  # recent months stay open: signatures still arrive
                store.month_done(m.strftime("%Y-%m"))
            stats["months"] += 1
        started = time.monotonic()
        for eid in store.needing_sponsors(sponsors_per_run):
            if time.monotonic() - started > sponsors_seconds:  # the rest at the next update
                break
            items = _items(_get(client, f"/EarlyDayMotion/{eid}", sleep=sleep))
            sponsors = normalise_sponsors(items[0]) if items else None
            if sponsors is not None:
                store.set_sponsors(eid, sponsors)
                stats["signatures"] += len(sponsors)
                stats["motions_signatures"] += 1
    except RateLimited as exc:
        log.warning("early day motions: %s; carrying on at the next update", exc)
        stats["rate_limited"] = True
    finally:
        if own:
            client.close()
    stats["total"] = store.count()
    stats["signatures_still_to_fetch"] = len(store.needing_sponsors(100000))
    if stats["unrecognised"]:
        log.warning("early day motions: %d records in an unrecognised shape", stats["unrecognised"])
    return stats


def check(client: httpx.Client | None = None) -> str:
    """A short report on what the API returns now, to confirm the fields this code reads (for the CLI)."""
    own = client is None
    client = client or _client()
    try:
        try:
            body = _get(client, "/EarlyDayMotions/list", {"parameters.take": 1, "parameters.orderBy": "DateTabledDesc"})
        except RateLimited:
            return "Parliament is asking for fewer requests just now (probably an update is fetching). Try again later."
        items = _items(body)
        lines = [f"list: top-level keys {sorted(body) if isinstance(body, dict) else type(body).__name__}"]
        if items:
            lines.append(f"motion keys: {sorted(items[0])}")
            lines.append(f"read as: {normalise_edm(items[0])}")
            one = _items(_get(client, f"/EarlyDayMotion/{_get_key(items[0], 'Id')}"))
            if one:
                sponsors = _get_key(one[0], "Sponsors") or []
                lines.append(f"detail keys: {sorted(one[0])}")
                if sponsors:
                    lines.append(f"sponsor keys: {sorted(sponsors[0])}")
                lines.append(f"sponsors read: {(normalise_sponsors(one[0]) or [])[:2]}")
        return "\n".join(lines)
    finally:
        if own:
            client.close()
