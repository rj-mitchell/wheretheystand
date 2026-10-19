"""Posts held between 1997 and 2010, from parlparse's older ministers.json."""

from __future__ import annotations

import json

from hansard.reference.build import legacy_ministers
from hansard.reference.portfolios import classify_post


def test_pre2010_posts_are_written_out_and_classified(tmp_path):
    path = tmp_path / "ministers.json"
    path.write_text(json.dumps({
        "organizations": [{"id": "home-office", "name": "Home Office"},
                          {"id": "home-affairs", "name": "Home Affairs"},
                          {"id": "house-of-commons", "name": "House of Commons"},
                          {"id": "health-committee", "name": "Health Committee"}],
        "memberships": [
            {"person_id": "p/1", "organization_id": "home-office", "role": "Minister of State",
             "start_date": "1999-07-29", "end_date": "2001-06-08"},
            {"person_id": "p/2", "organization_id": "home-affairs", "role": "Shadow Secretary of State",
             "start_date": "2003-11-10", "end_date": "2005-05-10"},
            {"person_id": "p/2", "organization_id": "home-affairs", "role": "Spokesperson",
             "start_date": "1998-01-01", "end_date": "1999-01-01"},
            {"person_id": "p/3", "organization_id": "house-of-commons", "role": "Lord Privy Seal",
             "start_date": "2001-06-08", "end_date": "2003-03-17"},
            {"person_id": "p/4", "organization_id": "health-committee", "role": "Chair",
             "start_date": "2005-07-13", "end_date": ""},
            {"person_id": "p/5", "organization_id": "home-office", "role": "Minister of State",
             "start_date": "1995-01-01", "end_date": "1996-01-01"}]}))
    _names, rows = legacy_ministers(path)
    by = {(r["person_id"], r["start_date"]): r for r in rows}
    assert ("p/5", "1995-01-01") not in by, "before 1997"
    assert by[("p/1", "1999-07-29")]["role"] == "Minister of State (Home Office)"
    assert by[("p/1", "1999-07-29")]["source"] == "parlparse/governmentpost"
    assert by[("p/2", "2003-11-10")]["source"] == "parlparse/oppositionpost"
    assert by[("p/2", "1998-01-01")]["role"] == "Spokesperson (Home Affairs)"
    assert by[("p/3", "2001-06-08")]["source"] == "parlparse/governmentpost", "not a shadow post"
    assert by[("p/4", "2005-07-13")]["source"] == "parlparse/committee"
    assert by[("p/4", "2005-07-13")]["end_date"] == "", "open end left open (cut at May 2010 only if dated)"
    home = classify_post("parlparse/governmentpost", by[("p/1", "1999-07-29")]["role"])
    assert "home_office" in home.portfolios
