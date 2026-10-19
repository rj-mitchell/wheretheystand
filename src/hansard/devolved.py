"""The devolved parliaments: the Scottish Parliament, the Senedd and the Northern Ireland Assembly.

Their official reports are published by TheyWorkForYou in nearly the same format as Westminster's (``scrapedxml/
sp-new``, ``senedd/en`` and ``ni``), with every speaker identified by the same parlparse person IDs. They are kept in
their own store (data/devolved.sqlite, with a full-text index) so that Westminster search and briefings are
unchanged: readers browse each sitting day, search the record, and follow speakers to their member pages. Party and
seat on the day come from the devolved memberships in parlparse's people.json.

Fetched a few dozen files per update (one at a time), newest first, from ``since`` (default: the first sittings, 1998
for the Assembly and 1999 for Holyrood; TheyWorkForYou has the Senedd from 2016). Older files name speakers by their
membership ID rather than person ID; those are mapped through parlparse. A republished file (a later revision of the
same day or meeting) replaces the earlier one.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from lxml import etree

from hansard.sources.demographics import USER_AGENT

log = logging.getLogger(__name__)

BASE = "https://www.theyworkforyou.com/pwdata/scrapedxml/"


LEGISLATURES: dict[str, dict[str, str]] = {
    "scottish-parliament": {"name": "Scottish Parliament", "dir": "sp-new/meeting-of-the-parliament/",
                            "file": r"((\d{4}-\d{2}-\d{2})_\d+)\.xml", "org": "scottish-parliament",
                            "member": "MSP", "nation": "Scotland",
                            # 1999 to early 2010, in TheyWorkForYou's earlier folder (the new one starts 24 Feb 2010)
                            "archives": [{"dir": "sp/", "file": r"(sp(\d{4}-\d{2}-\d{2}))[a-z]?\.xml",
                                          "until": "2010-02-23"}]},
    "senedd": {"name": "Senedd Cymru (Welsh Parliament)", "dir": "senedd/en/",
               "file": r"(senedd(\d{4}-\d{2}-\d{2}))[a-z]?\.xml", "org": "welsh-parliament", "member": "MS",
               "nation": "Wales"},
    "ni-assembly": {"name": "Northern Ireland Assembly", "dir": "ni/", "file": r"(ni(\d{4}-\d{2}-\d{2}))[a-z]?\.xml",
                    "org": "northern-ireland-assembly", "member": "MLA", "nation": "Northern Ireland"},
}
# from the start: the Assembly sat from July 1998, Holyrood from May 1999; TheyWorkForYou has the Senedd from May 2016
SINCE = date(1998, 7, 1)
PAUSE = 0.5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (legislature TEXT, key TEXT, name TEXT, date TEXT, fetched TEXT,
                                  PRIMARY KEY (legislature, key));
CREATE TABLE IF NOT EXISTS speeches (id INTEGER PRIMARY KEY, legislature TEXT, key TEXT, date TEXT, ord INTEGER,
                                     sid TEXT, major TEXT, minor TEXT, person_id TEXT, speaker TEXT, time TEXT,
                                     url TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS speeches_key ON speeches (legislature, key, ord);
CREATE INDEX IF NOT EXISTS speeches_person ON speeches (person_id, date);
CREATE INDEX IF NOT EXISTS speeches_date ON speeches (legislature, date);
CREATE VIRTUAL TABLE IF NOT EXISTS speech_text USING fts5(text, major, minor, speaker, content='speeches',
                                                          content_rowid='id');
"""


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def parse_file(xml: bytes) -> list[dict[str, Any]]:
    """Headings and speeches in order: each speech carries the headings in force above it."""
    root = etree.fromstring(xml, parser=etree.XMLParser(recover=True, huge_tree=True))
    out, major, minor = [], None, None
    for el in root:
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag == "major-heading":
            major, minor = _clean("".join(el.itertext())), None
        elif tag == "minor-heading":
            minor = _clean("".join(el.itertext()))
        elif tag == "speech":
            paras = [_clean("".join(p.itertext())) for p in el.findall(".//p")]
            text = "\n\n".join(p for p in paras if p)
            if not text:
                continue
            out.append({"sid": el.get("id"), "major": major, "minor": minor, "person_id": el.get("person_id"),
                        "member_id": el.get("speakerid"),  # older files: a membership ID, mapped to a person later
                        "speaker": _clean(el.get("speakername")) or None, "time": el.get("time"),
                        "url": el.get("url"), "text": text})
    return out


class DevolvedStore:
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

    def known(self, leg: str) -> dict[str, str]:
        with self.connect() as con:
            return {r["key"]: r["name"] for r in con.execute("SELECT key, name FROM files WHERE legislature = ?",
                                                              (leg,))}

    def replace(self, leg: str, key: str, name: str, day: str, speeches: list[dict[str, Any]]) -> None:
        with self.connect() as con:
            old = con.execute("SELECT id, text, major, minor, speaker FROM speeches WHERE legislature = ? AND key = ?",
                              (leg, key)).fetchall()
            for r in old:  # keep the full-text index in step (external-content table)
                con.execute("INSERT INTO speech_text (speech_text, rowid, text, major, minor, speaker) "
                            "VALUES ('delete', ?, ?, ?, ?, ?)", (r["id"], r["text"], r["major"], r["minor"], r["speaker"]))
            con.execute("DELETE FROM speeches WHERE legislature = ? AND key = ?", (leg, key))
            for k, s in enumerate(speeches):
                cur = con.execute("INSERT INTO speeches (legislature, key, date, ord, sid, major, minor, person_id, "
                                  "speaker, time, url, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                  (leg, key, day, k, s["sid"], s["major"], s["minor"], s["person_id"], s["speaker"],
                                   s["time"], s["url"], s["text"]))
                con.execute("INSERT INTO speech_text (rowid, text, major, minor, speaker) VALUES (?, ?, ?, ?, ?)",
                            (cur.lastrowid, s["text"], s["major"], s["minor"], s["speaker"]))
            con.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, date('now'))", (leg, key, name, day))

    # ---- reading ------------------------------------------------------------------------------------------
    def days(self, leg: str, limit: int = 40, before: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(f"""
                SELECT key, date, count(*) AS speeches, count(DISTINCT person_id) AS speakers
                FROM speeches WHERE legislature = ? {"AND date < ?" if before else ""}
                GROUP BY key ORDER BY date DESC, key DESC LIMIT ?""",
                               (leg, before, limit) if before else (leg, limit)).fetchall()
            out = []
            for r in rows:  # the day's main headings, in order
                heads = [h[0] for h in con.execute(
                    "SELECT major FROM speeches WHERE legislature = ? AND key = ? AND major IS NOT NULL "
                    "GROUP BY major ORDER BY min(ord)", (leg, r["key"]))]
                out.append(dict(r) | {"headings": heads[:10]})
        return out

    def day(self, leg: str, key: str) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM speeches WHERE legislature = ? AND key = ? ORDER BY ord",
                               (leg, key)).fetchall()
        return [dict(r) for r in rows]

    def search(self, q: str, leg: str | None = None, limit: int = 25, offset: int = 0) -> tuple[int, list[dict]]:
        words = re.findall(r"[\w'’-]+", q)[:8]
        if not words:
            return 0, []
        match = " ".join(f'"{w}"' for w in words)
        where = "speech_text MATCH ?" + (" AND s.legislature = ?" if leg else "")
        params: list[Any] = [match, *([leg] if leg else [])]
        with self.connect() as con:
            total = con.execute(f"SELECT count(*) FROM speech_text JOIN speeches s ON s.id = speech_text.rowid "
                                f"WHERE {where}", params).fetchone()[0]
            rows = con.execute(f"""
                SELECT s.id, s.legislature, s.key, s.date, s.ord, s.major, s.minor, s.person_id, s.speaker, s.url,
                       snippet(speech_text, 0, '[', ']', '…', 30) AS snippet
                FROM speech_text JOIN speeches s ON s.id = speech_text.rowid WHERE {where}
                ORDER BY s.date DESC, s.ord LIMIT ? OFFSET ?""", [*params, limit, offset]).fetchall()
        return total, [dict(r) for r in rows]

    def relevant(self, keywords: list[str], *, date_from: str | None = None, date_to: str | None = None,
                 per_body: int = 2, min_words: int = 60) -> list[dict[str, Any]]:
        """The best-matching members' contributions in each parliament (all the words, ranked by relevance)."""
        words = [w for w in keywords if re.fullmatch(r"[\w'’-]+", w)][:5]
        if not words:
            return []
        match = " ".join(f'"{w}"' for w in words)
        out = []
        with self.connect() as con:
            for leg in LEGISLATURES:
                where = ["speech_text MATCH ?", "s.legislature = ?", "s.person_id IS NOT NULL",
                         "length(s.text) >= ?"]
                params: list[Any] = [match, leg, min_words * 5]
                if date_from:
                    where.append("s.date >= ?")
                    params.append(str(date_from))
                if date_to:
                    where.append("s.date <= ?")
                    params.append(str(date_to))
                rows = con.execute(f"""
                    SELECT s.* FROM speech_text JOIN speeches s ON s.id = speech_text.rowid
                    WHERE {' AND '.join(where)} ORDER BY bm25(speech_text, 1.0, 3.0, 2.0, 0.0) LIMIT ?""",
                                   [*params, per_body * 4]).fetchall()
                seen: set[str] = set()
                for r in rows:  # one contribution per member, so a few speakers do not fill the evidence
                    if r["person_id"] in seen:
                        continue
                    seen.add(r["person_id"])
                    out.append(dict(r))
                    if len(seen) >= per_body:
                        break
        return out

    def yearly(self, phrase: str) -> dict[str, dict[int, tuple[int, int]]]:
        """{body: {year: (contributions using the phrase, all contributions)}}, members' contributions only."""
        words = re.findall(r"[\w'’-]+", phrase)[:8]
        if not words:
            return {}
        with self.connect() as con:
            totals = con.execute("SELECT legislature, CAST(substr(date, 1, 4) AS INTEGER), count(*) FROM speeches "
                                 "WHERE person_id IS NOT NULL GROUP BY 1, 2").fetchall()
            hits = con.execute("""
                SELECT s.legislature, CAST(substr(s.date, 1, 4) AS INTEGER), count(*)
                FROM speech_text JOIN speeches s ON s.id = speech_text.rowid
                WHERE speech_text MATCH ? AND s.person_id IS NOT NULL GROUP BY 1, 2""",
                               ('text: "' + " ".join(words) + '"',)).fetchall()
        found = {(leg, y): n for leg, y, n in hits}
        out: dict[str, dict[int, tuple[int, int]]] = {}
        for leg, y, n in totals:
            out.setdefault(leg, {})[y] = (found.get((leg, y), 0), n)
        return out

    def for_person(self, person_id: str, limit: int = 8) -> dict[str, Any]:
        with self.connect() as con:
            n = con.execute("SELECT legislature, count(*) FROM speeches WHERE person_id = ? GROUP BY legislature",
                            (person_id,)).fetchall()
            rows = con.execute("""SELECT legislature, key, date, ord, major, minor, substr(text, 1, 320) AS excerpt
                                  FROM speeches WHERE person_id = ? ORDER BY date DESC, ord DESC LIMIT ?""",
                               (person_id, limit)).fetchall()
        return {"counts": {r[0]: r[1] for r in n}, "recent": [dict(r) for r in rows]}

    def counts(self) -> dict[str, Any]:
        with self.connect() as con:
            rows = con.execute("SELECT legislature, count(*), min(date), max(date) FROM speeches "
                               "GROUP BY legislature").fetchall()
        return {r[0]: {"speeches": r[1], "from": r[2], "to": r[3]} for r in rows}


# ---- who the speakers were on the day --------------------------------------------------------------------------
class Members:
    """Devolved memberships (seat, party, dates) and names from parlparse's people.json, reloaded when it changes."""

    def __init__(self, people_json: Path):
        self.path = Path(people_json)
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._spells: dict[str, list[dict[str, Any]]] = {}
        self._names: dict[str, list[tuple[str, str, str]]] = {}
        self._members: dict[str, str] = {}

    def _load(self) -> None:
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime
            except OSError:
                return
            if mtime == self._mtime:
                return
            people = json.loads(self.path.read_text(encoding="utf-8"))
            orgs = {o["id"]: o.get("name", o["id"]) for o in people.get("organizations", [])}
            posts = {p["id"]: p for p in people.get("posts", [])}
            wanted = {v["org"] for v in LEGISLATURES.values()}
            spells: dict[str, list[dict[str, Any]]] = {}
            for m in people.get("memberships", []):
                post = posts.get(m.get("post_id", ""), {})
                org = m.get("organization_id") or post.get("organization_id")
                if org not in wanted or not m.get("person_id"):
                    continue
                spells.setdefault(m["person_id"], []).append({
                    "org": org, "area": (post.get("area") or {}).get("name"),
                    "party": orgs.get(m.get("on_behalf_of_id", ""), m.get("on_behalf_of_id")),
                    "start": (m.get("start_date") or "0001-01-01")[:10], "end": (m.get("end_date") or "9999-12-31")[:10]})
            names: dict[str, list[tuple[str, str, str]]] = {}
            for p in people.get("persons", []):
                for n in p.get("other_names", []):
                    if n.get("note") == "Main":
                        full = n.get("name") or " ".join(x for x in (n.get("given_name"), n.get("family_name")) if x)
                        if full:
                            names.setdefault(p["id"], []).append(
                                ((n.get("start_date") or "0001-01-01")[:10], (n.get("end_date") or "9999-12-31")[:10],
                                 full))
            members = {m["id"]: m["person_id"] for m in people.get("memberships", []) if m.get("id") and m.get("person_id")}
            self._spells, self._names, self._members, self._mtime = spells, names, members, mtime

    def person_for_member(self, member_id: str | None) -> str | None:
        """The person behind a membership ID (older files name speakers this way)."""
        if not member_id:
            return None
        self._load()
        return self._members.get(member_id)

    def on(self, person_id: str | None, org: str, day: str) -> dict[str, Any]:
        """Name, seat and party of a member on a day (empty when not known)."""
        if not person_id:
            return {}
        self._load()
        spell = next((s for s in sorted(self._spells.get(person_id, []), key=lambda s: s["start"], reverse=True)
                      if s["org"] == org and s["start"] <= day <= s["end"]), None)
        name = next((n for s, e, n in sorted(self._names.get(person_id, []), reverse=True) if s <= day <= e), None)
        return {"name": name, "seat": spell["area"] if spell else None, "party": spell["party"] if spell else None}

    def memberships(self, person_id: str) -> list[dict[str, Any]]:
        self._load()
        names = {v["org"]: (k, v["name"], v["member"]) for k, v in LEGISLATURES.items()}
        out = []
        for s in sorted(self._spells.get(person_id, []), key=lambda s: s["start"]):
            leg, name, member = names[s["org"]]
            out.append({"legislature": leg, "body": name, "member": member, "seat": s["area"], "party": s["party"],
                        "start": s["start"], "end": None if s["end"].startswith("9999") else s["end"]})
        return out


# ---- fetching ---------------------------------------------------------------------------------------------------
def listing(client: httpx.Client, leg: str, since: date) -> dict[str, tuple[str, str, str]]:
    """{key: (latest file name, date, folder)} for the files listed since ``since`` (a later revision wins), from the
    body's folder and any earlier folder it was published in."""
    cfg = LEGISLATURES[leg]
    out: dict[str, tuple[str, str, str]] = {}
    for folder in [{"dir": cfg["dir"], "file": cfg["file"], "until": None}, *cfg.get("archives", [])]:
        resp = client.get(BASE + folder["dir"])
        resp.raise_for_status()
        for name in sorted(set(re.findall(r'href="([^"/]+\.xml)"', resp.text))):
            m = re.fullmatch(folder["file"], name)
            if m and m.group(2) >= since.isoformat() and (not folder["until"] or m.group(2) <= folder["until"]):
                out[m.group(1)] = (name, m.group(2), folder["dir"])  # sorted, so the last revision of a key is kept
    return out


def sync_devolved(store: DevolvedStore, *, since: date = SINCE, max_files: int = 60, members: Members | None = None,
                  client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """New and revised files, newest first, up to ``max_files`` per run across the three bodies."""
    own = client is None
    client = client or httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    stats: dict[str, Any] = {"files": 0, "speeches": 0}
    try:
        todo = []
        for leg in LEGISLATURES:
            known = store.known(leg)
            for key, (name, day, folder) in listing(client, leg, since).items():
                if known.get(key) != name:
                    todo.append((day, leg, key, name, folder))
            sleep(PAUSE)
        todo.sort(reverse=True)  # the newest first, so the site is current before the backfill finishes
        for day, leg, key, name, folder in todo[:max_files]:
            resp = client.get(BASE + folder + name)
            resp.raise_for_status()
            speeches = parse_file(resp.content)
            for sp in speeches:  # older files: the speaker's membership ID instead of their person ID
                if not sp["person_id"] and members is not None:
                    sp["person_id"] = members.person_for_member(sp.get("member_id"))
            store.replace(leg, key, name, day, speeches)
            stats["files"] += 1
            stats["speeches"] += len(speeches)
            sleep(PAUSE)
        stats["still_to_fetch"] = max(0, len(todo) - max_files)
    finally:
        if own:
            client.close()
    stats["totals"] = store.counts()
    return stats
