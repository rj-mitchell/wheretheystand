"""Commons votes from May 1997 to May 2001, from Public Whip's vote matrix.

TheyWorkForYou's files for the 1997 Parliament have no division lists, so how each MP voted then comes from Public
Whip (publicwhip.org.uk), which keyed in the division lists from Hansard. Its vote matrix for that Parliament gives,
for each of the 1,273 divisions, the date, number, title and every MP's vote, with MPs identified by the same
member IDs as TheyWorkForYou (parlparse). It is kept with the code (data_static/votes, about 250 KB) and loaded into
the votes store once; loading it again only happens if the file changes.

Divisions already in the store (the same date and number, read from Hansard) are left as they are.

Hansard's own record of those years has no division lists, but it does print each result ("The House divided:
Ayes 151, Noes 422."). Each division is matched to that paragraph by its counts (within three votes, in order through
the day), and then takes its identifier, place, headings and links from it, as divisions read from Hansard do: so it
is shown in its debate, linked to the exact place in the record, and its kind of vote (amendment, second reading and
so on) is read from the question put just before it. About 1,190 of the 1,273 match; the others keep Public Whip's
title and a link to the day.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from hansard.config import STATIC_DIR
from hansard.parse.xml_stream import official_url, twfy_url
from hansard.store.parquet import partition_dir, remove_keys_from_compacted, write_divisions
from hansard.store.state import StateDB, utcnow

log = logging.getLogger(__name__)

MATRIX = STATIC_DIR / "votes" / "publicwhip_votematrix_1997.dat.gz"
SOURCE = "publicwhip-divisions"  # "division" in the name keeps it out of the search index's queue
CREDIT = "Public Whip"
LOADER = "2"  # changing this loads the matrix again (2: matched to Hansard's record of each result)
# the matrix's codes: tellers are coded apart from the MPs they counted
CODES = {"1": ("aye", True), "2": ("aye", False), "3": ("both", False), "4": ("no", False), "5": ("no", True)}


def division_id(day: date, number: int) -> str:
    return f"publicwhip/commons/{day.isoformat()}.0.{number}"


def key_for(day: date) -> str:
    return f"publicwhip-divisions{day.isoformat()}"


_RESULT = re.compile(r"(?:House|Committee) divided[^A-Za-z]*Ayes,?\s*(\d+)[;,.]?\s*Noes,?\s*(\d+)", re.I)


def _position(record_id: str) -> tuple[int, int]:
    """(column, item) from a record identifier such as uk.org.publicwhip/debate/1997-05-19a.583.2."""
    parts = record_id.rsplit(".", 2)
    if len(parts) < 3:
        return 0, 0
    return (int(parts[1]) if parts[1].isdigit() else 0), (int(parts[2]) if parts[2].isdigit() else 0)


def hansard_results(parquet_dir: Path, years: set[int]) -> dict[date, list[dict[str, Any]]]:
    """{day: the paragraphs of the Commons record that give a division's result, in order}."""
    out: dict[date, list[dict[str, Any]]] = {}
    con = duckdb.connect()
    for year in sorted(years):
        d = partition_dir(parquet_dir, "records", "debates", year)
        if not d.exists() or not any(d.glob("*.parquet")):
            continue
        rows = con.execute(
            f"SELECT record_id, date, text, major_heading, minor_heading, official_url "
            f"FROM read_parquet('{d.as_posix()}/*.parquet', union_by_name = true) "
            "WHERE regexp_matches(text, '(House|Committee) divided', 'i')").fetchall()
        for rid, day, text, major, minor, url in rows:
            m = _RESULT.search(text or "")
            if m:
                out.setdefault(day, []).append({"record_id": rid, "ayes": int(m.group(1)), "noes": int(m.group(2)),
                                                "major_heading": major, "minor_heading": minor, "official_url": url})
    for paras in out.values():
        paras.sort(key=lambda p: _position(p["record_id"]))
    return out


def match_results(divisions: list[dict[str, Any]], paragraphs: list[dict[str, Any]], tolerance: int = 3) -> None:
    """Give each of a day's divisions (in number order) the identifier, place, headings and links of the paragraph
    recording its result: the next one, in the order of the record, whose counts are within ``tolerance``."""
    at = 0
    for d in sorted(divisions, key=lambda x: x["number"]):
        for i in range(at, len(paragraphs)):
            p = paragraphs[i]
            if abs(p["ayes"] - d["ayes"]) <= tolerance and abs(p["noes"] - d["noes"]) <= tolerance:
                col, _ = _position(p["record_id"])
                d.update({"division_id": p["record_id"], "colnum": str(col) if col else None,
                          "major_heading": p["major_heading"] or d["major_heading"],
                          "minor_heading": p["minor_heading"] or d["minor_heading"],
                          "official_url": p["official_url"] or d["official_url"],
                          "twfy_url": twfy_url("debates", p["record_id"])})
                at = i + 1
                break


def _headings(title: str) -> tuple[str, str | None]:
    """Public Whip titles run the debate and the question together ("X Bill - New Clause 3 - Jurisdiction")."""
    major, sep, minor = title.partition(" - ")
    return major.strip(), (minor.strip() or None) if sep else None


def _divisions(path: Path) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """(division, its votes) as the store holds them, oldest first, one at a time."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="latin-1") as f:
        lines = f.read().splitlines()
    members = [h.strip().removeprefix("mpid") for h in lines[0].split("\t")[4:]]
    rows = [cells for cells in ([c.strip() for c in line.split("\t")] for line in lines[1:])
            if len(cells) >= 5 and cells[2].isdigit()]
    del lines
    rows.sort(key=lambda c: (c[1], int(c[2])))
    for cells in rows:
        day, number = date.fromisoformat(cells[1]), int(cells[2])
        did = division_id(day, number)
        counts = {"aye": 0, "no": 0}
        votes = []
        for member, code in zip(members, cells[4:], strict=False):
            if code not in CODES:
                continue
            vote, teller = CODES[code]
            votes.append({"division_id": did, "date": day, "house": "commons",
                          "xml_person_id": f"uk.org.publicwhip/member/{member}", "vote": vote, "teller": teller,
                          "proxy_person_id": None})
            if vote in counts and not teller:
                counts[vote] += 1
        major, minor = _headings(cells[3])
        yield {"division_id": did, "source_key": key_for(day), "house": "commons", "date": day,
               "number": number, "time": None, "colnum": None, "major_heading": major,
               "minor_heading": minor, "ayes": counts["aye"], "noes": counts["no"],
               "official_url": official_url("debates", None, day)[0],
               "twfy_url": f"https://www.theyworkforyou.com/debates/?d={day.isoformat()}"}, votes


def read_matrix(path: Path = MATRIX) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(divisions, votes) as the store holds them."""
    divisions, votes = [], []
    for d, vs in _divisions(path):
        divisions.append(d)
        votes += vs
    return divisions, votes


def _already_stored(parquet_dir: Path, years: set[int]) -> set[tuple[date, int]]:
    """(date, number) of Commons divisions the store already has from Hansard for these years."""
    out: set[tuple[date, int]] = set()
    con = duckdb.connect()
    for year in years:
        d = partition_dir(parquet_dir, "divisions", "commons", year)
        if not d.exists() or not any(d.glob("*.parquet")):
            continue
        out |= set(con.execute(
            f"SELECT date, number FROM read_parquet('{d.as_posix()}/*.parquet', union_by_name = true) "
            "WHERE NOT starts_with(source_key, 'publicwhip-')").fetchall())
    return out


def load_votes_1997(parquet_dir: Path, state: StateDB, path: Path = MATRIX, force: bool = False) -> dict[str, Any]:
    """Load the matrix into the votes store, once (again only if the file changes)."""
    if not path.exists():
        return {"loaded": False, "reason": "no file"}
    revision = f"{hashlib.sha256(path.read_bytes()).hexdigest()[:16]}-{LOADER}"
    prev = state.manifest().get((SOURCE, path.name))
    if prev and prev["revision"] == revision and not force:
        return {"loaded": False, "reason": "already loaded"}
    with (gzip.open if path.suffix == ".gz" else open)(path, "rt", encoding="latin-1") as f:
        years = {int(line[1][:4]) for line in (r.split("\t", 2) for r in f.read().splitlines()[1:])
                 if len(line) > 1 and line[1][:4].isdigit()}
    have = _already_stored(parquet_dir, years)
    results = hansard_results(parquet_dir, years)
    loaded = n_votes = skipped = matched = 0
    day: date | None = None
    divs: list[dict[str, Any]] = []
    votes: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal matched
        if day is not None and divs:
            ids = [d["division_id"] for d in divs]
            match_results(divs, results.get(day, []))
            new_ids = {old: d["division_id"] for old, d in zip(ids, divs, strict=True)}
            for v in votes:
                v["division_id"] = new_ids[v["division_id"]]
            matched += sum(1 for old, new in new_ids.items() if old != new)
            for table in ("votes", "divisions"):  # an earlier load may have been compacted into the year's file
                remove_keys_from_compacted(parquet_dir, table, "commons", day.year, {key_for(day)})
            write_divisions(parquet_dir, "commons", key_for(day), day.year, divs, votes)

    # one sitting day at a time (the matrix is about half a million votes)
    for d, vs in _divisions(path):
        if d["date"] != day:
            flush()
            day, divs, votes = d["date"], [], []
        if (d["date"], d["number"]) in have:
            skipped += 1
            continue
        divs.append(d)
        votes += vs
        loaded += 1
        n_votes += len(vs)
    flush()
    state.upsert_manifest({"source": SOURCE, "key": path.name, "revision": revision, "file_name": path.name,
                           "size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns, "rows": loaded,
                           "parsed_at": utcnow()})
    log.info("Public Whip: loaded %d Commons divisions (%d votes) from 1997-2001, %d matched to the record",
             loaded, n_votes, matched)
    return {"loaded": True, "divisions": loaded, "votes": n_votes, "already_stored": skipped,
            "matched_to_record": matched}
