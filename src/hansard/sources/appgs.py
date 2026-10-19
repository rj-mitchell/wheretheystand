"""All-Party Parliamentary Groups (and the devolved parliaments' cross-party groups): who runs them, who is in
them, who provides their secretariat and what benefits they have registered.

The Register of All-Party Parliamentary Groups is published by Parliament on a site that turns away automated
downloads, so this uses mySociety's "APPG groups and memberships" dataset (CC BY 4.0), which is built from the
register and kept on GitHub. It carries each group's officers, registered benefits and secretariat from the
register, membership lists the groups supplied, and the devolved parliaments' official lists.

* Membership lists that mySociety found by AI web search are left out: mySociety says they are not yet validated.
* People are linked to members by their ParlParse ID, else their UK Parliament ID, else (for names with neither)
  only when the full name is exactly that of one member sitting when the list was published. Anything else, such
  as companies listed as non-parliamentary members, is shown as listed and linked to no one.
* Contact addresses and email addresses are left out.

Kept in data/appgs.sqlite, rebuilt when the dataset is refreshed (weekly).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from hansard.sources.demographics import USER_AGENT
from hansard.sources.donations import name_tokens

log = logging.getLogger(__name__)

BASE = ("https://raw.githubusercontent.com/mysociety/appg-membership/main/data/packages/"
        "appg_groups_and_memberships/{name}.parquet")
DATASET = "https://github.com/mysociety/appg-membership"
OFFICIAL = "https://www.parliament.uk/mps-lords-and-offices/standards-and-financial-interests/parliamentary-commissioner-for-standards/registers-of-interests/register-of-all-party-party-parliamentary-groups/"
SOURCE = "mySociety, APPG groups and memberships (CC BY 4.0), from the Register of All-Party Parliamentary Groups"
REFRESH = timedelta(days=7)
PARLIAMENTS = {"uk": "UK Parliament", "scottish-parliament": "Scottish Parliament", "senedd-en": "Senedd",
               "ni-assembly": "Northern Ireland Assembly"}
SOURCES = {"parliament": "the register", "manual": "the group's own list", "official": "the parliament's list"}

_SCHEMA = """
CREATE TABLE groups (slug TEXT, parliament TEXT, title TEXT, purpose TEXT, category TEXT, categories TEXT,
                     secretariat TEXT, website TEXT, source_url TEXT, registrable_benefits TEXT, agm TEXT,
                     statement INTEGER, reporting_year TEXT, PRIMARY KEY (slug, parliament));
CREATE TABLE members (slug TEXT, parliament TEXT, person_id TEXT, name TEXT, role TEXT, is_officer INTEGER,
                      source TEXT, linked_by TEXT, list_url TEXT, updated TEXT);
CREATE INDEX members_person ON members (person_id);
CREATE INDEX members_group ON members (slug, parliament);
CREATE TABLE benefits (slug TEXT, parliament TEXT, funder TEXT, type TEXT, value TEXT, value_low REAL,
                       received TEXT, registered TEXT, description TEXT);
CREATE INDEX benefits_funder ON benefits (funder);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _day(v: Any) -> str | None:
    if isinstance(v, date):
        return v.isoformat()
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(v or ""))
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _low(v: str | None) -> float | None:
    """The lower end of a value or band ("67,501-69,000" -> 67501; "4,950" -> 4950)."""
    m = re.match(r"\s*£?([\d,]+(?:\.\d+)?)", v or "")
    try:
        return float(m.group(1).replace(",", "")) if m else None
    except ValueError:
        return None


def _tidy(text: str | None) -> str | None:
    """The register runs links into the sentence before them ("secretariat.https://..."): put a space back."""
    if not text:
        return None
    return " ".join(re.sub(r"(?<=\S)(https?://)", r" \1", re.sub(r"(?<=\S)and(https?://)", r" and \1", text)).split())


def _benefits(raw: str | None) -> list[dict[str, Any]]:
    try:
        items = json.loads(raw) if raw else []
    except ValueError:
        return []
    out = []
    for b in items if isinstance(items, list) else []:
        value = b.get("Value £s") or b.get("Value £s In bands of £1,500")
        out.append({"funder": " ".join(str(b.get("Source") or "").split()) or None,
                    "type": b.get("benefit_type"), "value": value, "value_low": _low(value),
                    "received": _day(b.get("Received")), "registered": _day(b.get("Registered")),
                    "description": " ".join(str(b.get("Description") or "").split()) or None})
    return out


class Linker:
    """Member IDs and exact names -> canonical parlparse person ids."""

    def __init__(self, con: Any):
        self.canonical = dict(con.execute("SELECT xml_id, person_id FROM ref_person_ids").fetchall())
        self.by_mnis = {str(m): p for p, m in con.execute(
            "SELECT person_id, datadotparl_id FROM ref_persons WHERE datadotparl_id IS NOT NULL").fetchall()}
        self._con = con
        self._names: dict[str, dict[tuple[str, ...], set[str]]] = {}

    def _sitting(self, on: str) -> dict[tuple[str, ...], set[str]]:
        if on not in self._names:
            names: dict[tuple[str, ...], set[str]] = {}
            for pid, name in self._con.execute("""
                    SELECT DISTINCT n.person_id, n.name FROM ref_names n JOIN ref_memberships m ON m.person_id = n.person_id
                    WHERE m.house IN ('commons', 'lords') AND m.start <= ? AND m."end" >= ?
                      AND n.start <= ? AND n."end" >= ?""", [on] * 4).fetchall():
                names.setdefault(tuple(name_tokens(name)), set()).add(pid)
            self._names[on] = names
        return self._names[on]

    def link(self, twfy_id: str | None, mnis_id: str | None, name: str | None, parliament: str,
             on: str | None) -> tuple[str | None, str | None]:
        if twfy_id:
            return self.canonical.get(twfy_id, twfy_id), "ParlParse ID"
        if mnis_id and str(mnis_id) in self.by_mnis:
            return self.by_mnis[str(mnis_id)], "UK Parliament ID"
        if parliament == "uk" and name and on:
            found = self._sitting(on).get(tuple(name_tokens(name)), set())
            if len(found) == 1:
                return next(iter(found)), "exact name of a sitting member"
        return None, None


def build(path: Path, files: dict[str, Path], con: Any) -> dict[str, Any]:
    """Write data/appgs.sqlite from the three dataset files (DuckDB connection with the reference attached)."""
    import duckdb

    d = duckdb.connect()
    register = d.execute(f"SELECT * FROM read_parquet('{files['register'].as_posix()}')").fetchall()
    rcols = [c[0] for c in d.description]
    members = d.execute(f"SELECT * FROM read_parquet('{files['members'].as_posix()}')").fetchall()
    mcols = [c[0] for c in d.description]
    linker = Linker(con)
    stats = {"groups": 0, "members": 0, "linked": 0, "left_out_ai_search": 0, "benefits": 0}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(tmp)
    try:
        out.executescript(_SCHEMA)
        for row in register:
            g = dict(zip(rcols, row, strict=True))
            if g["parliament"] not in PARLIAMENTS:  # the Welsh-language copy of the Senedd's list is a duplicate
                continue
            stats["groups"] += 1
            out.execute("INSERT OR REPLACE INTO groups VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                g["slug"], g["parliament"], g["title"], g["purpose"] or None, g["category"] or None,
                g["categories"] or None, _tidy(g["secretariat"]), g["website"] or None, g["source_url"] or None,
                g["registrable_benefits"] or None, _day(g.get("date_of_most_recent_agm")),
                None if g.get("published_income_expenditure_statement") is None
                else int(bool(g["published_income_expenditure_statement"])), g.get("reporting_year") or None))
            for b in _benefits(g.get("detailed_benefits")):
                stats["benefits"] += 1
                out.execute("INSERT INTO benefits VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                    g["slug"], g["parliament"], b["funder"], b["type"], b["value"], b["value_low"], b["received"],
                    b["registered"], b["description"]))
        seen = set()
        for row in members:
            m = dict(zip(mcols, row, strict=True))
            if m["parliament"] not in PARLIAMENTS or m.get("removed"):
                continue
            if m["source"] == "ai_search":
                stats["left_out_ai_search"] += 1
                continue
            name = " ".join((m["name"] or "").split())
            if not name or name.endswith(":"):  # list headings such as "Non-parliamentary members:"
                continue
            on = _day(m.get("last_updated"))
            pid, how = linker.link(m.get("twfy_id"), m.get("mnis_id"), name, m["parliament"], on)
            key = (m["appg"], m["parliament"], pid or name.lower(), bool(m["is_officer"]))
            if key in seen:  # the same person listed twice in one list
                continue
            seen.add(key)
            stats["members"] += 1
            stats["linked"] += bool(pid)
            out.execute("INSERT INTO members VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                m["appg"], m["parliament"], pid, name, m.get("officer_role"), int(bool(m["is_officer"])),
                m["source"], how, m.get("url_source"), on))
        latest = max((str(r[mcols.index("last_updated")]) for r in members if r[mcols.index("last_updated")]),
                     default=None)
        out.executemany("INSERT INTO meta VALUES (?, ?)", [
            ("built", datetime.now(UTC).isoformat(timespec="seconds")), ("as_of", latest), ("stats", json.dumps(stats))])
        out.commit()
    finally:
        out.close()
    tmp.replace(path)
    return stats


def refresh(path: Path, con: Any, *, client: httpx.Client | None = None, force: bool = False) -> dict[str, Any]:
    """Download the dataset (three small files) and rebuild; weekly unless ``force``."""
    path = Path(path)
    if not force and path.exists():
        built = Groups(path).meta().get("built")
        if built and datetime.now(UTC) - datetime.fromisoformat(built) < REFRESH:
            return {"updated": False, "built": built}
    own = client is None
    client = client or httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            files = {}
            for name in ("register", "members"):
                resp = client.get(BASE.format(name=name))
                resp.raise_for_status()
                files[name] = Path(tmp) / f"{name}.parquet"
                files[name].write_bytes(resp.content)
            return {"updated": True, **build(path, files, con)}
    finally:
        if own:
            client.close()


class Groups:
    def __init__(self, path: Path):
        self.path = Path(path)

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

    def index(self, q: str = "", parliament: str = "uk") -> dict[str, Any]:
        words = [w for w in re.findall(r"[\w'-]+", q.lower()) if len(w) > 1][:6]
        with self.connect() as con:
            if con is None:
                return {"available": False}
            where = ["g.parliament = ?"] + ["(lower(g.title) LIKE ? OR lower(coalesce(g.purpose, '')) LIKE ? "
                                            "OR lower(coalesce(g.secretariat, '')) LIKE ?)" for _ in words]
            params: list[Any] = [parliament] + [f"%{w}%" for w in words for _ in range(3)]
            rows = con.execute(f"""
                SELECT g.slug, g.title, g.category, g.purpose,
                       (SELECT count(*) FROM members m WHERE m.slug = g.slug AND m.parliament = g.parliament
                          AND m.is_officer = 1) AS officers,
                       (SELECT count(*) FROM benefits b WHERE b.slug = g.slug AND b.parliament = g.parliament) AS benefits,
                       g.secretariat IS NOT NULL AS has_secretariat
                FROM groups g WHERE {' AND '.join(where)} ORDER BY g.title""", params).fetchall()
            funders = con.execute("""
                SELECT funder, count(DISTINCT slug) AS groups, round(sum(value_low), 0) AS at_least
                FROM benefits WHERE parliament = ? AND funder IS NOT NULL
                GROUP BY funder ORDER BY at_least DESC NULLS LAST LIMIT 25""", (parliament,)).fetchall()
        return {"available": True, "parliament": parliament, "groups": [dict(r) for r in rows],
                "funders": [dict(r) for r in funders] if not words else [], **self.meta()}

    def group(self, slug: str, parliament: str = "uk") -> dict[str, Any] | None:
        with self.connect() as con:
            if con is None:
                return None
            g = con.execute("SELECT * FROM groups WHERE slug = ? AND parliament = ?", (slug, parliament)).fetchone()
            if g is None:
                return None
            members = con.execute("""SELECT person_id, name, role, is_officer, source, linked_by, list_url, updated
                                     FROM members WHERE slug = ? AND parliament = ?
                                     ORDER BY is_officer DESC, name""", (slug, parliament)).fetchall()
            benefits = con.execute("""SELECT funder, type, value, value_low, received, registered, description
                                      FROM benefits WHERE slug = ? AND parliament = ?
                                      ORDER BY received DESC""", (slug, parliament)).fetchall()
        return {**dict(g), "parliament_name": PARLIAMENTS[g["parliament"]],
                "members": [dict(r) | {"source_label": SOURCES.get(r["source"], r["source"])} for r in members],
                "benefits": [dict(r) for r in benefits], "official": OFFICIAL, "dataset": DATASET,
                "as_of": self.meta().get("as_of")}

    def for_person(self, person_id: str) -> list[dict[str, Any]]:
        with self.connect() as con:
            if con is None:
                return []
            rows = con.execute("""
                SELECT m.slug, m.parliament, g.title, m.role, m.is_officer, m.source, g.secretariat,
                       (SELECT count(*) FROM benefits b WHERE b.slug = m.slug AND b.parliament = m.parliament) AS benefits
                FROM members m JOIN groups g ON g.slug = m.slug AND g.parliament = m.parliament
                WHERE m.person_id = ? ORDER BY m.is_officer DESC, g.title""", (person_id,)).fetchall()
        best: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:  # an officer is usually on the group's own list too: keep the officer entry
            best.setdefault((r["slug"], r["parliament"]), dict(r) | {
                "parliament_name": PARLIAMENTS[r["parliament"]], "source_label": SOURCES.get(r["source"], r["source"])})
        return list(best.values())

    def funder(self, name: str) -> list[dict[str, Any]]:
        with self.connect() as con:
            if con is None:
                return []
            rows = con.execute("""SELECT b.*, g.title FROM benefits b JOIN groups g ON g.slug = b.slug
                                  AND g.parliament = b.parliament WHERE b.funder = ? ORDER BY b.received DESC""",
                               (name,)).fetchall()
        return [dict(r) for r in rows]
