from datetime import date

import pytest

from hansard.parse.xml_stream import norm_time, parse_divisions, parse_file

from .conftest import XML


def rows(directory, name):
    return list(parse_file(XML / directory / name, directory))


def test_modern_commons_debate():
    rs = rows("debates", "debates2026-09-15c.xml")
    q = next(r for r in rs if r["speech_type"] == "Start Question")
    assert q["house"] == "commons" and q["chamber"] == "House of Commons"
    assert q["xml_person_id"].startswith("uk.org.publicwhip/person/")
    assert q["oral_heading"] == "Oral Answers to Questions"
    assert q["major_heading"] == "Justice" and q["minor_heading"] == "Trial by Jury"
    assert q["colnum"] == "1557" and q["date"] == date(2026, 9, 15)
    assert q["official_url"] == "https://hansard.parliament.uk/Commons/2026-09-15" and q["url_kind"] == "sitting"
    assert q["twfy_url"] == f"https://www.theyworkforyou.com/debates/?id={q['record_id'].split('/', 2)[-1]}"
    assert q["source_key"] == "debates2026-09-15" and q["revision"] == "c"
    procedural = [r for r in rs if r["record_type"] == "procedural"]
    assert procedural and all(r["xml_person_id"] is None for r in procedural)


def test_2012_debate_uses_member_ids_latin1_and_entities():
    rs = rows("debates", "debates2012-03-07a.xml")
    sp = [r for r in rs if r["record_type"] == "speech"]
    assert sp and all(r["xml_person_id"].startswith("uk.org.publicwhip/member/") for r in sp)
    assert all("&" not in r["text"] or "&amp;" not in r["text"] for r in rs)
    assert sp[0]["official_url"].startswith("http://www.publications.parliament.uk/")
    assert sp[0]["url_kind"] == "record"
    assert sp[0]["time"] == "11:30"


def test_written_questions_and_answers_are_paired():
    rs = rows("wrans", "answers2026-10-08.xml")
    qs = [r for r in rs if r["record_type"] == "question"]
    ans = [r for r in rs if r["record_type"] == "answer"]
    assert len(qs) == len(ans) == 4
    a = ans[0]
    assert a["context"] == qs[0]["text"]
    assert a["thread_id"] == qs[0]["thread_id"] and a["uin"] == qs[0]["uin"]
    # Replies carry no URL of their own; they inherit the question's official page.
    assert a["official_url"].startswith("https://questions-statements.parliament.uk/written-questions/")
    assert a["colnum"] is None


def test_2012_written_answers_have_columns():
    a = next(r for r in rows("wrans", "answers2012-03-07a.xml") if r["record_type"] == "answer")
    assert a["colnum"].endswith("W") and a["uin"] and a["context"]


def test_lords_and_lords_written_answers():
    lord = next(r for r in rows("lordspages", "daylord2026-09-17b.xml") if r["record_type"] == "speech")
    assert lord["house"] == "lords" and lord["official_url"] == "https://hansard.parliament.uk/Lords/2026-09-17"
    lw = rows("lordswrans", "lordswrans2026-10-08.xml")
    assert {r["house"] for r in lw} == {"lords"} and any(r["uin"].startswith("HL") for r in lw)
    old = next(r for r in rows("lordspages", "daylord2012-03-07a.xml") if r["record_type"] == "speech")
    assert old["xml_person_id"].startswith("uk.org.publicwhip/lord/")


def test_westminster_hall_and_statements():
    wh = rows("westminhall", "westminster2026-09-15b.xml")
    assert {r["chamber"] for r in wh} == {"Westminster Hall"}
    assert wh[0]["twfy_url"].startswith("https://www.theyworkforyou.com/whall/?id=")
    wms = rows("wms", "ministerial2026-09-15.xml")
    assert {r["record_type"] for r in wms} == {"statement"}
    assert wms[0]["uin"] == "hcws353"
    assert wms[0]["official_url"].endswith("/written-statements/detail/2026-09-15/hcws353")
    assert rows("lordswms", "lordswms2026-09-16.xml")[0]["house"] == "lords"


def test_public_bill_committee():
    rs = rows("standing", "standing5709_IMMIGRATION_02-0_2026-09-15b.xml")
    sp = [r for r in rs if r["record_type"] == "speech"]
    assert sp and {r["bill"] for r in sp} == {"Immigration and Asylum Bill"}
    assert sp[0]["bill_session"] == "2026-27"
    assert sp[0]["source_key"] == "standing5709_IMMIGRATION_02-0_2026-09-15"
    assert "/pbc/2026-27/Immigration_and_Asylum_Bill/" in sp[0]["twfy_url"]


def test_divisions_commons_old_and_new_and_lords():
    divs, votes = parse_divisions(XML / "divisionsonly" / "divisions2012-03-07a.xml", "divisionsonly")
    assert divs[0]["ayes"] == 308 and divs[0]["noes"] == 183 and divs[0]["time"] == "20:43"
    assert {v["vote"] for v in votes} == {"aye", "no"}
    divs, votes = parse_divisions(XML / "divisionsonly" / "divisions2025-12-16c.xml", "divisionsonly")
    assert len(divs) == 2 and divs[0]["major_heading"] == "Finance (No. 2) Bill"
    assert any(v["teller"] for v in votes)
    divs, votes = parse_divisions(XML / "lordsdivisionsonly" / "lordsdivisions2025-12-10b.xml",
                                  "lordsdivisionsonly")
    assert divs[0]["house"] == "lords" and divs[0]["ayes"] == 244 and divs[0]["noes"] == 220
    assert {v["vote"] for v in votes} == {"aye", "no"}  # content / not-content


@pytest.mark.parametrize("raw,expected", [
    ("11:30:00", "11:30"), ("020:43:00", "20:43"), ("9:05", "09:05"), ("", None), (None, None), ("x", None),
])
def test_time_normalisation(raw, expected):
    assert norm_time(raw) == expected
