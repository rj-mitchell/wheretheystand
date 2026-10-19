"""Oral evidence transcripts: what witnesses and committee members said in select committee evidence sessions.

Each session's transcript is published by the UK Parliament Committees API as an HTML document (converted from Word):
a header (committee, inquiry, date, members present, witnesses), then turns of the form "Speaker: words", with
following paragraphs continuing the same speaker's answer. They are fetched a few hundred per update, newest first,
from ``since``, into their own store (data/evidence.sqlite, with a full-text index), so that witness evidence can be
read and searched on the site. Witnesses are not members of Parliament: their words are labelled as evidence to a
committee, never as a parliamentary position.

Substantial answers are also embedded for meaning-based search (hansard.index.evidence), so a briefing finds evidence
that puts a point in different words from the question. ``embedded`` notes which sessions are done; a re-fetched
transcript is embedded again.
"""

from __future__ import annotations

import base64
import html
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

from hansard.sources.demographics import USER_AGENT

log = logging.getLogger(__name__)

API = "https://committees-api.parliament.uk/api"
SINCE = date(2014, 1, 1)  # Parliament's committees service has transcripts from 2014
PAUSE = 0.5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (session_id INTEGER PRIMARY KEY, fetched TEXT, title TEXT, header TEXT,
                                        turns INTEGER, status TEXT);
CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, session_id INTEGER, ord INTEGER, speaker TEXT, question TEXT,
                                  text TEXT);
CREATE INDEX IF NOT EXISTS turns_session ON turns (session_id, ord);
CREATE VIRTUAL TABLE IF NOT EXISTS turn_text USING fts5(text, speaker, content='turns', content_rowid='id');
CREATE TABLE IF NOT EXISTS embedded (session_id INTEGER PRIMARY KEY, at TEXT);
"""
# "Q12 The Chair:", "Professor Keith Bell:", "Lord Krebs:", "Dr Emily Wallace:"
_TURN = re.compile(r"^(?:(Q\d+)\s+)?((?:[A-Z][\w'’.\-]*\.?|of|the|for|and|de|van|von)"
                   r"(?:\s+(?:[A-Z][\w'’.\-]*\.?|of|the|for|and|de|van|von|\([^)]{1,40}\))){0,7}):\s+(.+)$", re.S)
_SKIP = re.compile(r"^(\d{1,3}|Watch the meeting|USE OF THE TRANSCRIPT)$", re.I)


def _paragraphs(doc: str) -> list[str]:
    doc = re.sub(r"<img[^>]*>", "", doc)
    paras = re.findall(r"<p[^>]*>(.*?)</p>", doc, re.S | re.I)
    out = [" ".join(html.unescape(re.sub(r"<[^>]+>", "", p)).split()) for p in paras]
    return [p for p in out if p and not _SKIP.match(p)]


def parse_transcript(doc: str) -> tuple[list[str], list[dict[str, Any]]]:
    """(header paragraphs, turns). The header runs to "Examination of witness(es)" (or the first turn)."""
    paras = _paragraphs(doc)
    start = next((k + 1 for k, p in enumerate(paras) if p.lower().startswith("examination of witness")), None)
    if start is None:
        start = next((k for k, p in enumerate(paras) if _TURN.match(p)), len(paras))
    header, turns = paras[:start], []
    for p in paras[start:]:
        m = _TURN.match(p)
        if m and len(m.group(2)) <= 80:
            turns.append({"question": m.group(1), "speaker": m.group(2), "text": m.group(3).strip()})
        elif turns:
            turns[-1]["text"] += "\n\n" + p
        # text before the first turn (stage directions) is dropped
    return header, turns


_TITLES = {"professor", "prof", "dr", "sir", "dame", "mr", "mrs", "ms", "miss", "mx", "rt", "hon", "the", "right",
           "honourable", "mp", "obe", "mbe", "cbe", "kc", "qc", "frs", "freng", "fmedsci", "cb", "dbe", "kbe"}


def _name_key(name: str | None) -> list[str]:
    words = re.findall(r"[a-z'’-]+", (name or "").lower().replace("’", "'"))
    return [w for w in words if w not in _TITLES]


def witness_for(speaker: str | None, witnesses: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The listed witness a transcript speaker is, if any (committee members and the chair are not witnesses)."""
    said = _name_key(speaker)
    if not said or said[0] == "chair":
        return None
    for w in witnesses:
        listed = _name_key(w.get("name"))
        if not listed:
            continue
        if said == listed or (said[-1] == listed[-1] and (len(said) == 1 or said[0] == listed[0])):
            return w
    return None


def header_witnesses(header: list[str]) -> list[dict[str, Any]]:
    """The witnesses a transcript's own header lists, for sessions Parliament's records list none for: after a
    "Witnesses" line, panels such as "I: Dr Sarah Scobie, Deputy Director of Research, Nuffield Trust; Professor Sir
    Nigel Shadbolt, Executive Chair, Open Data Institute; and Matt Hennessy, ..." (name, then role, then body)."""
    out: list[dict[str, Any]] = []
    started = False
    for para in header:
        m = re.match(r"^\s*Witness(?:es)?\b\s*:?\s*(.*)$", para, re.I)
        if m:
            started, para = True, m.group(1)
        elif not started:
            continue
        para = re.sub(r"^\s*[IVX]+\s*[:.]\s*", "", para).strip().rstrip(".")
        if not para or para.lower().startswith("examination of witness"):
            continue
        for item in para.split(";"):
            item = re.sub(r"^\s*and\s+", "", item.strip())
            name, _, rest = item.partition(",")
            if not name.strip() or len(name) > 80:
                continue
            role, _, body = rest.strip().rpartition(", ") if ", " in rest else (rest.strip(), "", "")
            out.append({"name": name.strip(), "role": role.strip() or None, "organisation": body.strip() or None})
    return out


def decode(body: Any) -> str:
    """The API returns {"data": base64 HTML}; older responses may be the HTML itself."""
    if isinstance(body, dict) and body.get("data"):
        return base64.b64decode(body["data"]).decode("utf-8", "ignore")
    return body if isinstance(body, str) else ""


class EvidenceStore:
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

    def fetched(self) -> set[int]:
        with self.connect() as con:
            return {r[0] for r in con.execute("SELECT session_id FROM transcripts")}

    def save(self, session_id: int, header: list[str], turns: list[dict[str, Any]], status: str = "ok") -> None:
        with self.connect() as con:
            for r in con.execute("SELECT id, text, speaker FROM turns WHERE session_id = ?", (session_id,)).fetchall():
                con.execute("INSERT INTO turn_text (turn_text, rowid, text, speaker) VALUES ('delete', ?, ?, ?)",
                            (r["id"], r["text"], r["speaker"]))
            con.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            for k, t in enumerate(turns):
                cur = con.execute("INSERT INTO turns (session_id, ord, speaker, question, text) VALUES (?, ?, ?, ?, ?)",
                                  (session_id, k, t["speaker"], t["question"], t["text"]))
                con.execute("INSERT INTO turn_text (rowid, text, speaker) VALUES (?, ?, ?)",
                            (cur.lastrowid, t["text"], t["speaker"]))
            con.execute("DELETE FROM embedded WHERE session_id = ?", (session_id,))  # embedded again when new
            con.execute("INSERT OR REPLACE INTO transcripts VALUES (?, date('now'), ?, ?, ?, ?)",
                        (session_id, header[1] if len(header) > 1 else (header[0] if header else None),
                         json.dumps(header[:12]), len(turns), status))

    def transcript(self, session_id: int) -> dict[str, Any] | None:
        with self.connect() as con:
            t = con.execute("SELECT * FROM transcripts WHERE session_id = ? AND status = 'ok'", (session_id,)).fetchone()
            if t is None:
                return None
            turns = con.execute("SELECT ord, speaker, question, text FROM turns WHERE session_id = ? ORDER BY ord",
                                (session_id,)).fetchall()
        return {"session_id": session_id, "title": t["title"], "header": json.loads(t["header"] or "[]"),
                "turns": [dict(r) for r in turns]}

    def distinctive(self, words: list[str], share: float = 0.01) -> list[str]:
        """Those of the words found in under ``share`` of all answers ("Palantir", not "company" or "NHS"): an answer
        that mentions none of them is not specific enough to the question to quote. Counted once per word."""
        cache = self.__dict__.setdefault("_word_counts", {})
        out = []
        with self.connect() as con:
            if "" not in cache:
                cache[""] = con.execute("SELECT count(*) FROM turns").fetchone()[0] or 1
            for w in words:
                if not re.fullmatch(r"[\w'’-]+", w):
                    continue
                if w not in cache:
                    cache[w] = con.execute("SELECT count(*) FROM turn_text WHERE turn_text MATCH ?",
                                           (f'"{w}"',)).fetchone()[0]
                if 0 < cache[w] < share * cache[""]:
                    out.append(w)
        return out

    def headers(self, session_ids: set[int]) -> dict[int, list[str]]:
        """{session_id: the transcript's header paragraphs}."""
        if not session_ids:
            return {}
        marks = ", ".join("?" for _ in session_ids)
        with self.connect() as con:
            rows = con.execute(f"SELECT session_id, header FROM transcripts WHERE session_id IN ({marks})",
                               list(session_ids)).fetchall()
        return {r["session_id"]: json.loads(r["header"] or "[]") for r in rows}

    def search(self, q: str, limit: int = 25, offset: int = 0) -> tuple[int, list[dict[str, Any]]]:
        words = re.findall(r"[\w'’-]+", q)[:8]
        if not words:
            return 0, []
        match = " ".join(f'"{w}"' for w in words)
        with self.connect() as con:
            total = con.execute("SELECT count(*) FROM turn_text WHERE turn_text MATCH ?", (match,)).fetchone()[0]
            rows = con.execute("""
                SELECT t.session_id, t.ord, t.speaker, snippet(turn_text, 0, '[', ']', '…', 30) AS snippet
                FROM turn_text JOIN turns t ON t.id = turn_text.rowid WHERE turn_text MATCH ?
                ORDER BY t.session_id DESC, t.ord LIMIT ? OFFSET ?""", (match, limit, offset)).fetchall()
        return total, [dict(r) for r in rows]

    def relevant(self, keywords: list[str], limit: int = 120, min_chars: int = 300) -> list[dict[str, Any]]:
        """Substantial turns containing all the words, best match first."""
        words = [w for w in keywords if re.fullmatch(r"[\w'’-]+", w)][:5]
        if not words:
            return []
        match = " ".join(f'"{w}"' for w in words)
        with self.connect() as con:
            rows = con.execute("""
                SELECT t.session_id, t.ord, t.speaker, t.question, t.text FROM turn_text JOIN turns t
                ON t.id = turn_text.rowid WHERE turn_text MATCH ? AND length(t.text) >= ?
                ORDER BY bm25(turn_text, 1.0, 0.0) LIMIT ?""", (match, min_chars, limit)).fetchall()
        return [dict(r) for r in rows]

    def available(self) -> set[int]:
        with self.connect() as con:
            return {r[0] for r in con.execute("SELECT session_id FROM transcripts WHERE status = 'ok'")}

    # ---- meaning-based search (hansard.index.evidence) -----------------------------------------------------------
    def unembedded(self, limit: int | None = None) -> list[int]:
        """Sessions with a transcript whose answers have not been embedded yet, newest first."""
        with self.connect() as con:
            rows = con.execute("""SELECT t.session_id FROM transcripts t LEFT JOIN embedded e USING (session_id)
                                  WHERE t.status = 'ok' AND e.session_id IS NULL ORDER BY t.session_id DESC"""
                               + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
        return [r[0] for r in rows]

    def turns_for(self, session_ids: list[int], min_chars: int = 300) -> list[dict[str, Any]]:
        """The substantial turns of these sessions (the ones a briefing could quote)."""
        if not session_ids:
            return []
        marks = ", ".join("?" for _ in session_ids)
        with self.connect() as con:
            rows = con.execute(f"""SELECT session_id, ord, speaker, question, text FROM turns
                                   WHERE session_id IN ({marks}) AND length(text) >= ? ORDER BY session_id, ord""",
                               (*session_ids, min_chars)).fetchall()
        return [dict(r) for r in rows]

    def mark_embedded(self, session_ids: list[int]) -> None:
        with self.connect() as con:
            con.executemany("INSERT OR REPLACE INTO embedded VALUES (?, datetime('now'))", [(s,) for s in session_ids])

    def turns_by_key(self, keys: list[tuple[int, int]]) -> dict[tuple[int, int], dict[str, Any]]:
        """{(session_id, ord): turn} for the turns found by meaning-based search."""
        out: dict[tuple[int, int], dict[str, Any]] = {}
        with self.connect() as con:
            for i in range(0, len(keys), 400):
                part = keys[i:i + 400]
                where = " OR ".join("(session_id = ? AND ord = ?)" for _ in part)
                for r in con.execute(f"SELECT session_id, ord, speaker, question, text FROM turns WHERE {where}",
                                     [x for k in part for x in k]):
                    out[(r["session_id"], r["ord"])] = dict(r)
        return out


def sync_transcripts(store: EvidenceStore, sessions: list[tuple[int, str]], *, since: date = SINCE,
                     max_sessions: int = 300, client: httpx.Client | None = None,
                     sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Fetch transcripts for ``sessions`` [(id, date)] not yet fetched, newest first."""
    done = store.fetched()
    todo = sorted(((d, sid) for sid, d in sessions if d and d >= since.isoformat() and sid not in done), reverse=True)
    own = client is None
    client = client or httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    stats = {"fetched": 0, "turns": 0, "missing": 0}
    try:
        for _, sid in todo[:max_sessions]:
            resp = client.get(f"{API}/OralEvidence/{sid}/Document/Html")
            if resp.status_code == 429:
                log.warning("committee transcripts: rate limited; carrying on at the next update")
                stats["rate_limited"] = True
                break
            if resp.status_code == 404:
                store.save(sid, [], [], status="missing")
                stats["missing"] += 1
                sleep(PAUSE)
                continue
            resp.raise_for_status()
            header, turns = parse_transcript(decode(resp.json() if "json" in resp.headers.get("content-type", "")
                                                    else resp.text))
            store.save(sid, header, turns, status="ok" if turns else "empty")
            stats["fetched"] += 1
            stats["turns"] += len(turns)
            sleep(PAUSE)
    finally:
        if own:
            client.close()
    stats["still_to_fetch"] = max(0, len(todo) - max_sessions)
    return stats
