"""Turn the House of Commons Library's by-election research papers into data_static/elections/by_elections_2001_2010.csv.

Research Paper 05/34 (By-elections 2001-05) and 10/50 (By-elections 2005-10), Open Parliament Licence. Each by-election
has a table: candidates with their votes, then the majority, total poll and electorate. The text is read with
pdftotext -layout. Run once; the CSV is kept with the code.

    uv run python scripts/library_by_elections.py RP05-34.pdf RP10-50.pdf
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data_static" / "elections" / "by_elections_2001_2010.csv"
FIELDS = ["year", "date", "constituency", "region", "electorate", "votes", "winner", "winner_votes", "runner_up",
          "runner_up_votes", "majority", "result", "winner_name", "paper"]

HEADING = re.compile(r"^\s*(?:\d+\.\s+|2\.\d+\s+)(?P<seat>.+?)\s+-\s+(?P<date>\d{1,2} \w+ \d{4})\s{2,}(?P<result>\S.*?)\s*$")
CANDIDATE = re.compile(r"^\s*(?P<name>\S.*?)\s+\+?\s{2,}(?P<party>\S.*?)\s{2,}(?P<votes>[\d,]+)\s*\**\s+[\d.]+%")
NUMBER = re.compile(r"^(?P<label>Majority of winning candidate|Total poll|Electorate)\s+(?P<n>[\d,]+)")
# the papers shorten a few seat names
SEATS = {"Dunfermline & W Fife": "Dunfermline and West Fife"}
PARTIES = {"Liberal Democrat": "Liberal Democrat", "Labour": "Labour", "Conservative": "Conservative",
           "SNP": "Scottish National Party", "Respect": "Respect", "Independent": "Independent"}
RESULT_PARTY = {"Labour": "Lab", "Liberal Democrat": "LD", "Lib Dem": "LD", "L Dem": "LD", "Conservative": "Con",
                "SNP": "SNP", "Independent": "Ind", "Respect": "Respect"}


def text_of(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], check=True, capture_output=True, text=True).stdout


def _int(s: str) -> int:
    return int(s.replace(",", ""))


def _result(raw: str) -> str:
    """'Liberal Democrat gain' -> 'LD Gain', as the Members API writes results."""
    party, _, outcome = raw.rpartition(" ")
    return f"{RESULT_PARTY.get(party, party)} {outcome.capitalize()}"


def parse(text: str, paper: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    cur: dict[str, object] | None = None
    for line in text.splitlines():
        m = HEADING.match(line)
        if m and not re.search(r"\s\d+$", line):  # a contents line ends in its page number
            cur = {"seat": SEATS.get(m["seat"].strip(), m["seat"].strip()), "result": m["result"],
                   "date": datetime.strptime(m["date"], "%d %B %Y").date(), "candidates": []}
            continue
        if cur is None:
            continue
        n = NUMBER.match(line.strip())
        if n:
            cur[n["label"]] = _int(n["n"])
            if n["label"] == "Electorate":
                cands = cur["candidates"]
                out.append({
                    "year": cur["date"].year, "date": cur["date"].isoformat(), "constituency": cur["seat"],
                    "region": "", "electorate": cur["Electorate"], "votes": cur["Total poll"],
                    "winner": PARTIES.get(cands[0][1], cands[0][1]), "winner_votes": cands[0][2],
                    "runner_up": PARTIES.get(cands[1][1], cands[1][1]), "runner_up_votes": cands[1][2],
                    "majority": cur["Majority of winning candidate"], "result": _result(cur["result"]),
                    "winner_name": cands[0][0], "paper": paper})
                cur = None
            continue
        c = CANDIDATE.match(line)
        if c and not c["name"].startswith(("Other", "Majority")):
            cur["candidates"].append((c["name"].rstrip(" +"), c["party"].strip(), _int(c["votes"])))
    return out


def main(pdfs: list[str]) -> None:
    rows = []
    for p in pdfs:
        paper = "RP" + re.search(r"(\d\d)[-/](\d\d)", Path(p).name).group(0).replace("-", "/")
        rows += parse(text_of(Path(p)), paper)
    rows.sort(key=lambda r: r["date"])
    for r in rows:  # the papers' own check: the majority is the gap between the first two
        assert r["winner_votes"] - r["runner_up_votes"] == r["majority"], r
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} by-elections written to {OUT}")


if __name__ == "__main__":
    main(sys.argv[1:])
