"""Debate pages: a whole debate in order, found from any of its contributions."""

from __future__ import annotations

import duckdb
from fastapi.testclient import TestClient

from hansard.debates import anchor, debate_path, title_of
from hansard.store.parquet import attach_store

from .test_api import make_app


def test_addresses():
    assert title_of("westminhall", None, "Excess Death Trends — [Sir Gary Streeter in the Chair]", None) == \
        "Excess Death Trends"
    assert title_of("standing", "Public Bill Committee", "Clause 15", "Criminal Justice Bill") == "Criminal Justice Bill"
    assert title_of("wrans", "Health", "Waiting Lists", None) is None, "written answers are not debates"
    assert debate_path("debates", "2023-12-12", "Safety of Rwanda (Asylum and Immigration) Bill", None, None,
                       "uk.org.publicwhip/debate/2023-12-12b.851.3") == \
        "/debates/2023-12-12/commons/safety-of-rwanda-asylum-and-immigration-bill#c851-3"
    assert debate_path("lordspages", "2024-01-16", "Coram’s Charter for Children - Question") == \
        "/debates/2024-01-16/lords/coram-s-charter-for-children-question"
    assert anchor("uk.org.publicwhip/standing/standing5142_CRIMINAL_07-0_2024-01-16a.183.4") == "c183-4"


def test_a_debate_from_one_of_its_contributions(indexed_settings, tmp_path):
    con = duckdb.connect()
    attach_store(con, indexed_settings.parquet_dir)
    rid, source, day, major, minor, bill = con.execute(
        "SELECT record_id, source, date, major_heading, minor_heading, bill FROM records "
        "WHERE source = 'debates' AND xml_person_id IS NOT NULL AND major_heading IS NOT NULL LIMIT 1").fetchone()
    path = debate_path(source, day, major, minor, bill, rid)
    page_path, frag = path.split("#")
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    d = client.get(f"/api{page_path}").json()
    assert d["title"] == major and d["chamber"] == "commons" and d["contributions"] >= 1
    ids = [i["id"] for i in d["items"]]
    assert frag in ids and len(ids) == len(set(ids)), "every contribution has its own anchor"
    said = [i for i in d["items"] if i["kind"] == "said"]
    assert all(i["speaker"]["name"] for i in said)
    order = [tuple(int(x) for x in i["record_id"].rsplit(".", 2)[1:]) for i in d["items"] if "record_id" in i]
    assert order == sorted(order), "in the order of the record"
    page = client.get(page_path)
    assert page.status_code == 200 and major.split()[0] in page.text and 'name="robots"' not in page.text
    assert client.get(f"/api/debates/{day}/commons/no-such-debate").status_code == 404
    assert client.get(f"/debates/{day}/commons/no-such-debate").status_code == 404
    assert client.get(f"/api/debates/{day}/written-answers/x").status_code == 404
    assert client.get("/api/debates/2020-13-45/commons/x").status_code == 404


def test_feeds(indexed_settings, tmp_path):
    import xml.etree.ElementTree as ET

    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    for path in ("/feeds/bills.xml", "/feeds/topics.xml", "/feeds/corrections.xml", "/feeds/topics/cost-of-living.xml"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/rss+xml")
        channel = ET.fromstring(r.content).find("channel")
        assert channel is not None and channel.findtext("title").endswith("Where MPs Stand")
        for item in channel.findall("item"):
            assert item.findtext("title") and item.findtext("link") and item.findtext("pubDate")
    con = duckdb.connect()
    attach_store(con, indexed_settings.parquet_dir)
    rid, source, day, major = con.execute(
        "SELECT record_id, source, date, major_heading FROM records WHERE source = 'debates' "
        "AND xml_person_id IS NOT NULL AND major_heading IS NOT NULL LIMIT 1").fetchone()
    debate = client.get("/api" + debate_path(source, day, major)).json()
    number = next(i["speaker"]["number"] for i in debate["items"] if i["kind"] == "said" and i["speaker"]["number"])
    feed = ET.fromstring(client.get(f"/feeds/people/{number}.xml").content)
    assert feed.find("channel/item") is not None, "a member's feed lists their contributions"
    assert client.get("/feeds/people/999999999.xml").status_code == 404
    assert client.get("/feeds/topics/no-such-topic.xml").status_code == 404
    r = client.get("/changelog", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/about"


def test_the_week_in_parliament(indexed_settings, tmp_path):
    from hansard.week import monday

    con = duckdb.connect()
    attach_store(con, indexed_settings.parquet_dir)
    day = con.execute("SELECT max(date) FROM records WHERE source = 'debates'").fetchone()[0]
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    latest = client.get("/api/week").json()
    assert latest["start"] == monday(day).isoformat(), "the latest week by default"
    w = client.get("/api/week", params={"start": day.isoformat()}).json()
    assert w["start"] == monday(day).isoformat() and w["days"]
    for d in w["days"]:
        for c in d["chambers"]:
            for x in c["debates"]:
                assert x["said"] >= 3 and client.get("/api" + x["path"]).status_code == 200
    assert client.get("/this-week").status_code == 200
    assert client.get(f"/weeks/{w['start']}").status_code == 200
    assert client.get("/api/week", params={"start": "1999-01-04"}).status_code == 404
    assert client.get("/weeks/1999-01-04").status_code == 404
    assert client.get("/api/week", params={"start": "not-a-date"}).status_code == 404


def test_this_week_says_when_parliament_is_not_sitting(indexed_settings):
    from datetime import timedelta

    from hansard.week import Weeks

    weeks = Weeks(indexed_settings)
    last_debate, newest = weeks.con.execute("""
        SELECT max(date) FILTER (WHERE source IN ('debates', 'lordspages')), max(date) FROM records""").fetchone()
    assert weeks.pause(last_debate + timedelta(days=3)) is None, "a weekend is not a recess"
    later = weeks.pause(newest + timedelta(days=30))
    assert later["last_sat"] == last_debate.isoformat()
    assert later["recess"] is False, "nothing at all for a month: the record may be stale, not a recess"
    if newest > last_debate + timedelta(days=7):  # written answers after the last sitting: a recess
        assert weeks.pause(newest + timedelta(days=1))["recess"]
