"""Committees: building the local copy from the API, the pages, and the witness index (invented data)."""

from __future__ import annotations

import httpx

from hansard.sources.committees import Committees, build, refresh

INQ = {"id": 900, "title": "Rail fares", "type": {"name": "Inquiry"}, "openDate": "2024-01-10T00:00:00",
       "closeDate": None}
COMMITTEE = {"id": 10, "name": "Transport Committee", "house": "Commons", "category": {"name": "Select"},
             "startDate": "2010-01-01T00:00:00", "endDate": None, "parentCommittee": None,
             "scrutinisingDepartments": [{"name": "Department for Transport"}]}
DATA = {
    "/api/Committees": [COMMITTEE, {**COMMITTEE, "id": 11, "name": "Old Committee", "endDate": "2015-03-30T00:00:00"}],
    "/api/CommitteeBusiness": [INQ, {**INQ, "id": 901, "title": "Bus services", "openDate": "2012-02-01T00:00:00"}],
    "/api/Publications": [
        {"id": 1, "type": {"id": 1}, "description": "Rail fares: first report", "publicationStartDate": "2024-06-01T10:00:00",
         "committee": COMMITTEE, "hcNumber": {"number": "HC 123", "sessionId": 38}, "hlPaper": None,
         "businesses": [INQ], "documents": [{"documentId": 55}]},
        {"id": 2, "type": {"id": 2}, "description": "Government response", "publicationStartDate": "2024-07-31T10:00:00",
         "committee": COMMITTEE, "responseToPublicationId": 1, "businesses": [INQ], "documents": []},
    ],
    "/api/OralEvidence": [
        {"id": 77, "meetingDate": "2024-03-05T00:00:00", "committees": [COMMITTEE], "committeeBusinesses": [INQ],
         "witnesses": [{"name": "Ann  Expert", "organisations": [{"name": "Passenger Watch", "role": "Chief Executive"}]},
                       {"name": "A Minister", "organisations": [{"name": "Department for Transport", "role": "Minister"}],
                        "memberInfo": {"mnisId": 4514}}]},
        {"id": 78, "meetingDate": "2024-04-05T00:00:00", "committees": [COMMITTEE], "committeeBusinesses": [INQ],
         "witnesses": [{"name": "Bo Other", "organisations": [{"name": "passenger watch", "role": "Analyst"}]}]},
    ],
}


calls: list[int] = []


def fake_api(fail_owner: str | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/api/Committees/"):
            return httpx.Response(200, json={"purpose": "<p>We scrutinise <b>transport</b>.</p><p>More.</p>"})
        items = DATA.get(path, [])
        owner = request.url.params.get("CommitteeId")
        if owner and owner == fail_owner:
            return httpx.Response(500)
        if owner:  # whose inquiry each is: only this list says so
            items = [i for i in items if (i["id"] == 900) == (owner == "10")]
            calls.append(int(owner))
        start, end = request.url.params.get("StartDate"), request.url.params.get("EndDate")
        if start:  # the real service filters by date
            items = [i for i in items
                     if start <= (i.get("publicationStartDate") or i.get("meetingDate") or "")[:10] <= end]
        skip, take = int(request.url.params.get("Skip", 0)), int(request.url.params.get("Take", 500))
        return httpx.Response(200, json={"items": items[skip:skip + take], "totalResults": len(items)})

    return httpx.Client(transport=httpx.MockTransport(handler))


def no_wait(_s: float) -> None:
    return None


def test_build_and_read(tmp_path):
    path = tmp_path / "committees.sqlite"
    stats = build(path, fake_api(), no_wait)
    assert stats["sessions"] == 2 and stats["witnesses"] == 3
    c = Committees(path)
    index = c.index()
    assert [r["name"] for r in index] == ["Transport Committee"], "ended committees are not listed"
    assert index[0]["inquiries"] == 1 and index[0]["reports"] == 1
    page = c.committee(10)
    assert page["purpose"] == "We scrutinise transport." and page["departments"] == "Department for Transport"
    assert page["top_organisations"][0] == {"organisation": "Passenger Watch", "n": 2}, "counted whatever the case"
    inq = c.inquiry(900)
    response = next(p for p in inq["publications"] if p["type"] == "Government response")
    assert response["days_after_report"] == 60
    assert next(p for p in inq["publications"] if p["type"] == "Report")["hc"] == "HC 123", "the paper number as given"
    assert [len(s["witnesses"]) for s in inq["sessions"]] == [2, 1]
    found = c.witnesses("passenger watch")
    # both sessions count, but the recent list shows the body once for the inquiry, so one body does not fill it
    assert found["organisations"][0]["sessions"] == 2 and len(found["sessions"]) == 1
    assert c.for_member(4514)[0]["id"] == 77, "members who gave evidence are found by their UK Parliament ID"
    assert c.committee(999) is None and c.inquiry(999) is None


def test_refresh_waits_a_week(tmp_path):
    path = tmp_path / "committees.sqlite"
    assert refresh(path, fake_api(), no_wait)["updated"]
    assert refresh(path, fake_api(), no_wait)["updated"] is False


def test_pages(indexed_settings, tmp_path):
    from fastapi.testclient import TestClient

    from hansard.sync import committees_path

    from .test_api import make_app

    path = committees_path(indexed_settings)
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    try:
        assert client.get("/api/committees").status_code == 503, "a clear message before the first collection"
        build(path, fake_api(), no_wait)
        assert client.get("/api/committees").json()["committees"][0]["id"] == 10
        assert client.get("/api/inquiries/900").json()["title"] == "Rail fares"
        assert client.get("/committees/10").status_code == 200 and client.get("/committees/999").status_code == 404
        assert client.get("/inquiries/900").status_code == 200
        assert client.get("/api/witnesses", params={"q": "passenger"}).json()["organisations"]
    finally:
        path.unlink(missing_ok=True)


def test_reports_feed(indexed_settings, tmp_path):
    import xml.etree.ElementTree as ET

    from fastapi.testclient import TestClient

    from hansard.sync import committees_path

    from .test_api import make_app

    path = committees_path(indexed_settings)
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    try:
        assert client.get("/feeds/committee-reports.xml").status_code == 200, "an empty feed before collection"
        build(path, fake_api(), no_wait)
        items = ET.fromstring(client.get("/feeds/committee-reports.xml").content).findall("channel/item")
        assert [i.findtext("title") for i in items] == ["Government response: Government response",
                                                        "Report: Rail fares: first report"]
        assert items[0].findtext("link").endswith("/inquiries/900")
        assert client.get("/feeds/edms.xml").status_code == 200
    finally:
        path.unlink(missing_ok=True)


def test_sitemaps(indexed_settings, tmp_path):
    from fastapi.testclient import TestClient

    from hansard.sync import committees_path

    from .test_api import make_app

    path = committees_path(indexed_settings)
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    try:
        build(path, fake_api(), no_wait)
        body = client.get("/sitemap-committees.xml").text
        assert "/committees/10</loc>" in body and "/inquiries/900</loc>" in body and "/committees/11<" not in body
        for p in ("/sitemap-edms.xml", "/sitemap-devolved.xml"):
            assert client.get(p).status_code == 200
    finally:
        path.unlink(missing_ok=True)


def test_inquiries_get_their_committee_from_its_own_list(tmp_path):
    path = tmp_path / "committees.sqlite"
    calls.clear()
    build(path, fake_api(), no_wait)
    assert sorted(calls) == [10, 11]
    c = Committees(path)
    assert [x["name"] for x in c.inquiry(901)["committees"]] == ["Old Committee"]
    calls.clear()  # a rebuild asks again only about committees still sitting
    build(path, fake_api(), no_wait)
    assert calls == [10]
    assert [x["name"] for x in Committees(path).inquiry(901)["committees"]] == ["Old Committee"]
    assert [x["name"] for x in Committees(path).inquiry(900)["committees"]] == ["Transport Committee"]


def test_a_committee_the_service_fails_on_does_not_stop_the_build(tmp_path):
    path = tmp_path / "committees.sqlite"
    build(path, fake_api(fail_owner="11"), no_wait)
    assert Committees(path).inquiry(901)["committees"] == []
    calls.clear()
    build(path, fake_api(), no_wait)
    assert sorted(calls) == [10, 11], "tried again next time"
    assert [x["name"] for x in Committees(path).inquiry(901)["committees"]] == ["Old Committee"]


def test_one_organisation_however_its_name_is_written():
    from hansard.sources.committees import group_organisations, org_key

    same = ["University of Oxford", "the University of Oxford", "Oxford University", "Nuffield College, University of Oxford"]
    assert {org_key(n) for n in same} == {"university of oxford"}
    assert org_key("Oxford Brookes University") != org_key("University of Oxford")
    assert org_key("Foreign, Commonwealth & Development Office (FCDO)") == org_key("Foreign, Commonwealth and Development Office")
    assert org_key("Professor A, University of Oxford, Dr B, Leeds University, and C, RUSI, and D, KCL") == ""  # a panel
    rows = [{"organisation": n, "session_id": i, "date": f"2025-0{i + 1}-01"} for i, n in enumerate(same)]
    [only] = group_organisations(rows, 10)
    assert only["organisation"] in same and only["sessions"] == 4 and len(only["also_written"]) == 3
