"""Election results, majorities at the time and now, first terms: data, matching, filters and grouping."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from hansard.engine import Engine, Filters
from hansard.rag.query_parse import QueryPlan, parse_offline
from hansard.rag.sources import group_label, speaker_label
from hansard.sources.elections import (
    add_library_results,
    commons_member_ids,
    first_term,
    general_elections,
    library_results,
    load_elections,
    majority_band,
    refresh_elections,
    seat_key,
)
from tests.conftest import BENN, CAMERON, MEMBERS


def test_majority_bands():
    assert [majority_band(x) for x in (0.9, 9.99, 10, 19.9, 20, 49.7)] == [
        "marginal", "marginal", "middle", "middle", "safe", "safe"]
    assert majority_band(None) is None


def test_first_term_lasts_until_the_next_general_election():
    ge = general_elections()
    assert first_term(date(2019, 12, 12), date(2024, 7, 3), ge) is True
    assert first_term(date(2019, 12, 12), date(2024, 7, 4), ge) is False   # re-elected (or not) on this day
    assert first_term(date(2021, 7, 2), date(2023, 1, 1), ge) is True      # by-election entrant
    assert first_term(date(1999, 6, 10), date(2018, 1, 1), ge) is False
    assert first_term(None, date(2018, 1, 1), ge) is None


def test_majority_then_and_now_from_real_results(resolver):
    cameron = resolver.profile(CAMERON, date(2012, 6, 1)).to_dict()
    assert cameron["majority_then"] == {"majority": 22740, "pct": 39.4, "band": "safe",
                                        "election": "2010 general election", "date": "2010-05-06"}
    assert cameron["first_term"] is False and cameron.get("majority_now") is None  # no longer an MP
    benn = resolver.profile(BENN, date(2018, 3, 1)).to_dict()
    assert benn["majority_then"]["election"] == "2017 general election"
    assert benn["majority_now"]["constituency"] == "Leeds South"
    lord = resolver.profile(CAMERON, date(2024, 2, 1)).to_dict()
    assert "majority_then" not in lord and "first_term" not in lord  # a peer: no seat facts


def test_labels_mention_majority_and_term_only_when_asked(resolver):
    rec = {"speaker": resolver.profile(BENN, date(2018, 3, 1)).to_dict()}
    assert "majority" not in speaker_label(rec)
    label = speaker_label(rec, {"majority", "tenure"})
    assert "majority 23,698 (49.7%) at the 2017 general election" in label and "re-elected MP" in label
    assert group_label(rec["speaker"], "majority") == "Safe (majority over 20%)"
    assert group_label(rec["speaker"], "tenure") == "Re-elected MPs"


def test_offline_parser_reads_new_mps_and_marginal_seats():
    assert parse_offline("What have new MPs said about housing?", {}).filters.tenure == ["first-term"]
    assert parse_offline("How have MPs in marginal seats talked about immigration?", {}).filters.majority_bands == [
        "marginal"]
    both = parse_offline("How do MPs in marginal and safe seats differ on onshore wind?", {})
    assert both.group_by == "majority" and both.filters.majority_bands == []
    assert parse_offline("What have MPs said about safe standing at football?", {}).filters.majority_bands == []
    again = QueryPlan.from_dict({"filters": {"tenure": ["first-term", "x"], "majority_bands": ["safe", "y"]},
                                 "group_by": "majority"})
    assert again.filters.tenure == ["first-term"] and again.filters.majority_bands == ["safe"]
    assert again.group_by == "majority"


def test_member_ids_come_from_commons_memberships_since_1997():
    ids = commons_member_ids(MEMBERS / "people.json")
    assert "1467" in ids and "413" in ids  # Cameron, Benn
    assert set(commons_member_ids(MEMBERS / "people.json", since="2010-05-06")) < set(ids)


def test_results_before_2010_come_from_the_commons_library_by_seat_name():
    assert seat_key("LIVERPOOL, WEST DERBY") == seat_key("Liverpool West Derby")
    assert seat_key("BEDFORDSHIRE MID") == seat_key("Mid Bedfordshire")
    assert seat_key("Ynys Môn") == seat_key("YNYS MON") and seat_key("Richmond (Yorks)") == seat_key("RICHMOND")
    library = library_results()
    assert library[(seat_key("Mitcham and Morden"), "1997-05-01")]["majority"] == 13741
    assert library[(seat_key("Tatton"), "1997-05-01")]["majority"] is None, "won by an independent: not knowable"
    reps = {"193": [{"id": 1986, "name": "Mitcham and Morden", "start": "1997-05-01", "end": "2010-05-06"}],
            "9": [{"id": 50, "name": "Peckham", "start": "1992-04-09", "end": "1997-05-01"}]}  # abolished in 1997
    results: dict = {}
    assert add_library_results(reps, results, library) == 3
    assert [r["date"] for r in results["1986"]] == ["1997-05-01", "2001-06-07", "2005-05-05"]
    assert "50" not in results, "a seat that ended at the 1997 election did not fight it"


def test_by_elections_of_the_1997_parliament_come_from_factsheet_m16():
    library = library_results()
    leeds = library[(seat_key("Leeds Central"), "1999-06-10")]
    assert (leeds["majority"], leeds["votes"], leeds["result"], leeds["general"]) == (2293, 13187, "Lab Hold", False)
    assert "M16" in leeds["source"] and leeds["electorate"] is None
    assert library[(seat_key("Glasgow Anniesland"), "2000-11-23")]["party"] == "Labour"
    assert library[(seat_key("South Antrim"), "2000-09-21")]["result"] == "DUP Gain"
    reps = {"413": [{"id": 1688, "name": "Leeds Central", "start": "1999-06-10", "end": "2015-03-30"}]}
    results: dict = {}
    add_library_results(reps, results, library)
    assert [r["date"] for r in results["1688"]] == ["1999-06-10", "2001-06-07", "2005-05-05"]


def test_refresh_keeps_the_old_copy_when_the_api_fails(tmp_path):
    cache = tmp_path / "elections.json"
    bio = {"value": {"representations": [{"house": 1, "id": 7, "name": "Seat", "startDate": "2019-12-12T00:00:00",
                                          "endDate": None}]}}
    res = {"value": [{"electionDate": "2019-12-12T00:00:00", "electionTitle": "2019 General Election",
                      "isGeneralElection": True, "isNotional": False, "majority": 100, "turnout": 1000,
                      "electorate": 2000, "result": "Lab Hold", "winningParty": {"name": "Labour"}},
                     {"electionDate": "2019-12-12T00:00:00", "isNotional": True, "majority": 5}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bio if "Biography" in request.url.path else res)

    ok = refresh_elections(cache, ["1"], client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert ok["status"] == "updated" and ok["results"] == 1  # the notional result is ignored
    down = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert refresh_elections(cache, ["1"], force=True, client=down)["status"] == "failed"
    assert '"majority": 100' in cache.read_text()
    calls: list[str] = []
    counting = httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r.url.path) or handler(r)))
    assert refresh_elections(cache, ["1"], client=counting)["status"] == "fresh"
    assert refresh_elections(cache, ["1", "2"], client=counting)["status"] == "updated", \
        "members the copy never looked up (the record grew) are fetched straight away"
    assert calls


@pytest.fixture(scope="module")
def engine(indexed_settings):
    return Engine(indexed_settings, today=date(2026, 10, 10))


def test_majority_and_tenure_filters_check_the_day(engine):
    safe = engine.search("the", Filters(majority_bands=["safe"]), limit=200)
    assert safe.records and all(r["speaker"]["majority_band"] == "safe" for r in safe.records)
    first = engine.search("the", Filters(tenure=["first-term"]), limit=200)
    assert all(r["speaker"]["first_term"] is True for r in first.records)
    returning = engine.search("the", Filters(tenure=["returning"]), limit=200)
    assert returning.records and all(r["speaker"]["first_term"] is False for r in returning.records)


def _api(calls: list[str], throttle_after: int | None = None):
    """A stand-in Members API: member N sat for seat N (member "9" left in 2015); every seat has one result."""
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if throttle_after is not None and len(calls) > throttle_after:
            return httpx.Response(429)
        n = request.url.path.split("/")[-2]
        if "Biography" in request.url.path:
            end = "2015-03-30T00:00:00" if n == "9" else None
            return httpx.Response(200, json={"value": {"representations": [
                {"house": 1, "id": int(n), "name": f"Seat {n}", "startDate": "2010-05-06T00:00:00", "endDate": end}]}})
        return httpx.Response(200, json={"value": [{"electionDate": "2010-05-06T00:00:00", "isGeneralElection": True,
                                                    "isNotional": False, "majority": int(n), "result": "Hold"}]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_throttled_refresh_keeps_its_progress_and_carries_on(tmp_path):
    cache, calls = tmp_path / "elections.json", []
    members = ["1", "2", "3", "9"]
    first = refresh_elections(cache, members, client=_api(calls, throttle_after=3), sleep=lambda s: None)
    assert first["status"] == "partial" and "slow down" in first["stopped"] and len(calls) == 4  # stopped at once
    second = refresh_elections(cache, members, client=_api(calls), sleep=lambda s: None)
    assert second["status"] == "updated" and second["remaining"] == 0
    assert len(calls) == 4 + 5  # only what was left: one member and four seats
    assert refresh_elections(cache, members, client=_api(calls), sleep=lambda s: None)["status"] == "fresh"


def test_the_weekly_refresh_covers_sitting_members_only(tmp_path):
    from datetime import UTC, datetime, timedelta

    cache, calls = tmp_path / "elections.json", []
    members = ["1", "2", "9"]
    refresh_elections(cache, members, client=_api(calls), sleep=lambda s: None)
    calls.clear()
    week_later = datetime.now(UTC) + timedelta(days=8)
    out = refresh_elections(cache, members, client=_api(calls), sleep=lambda s: None, now=lambda: week_later)
    assert out["status"] == "updated"
    assert sorted(calls) == sorted(["/api/Members/1/Biography", "/api/Members/2/Biography",
                                    "/api/Location/Constituency/1/ElectionResults",
                                    "/api/Location/Constituency/2/ElectionResults"])  # not member 9, who left


def test_the_saved_copy_supplies_seats_and_results_when_the_server_has_none(tmp_path):
    # Parliament's service unreachable from the server: no copy of its own, yet Adam Dance (5191) has Yeovil's result
    data = load_elections(tmp_path / "missing.json")
    seat = data["representations"]["5191"][0]
    assert seat["name"] == "Yeovil"
    assert any(r["date"] == "2024-07-04" for r in data["results"][str(seat["id"])])


def test_one_parliament_id_per_person_when_parlparse_has_a_member_twice():
    from hansard.reference.build import parliament_id_for

    # Willie Rennie: an MP (no ID in parlparse, found and saved) and an MSP, with the same Wikidata item
    saved, by_qid = {"mp": "1600"}, {"Q2581473": "1600"}
    claimed = set(saved.values())
    assert parliament_id_for("mp", {"wikidata": "Q2581473"}, saved, by_qid, claimed) == "1600"
    assert parliament_id_for("msp", {"wikidata": "Q2581473"}, saved, by_qid, claimed) is None
    # two people found only through Wikidata: the first keeps it
    claimed = set()
    assert parliament_id_for("a", {"wikidata": "Q2581473"}, {}, by_qid, claimed) == "1600"
    assert parliament_id_for("b", {"wikidata": "Q2581473"}, {}, by_qid, claimed) is None
    assert parliament_id_for("c", {"datadotparl_id": "7"}, {}, by_qid, claimed) == "7"
