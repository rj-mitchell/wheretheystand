"""Turn House of Commons Information Office Factsheet M16 (By-election results: 1997-2001) into
data_static/elections/by_elections_1997_2001.csv, in the same columns as the 2001-2010 file.

Each of the 17 by-elections has a table: candidates with their votes and shares, then the turnout and the majority.
The factsheet gives no electorate, so that column is left empty. The text is read with pdftotext -layout. Run once;
the CSV is kept with the code.

    uv run python scripts/factsheet_by_elections.py m16.pdf
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data_static" / "elections" / "by_elections_1997_2001.csv"
FIELDS = ["year", "date", "constituency", "region", "electorate", "votes", "winner", "winner_votes", "runner_up",
          "runner_up_votes", "majority", "result", "winner_name", "paper"]

HEADING = re.compile(r"^\s*\d+\.\s+(?P<seat>.+?)\s+\((?P<result>[^)]+)\)\s+[-–]\s+(?P<date>\d{1,2} \w+ \d{4})\s*$")
CANDIDATE = re.compile(r"^\s*(?P<name>\S.*?)(?:\s+\+)?(?:\s+\*)?\s{2,}(?P<party>\S.*?)\s{2,}(?P<votes>[\d,]+)\s+[\d.]+%")
NUMBER = re.compile(r"^\s*(?P<label>Turnout|Majority)\s+(?P<n>[\d,]+)")
SEATS = {"Ceredigeon": "Ceredigion"}  # a typo in the factsheet
PARTIES = {"Con": "Conservative", "Lab": "Labour", "LD": "Liberal Democrat", "SNP": "Scottish National Party",
           "PC": "Plaid Cymru", "UDU": "Democratic Unionist Party", "UUP": "Ulster Unionist Party",
           "SSP": "Scottish Socialist Party"}
RESULT_PARTY = {"Lib Dem": "LD", "UDU": "DUP"}
# the new members' names as Table 1 gives them (the constituency tables have initials only)
NAMES = {"Uxbridge": "John Randall", "Paisley South": "Douglas Alexander", "Beckenham": "Jacqui Lait",
         "Winchester": "Mark Oaten", "Leeds Central": "Hilary Benn", "Eddisbury": "Stephen O'Brien",
         "Hamilton South": "Bill Tynan", "Wigan": "Neil Turner", "Kensington and Chelsea": "Michael Portillo",
         "Ceredigion": "Simon Thomas", "Romsey": "Sandra Gidley", "Tottenham": "David Lammy",
         "South Antrim": "William McCrea", "Glasgow, Anniesland": "John Robertson", "Preston": "Mark Hendrick",
         "West Bromwich West": "Adrian Bailey", "Falkirk West": "Eric Joyce"}


def text_of(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], check=True, capture_output=True, text=True).stdout


def _int(s: str) -> int:
    return int(s.replace(",", ""))


def _result(raw: str) -> str:
    """'Lib Dem gain from Con' -> 'LD Gain', as the Members API writes results."""
    party, outcome = re.match(r"(.+?) (hold|gain|win)\b", raw).groups()
    return f"{RESULT_PARTY.get(party, party)} {outcome.capitalize()}"


def parse(text: str) -> list[dict[str, object]]:
    lines = text.splitlines()
    # a heading the layout wrapped ("9. Kensington and Chelsea (Con hold) - 25 Nov" / "1999") is put back together
    for i, line in enumerate(lines[:-1]):
        if re.match(r"^\s*\d+\.\s+.+\)\s+[-–]\s+\d{1,2} \w+$", line) and re.match(r"^\s*\d{4}\s*$", lines[i + 1]):
            lines[i], lines[i + 1] = f"{line} {lines[i + 1].strip()}", ""
    out: list[dict[str, object]] = []
    cur: dict[str, object] | None = None
    for line in lines:
        m = HEADING.match(line)
        if m:
            cur = {"seat": SEATS.get(m["seat"].strip(), m["seat"].strip()), "result": _result(m["result"]),
                   "date": datetime.strptime(m["date"], "%d %b %Y").date(), "candidates": []}
            continue
        if cur is None:
            continue
        n = NUMBER.match(line)
        if n:
            cur[n["label"]] = _int(n["n"])
            if n["label"] == "Majority":
                (_, p1, v1), (_, p2, v2) = cur["candidates"][:2]
                out.append({
                    "year": cur["date"].year, "date": cur["date"].isoformat(), "constituency": cur["seat"],
                    "region": "", "electorate": "", "votes": cur["Turnout"],
                    "winner": PARTIES.get(p1, p1), "winner_votes": v1, "runner_up": PARTIES.get(p2, p2),
                    "runner_up_votes": v2, "majority": cur["Majority"], "result": cur["result"],
                    "winner_name": NAMES[cur["seat"]], "paper": "M16"})
                cur = None
            continue
        c = CANDIDATE.match(line)
        if c and c["name"] != "Candidate":
            cur["candidates"].append((c["name"], c["party"].strip(), _int(c["votes"])))
    return out


def main(pdf: str) -> None:
    rows = sorted(parse(text_of(Path(pdf))), key=lambda r: r["date"])
    assert len(rows) == 17, len(rows)
    for r in rows:  # the factsheet's own check: the majority is the gap between the first two
        assert r["winner_votes"] - r["runner_up_votes"] == r["majority"], r
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} by-elections written to {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
