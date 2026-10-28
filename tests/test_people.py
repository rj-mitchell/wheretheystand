"""MP and peer pages, Your MP by postcode, and the people API."""

from __future__ import annotations

from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from hansard.people import People, slug
from tests.conftest import BENN, CAMERON

from .test_api import make_app


@pytest.fixture(scope="module")
def people(indexed_settings):
    from hansard.reference.profiles import ProfileResolver

    return People(indexed_settings, ProfileResolver(indexed_settings.reference_dir), today=date(2026, 10, 10))


def test_page_has_roles_seats_and_majorities(people):
    page = people.page(BENN)
    assert page["name"] == "Hilary Benn" and page["number"] == 10669 and page["slug"] == "hilary-benn"
    assert page["status"].startswith("MP for Leeds South")
    leeds_central = next(s for s in page["seats"] if s["constituency"] == "Leeds Central" and s["results"])
    assert leeds_central["results"][0]["election"] == "2019 general election"
    assert any("Northern Ireland" in o["title"] for o in page["offices"])


def test_former_mp_now_a_peer(people):
    page = people.page(CAMERON)
    assert page["name"] == "Lord Cameron of Chipping Norton" and "David Cameron" in page["other_names"]
    assert any(m["house"] == "lords" for m in page["memberships"])


def test_contributions_and_voting_record(people):
    pid = people.resolve(10669)
    assert pid == BENN
    contributions = people.contributions(pid)
    assert all(c["date"] and c["twfy_url"] for c in contributions)
    voting = people.voting(BENN)
    assert voting is None or (voting["votes"] >= voting["rebellions"] >= 0 and "free votes" in voting["method"])


def test_what_a_member_speaks_about_most(people):
    out = people.topics(BENN)
    assert set(out) == {"topics", "since", "until"}
    for t in out["topics"]:
        assert t["days"] >= 2 and t["subject"].lower() not in {"engagements", "business of the house"}
    days = [t["days"] for t in out["topics"]]
    assert days == sorted(days, reverse=True)


def test_routine_subjects_are_left_out():
    import re

    from hansard.people import ROUTINE_SUBJECTS

    for s in ("Engagements", "Topical Questions", "Business of the House", "Point of Order", "Prime Minister",
              "Members: Correspondence"):
        assert re.search(ROUTINE_SUBJECTS, s.lower())
    for s in ("Onshore Wind", "Northern Ireland Troubles (Legacy and Reconciliation) Bill", "Business Rates"):
        assert not re.search(ROUTINE_SUBJECTS, s.lower())


def test_search_and_slug(people):
    found = people.search("benn")
    assert any(p["number"] == 10669 for p in found)
    assert slug("Lord Cameron of Chipping Norton") == "lord-cameron-of-chipping-norton"


def test_postcode_finds_the_mp(people):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["searchText"] == "LS11 5DR"
        return httpx.Response(200, json={"items": [{"value": {"id": 4138, "name": "Leeds South", "currentRepresentation":
                                                              {"member": {"value": {"id": 413, "nameDisplayAs": "Hilary Benn"}}}}}]})

    found = people.by_postcode("ls115dr", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert found == {"postcode": "LS11 5DR", "constituency": "Leeds South", "name": "Hilary Benn", "number": 10669,
                     "slug": "hilary-benn"}
    with pytest.raises(ValueError):
        people.by_postcode("not a postcode")


def test_people_api_and_page(indexed_settings, tmp_path):
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    body = client.get("/api/people/10669").json()
    assert body["name"] == "Hilary Benn" and "contributions" in body and "voting" in body
    # the page loads in parts, the slower ones after who the member is
    core = client.get("/api/people/10669?part=core").json()
    assert core["name"] == "Hilary Benn" and "contributions" not in core and "interests" not in core
    assert "voting" in client.get("/api/people/10669?part=activity").json()
    assert "donations" in client.get("/api/people/10669?part=interests").json()
    more = client.get("/api/people/10669/contributions?offset=0&limit=1").json()
    assert len(more["contributions"]) <= 1 and isinstance(more["more"], bool)
    assert client.get("/api/people/99999999").status_code == 404
    assert any(p["number"] == 10669 for p in client.get("/api/people?q=benn").json()["people"])
    assert client.get("/api/postcode/xyz").status_code == 400
    page = client.get("/people/10669/hilary-benn")
    assert page.status_code == 200 and "<title>Hilary Benn — Where MPs Stand</title>" in page.text
    assert 'content="noindex"' not in page.text  # member pages are for search engines
    assert "/people/10669/hilary-benn" in client.get("/sitemap-people.xml").text


def test_search_matches_every_word_and_former_members(people):
    # "hilary benn" and "benn hilary" both find him: each word may be anywhere in a name he has used or a seat held
    for q in ("hilary benn", "benn hilary", "benn leeds"):
        assert any(r["number"] == 10669 for r in people.search(q, current_only=False)), q
    assert not people.search("hilary nobodyatall", current_only=False)
    assert all("current" in r and "until" in r for r in people.search("benn", current_only=False))


def test_slight_interventions_are_not_listed_as_contributions():
    import duckdb

    from hansard.people import TRIVIAL_PATTERN, TRIVIAL_SQL

    con = duckdb.connect()

    def trivial(text: str) -> bool:
        return con.execute(f"SELECT {TRIVIAL_SQL.replace('text', '$1').replace('?', '$2')}",
                           [text, TRIVIAL_PATTERN]).fetchone()[0]

    assert trivial("Will the hon. Gentleman give way?")
    assert trivial("On a point of order, Madam Deputy Speaker.")
    assert trivial("I beg to move, That the Bill be now read a Second time.")
    assert not trivial("Does the Minister agree that bus services in rural areas are a lifeline for older people?")
    assert not trivial("I thank my right hon. Friend for giving way. Does he agree that the steel industry needs a "
                       "long-term plan for energy costs, as other countries have?")


def test_a_seat_does_not_claim_the_election_that_ended_it():
    from types import SimpleNamespace

    class Seats:
        def _seat_index(self):
            return {"reps": {"p": [(date(2005, 9, 29), date(2010, 5, 6), 1826, "Livingston")]},
                    "results": {1826: [
                        {"date": "2005-09-29", "general": False, "majority": 2680, "votes": 29477, "party": "Labour"},
                        {"date": "2010-05-06", "general": True, "majority": 10791, "votes": 48000, "party": "Labour"}]}}

    seats = People._seats(SimpleNamespace(resolver=Seats()), "p")
    assert [r["election"] for r in seats[0]["results"]] == ["2005 by-election"]
