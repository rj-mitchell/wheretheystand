"""Every edition of the Register of Members' Financial Interests since 2000, as one archive of entries.

TheyWorkForYou keeps each edition (about 400 since November 2000). Each is read once (a few dozen per update) and
every entry is kept with the first and last edition that listed it, so the site can tell which interests an MP had
registered on any day: briefings check what members said against the interests they held then, not only the ones
they hold now, and MPs who left long ago still have their last entries. Entries are matched across editions by
member, category and wording; a reworded entry counts as a new one.

Kept in data/regmem_archive.sqlite. Addresses are left out, as for the latest edition.
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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from hansard.sources.demographics import USER_AGENT
from hansard.sources.regmem import BASE, editions, parse

log = logging.getLogger(__name__)

# an entry is taken to have still applied for a month after the last edition that listed it (editions come every two
# to four weeks, so it was dropped at some point before the next one)
GRACE = timedelta(days=31)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS editions (edition TEXT PRIMARY KEY, members INTEGER, read_at TEXT);
CREATE TABLE IF NOT EXISTS entries (person_id TEXT, ctype TEXT, cname TEXT, key TEXT, summary TEXT, details TEXT,
                                    children TEXT, registered TEXT, first_seen TEXT, last_seen TEXT,
                                    PRIMARY KEY (person_id, ctype, key));
CREATE INDEX IF NOT EXISTS entries_person ON entries (person_id, last_seen);
"""


def _key(summary: str) -> str:
    return " ".join(re.sub(r"[^\w£$€%.,&'-]+", " ", summary.lower()).split())[:300]


def _iso(raw: str | None) -> str | None:
    for fmt in ("%d %B %Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


class RegisterArchive:
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

    def read(self) -> set[str]:
        with self.connect() as con:
            return {r[0] for r in con.execute("SELECT edition FROM editions")}

    def add_edition(self, edition: str, members: dict[str, Any]) -> int:
        """Fold one edition in: new entries are added, known ones have their first and last edition widened."""
        rows = []
        for pid, m in members.items():
            for c in m.get("categories") or []:
                for item in c.get("items") or []:
                    if not item.get("summary"):
                        continue
                    rows.append((pid, c.get("type") or "", c.get("name") or "", _key(item["summary"]), item["summary"],
                                 json.dumps(item.get("details") or []), json.dumps(item.get("children") or []),
                                 _iso(item.get("registered")), edition, edition))
        with self.connect() as con:
            # the newest wording and details win; the dates only ever widen
            con.executemany("""
                INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (person_id, ctype, key) DO UPDATE SET
                    cname = CASE WHEN excluded.last_seen >= entries.last_seen THEN excluded.cname ELSE entries.cname END,
                    summary = CASE WHEN excluded.last_seen >= entries.last_seen THEN excluded.summary
                                   ELSE entries.summary END,
                    details = CASE WHEN excluded.last_seen >= entries.last_seen THEN excluded.details
                                   ELSE entries.details END,
                    children = CASE WHEN excluded.last_seen >= entries.last_seen THEN excluded.children
                                    ELSE entries.children END,
                    registered = coalesce(entries.registered, excluded.registered),
                    first_seen = min(entries.first_seen, excluded.first_seen),
                    last_seen = max(entries.last_seen, excluded.last_seen)""", rows)
            con.execute("INSERT OR REPLACE INTO editions VALUES (?, ?, datetime('now'))", (edition, len(members)))
        return len(rows)

    def _categories(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        cats: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for r in rows:
            cats.setdefault((r["ctype"], r["cname"]), []).append({
                "summary": r["summary"], "details": json.loads(r["details"] or "[]"),
                "children": json.loads(r["children"] or "[]"),
                # when it was registered, or failing that the first edition that listed it
                "registered_on": r["registered"] or r["first_seen"], "first_seen": r["first_seen"],
                "last_seen": r["last_seen"]})

        def order(k: tuple[str, str]) -> tuple[float, str]:
            try:
                return float(k[0]), k[1]
            except ValueError:
                return 99.0, k[1]

        return [{"type": t, "name": n, "items": items} for (t, n), items in sorted(cats.items(), key=lambda kv: order(kv[0]))]

    def categories_on(self, person_id: str, on: date) -> list[dict[str, Any]] | None:
        """The entries a member had registered on ``on`` (registered by then, and still listed then or within a
        month of it). None when the archive has nothing for them, so callers can fall back to the latest edition."""
        with self.connect() as con:
            if con.execute("SELECT 1 FROM entries WHERE person_id = ? LIMIT 1", (person_id,)).fetchone() is None:
                return None
            rows = con.execute("""
                SELECT * FROM entries WHERE person_id = ? AND coalesce(registered, first_seen) <= ?
                  AND first_seen <= ? AND last_seen >= ? ORDER BY ctype, summary""",
                (person_id, on.isoformat(), (on + GRACE).isoformat(), (on - GRACE).isoformat())).fetchall()
        return self._categories(rows)

    def last_listed(self, person_id: str) -> dict[str, Any] | None:
        """A member's entries in the last edition that listed them: for MPs who left before the site's other copies."""
        with self.connect() as con:
            last = con.execute("SELECT max(last_seen) FROM entries WHERE person_id = ?", (person_id,)).fetchone()[0]
            if not last:
                return None
            rows = con.execute("SELECT * FROM entries WHERE person_id = ? AND last_seen = ? ORDER BY ctype, summary",
                               (person_id, last)).fetchall()
        return {"edition": last, "categories": self._categories(rows)}


def refresh_archive(archive: RegisterArchive, *, client: httpx.Client | None = None, max_editions: int = 40,
                    sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Read up to ``max_editions`` editions not read yet, newest first (so recent years are complete soonest)."""
    own = client is None
    client = client or httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    done, entries, read = archive.read(), 0, 0
    try:
        todo = [(n, d) for n, d in reversed(editions(client)) if d not in done]
        for name, day in todo[:max_editions]:
            resp = client.get(BASE + name)
            resp.raise_for_status()
            entries += archive.add_edition(day, parse(resp.content)["members"])
            read += 1
            sleep(1.0)
    finally:
        if own:
            client.close()
    return {"editions_read": read, "entries": entries, "editions_left": max(0, len(todo) - read)}
