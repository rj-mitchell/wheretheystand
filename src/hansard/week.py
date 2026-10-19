"""In Parliament this week: the debates, votes, bills and statements of one sitting week, from the record.

Free (no AI): a guide to what happened, linking to each debate in full. Weeks run Monday to Sunday.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, defaultdict
from datetime import date, timedelta
from typing import Any

from hansard.config import Settings
from hansard.debates import SOURCES, anchor, debate_path, slug, title_of
from hansard.store.parquet import attach_store
from hansard.store.shared import shared
from hansard.vote_types import vote_types

CHAMBERS = [("debates", "House of Commons"), ("lordspages", "House of Lords"), ("westminhall", "Westminster Hall"),
            ("standing", "Public Bill Committees")]
MIN_CONTRIBUTIONS = 3  # shorter items (a point of order, a single announcement) are counted but not listed

_DEBATES_SQL = """
SELECT date, source, major_heading, minor_heading, bill,
       count(*) FILTER (WHERE xml_person_id IS NOT NULL) AS said, count(*) FILTER (WHERE speech_type ILIKE '%answer%') AS answers,
       count(*) FILTER (WHERE speech_type = 'Start Statement') AS statements,
       min(try_cast(regexp_extract(record_id, '\\.(\\d+)\\.\\d+$', 1) AS INTEGER)) AS first_col
FROM records
WHERE date BETWEEN ? AND ? AND source IN ('debates', 'lordspages', 'westminhall', 'standing')
GROUP BY ALL
"""


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


class Weeks:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.con = shared()
        self.stores = set(attach_store(self.con, settings.parquet_dir))
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple, dict[str, Any] | None] = OrderedDict()

    def latest(self) -> date | None:
        if "records" not in self.stores:
            return None
        row = self.con.execute("SELECT max(date) FROM records WHERE source IN ('debates', 'lordspages')").fetchone()
        return monday(row[0]) if row and row[0] else None

    def pause(self, today: date) -> dict[str, Any] | None:
        """When neither House has sat for over a week: the last sitting days, and whether the record is still being
        updated (written answers keep coming in a recess). None while Parliament is sitting."""
        if "records" not in self.stores:
            return None
        commons, lords, newest = self.con.execute("""
            SELECT max(date) FILTER (WHERE source = 'debates'), max(date) FILTER (WHERE source = 'lordspages'),
                   max(date) FROM records""").fetchone()
        last = max((d for d in (commons, lords) if d), default=None)
        if last is None or (today - last).days <= 6:
            return None
        since = dict(self.con.execute("""
            SELECT source, count(DISTINCT record_id) FROM records
            WHERE date > ? AND source IN ('wrans', 'lordswrans', 'wms', 'lordswms') GROUP BY 1""", [last]).fetchall())
        return {"last_sat": last.isoformat(), "commons_last_sat": commons.isoformat() if commons else None,
                "lords_last_sat": lords.isoformat() if lords else None, "record_to": newest.isoformat(),
                "recess": (today - newest).days <= 7,  # the record is up to date, so the Houses are not sitting
                "written_answers_since": since.get("wrans", 0) + since.get("lordswrans", 0),
                "written_statements_since": since.get("wms", 0) + since.get("lordswms", 0)}

    def get(self, start: date, version: Any = None) -> dict[str, Any] | None:
        start = monday(start)
        key = (str(version), start)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        with self._lock:  # one connection: build under the lock
            out = self._build(start)
            self._cache[key] = out
            while len(self._cache) > 60:
                self._cache.popitem(last=False)
        return out

    def _build(self, start: date) -> dict[str, Any] | None:
        if "records" not in self.stores:
            return None
        end = start + timedelta(days=6)
        rows = self.con.execute(_DEBATES_SQL, [start, end]).fetchall()
        if not rows:
            return None
        debates: dict[tuple, dict[str, Any]] = {}
        for day, source, major, minor, bill, said, answers, statements, first_col in rows:
            title = title_of(source, major, minor, bill)
            if not title:
                continue
            k = (day, source, slug(title))
            d = debates.setdefault(k, {"title": title, "said": 0, "first": first_col or 0,
                                       "path": debate_path(source, day, title, None, title)})
            d["said"] += said
            # the record marks speeches as questions, answers and statements (since about 2016)
            d["answers"] = d.get("answers", 0) + answers
            d["statements"] = d.get("statements", 0) + statements
            d["kind"] = None
            if source == "debates" and d["statements"]:
                d["kind"] = "Statement"  # a ministerial statement or urgent question, and questions on it
            elif source == "debates" and d["said"] and d["answers"] * 3 >= d["said"]:
                d["kind"] = "Questions"  # oral questions to a department
            d["first"] = min(d["first"], first_col or 0)
        days: dict[date, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        short: dict[date, int] = defaultdict(int)
        for (day, source, _), d in debates.items():
            if d["said"] >= MIN_CONTRIBUTIONS:
                days[day][source].append(d)
            else:
                short[day] += 1
        divisions = self._divisions(start, end)
        div_by_day: dict[str, list] = defaultdict(list)
        for v in divisions:
            div_by_day[v["date"]].append(v)
        out_days = []
        for day in sorted(days):
            chambers = []
            for source, name in CHAMBERS:
                items = sorted(days[day].get(source, []), key=lambda d: d["first"])
                if items:
                    chambers.append({"chamber": SOURCES[source], "name": name,
                                     "debates": [{k: d[k] for k in ("title", "kind", "said", "path")}
                                                 for d in items]})
            out_days.append({"date": day.isoformat(), "chambers": chambers, "short_items": short.get(day, 0),
                             "divisions": div_by_day.get(day.isoformat(), [])})
        written = self.con.execute("""
            SELECT source, count(DISTINCT record_id) FROM records
            WHERE date BETWEEN ? AND ? AND source IN ('wrans', 'lordswrans', 'wms', 'lordswms') GROUP BY 1""",
                                   [start, end]).fetchall()
        counts = dict(written)
        statements = self.con.execute("""
            SELECT DISTINCT date, coalesce(minor_heading, major_heading) AS t, major_heading
            FROM records WHERE date BETWEEN ? AND ? AND source = 'wms' AND xml_person_id IS NOT NULL
            ORDER BY date, t""", [start, end]).fetchall()
        prev_row = self.con.execute("SELECT max(date) FROM records WHERE date < ? AND source IN ('debates', "
                                    "'lordspages')", [start]).fetchone()
        next_row = self.con.execute("SELECT min(date) FROM records WHERE date > ? AND source IN ('debates', "
                                    "'lordspages')", [end]).fetchone()
        return {"start": start.isoformat(), "end": end.isoformat(), "days": out_days,
                "divisions": len(divisions),
                # how far the votes have been loaded: a week after it has no votes yet, not "0 votes"
                "votes_loaded_to": self._votes_loaded_to(),
                "written_questions": counts.get("wrans", 0) + counts.get("lordswrans", 0),
                "written_statements": [{"date": d.isoformat(), "title": t, "department": m if m != t else None,
                                        "path": debate_path("wms", d, m, t)}
                                       for d, t, m in statements],
                "lords_written_statements": counts.get("lordswms", 0),
                "previous": monday(prev_row[0]).isoformat() if prev_row and prev_row[0] else None,
                "next": monday(next_row[0]).isoformat() if next_row and next_row[0] else None}

    def _votes_loaded_to(self) -> str | None:
        if "divisions" not in self.stores:
            return None
        row = self.con.execute("SELECT max(date) FROM divisions").fetchone()
        return row[0].isoformat() if row and row[0] else None

    def _divisions(self, start: date, end: date) -> list[dict[str, Any]]:
        if "divisions" not in self.stores:
            return []
        rows = self.con.execute("""
            SELECT division_id, date, house, number, major_heading, minor_heading, ayes, noes
            FROM divisions WHERE date BETWEEN ? AND ? ORDER BY date, house, number""", [start, end]).fetchall()
        out = []
        for did, day, house, number, major, minor, ayes, noes in rows:
            source = "debates" if house == "commons" else "lordspages"
            title = title_of(source, major, minor, None)
            path = debate_path(source, day, title, None, None)
            out.append({"division_id": did, "date": day.isoformat(), "house": house, "number": number,
                        "title": " — ".join(x for x in (major, minor) if x), "ayes": ayes, "noes": noes,
                        "path": f"{path}#div-{anchor(did)[1:]}" if path else None})
        return vote_types(self.settings).annotate(out)
