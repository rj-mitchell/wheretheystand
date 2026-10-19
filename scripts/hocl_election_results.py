"""Turn the House of Commons Library's general election results (CBP-8647, by constituency, 1918-2019) into the
small file the site reads for elections before 2010: data_static/elections/general_elections_1997_2005.csv.

Run once by hand, with openpyxl installed (it is not one of the site's own dependencies):
    python scripts/hocl_election_results.py 1918-2019election_results_by_pcon.xlsx

The Library publishes votes by party, with smaller parties and independents added together as "Other". The winner
and runner-up are the two parties with most votes; when either is "Other" the majority cannot be known from this
file and is left blank.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import openpyxl

ELECTIONS = {"1997": "1997-05-01", "2001": "2001-06-07", "2005": "2005-05-05"}
OUT = Path(__file__).resolve().parents[1] / "data_static" / "elections" / "general_elections_1997_2005.csv"
PARTY = {"conservative": "Conservative", "conservative party": "Conservative", "labour": "Labour",
         "liberal democrat": "Liberal Democrat", "liberal democrats": "Liberal Democrat", "snp": "Scottish National Party",
         "plaid cymru": "Plaid Cymru", "dup": "Democratic Unionist Party", "sdlp": "Social Democratic and Labour Party",
         "sf": "Sinn Féin", "sinn fein": "Sinn Féin", "uu": "Ulster Unionist Party", "uup": "Ulster Unionist Party",
         "ukup": "UK Unionist Party", "alliance": "Alliance", "other": "Other"}


def party(header: str, region: str) -> str:
    h = " ".join(str(header).split()).lower()
    if h in ("pc/snp", "snp/plaid cymru"):  # one column for both: the seat's nation says which
        return "Scottish National Party" if region == "Scotland" else "Plaid Cymru"
    return PARTY.get(h, " ".join(str(header).split()))


def sheet_rows(ws, year: str):
    rows = list(ws.iter_rows(values_only=True))
    parties = rows[2]
    header = [str(c).strip().lower() if c else "" for c in rows[3]]
    name_col = header.index("constituency")
    region_col = header.index("country/region")
    elect_col = next(i for i, h in enumerate(header) if h.startswith("electorate"))
    total_col = header.index("total votes")
    party_cols = [(i, parties[i]) for i, h in enumerate(header) if h == "votes" and parties[i]]
    for r in rows[4:]:
        name = r[name_col]
        if not name or not isinstance(r[elect_col], (int, float)):
            continue  # notes at the foot of the sheet
        region = r[region_col] or ""
        votes = sorted(((int(r[i]), party(p, region)) for i, p in party_cols if isinstance(r[i], (int, float))),
                       reverse=True)
        if not votes:
            continue
        (v1, p1), (v2, p2) = votes[0], votes[1] if len(votes) > 1 else (0, "")
        majority = v1 - v2 if "Other" not in (p1, p2) else None
        yield {"year": year, "date": ELECTIONS[year], "constituency": " ".join(str(name).split()), "region": region,
               "electorate": int(r[elect_col]), "votes": int(r[total_col]) if r[total_col] else None,
               "winner": p1, "winner_votes": v1, "runner_up": p2, "runner_up_votes": v2, "majority": majority}


def main(path: str) -> None:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = [row for year in ELECTIONS for row in sheet_rows(wb[year], year)]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print(f"{len(out)} results written to {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
