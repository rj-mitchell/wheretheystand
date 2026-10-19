"""Gender and age on the date of a speech: sources, matching by Parliament ID, filters and grouping."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from hansard.engine import Engine, Filters
from hansard.rag.query_parse import QueryPlan, parse_offline
from hansard.rag.sources import speaker_label
from hansard.rank.evidence import evidence_summary
from hansard.sources.demographics import (
    age_band,
    age_on,
    assumed_birth,
    band_dates,
    load_demographics,
    parse_births,
    refresh_demographics,
)
from tests.conftest import BENN, CAMERON, GREENING


def _binding(mnis: str, dob: str, precision: int) -> dict:
    return {"mnis": {"value": mnis}, "dob": {"value": dob}, "precision": {"value": str(precision)}}


def test_parse_births_prefers_the_most_precise_statement():
    body = {"results": {"bindings": [
        _binding("1", "1966-01-01T00:00:00Z", 9),
        _binding("1", "1966-10-09T00:00:00Z", 11),
        _binding("2", "1953-01-01T00:00:00Z", 9),
        _binding("3", "1953-00-00T00:00:00Z", 8),   # decade precision: not usable
    ]}}
    births = parse_births(body)
    assert births["1"] == {"date": "1966-10-09", "precision": "day"}
    assert births["2"] == {"date": "1953-01-01", "precision": "year"}
    assert "3" not in births


def test_age_is_on_the_date_of_the_speech_not_today():
    born = date(1966, 10, 9)
    assert age_on(born, date(2012, 10, 8)) == 45
    assert age_on(born, date(2012, 10, 9)) == 46
    assert age_on(born, date(2024, 2, 1)) == 57
    # year only: the middle of the year is assumed
    assert assumed_birth("1966-01-01", "year") == date(1966, 7, 1)
    assert assumed_birth("1966-10-01", "month") == date(1966, 10, 15)


def test_age_bands_and_their_dates():
    assert [age_band(a) for a in (25, 39, 40, 54, 55, 69, 70, 91)] == [
        "under-40", "under-40", "40-54", "40-54", "55-69", "55-69", "70-plus", "70-plus"]
    assert age_band(None) is None
    assert band_dates(date(1966, 10, 9), "40-54") == (date(2006, 10, 9), date(2021, 10, 8))
    leap = date(1964, 2, 29)
    assert band_dates(leap, "under-40")[1] == date(2004, 2, 28)   # 2004 is a leap year
    end_of_40s_50s = band_dates(leap, "40-54")[1]
    assert end_of_40s_50s == date(2019, 2, 28)
    assert age_on(leap, end_of_40s_50s) == 54 and age_on(leap, date(2019, 3, 1)) == 55


def _members_transport(pages: dict[int, list[dict]]):
    def handler(request: httpx.Request) -> httpx.Response:
        house = int(request.url.params["House"])
        skip = int(request.url.params["skip"])
        items = pages.get(house, [])
        return httpx.Response(200, json={"totalResults": len(items),
                                         "items": [{"value": v} for v in items[skip:skip + 20]]})
    return httpx.MockTransport(handler)


def test_refresh_saves_each_part_and_keeps_the_old_copy_on_failure(tmp_path):
    cache = tmp_path / "demographics.json"
    client = httpx.Client(transport=_members_transport({1: [{"id": 1467, "gender": "M"}],
                                                        2: [{"id": 413, "gender": "M"}]}))

    def no_wikidata(*_a):
        raise OSError("403 robot policy")

    wiki = {"413": {"date": "1950-01-01", "precision": "year", "source": "https://en.wikipedia.org/wiki/X"},
            "1467": {"date": "1999-01-01", "precision": "year", "source": "https://en.wikipedia.org/wiki/Y"}}
    first = refresh_demographics(cache, client=client, fetch_births_fn=no_wikidata,
                                 fetch_wikipedia_births_fn=lambda _c: wiki)
    assert first["status"] == "partial" and first["updated"] == ["genders", "wikipedia_births"]
    assert load_demographics(cache)["genders"] == {"1467": "M", "413": "M"}
    assert load_demographics(cache)["births"]["413"]["precision"] == "year"

    # births arrive later; genders are fresh, so they are not fetched again
    second = refresh_demographics(cache, client=client,
                                  fetch_births_fn=lambda: {"1467": {"date": "1966-10-09", "precision": "day"}})
    assert second["updated"] == ["births"]
    assert load_demographics(cache)["births"]["1467"]["date"] == "1966-10-09", "Wikidata wins over Wikipedia"

    # a failure on a forced refresh keeps what was there
    broken = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    third = refresh_demographics(cache, force=True, client=broken, fetch_births_fn=no_wikidata,
                                 fetch_wikipedia_births_fn=no_wikidata)
    assert third["status"] == "failed"
    assert load_demographics(cache)["genders"] == {"1467": "M", "413": "M"}
    assert refresh_demographics(cache, client=broken, fetch_births_fn=no_wikidata)["status"] == "fresh"


def test_profiles_carry_gender_and_age_on_the_date(resolver):
    cameron_2012 = resolver.profile(CAMERON, date(2012, 6, 1)).to_dict()
    cameron_2024 = resolver.profile(CAMERON, date(2024, 2, 1)).to_dict()
    assert cameron_2012["gender"] == "M"
    assert cameron_2012["age"] == 45 and cameron_2012["age_band"] == "40-54"
    assert cameron_2024["age"] == 57 and cameron_2024["age_band"] == "55-69"
    assert resolver.profile(GREENING, date(2015, 1, 1)).gender == "F"
    assert resolver.profile(BENN, date(2018, 1, 1)).age == 64


def test_reference_built_without_demographics_says_not_known(tmp_path):
    from hansard.reference.build import build_reference, write_reference
    from hansard.reference.profiles import ProfileResolver
    from tests.conftest import GEO_CSV, MEMBERS

    write_reference(build_reference(MEMBERS, GEO_CSV), tmp_path)
    p = ProfileResolver(tmp_path).profile(CAMERON, date(2012, 6, 1)).to_dict()
    assert p["gender"] is None and p["age"] is None and p["age_band"] is None


def test_older_reference_files_without_the_columns_still_load(tmp_path, reference_dir):
    import shutil

    import pyarrow.parquet as pq

    from hansard.reference.profiles import ProfileResolver

    shutil.copytree(reference_dir, tmp_path / "ref")
    persons = pq.read_table(tmp_path / "ref" / "persons.parquet").drop_columns(
        ["gender", "birth_date", "birth_precision"])
    pq.write_table(persons, tmp_path / "ref" / "persons.parquet")
    p = ProfileResolver(tmp_path / "ref").profile(CAMERON, date(2012, 6, 1))
    assert p.name == "David Cameron" and p.age is None


def test_speaker_label_mentions_gender_and_age_only_when_asked(resolver):
    rec = {"speaker": resolver.profile(GREENING, date(2015, 1, 1)).to_dict(), "speaker_name_raw": "Justine Greening"}
    assert "woman" not in speaker_label(rec) and "aged" not in speaker_label(rec)
    label = speaker_label(rec, {"gender", "age"})
    assert "woman" in label and "aged 45" in label


def test_offline_parser_reads_gender_and_age_without_mistaking_topics():
    assert parse_offline("What have female MPs said about childcare?", {}).filters.genders == ["F"]
    women_topic = parse_offline("What has been said about women in prison?", {})
    assert women_topic.filters.genders == [] and "women" in women_topic.search_text
    split = parse_offline("How do women and men MPs differ on assisted dying?", {})
    assert split.group_by == "gender" and split.filters.genders == []
    assert parse_offline("What have younger MPs said about student loans?", {}).filters.age_bands == ["under-40"]
    assert parse_offline("Views on housing by age", {}).group_by == "age"


def test_plan_round_trip_keeps_demographic_filters():
    plan = parse_offline("What have female MPs said about childcare?", {})
    again = QueryPlan.from_dict(json.loads(json.dumps(plan.to_dict(), default=str)))
    assert again.filters.genders == ["F"] and again.group_by == "role"
    bad = QueryPlan.from_dict({"filters": {"genders": ["X"], "age_bands": ["teen"]}, "group_by": "height"})
    assert bad.filters.genders == [] and bad.filters.age_bands == [] and bad.group_by == "role"


def test_evidence_summary_counts_gender_and_age(resolver):
    recs = [
        {"record_id": "a", "date": "2015-01-01", "speaker": resolver.profile(GREENING, date(2015, 1, 1)).to_dict()},
        {"record_id": "b", "date": "2012-06-01", "speaker": resolver.profile(CAMERON, date(2012, 6, 1)).to_dict()},
        {"record_id": "c", "date": "2012-06-01", "speaker": None, "speaker_name_raw": "Hon. Members"},
    ]
    ev = evidence_summary(recs)
    assert ev["genders"] == {"F": 1, "M": 1}
    assert ev["age_bands"] == {"40-54": 2}


@pytest.fixture(scope="module")
def engine(indexed_settings):
    return Engine(indexed_settings, today=date(2026, 10, 10))


def test_gender_filter_limits_speakers(engine):
    every = engine.search("the", Filters(), limit=200)
    women = engine.search("the", Filters(genders=["F"]), limit=200)
    assert women.records and len(women.records) < len(every.records)
    assert all(r["speaker"]["gender"] == "F" for r in women.records)


def test_age_filter_checks_age_on_the_day(engine):
    result = engine.search("the", Filters(age_bands=["55-69"]), limit=200)
    assert result.records
    assert all(r["speaker"]["age_band"] == "55-69" for r in result.records)
    nobody = engine.search("the", Filters(age_bands=["55-69"], genders=["F"], person_ids=[CAMERON]), limit=50)
    assert nobody.records == []


def test_sources_are_listed_under_the_grouping_asked_for(resolver):
    from hansard.rag.sources import group_label

    greening = resolver.profile(GREENING, date(2015, 1, 1)).to_dict()
    assert group_label(greening, "gender") == "Women"
    assert group_label(greening, "age") == "Aged 40 to 54 on the day"
    assert group_label(None, "age") == "Age not known"
    assert group_label(greening) == "Government ministers"


def test_a_member_missing_their_parliament_id_is_matched_by_wikidata_item(tmp_path):
    import json
    import shutil

    from hansard.reference.build import build_reference

    from .conftest import BENN, DEMOGRAPHICS, GEO_CSV, MEMBERS

    members = tmp_path / "members"
    shutil.copytree(MEMBERS, members)
    people = json.loads((members / "people.json").read_text())
    for p in people["persons"]:
        if p["id"] == BENN:
            p["identifiers"] = [i for i in p["identifiers"] if i["scheme"] != "datadotparl_id"]
    (members / "people.json").write_text(json.dumps(people))
    demo = load_demographics(DEMOGRAPHICS)
    without = build_reference(members, GEO_CSV, demographics=demo)
    assert next(p for p in without.persons if p["person_id"] == BENN)["birth_date"] is None
    demo["births"]["413"] = {**demo["births"]["413"], "qid": "Q262742"}
    benn = next(p for p in build_reference(members, GEO_CSV, demographics=demo).persons if p["person_id"] == BENN)
    assert benn["datadotparl_id"] == "413" and benn["gender"] == "M" and str(benn["birth_date"]) == "1953-11-26"


def test_birth_dates_from_wikipedia_infobox_templates_only():
    from hansard.sources.demographics import birth_from_wikitext, fetch_wikipedia_births

    box = "{{Infobox officeholder\n| name = A Member\n| birth_date = %s\n| party = X\n}}"
    assert birth_from_wikitext(box % "{{birth date and age|df=yes|1975|4|2}}") == {"date": "1975-04-02",
                                                                                   "precision": "day"}
    assert birth_from_wikitext(box % "{{Birth year and age|1979}}") == {"date": "1979-01-01", "precision": "year"}
    assert birth_from_wikitext(box % "{{birth year and age|1979|5}}")["precision"] == "month"
    assert birth_from_wikitext(box % "c. 1970") is None, "free text is not used"
    assert birth_from_wikitext(box % "{{birth date and age|1975|13|2}}") is None
    assert birth_from_wikitext("He was born in 1960. {{birth date|1960|1|1}}") is None, "only the infobox field"

    def handler(request: httpx.Request) -> httpx.Response:
        assert set(request.url.params["titles"].split("|")) == {"A Member", "B Member (politician)"}
        return httpx.Response(200, json={"query": {
            "redirects": [{"from": "B Member (politician)", "to": "B Member"}],
            "pages": [{"title": "A Member", "revisions": [{"slots": {"main": {"content": box % "{{bda|1980|2|3}}"}}}]},
                      {"title": "B Member", "revisions": [{"slots": {"main": {"content": box % "unknown"}}}]}]}})

    out = fetch_wikipedia_births(httpx.Client(transport=httpx.MockTransport(handler)), query_fn=lambda: [
        ("5288", "https://en.wikipedia.org/wiki/A_Member"),
        ("5238", "https://en.wikipedia.org/wiki/B_Member_(politician)")])
    assert out == {"5288": {"date": "1980-02-03", "precision": "day",
                            "source": "https://en.wikipedia.org/wiki/A_Member"}}
