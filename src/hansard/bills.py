"""Bill pages: each bill's journey through Parliament, sitting by sitting, with its divisions.

Bills are found from the record itself: debate headings that name a bill ("Safety of Rwanda (Asylum and
Immigration) Bill"), the Lords' headings, which also name the stage ("… Bill - Committee (1st Day)"), and Public
Bill Committee sittings. The Commons record usually does not label the stage, so it is read from the standard
wording of the motions ("That the Bill be now read a Second time", "Lords amendments" and so on).

A bill that returns with the same name (every year's Finance Bill, a bill reintroduced in a new session) is split
where there is a long gap between sittings. Built from the stored record (no AI) and cached until the next update.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb

from hansard.config import Settings
from hansard.debates import debate_path
from hansard.store.parquet import attach_store
from hansard.vote_types import vote_types

log = logging.getLogger(__name__)

FORMAT = 4  # raise when the shape of a bill changes, so a cached index is rebuilt (2: kinds of vote, 3: debates)
GAP = timedelta(days=200)  # sittings further apart than this belong to different bills of the same name
_HEADING = re.compile(r"^(?P<name>.*?\bBill\b(?:\s*\[(?:HL|Lords)\])?)\s*(?P<tail>.*)$", re.I)
_MOTIONS = [("programme", "Programme motion"), ("money", "Money resolution"),
            ("ways and means", "Ways and means resolution"), ("carry-over", "Carry-over motion"),
            ("instruction", "Instruction")]
_SITTINGS_SQL = """
SELECT raw, date, house, source, count(*) AS n,
       arg_min(coalesce(official_url, twfy_url), record_id) AS url,
       bool_or(contains(t, 'be now read a second time')) AS second,
       bool_or(contains(t, 'be now read the third time') OR contains(t, 'be now read a third time')) AS third,
       bool_or(contains(t, 'lords amendment')) AS lords_amendments,
       bool_or(contains(t, 'commons amendment') OR contains(t, 'commons reason')) AS commons_amendments,
       list(DISTINCT minor_heading) FILTER (WHERE minor_heading IS NOT NULL) AS minors,
       bool_or(presented) AS presented
FROM (
  SELECT date, house, source, record_id, official_url, twfy_url, minor_heading, lower(left(text, 2000)) AS t,
         upper(major_heading) = 'BILL PRESENTED' AS presented,
         CASE WHEN source = 'standing' THEN bill
              WHEN upper(major_heading) = 'BILL PRESENTED' THEN minor_heading
              ELSE major_heading END AS raw
  FROM records
  WHERE source IN ('debates', 'lordspages', 'westminhall', 'standing')
    AND ((source = 'standing' AND bill IS NOT NULL) OR regexp_matches(major_heading, '(?i)\\bbill\\b'))
)
WHERE raw IS NOT NULL
GROUP BY raw, date, house, source
"""


def bill_key(name: str) -> str:
    n = re.sub(r"\[(?:hl|lords)\]", "", name.lower()).replace("&", "and")
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return " ".join(n.split())


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_heading(raw: str) -> tuple[str, str] | None:
    """(bill name, what follows it) or None if the heading is not about a bill."""
    m = _HEADING.match(" ".join(raw.split()))
    if not m:
        return None
    name, tail = m.group("name").strip(), m.group("tail").strip(" -–—:")
    if len(bill_key(name).split()) < 2:  # "Bill" alone, "Bill of Rights" etc. are not bill names here
        return None
    return name, tail


def _display(names: Counter) -> str:
    """The most common spelling, preferring one not in capitals (older Commons headings are upper case)."""
    proper = Counter({n: c for n, c in names.items() if not n.isupper()})
    best = (proper or names).most_common(1)[0][0]
    return re.sub(r"\s*\[(?:HL|Lords)\]", "", best).strip() if not best.isupper() else best.title()


_PROPER = re.compile(r"\b(commons|lords|house|bill|royal assent|hl)\b")


def sentence(label: str) -> str:
    """'Committee (1st Day)' -> 'Committee (1st day)', keeping Commons, Lords and the like capitalised."""
    out = label[:1].upper() + label[1:].lower()
    return _PROPER.sub(lambda m: m.group(1).title() if m.group(1) != "hl" else "HL", out)


def _motion(tail: str) -> str | None:
    t = tail.lower()
    return next((label for word, label in _MOTIONS if t.startswith(f"({word}")), None)


def stage_of(s: dict[str, Any], tail: str) -> list[str]:
    """What happened at one sitting, from the heading and the standard wording of the motions."""
    if s["source"] == "standing":
        return ["Public Bill Committee"]
    if s["source"] == "westminhall":
        return ["Westminster Hall debate"]
    t = tail.lower()
    if t.startswith("(") and _motion(tail):
        return [_motion(tail)]
    minors = " ".join(s["minors"] or []).lower()
    if s["house"] == "lords" and tail and not tail.startswith("["):
        return [sentence(re.sub(r"\s*\(continued\)", "", " ".join(tail.split()), flags=re.I)[:80])]
    out = []
    if s["raw_is_presented"]:
        out.append("Presented (first reading)")
    if s["second"]:
        out.append("Second reading")
    if s["lords_amendments"] and s["house"] == "commons":
        out.append("Consideration of Lords amendments")
    if s["commons_amendments"] and s["house"] == "lords":
        out.append("Consideration of Commons amendments")
    if "allocated day" in t or "allocated day" in minors:
        out.append("Committee of the whole House")
    elif not out and ("clause" in minors or "amendment" in minors or "schedule" in minors):
        out.append("Committee or report stage")
    if s["third"]:
        out.append("Third reading")
    return out or ["Debate"]


def build_bills(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = con.execute(_SITTINGS_SQL).fetchall()
    cols = [d[0] for d in con.description]
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    names: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        s = dict(zip(cols, r, strict=True))
        parsed = parse_heading(s["raw"])
        if parsed is None:
            continue
        name, tail = parsed
        s["raw_is_presented"] = bool(s["presented"])
        s["tail"] = tail
        key = bill_key(name)
        names[key][name] += s["n"]
        by_key[key].append(s)
    bills = []
    for key, sittings in by_key.items():
        sittings.sort(key=lambda s: s["date"])
        group: list[dict[str, Any]] = []
        for s in sittings:
            if group and s["date"] - group[-1]["date"] > GAP:
                bills.append(_bill(key, names[key], group))
                group = []
            group.append(s)
        if group:
            bills.append(_bill(key, names[key], group))
    # keep bills that were debated, not just presented or mentioned once
    bills = [b for b in bills if b["contributions"] >= 5 or len(b["sittings"]) >= 2]
    _divisions(con, bills)
    seen: Counter = Counter()
    for b in sorted(bills, key=lambda b: b["first"]):
        base = f"{slugify(b['name'])}-{b['first'][:4]}"
        seen[base] += 1
        b["slug"] = base if seen[base] == 1 else f"{base}-{seen[base]}"
    bills.sort(key=lambda b: b["last"], reverse=True)
    return bills


def _bill(key: str, names: Counter, group: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[tuple, dict[str, Any]] = {}
    for s in group:  # one entry per day and House (a debate may be split across headings)
        k = (s["date"], s["house"], s["source"])
        cur = merged.get(k)
        stages = stage_of(s, s["tail"])
        # the debate page for this sitting (not for "Bill presented" entries, which sit under another heading)
        path = None if s["presented"] else debate_path(s["source"], s["date"], s["raw"], None, s["raw"])
        if cur is None:
            merged[k] = {"date": s["date"].isoformat(), "house": s["house"], "source": s["source"],
                         "stages": stages, "contributions": s["n"], "url": s["url"], "path": path,
                         "_path_n": s["n"] if path else 0}
        else:
            cur["contributions"] += s["n"]
            cur["stages"] += [x for x in stages if x not in cur["stages"]]
            if path and s["n"] > cur["_path_n"]:  # link to the main debate, not a short timetable motion
                cur["path"], cur["_path_n"] = path, s["n"]
    sittings = sorted(merged.values(), key=lambda s: (s["date"], s["house"]))
    for s in sittings:  # "Debate" adds nothing once a stage is known
        s.pop("_path_n", None)
        if len(s["stages"]) > 1 and "Debate" in s["stages"]:
            s["stages"].remove("Debate")
    return {"key": key, "name": _display(names), "first": sittings[0]["date"], "last": sittings[-1]["date"],
            "houses": sorted({s["house"] for s in sittings}), "sittings": sittings,
            "contributions": sum(s["contributions"] for s in sittings), "divisions": []}


def _divisions(con: duckdb.DuckDBPyConnection, bills: list[dict[str, Any]]) -> None:
    try:
        rows = con.execute("SELECT date, house, number, major_heading, minor_heading, ayes, noes, "
                           "coalesce(official_url, twfy_url), division_id FROM divisions WHERE regexp_matches(major_heading, "
                           "'(?i)\\bbill\\b') ORDER BY date, number").fetchall()
    except duckdb.Error:
        return
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for b in bills:
        index[b["key"]].append(b)
    for d, house, number, major, minor, ayes, noes, url, division_id in rows:
        parsed = parse_heading(major or "")
        if not parsed:
            continue
        for b in index.get(bill_key(parsed[0]), []):
            lo, hi = date.fromisoformat(b["first"]) - timedelta(days=1), date.fromisoformat(b["last"]) + timedelta(days=1)
            if lo <= d <= hi:
                b["divisions"].append({"date": d.isoformat(), "house": house, "number": number,
                                       "title": minor or _motion(parsed[1]) or parsed[1] or major,
                                       "ayes": ayes, "noes": noes, "url": url, "division_id": division_id})
                break


class Bills:
    """The bill index, built once per data update and kept in memory (and on disk, so a restart is quick)."""

    def __init__(self, settings: Settings, version: Any = None):
        self.settings = settings
        self.path = settings.data_dir / "bills.json"
        self._lock = threading.Lock()
        self._bills: list[dict[str, Any]] | None = None
        self._version = version

    def _load(self, version: Any) -> list[dict[str, Any]]:
        with self._lock:
            if self._bills is not None and version == self._version:
                return self._bills
            stamp = f"{version}|{FORMAT}"
            if self.path.exists():
                try:
                    cached = json.loads(self.path.read_text())
                    if cached.get("version") == stamp:
                        self._bills, self._version = cached["bills"], version
                        return self._bills
                except (OSError, ValueError):
                    pass
            self._bills = rebuild(self.settings, stamp)
            self._version = version
            return self._bills

    def all(self, version: Any = None) -> list[dict[str, Any]]:
        return self._load(version)

    def get(self, slug: str, version: Any = None) -> dict[str, Any] | None:
        return next((b for b in self._load(version) if b["slug"] == slug), None)


def rebuild(settings: Settings, stamp: str) -> list[dict[str, Any]]:
    con = duckdb.connect()
    if "records" not in attach_store(con, settings.parquet_dir):
        return []
    bills = build_bills(con)
    try:
        types = vote_types(settings).all()
    except (OSError, duckdb.Error):
        log.exception("vote types unavailable")
        types = {}
    for b in bills:
        for d in b["divisions"]:
            vt = types.get(d["division_id"])
            if vt:  # kept small: the bill page shows the label, note, motion and outcome
                d["vote_type"] = vt
    path = Path(settings.data_dir) / "bills.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": stamp, "bills": bills}))
    tmp.replace(path)
    log.info("bill index: %d bills", len(bills))
    return bills
