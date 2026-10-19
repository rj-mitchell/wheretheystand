"""Spotting contributions about one locality (a town's bus route, a constituency's station), so that a question
about a national subject is answered mainly from contributions of national relevance. Nothing is excluded: local
items are down-weighted, and not at all when the question is itself about a place, a region or a member."""

from __future__ import annotations

import re
import threading
from pathlib import Path

import duckdb

# Nations and regions are not "local"; nor are place names that are also everyday words in headings.
NOT_LOCAL = {
    "England", "Scotland", "Wales", "Northern Ireland", "Britain", "Great Britain", "United Kingdom", "London",
    "Greater London", "North", "South", "East", "West", "Central", "Mid", "Reading", "Wells", "Deal", "Street",
    "Battle", "March", "Rugby", "Hope", "Bury", "Ware", "Diss", "Hull", "Sale", "Bath", "Ely", "Eye", "Holt",
    "Wick", "Cheam", "Herne", "Hove", "Speke", "Ilford", "Crewe", "Harrow", "Cross", "Bridge", "Park", "Green",
    "Heath", "Vale", "Valley", "Forest", "Isles", "Islands", "Coast", "Downs", "Fields", "Hill", "Hills", "Moor",
    "Marsh", "Kingston", "Richmond", "Newport", "Washington", "Durham", "Lincoln", "Chester", "York",
}
_SPLIT = re.compile(r"\s*(?:,|&|\band\b|\bwith\b|\(|\))\s*")
_DIRECTION = re.compile(r"^(?:North|South|East|West|Central|Mid|Inner|Outer|City of|The|Upper|Lower)\s+|"
                        r"\s+(?:North|South|East|West|Central|Mid|Inner|Outer|Rural)$")
_CHAIR = re.compile(r"\s*[—-]\s*\[[^\]]*\bin the Chair\]", re.I)  # "— [David Mundell in the Chair]"
_MY_SEAT = re.compile(r"\bmy (?:constituency|constituents|patch)\b|\bin my area\b|\bmy local\b", re.I)


def place_names(reference_dir: Path) -> set[str]:
    con = duckdb.connect()
    names: set[str] = set()
    for sql in ("SELECT DISTINCT constituency FROM read_parquet(?) WHERE constituency IS NOT NULL",
                "SELECT DISTINCT urban_area FROM read_parquet(?) WHERE urban_area IS NOT NULL"):
        for (raw,) in con.execute(sql, [str(Path(reference_dir) / "memberships.parquet")]).fetchall():
            for part in _SPLIT.split(raw):
                part = part.strip()
                while True:  # "North East Derbyshire" -> "Derbyshire"
                    stripped = _DIRECTION.sub("", part).strip()
                    if stripped == part:
                        break
                    part = stripped
                if len(part) >= 4 and part[0].isupper() and part not in NOT_LOCAL:
                    names.add(part)
    return names


class Places:
    def __init__(self, names: set[str]):
        self.names = names
        alternation = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        self._re = re.compile(rf"(?<![\w-])(?:{alternation})(?![\w-])") if names else None

    def mentioned(self, text: str | None) -> set[str]:
        if not text or self._re is None:
            return set()
        return set(self._re.findall(text))

    def is_local(self, heading: str | None, text: str | None) -> bool:
        """A contribution mainly about one place: a place in its heading, or a member's own patch named. (Several
        big cities listed as examples is usually a national point, so that alone does not count.)"""
        if self.mentioned(_CHAIR.sub("", heading or "")):
            return True
        return bool(_MY_SEAT.search(text or "")) and bool(self.mentioned(text))


_shared: dict[str, Places] = {}
_lock = threading.Lock()


def places_for(reference_dir: Path) -> Places:
    key = str(Path(reference_dir).resolve())
    with _lock:
        if key not in _shared:
            try:
                _shared[key] = Places(place_names(reference_dir))
            except (duckdb.Error, OSError):  # no reference data yet: nothing counts as local
                _shared[key] = Places(set())
        return _shared[key]
