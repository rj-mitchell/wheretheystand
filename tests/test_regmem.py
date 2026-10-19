"""The Register of Members' Financial Interests: parsing an edition, refreshing it, and the MP page."""

from __future__ import annotations

from pathlib import Path

import httpx

from hansard.sources.regmem import Register, parse, refresh

FIXTURE = Path(__file__).parent / "fixtures" / "regmem" / "regmem2026-09-21.xml"
BENN = "uk.org.publicwhip/person/10669"


def fake_twfy(calls: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/regmem/"):
            return httpx.Response(200, text='<a href="regmem2026-09-07.xml"></a> <a href="regmem2026-09-21.xml"></a>')
        return httpx.Response(200, content=FIXTURE.read_bytes())

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_an_edition():
    d = parse(FIXTURE.read_bytes())
    assert d["edition"] == "2026-09-21" and set(d["members"]) >= {BENN}
    cats = d["members"][BENN]["categories"]
    types = [c["type"] for c in cats]
    assert types == sorted(types, key=float), "categories in the register's order"
    items = [i for c in cats for i in c["items"]]
    assert all(i["summary"] for i in items)
    assert any(i["children"] for i in items), "payments are kept under the job they were for"
    names = {n for i in items for n, _ in i["details"]} | {n for i in items for c in i["children"] for n, _ in c["details"]}
    assert not [n for n in names if "address" in n.lower()], "addresses are left out"


def test_refresh_only_when_there_is_a_new_edition(tmp_path):
    path = tmp_path / "regmem.json"
    calls: list[str] = []
    first = refresh(path, fake_twfy(calls))
    assert first["updated"] and first["edition"] == "2026-09-21"
    calls.clear()
    again = refresh(path, fake_twfy(calls))
    assert not again["updated"] and len(calls) == 1, "only the listing is fetched when nothing is new"
    reg = Register(path)
    assert reg.for_person(BENN)["listed"] and reg.for_person("uk.org.publicwhip/person/1")["listed"] is False
    assert Register(tmp_path / "missing.json").for_person(BENN) is None


def test_the_mp_page_shows_the_register(indexed_settings, tmp_path):
    from fastapi.testclient import TestClient

    from hansard.sync import regmem_path

    from .test_api import make_app

    path = regmem_path(indexed_settings)
    try:
        refresh(path, fake_twfy([]))
        app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
        page = TestClient(app).get("/api/people/10669").json()
        assert page["interests"]["edition"] == "2026-09-21" and page["interests"]["categories"]
    finally:
        path.unlink(missing_ok=True)


def test_details_are_tidied():
    benn = parse(FIXTURE.read_bytes())["members"][BENN]["categories"]
    gift = benn[1]["items"][0]
    assert [n for n, _ in gift["details"]] == ["Payment Description", "Donor Name"], \
        "addresses and repeated values are left out"
    plain = parse(FIXTURE.read_bytes())["members"]["uk.org.publicwhip/person/10777"]["categories"][0]["items"][0]
    assert plain["summary"] == "Older-style entry written as plain text."


OLD = Path(__file__).parent / "fixtures" / "regmem" / "regmem2016-09-05.xml"
FORMER = "uk.org.publicwhip/person/99999"


def test_older_editions_are_read():
    cats = parse(OLD.read_bytes())["members"][FORMER]["categories"]
    visit = cats[0]["items"][0]
    assert visit["summary"] == "Name of donor: Example Foundation"
    assert ["Estimate of the probable value", "£1,500"] in visit["details"]
    assert not [n for n, _ in visit["details"] if "address" in n.lower()], "address lines are left out"
    assert cats[1]["items"][0] == {"summary": "Trustee of an example charity.", "details": [],
                                   "registered": "2 May 2015", "published": None, "children": []}


def test_former_members_get_the_last_edition_before_they_left(tmp_path):
    from hansard.sources.regmem import refresh_history

    files = {"regmem2016-09-05.xml": OLD.read_bytes(), "regmem2026-09-21.xml": FIXTURE.read_bytes()}
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[1]
        if not name:
            return httpx.Response(200, text=" ".join(f'<a href="{n}"></a>' for n in files))
        fetched.append(name)
        return httpx.Response(200, content=files[name])

    latest = tmp_path / "regmem.json"
    refresh(latest, httpx.Client(transport=httpx.MockTransport(handler)))
    hist = tmp_path / "regmem_history.json"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = refresh_history(hist, {FORMER: "2016-09-12"}, client=client, sleep=lambda _s: None)
    assert out["former_members"] == 1 and fetched[-1] == "regmem2016-09-05.xml"
    page = Register(latest).for_person(FORMER)
    assert page["former"] and page["edition"] == "2016-09-05" and page["categories"]
    fetched.clear()
    assert refresh_history(hist, {FORMER: "2016-09-12"}, client=client)["editions_fetched"] == 0
    assert fetched == [], "nothing is fetched again once known"


def test_changes_since_the_edition_a_year_earlier(tmp_path):
    import copy
    import json

    from hansard.sources.regmem import refresh_previous

    files = {"regmem2025-09-01.xml": b"", "regmem2025-09-29.xml": b"", "regmem2026-09-21.xml": FIXTURE.read_bytes()}
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[1]
        if not name:
            return httpx.Response(200, text=" ".join(f'<a href="{n}"></a>' for n in files))
        fetched.append(name)
        return httpx.Response(200, content=FIXTURE.read_bytes())

    latest = tmp_path / "regmem.json"
    refresh(latest, httpx.Client(transport=httpx.MockTransport(handler)))
    previous = tmp_path / "regmem_previous.json"
    fetched.clear()
    out = refresh_previous(previous, "2026-09-21", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert out == {"edition": "2025-09-01", "updated": True} and fetched == ["regmem2025-09-01.xml"], \
        "the newest edition at least a year older"
    assert not refresh_previous(previous, "2026-09-21",
                                client=httpx.Client(transport=httpx.MockTransport(handler)))["updated"]

    assert Register(latest).for_person(BENN)["changes"] == {"since": "2025-09-01", "added": [], "removed": []}
    data = json.loads(previous.read_text())
    cats = copy.deepcopy(data["members"][BENN]["categories"])
    gone = cats[0]["items"].pop(0)
    cats[0]["items"].append({**gone, "summary": "An old job"})
    data["members"][BENN]["categories"] = cats
    previous.write_text(json.dumps(data))
    ch = Register(latest).for_person(BENN)["changes"]
    assert ch["added"] == [{"category": cats[0]["name"], "summary": gone["summary"]}]
    assert ch["removed"] == [{"category": cats[0]["name"], "summary": "An old job"}]


OLDEST = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
<publicwhip>
<regmem personid="uk.org.publicwhip/person/10001" memberid="uk.org.publicwhip/member/1" membername="Test Member"
        date="2013-11-11">
  <category type="4" name="Sponsorships">
    (b)
    <item subcategory="b">Support in the capacity as an MP:</item>
    <item subcategory="b">Name of donor: Mr John Readman</item>
    <item subcategory="b">Address of donor: private</item>
    <item subcategory="b">Amount of donation: \xa33,200 towards auction items</item>
    <item subcategory="b"><em>(Registered 21 July 2013)</em></item>
    <item subcategory="b">Name of donor: Fibrelite Composites Limited</item>
    <item subcategory="b">Amount of donation: \xa32,250 towards tickets</item>
    <item subcategory="b"><em>(Registered 22 July 2013)</em></item>
  </category>
  <category type="8" name="Land and Property">
    <item>House in Kent.</item>
    <item>Flat in London. (Registered 3 May 2012)</item>
  </category>
</regmem>
</publicwhip>"""


def test_the_oldest_editions_lines_are_grouped_into_entries():
    cats = parse(OLDEST)["members"]["uk.org.publicwhip/person/10001"]["categories"]
    spons, land = cats
    assert [(i["summary"], i["registered"]) for i in spons["items"]] == [
        ("Mr John Readman", "21 July 2013"), ("Fibrelite Composites Limited", "22 July 2013")]
    assert ["Kind", "Support in the capacity as an MP"] in spons["items"][0]["details"]
    assert not any("Address" in k for k, _ in spons["items"][0]["details"]), "addresses are left out"
    assert [(i["summary"], i["registered"]) for i in land["items"]] == [("House in Kent.", None),
                                                                        ("Flat in London.", "3 May 2012")]


def test_the_archive_knows_what_was_registered_on_a_day(tmp_path):
    from datetime import date

    from hansard.sources.regmem_archive import RegisterArchive

    pid = "uk.org.publicwhip/person/10001"
    a = RegisterArchive(tmp_path / "archive.sqlite")

    def edition(day: str, *summaries: str) -> None:
        a.add_edition(day, {pid: {"categories": [{"type": "1", "name": "Directorships",
                                                  "items": [{"summary": s, "details": []} for s in summaries]}]}})

    edition("2010-01-10", "Director, Old Co")
    edition("2010-06-10", "Director, Old Co", "Director, New Co")
    edition("2011-01-10", "Director, New Co")  # Old Co gone by now
    on = lambda d: [i["summary"] for c in a.categories_on(pid, d) for i in c["items"]]  # noqa: E731
    assert on(date(2010, 3, 1)) == ["Director, Old Co"], "New Co was not yet registered"
    assert on(date(2010, 7, 1)) == ["Director, New Co", "Director, Old Co"]
    assert on(date(2011, 1, 20)) == ["Director, New Co"], "Old Co had been dropped"
    assert on(date(2012, 1, 1)) == [], "not listed for over a month: no longer taken to apply"
    assert a.categories_on("uk.org.publicwhip/person/99", date(2011, 3, 1)) is None, "nothing known: fall back"
    assert a.last_listed(pid)["edition"] == "2011-01-10"
    assert a.read() == {"2010-01-10", "2010-06-10", "2011-01-10"}
