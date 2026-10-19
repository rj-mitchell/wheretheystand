"""Charts: how often a subject has been raised in Parliament each year, overall or by party, House or nation.

Counts are of contributions (speeches, questions, answers, statements and Public Bill Committee speeches) that
contain the exact words, as whole words. Every count comes with its context: the number of all contributions by the
same group that year, and the rate per 10,000 of them, so a busy session or a large party does not look keener just
because it said more. Free (no AI); results are cached until the next data update.
"""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Any

from hansard.config import Settings
from hansard.reference.profiles import attach_reference
from hansard.store.parquet import attach_store
from hansard.store.shared import shared

PHRASE = re.compile(r"^[\w][\w '’\-]{1,58}[\w]$")
BY = ("all", "house", "party", "nation")
PARTIES = {"labour": "Labour", "conservative": "Conservative", "liberal-democrat": "Liberal Democrat",
           "scottish-national-party": "SNP", "reform": "Reform UK", "green": "Green"}
NATIONS = ["England", "Scotland", "Wales", "Northern Ireland"]
HOUSES = {"commons": "Commons", "lords": "Lords"}

# each contribution once, with the speaker's party and seat's nation on the day
_ENRICHED = """
WITH rec AS (
  SELECT DISTINCT ON (r.record_id) r.record_id, r.year, r.house, r.date,
         coalesce(pid.person_id, r.xml_person_id) AS person_id
  FROM records r LEFT JOIN ref_person_ids pid ON pid.xml_id = r.xml_person_id
  WHERE {where}
),
mem AS (
  SELECT rec.record_id, arg_max(coalesce(p.whip_group, mm.party_id), mm.start) AS party,
         arg_max(mm.nation, mm.start) AS nation
  FROM rec JOIN ref_memberships mm ON mm.person_id = rec.person_id AND rec.date BETWEEN mm.start AND mm."end"
  LEFT JOIN ref_parties p ON p.party_id = mm.party_id
  GROUP BY rec.record_id
)
SELECT rec.year, {group} AS grp, count(*) AS n
FROM rec LEFT JOIN mem USING (record_id)
GROUP BY ALL
"""


def _group(by: str) -> str:
    if by == "house":
        return "rec.house"
    if by == "party":
        keep = ", ".join(f"'{p}'" for p in PARTIES)
        return f"CASE WHEN mem.party IN ({keep}) THEN mem.party ELSE 'other' END"
    if by == "nation":
        return "CASE WHEN rec.house = 'lords' THEN 'lords' ELSE coalesce(mem.nation, 'unknown') END"
    return "'all'"


def clean_phrase(text: str) -> str:
    p = " ".join((text or "").strip().strip('"“”').split()).lower()
    if not PHRASE.match(p):
        raise ValueError("Use words of 3 to 60 letters, numbers, spaces, hyphens or apostrophes.")
    return p


class Trends:
    def __init__(self, settings: Settings, today: date | None = None):
        self.settings = settings
        self.today = today
        self.con = shared()
        attach_reference(self.con, settings.reference_dir)
        self.stores = set(attach_store(self.con, settings.parquet_dir))
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple, dict[str, Any]] = OrderedDict()
        self._totals: dict[str, dict[tuple[int, str], int]] = {}
        self._totals_version: str | None = None

    # ---- the context: every contribution, by year and group --------------------------------------------------
    def totals(self, by: str, version: str) -> dict[tuple[int, str], int]:
        with self._lock:
            if self._totals_version != version:
                self._totals, self._totals_version = self._load_totals(version), version
            if by not in self._totals:
                rows = self.con.execute(_ENRICHED.format(where="r.source IS NOT NULL", group=_group(by))).fetchall()
                self._totals[by] = {(int(y), g): int(n) for y, g, n in rows}
                self._save_totals(version)
            return self._totals[by]

    def _path(self) -> Path:
        return self.settings.data_dir / "trend_totals.json"

    def _load_totals(self, version: str) -> dict[str, dict[tuple[int, str], int]]:
        try:
            raw = json.loads(self._path().read_text())
            if raw.get("version") == version:
                return {by: {(int(k.split("|")[0]), k.split("|", 1)[1]): v for k, v in t.items()}
                        for by, t in raw["totals"].items()}
        except (OSError, ValueError, KeyError):
            pass
        return {}

    def _save_totals(self, version: str) -> None:
        out = {by: {f"{y}|{g}": n for (y, g), n in t.items()} for by, t in self._totals.items()}
        tmp = self._path().with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": version, "totals": out}))
        tmp.replace(self._path())

    # ---- a chart --------------------------------------------------------------------------------------------
    def chart(self, phrases: list[str], by: str, version: str) -> dict[str, Any]:
        if "records" not in self.stores:
            return {"years": [], "series": []}
        phrases = list(dict.fromkeys(clean_phrase(p) for p in phrases if p.strip()))[:3]
        if not phrases:
            raise ValueError("Give a word or phrase.")
        if by not in BY:
            raise ValueError("Unknown grouping.")
        if len(phrases) > 1:
            by = "all"  # several phrases are compared with each other, not split further
        key = (version, tuple(phrases), by)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        totals = self.totals(by, version)
        years = sorted({y for y, _ in totals})
        series = []
        for p in phrases:
            pattern = r"(?i)(^|[^\w])" + re.escape(p).replace(r"\ ", r"\s+") + r"($|[^\w])"
            rows = self.con.execute(_ENRICHED.format(
                where="contains(lower(r.text), ?) AND regexp_matches(r.text, ?)", group=_group(by)),
                [p, pattern]).fetchall()
            counts: dict[tuple[int, str], int] = {(int(y), g): int(n) for y, g, n in rows}
            for g, label in self._groups(by, counts):
                c = [counts.get((y, g), 0) for y in years]
                t = [totals.get((y, g), 0) for y in years]
                if not any(c):
                    continue
                series.append({"key": f"{p}|{g}", "phrase": p, "group": g,
                               "label": label if len(phrases) == 1 else f"“{p}”", "counts": c, "totals": t,
                               "rate": [round(10000 * a / b, 2) if b else None for a, b in zip(c, t, strict=True)]})
        today = self.today or date.today()
        out = {"phrases": phrases, "by": by, "years": years, "series": series,
               "partial_years": [y for y in years if y in (self.settings.archive_start.year, today.year)],
               "total": sum(sum(s["counts"]) for s in series)}
        with self._lock:
            self._cache[key] = out
            while len(self._cache) > 300:
                self._cache.popitem(last=False)
        return out

    @staticmethod
    def _groups(by: str, counts: dict[tuple[int, str], int]) -> list[tuple[str, str]]:
        if by == "all":
            return [("all", "All contributions")]
        if by == "house":
            return list(HOUSES.items())
        if by == "party":
            return [*PARTIES.items(), ("other", "Other parties and none")]
        return [*[(n, n) for n in NATIONS], ("lords", "Lords (no constituency)")]
