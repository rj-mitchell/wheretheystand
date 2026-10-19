"""The lighter side of the record: what made the House laugh (Hansard marks it "[Laughter.]"), the same week in
earlier years, and early day motions you might not expect. Found in the record by plain rules, with no AI and no
commentary; every item links to where it was said.

Laughter is not always light: tributes and grave subjects are left out, as are short or formulaic lines that only
make sense in the moment, and anything listed in data_static/lighter_exclude.txt."""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from typing import Any

from hansard.config import STATIC_DIR
from hansard.debates import debate_path

LAUGHTER = "[Laughter"
# Early day motions on the lighter side, found by whole words in their titles: food and drink prizes, birthdays and
# centenaries, animals, records and village life
EDM_WORDS = re.compile(
    r"\b(pies?|cakes?|cheeses?|crisps|chip shops?|fish and chips|pub of the year|pubs?|brass bands?|gnomes?|pigeons?|"
    r"ferrets?|tortoises?|owls?|biscuits?|scones?|pantomimes?|morris (?:men|dancers?|dancing)|bagpipes?|conkers?|"
    r"sausages?|pasties|pasty|doughnuts?|100th birthday|centenar\w+|world records?|birthday|bake off|"
    r"curry|curries|tea rooms?|ice cream|cats?|dogs?|ducks?|donkeys?|llamas?|alpacas?)\b", re.I)


# debates where laughter is not light: tributes, condolences and grave subjects
GRAVE_HEADING = re.compile(
    r"tribute|condolence|death of|in memoriam|obituar|memorial|remembrance|bereave|funeral|passing of|"
    r"murder|terror|attack|abuse|suicide|genocide|massacre|famine|chagos|hostage|disaster|grenfell|hillsborough|"
    r"covid|pandemic|bereavement|loss of life|killed|war\b|conflict|gaza|ukraine|israel|cancer|hospice|dementia|"
    r"knife crime|domestic violence|self-harm|stillbirth|baby loss", re.I)
GRAVE_TEXT = re.compile(
    r"\b(died|death|dead|pain|hurt|grief|grieving|victims?|abuse|murder\w*|killed|suicide|cancer|tragic|tragedy|"
    r"funeral|condolences|mourn\w*|bereaved|harrowing|suffering|trauma\w*)\b", re.I)
# lines that raise a laugh only in the moment: thanks, courtesies, the Chair's interventions
FORMULAIC = re.compile(
    r"^…?\s*(?:(?:My Lords|Mr Speaker|Madam Speaker|Mr Deputy Speaker|Madam Deputy Speaker),\s*)?"
    r"(I (?:do )?thank|I am (?:very )?grateful|I think the (?:Minister|hon\.|right hon\.)[^.]{0,40}got the message|"
    r"Order\b|I give way|Will the hon\.|I will give way|Thank you)", re.I)
MIN_WORDS = 12
TITLE_PART = re.compile(r":")


def _excluded() -> tuple[set[str], list[str]]:
    """Record IDs and phrases to leave out (data_static/lighter_exclude.txt: one per line, # for notes)."""
    ids, phrases = set(), []
    try:
        lines = (STATIC_DIR / "lighter_exclude.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ids, phrases
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line.startswith("uk.org.publicwhip/"):
            ids.add(line)
        elif line:
            phrases.append(line.lower())
    return ids, phrases


def heading(major: str | None, minor: str | None) -> str | None:
    """One title for the moment: the debate's own title. A Westminster Hall day's major heading can be the previous
    debate's title, so when both are full titles ("School Funding: Stockport", "Nuclear Energy: Scotland") only the
    second is kept; "[Mr X in the Chair]" is dropped."""
    from hansard.rank.places import _CHAIR

    major, minor = (_CHAIR.sub("", x or "").strip() or None for x in (major, minor))
    if major and minor and TITLE_PART.search(major) and TITLE_PART.search(minor):
        return minor
    return " — ".join(x for x in (major, minor) if x) or None


def light(m: dict[str, Any], row: dict[str, Any], excluded: tuple[set[str], list[str]]) -> bool:
    """Whether a moment of laughter belongs on the lighter side."""
    ids, phrases = excluded
    head = " ".join(x for x in (row.get("major_heading"), row.get("minor_heading")) if x)
    text = m["text"]
    return not (row["record_id"] in ids or GRAVE_HEADING.search(head) or GRAVE_TEXT.search(text)
                or FORMULAIC.search(text) or len(text.split()) < MIN_WORDS
                or any(p in text.lower() for p in phrases))


def _sentences_before(text: str, at: int, limit: int = 320) -> str:
    """The sentence or two that drew the laugh: the text before the marker, from a sentence start."""
    before = text[:at].rstrip(" —-–").rstrip()
    start = max(before.rfind("\n", 0, len(before) - 1) + 1, 0)
    chunk = before[start:]
    if len(chunk) > limit:
        cut = chunk[-limit:]
        m = re.search(r"(?<=[.!?”\"])\s+[A-Z“\"]", cut)
        chunk = ("…" + cut[m.start() + 1:].lstrip()) if m else "…" + cut.split(" ", 1)[-1]
    return chunk.strip()


def _moment(row: dict[str, Any], marker: str) -> dict[str, Any] | None:
    text = row["text"] or ""
    at = text.find(marker)
    if at < 0:
        return None
    line = _sentences_before(text, at) if marker == LAUGHTER else _around(text, at)
    if len(line) < 20:
        return None
    d = row["date"]
    speaker = re.sub(r"^(?:Mr|Mrs|Ms|Miss|Dr)\.?\s+", "", row["speaker_name_raw"] or "") or None
    return {"date": d.isoformat(), "speaker": speaker, "person_id": row["person_id"],
            "heading": heading(row["major_heading"], row["minor_heading"]),
            "chamber": row["chamber"], "text": line, "twfy_url": row["twfy_url"],
            "debate_path": debate_path(row["source"], d, row["major_heading"], row["minor_heading"], row["bill"],
                                       row["record_id"])}


def _around(text: str, at: int, width: int = 160) -> str:
    lo, hi = max(at - width, 0), min(at + width, len(text))
    out = text[lo:hi].replace("\n", " ").strip()
    return ("…" if lo else "") + out + ("…" if hi < len(text) else "")


class Lighter:
    def __init__(self, con, today: date | None = None):
        self.con = con  # DuckDB with the records store and reference tables attached
        self.today = today or date.today()

    def _rows(self, where: str, params: list[Any], limit: int) -> list[dict[str, Any]]:
        rel = self.con.execute(f"""
            SELECT DISTINCT ON (r.record_id) r.record_id, r.date, r.source, r.chamber, r.major_heading,
                   r.minor_heading, r.bill, r.speaker_name_raw, r.text, r.twfy_url, p.person_id
            FROM records r LEFT JOIN ref_person_ids p ON p.xml_id = r.xml_person_id
            WHERE {where} ORDER BY r.record_id, r.date DESC""", params)
        cols = [c[0] for c in rel.description]
        rows = [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
        rows.sort(key=lambda r: (r["date"], r["record_id"]), reverse=True)
        return rows[:limit]

    def laughter(self, limit: int = 30) -> list[dict[str, Any]]:
        """The most recent moments the record marks as laughter, one per speaker per day."""
        since = self.today.year - 1
        rows = self._rows("r.year >= ? AND r.text LIKE '%[Laughter%' AND r.record_type = 'speech'", [since], 600)
        out, seen, excluded = [], set(), _excluded()
        for r in rows:
            m = _moment(r, LAUGHTER)
            if m and light(m, r, excluded) and (m["date"], m["speaker"]) not in seen:
                seen.add((m["date"], m["speaker"]))
                out.append(m)
            if len(out) >= limit:
                break
        return out

    def on_this_day(self, limit: int = 12) -> list[dict[str, Any]]:
        """Laughter in the same week of earlier years, newest year first, at most two a year."""
        days = [(self.today + timedelta(days=k)).strftime("%m-%d") for k in range(-3, 4)]
        rows = self._rows("r.year < ? AND strftime(r.date, '%m-%d') IN (SELECT unnest(?)) "
                          "AND r.text LIKE '%[Laughter%' AND r.record_type = 'speech'",
                          [self.today.year, days], 900)
        out, per_year, excluded = [], {}, _excluded()
        for r in rows:
            m = _moment(r, LAUGHTER)
            year = r["date"].year
            if m and light(m, r, excluded) and per_year.get(year, 0) < 2:
                per_year[year] = per_year.get(year, 0) + 1
                out.append(m)
            if len(out) >= limit:
                break
        return out


def lighter_edms(path, limit: int = 20) -> list[dict[str, Any]]:
    """Early day motions whose titles are on the lighter side (by whole words), newest first."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute("SELECT id, uin, title, date_tabled, primary_name, primary_mnis FROM edms "
                           "WHERE date_tabled >= date('now', '-3 years') ORDER BY date_tabled DESC").fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        title = r[2] or ""
        if not EDM_WORDS.search(title) or GRAVE_HEADING.search(title) or any(similar(title, o["title"]) for o in out):
            continue  # not light, or much the same as one already listed (several MPs table the same motion)
        out.append({"id": r[0], "uin": r[1], "title": title, "date": r[3], "tabled_by": r[4], "mnis": r[5]})
        if len(out) >= limit:
            break
    return out


def similar(a: str, b: str) -> bool:
    """Titles sharing most of their words ("100th birthday of Sir David Attenborough" tabled three ways)."""
    wa, wb = (set(re.findall(r"[a-z0-9]+", x.lower())) - {"the", "of", "and", "a", "in", "on", "s"} for x in (a, b))
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.6
