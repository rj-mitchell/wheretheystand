"""Whip reports from TheyWorkForYou Votes: collecting them a slice at a time, and what they change."""

from __future__ import annotations

import json

import duckdb
import httpx

from hansard.sources.whips import refresh, reports, whip_text
from hansard.votes import division_text, free_parties, party_key


def write_divisions(path):
    duckdb.connect().execute(f"""COPY (SELECT * FROM (VALUES
        ('g/1', 'commons', DATE '2025-06-20', 245), ('g/2', 'commons', DATE '2024-01-10', 12),
        ('g/3', 'lords', DATE '2023-03-01', 7), ('g/4', 'scotland', DATE '2025-01-01', 1))
        AS t(source_gid, chamber, division_date, division_number)) TO '{path}' (FORMAT parquet)""")


FREE = [{"party": "Labour", "whip_direction": "free", "whip_priority": "free"},
        {"party": "Conservative", "whip_direction": "against", "whip_priority": "three_line"}]


def test_checks_newest_first_then_weekly(tmp_path):
    parquet = tmp_path / "divisions.parquet"
    write_divisions(parquet)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/2025-06-20/245.json"):
            return httpx.Response(200, json={"whip_reports": FREE})
        return httpx.Response(200, json={"whip_reports": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    path = tmp_path / "whips.json"
    out = refresh(path, parquet, per_run=2, client=client, sleep=lambda _s: None)
    assert out["checked"] == 2 and out["still_unchecked"] == 1 and out["votes_with_reports"] == 1
    assert seen == ["/decisions/division/commons/2025-06-20/245.json", "/decisions/division/commons/2024-01-10/12.json"]
    assert "scotland" not in json.dumps(seen), "Westminster only"
    refresh(path, parquet, per_run=2, client=client, sleep=lambda _s: None)
    assert seen[2].startswith("/decisions/division/lords/"), "every update until all are checked once"
    assert not refresh(path, parquet, client=client, sleep=lambda _s: None)["updated"], "then weekly"
    assert reports(path)["g/1"][0] == {"party": "Labour", "direction": "free", "priority": "free"}


def test_wording_and_free_votes_are_not_rebellions():
    found = [{"party": "Labour", "direction": "free", "priority": "free"},
             {"party": "Conservative", "direction": "against", "priority": "three_line"}]
    assert whip_text(found) == "Labour: a free vote; Conservative: told to vote against (three line whip)"
    vt = {"kind": "other", "whips": found, "whip_text": whip_text(found)}
    assert free_parties(vt) == {"labour"} and party_key("SNP") == "scottishnationalparty"
    d = {"house": "commons", "number": 245, "date": "2025-06-20", "major_heading": "Assisted Dying",
         "minor_heading": None, "ayes": 314, "noes": 291, "vote_type": vt, "scope_note": "all members",
         "party_votes": {"Labour": {"aye": 200, "no": 150}},
         "rebels": [{"name": "A Member", "party": "Labour", "constituency": "Somewhere", "vote": "no",
                     "majority": "aye"},
                    {"name": "B Member", "party": "Conservative", "constituency": "Elsewhere", "vote": "aye",
                     "majority": "no"}]}
    text = " ".join(division_text(d))
    assert "as reported by TheyWorkForYou Votes: Labour: a free vote" in text
    assert "unlike most of their party (aye), in a free vote for the Labour" in text
    assert "against the Conservative majority (no)" in text
