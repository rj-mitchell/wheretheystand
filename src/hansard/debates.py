"""Debate pages: a whole debate as recorded, in order, with each speaker as they were on the day.

A debate is every contribution under one heading on one sitting day in one chamber: a Commons or Lords debate
(by its main heading), a Westminster Hall debate, or a Public Bill Committee sitting on a bill. Divisions held in the
debate appear where they happened, with the kind of vote. Built from the stored record (no AI).
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from datetime import date
from typing import Any

from hansard.config import Settings
from hansard.reference.profiles import ProfileResolver
from hansard.store.parquet import attach_store, partition_dir
from hansard.store.shared import shared
from hansard.vote_types import vote_types

# the chamber as it appears in a debate's address, and the record source behind it
CHAMBERS = {"commons": "debates", "lords": "lordspages", "westminster-hall": "westminhall",
            "public-bill-committee": "standing",
            "commons-written-statements": "wms", "lords-written-statements": "lordswms"}
STATEMENTS = {"wms", "lordswms"}  # written ministerial statements: one page per statement
SOURCES = {v: k for k, v in CHAMBERS.items()}
CHAMBER_NAMES = {"commons": "House of Commons", "lords": "House of Lords", "westminster-hall": "Westminster Hall",
                 "public-bill-committee": "Public Bill Committee",
                 "commons-written-statements": "House of Commons", "lords-written-statements": "House of Lords"}
_IN_THE_CHAIR = re.compile(r"\s*[—-]\s*\[[^\]]*in the chair\]\s*$", re.I)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def person_number(person_id: str) -> int | None:
    tail = person_id.rsplit("/", 1)[-1]
    return int(tail) if "/person/" in person_id and tail.isdigit() else None


def title_of(source: str, major: str | None, minor: str | None, bill: str | None) -> str | None:
    """The heading a contribution's debate is known by (None for records that are not part of a debate)."""
    if source == "standing":
        return bill
    if source == "westminhall":
        t = major or minor
        return _IN_THE_CHAIR.sub("", t) if t else None
    if source in ("debates", "lordspages"):
        return major or minor
    if source in STATEMENTS:  # the statement's title (the department is its main heading)
        return minor or major
    return None


def debate_path(source: str, on: date | str, major: str | None, minor: str | None = None,
                bill: str | None = None, record_id: str | None = None) -> str | None:
    """The site address of the debate a record belongs to, with the record's anchor, or None."""
    title = title_of(source, major, minor, bill)
    s = slug(title or "")
    if source not in SOURCES or not s:
        return None
    day = on.isoformat() if isinstance(on, date) else str(on)[:10]
    return f"/debates/{day}/{SOURCES[source]}/{s}" + (f"#{anchor(record_id)}" if record_id else "")


def anchor(record_id: str) -> str:
    col, n = _order(record_id)
    return f"c{col}-{n}"


def _order(record_id: str) -> tuple[int, int]:
    parts = record_id.rsplit(".", 2)
    if len(parts) < 3:
        return 0, 0
    return (int(parts[1]) if parts[1].isdigit() else 0), (int(parts[2]) if parts[2].isdigit() else 0)


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


class Debates:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.con = shared()
        self.stores = set(attach_store(self.con, settings.parquet_dir))
        self.resolver = ProfileResolver(settings.reference_dir)
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple, dict[str, Any] | None] = OrderedDict()

    def _day(self, source: str, day: date) -> list[dict[str, Any]]:
        d = partition_dir(self.settings.parquet_dir, "records", source, day.year)
        if not d.exists() or not any(d.glob("*.parquet")):
            return []
        rel = self.con.execute(
            f"SELECT record_id, source_key, record_type, time, colnum, major_heading, minor_heading, bill, "
            f"xml_person_id, speaker_name_raw, text, official_url, twfy_url, house FROM read_parquet("
            f"'{d.as_posix()}/*.parquet', union_by_name = true) WHERE date = ?", [day])
        cols = [c[0] for c in rel.description]
        seen: dict[str, dict[str, Any]] = {}
        for row in rel.fetchall():
            r = dict(zip(cols, row, strict=True))
            seen.setdefault(r["record_id"], r)  # an ID can repeat within a file: keep one
        return list(seen.values())

    def get(self, day: str, chamber: str, debate_slug: str, version: Any = None) -> dict[str, Any] | None:
        key = (str(version), day, chamber, debate_slug)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        out = self._build(day, chamber, debate_slug)
        with self._lock:
            self._cache[key] = out
            while len(self._cache) > 200:
                self._cache.popitem(last=False)
        return out

    def _build(self, day: str, chamber: str, debate_slug: str) -> dict[str, Any] | None:
        source = CHAMBERS.get(chamber)
        try:
            on = date.fromisoformat(day)
        except ValueError:
            return None
        if source is None or "records" not in self.stores:
            return None
        records = self._day(source, on)
        titles = {}
        for r in records:
            t = title_of(source, r["major_heading"], r["minor_heading"], r["bill"])
            if t:
                titles.setdefault(slug(t), t)
        mine = [r for r in records
                if slug(title_of(source, r["major_heading"], r["minor_heading"], r["bill"]) or "") == debate_slug]
        if not mine:
            return None
        mine.sort(key=lambda r: (r["source_key"] or "", _order(r["record_id"])))
        profiles = self.resolver.profiles([(r["record_id"], r["xml_person_id"], on) for r in mine
                                           if r["xml_person_id"]])
        items: list[dict[str, Any]] = []
        used: set[str] = set()
        section = None
        for r in mine:
            sub = r["minor_heading"] if source not in ("westminhall", *STATEMENTS) else None
            if source == "standing" and r["major_heading"] not in (None, "Public Bill Committee"):
                sub = r["minor_heading"] or r["major_heading"]
            if sub and sub != section:
                section = sub
                items.append({"kind": "heading", "text": sub, "id": f"h-{anchor(r['record_id'])}"})
            prof = profiles.get(r["record_id"])
            speaker = None
            if r["xml_person_id"]:
                speaker = {"name": (prof.name if prof else None) or r["speaker_name_raw"],
                           "party": prof.party if prof else None,
                           "constituency": prof.constituency if prof else None,
                           "roles": prof.office_titles[:2] if prof else [],
                           "number": person_number(prof.person_id) if prof and prof.person_id else None}
                if speaker["number"]:
                    speaker["slug"] = slug(speaker["name"] or "")
            aid = anchor(r["record_id"])
            if aid in used:  # two sittings of a committee on one day can share column numbers
                aid = f"{aid}-{r['source_key']}"
            used.add(aid)
            items.append({"kind": "said" if speaker else "procedural", "id": aid,
                          "record_id": r["record_id"], "time": r["time"], "colnum": r["colnum"],
                          "speaker": speaker, "speaker_name_raw": r["speaker_name_raw"],
                          "paragraphs": _paragraphs(r["text"]), "official_url": r["official_url"],
                          "twfy_url": r["twfy_url"]})
        self._add_divisions(items, source, on, debate_slug)
        title = titles[debate_slug]
        house = mine[0]["house"]
        others = sorted(((t, s) for s, t in titles.items() if s != debate_slug), key=lambda x: x[0].lower())
        statement = source in STATEMENTS
        return {"title": title, "date": on.isoformat(), "chamber": chamber, "chamber_name": CHAMBER_NAMES[chamber],
                "kind": "statement" if statement else "debate",
                "department": (mine[0]["major_heading"] if statement and mine[0]["major_heading"] != title else None),
                "house": house, "official_url": next((r["official_url"] for r in mine if r["official_url"]), None),
                "contributions": sum(1 for i in items if i["kind"] == "said"),
                "speakers": len({i["speaker"]["name"] for i in items if i["kind"] == "said"}),
                "items": items,
                "same_day": [{"title": t, "path": f"/debates/{on.isoformat()}/{chamber}/{s}"} for t, s in others]}

    def _add_divisions(self, items: list[dict[str, Any]], source: str, on: date, debate_slug: str) -> None:
        if source not in ("debates", "lordspages") or "divisions" not in self.stores:
            return
        house = "commons" if source == "debates" else "lords"
        rows = self.con.execute("""SELECT division_id, number, major_heading, minor_heading, ayes, noes,
                                          coalesce(official_url, twfy_url) FROM divisions
                                   WHERE date = ? AND house = ?""", [on, house]).fetchall()
        divs = [{"kind": "division", "id": "div-" + anchor(r[0])[1:], "division_id": r[0], "number": r[1], "title": r[3] or r[2],
                 "ayes": r[4], "noes": r[5], "url": r[6]}
                for r in rows if slug(r[2] or r[3] or "") == debate_slug]
        if not divs:
            return
        vote_types(self.settings).annotate(divs)
        for d in divs:  # after the last contribution that comes before it in the record
            pos = _order(d["division_id"])
            at = max((k + 1 for k, i in enumerate(items) if "record_id" in i and _order(i["record_id"]) <= pos),
                     default=len(items))
            items.insert(at, d)
