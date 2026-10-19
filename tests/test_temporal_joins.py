"""Temporal joins between speeches and offices: right post on the right date, no fan-out."""

import json
import shutil
from datetime import date

import duckdb

from hansard.reference.build import build_reference, write_reference
from hansard.reference.profiles import ProfileResolver, attach_reference, enrich

from .conftest import BENN, GEO_CSV, GREENING, MEMBERS


def test_benn_role_history(resolver):
    def titles(d):
        return resolver.profile(BENN, d).office_titles

    assert titles(date(2015, 10, 14)) == ["Shadow Foreign Secretary"]
    # Committee chair while a backbencher; the source lists this chair spell three
    # times (exact duplicates) - exactly one office must come back.
    assert titles(date(2018, 3, 1)) == ["Chair, Exiting the European Union Committee"]
    assert titles(date(2022, 3, 1)) == []  # backbencher
    assert titles(date(2025, 3, 1)) == ["The Secretary of State for Northern Ireland"]
    # Same person, different seat after the 2024 boundary changes.
    assert resolver.profile(BENN, date(2015, 10, 14)).constituency == "Leeds Central"
    assert resolver.profile(BENN, date(2025, 3, 1)).constituency == "Leeds South"


def test_duplicate_source_rows_are_merged(reference):
    ref, _ = reference
    chair = [o for o in ref.offices if o["person_id"] == BENN and o["kind"] == "chair"]
    # 2016-10-19..2017-05-03, 2017-07-12..2019-11-06, 2020-01-27..2021-01-16
    assert len(chair) == 3
    assert all(len(o["source_ids"]) >= 2 for o in chair)


def test_concurrent_posts_do_not_duplicate_records(resolver):
    p = resolver.profile(GREENING, date(2017, 3, 1))
    roles = set(p.office_titles)
    assert "The Secretary of State for Education" in roles
    assert "Minister for Women and Equalities" in roles


def test_enrichment_is_one_row_per_record(reference_dir):
    con = duckdb.connect()
    attach_reference(con, reference_dir)
    con.execute("CREATE TABLE recs (record_id VARCHAR, xml_person_id VARCHAR, date DATE)")
    rows = []
    for i, d in enumerate([date(2012, 3, 1), date(2017, 3, 1), date(2018, 3, 1), date(2024, 2, 1)]):
        for pid in (BENN, GREENING, "uk.org.publicwhip/person/10777", None, "uk.org.publicwhip/person/0"):
            rows.append((f"r{i}-{pid}", pid, d))
    con.executemany("INSERT INTO recs VALUES (?, ?, ?)", rows)
    out = enrich(con).fetchall()
    assert len(out) == len(rows)
    assert len({r[0] for r in out}) == len(rows)


def _members_with_open_ended_offices(tmp_path):
    members = tmp_path / "members"
    shutil.copytree(MEMBERS, members)
    f = members / "ministers-2010.json"
    data = json.loads(f.read_text())
    base = {"source": "datadotparl/governmentpost", "organization_id": "house-of-commons",
            "person_id": BENN, "start_date": "2026-01-01"}
    data["memberships"] += [
        {**base, "id": "t/null", "role": "Minister of State (Department for Transport)", "end_date": None},
        {**base, "id": "t/empty", "role": "Minister of State (Home Office)", "end_date": ""},
        {**base, "id": "t/missing", "role": "Minister of State (Department of Health and Social Care)"},
        {**base, "id": "t/year", "role": "Minister of State (Ministry of Defence)", "start_date": "2026",
         "end_date": "2026"},
    ]
    f.write_text(json.dumps(data))
    return members


def test_open_ended_end_dates_match_current_speeches(tmp_path):
    ref = build_reference(_members_with_open_ended_offices(tmp_path), GEO_CSV)
    write_reference(ref, tmp_path / "ref")
    r = ProfileResolver(tmp_path / "ref")
    titles = set(r.profile(BENN, date(2026, 9, 30)).office_titles)
    assert "Minister of State (Department for Transport)" in titles      # end_date: null
    assert "Minister of State (Home Office)" in titles                  # end_date: ""
    assert "Minister of State (Department of Health and Social Care)" in titles  # missing
    assert "Minister of State (Ministry of Defence)" in titles          # year-only "2026"
    assert "Minister of State (Ministry of Defence)" not in set(r.profile(BENN, date(2027, 1, 2)).office_titles)
