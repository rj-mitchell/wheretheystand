"""Early day motions: reading the API tolerantly, the local copy, search and the pages."""

from __future__ import annotations

from datetime import date

import httpx

from hansard.sources.edms import EdmStore, normalise_edm, normalise_sponsors, sync_edms


def NO_WAIT(_seconds: float) -> None:
    return None


def motion(i: int, title: str, tabled: str, mnis: int = 4514, **extra) -> dict:
    return {"Id": i, "UIN": str(i), "Title": title, "MotionText": f"That this House notes {title.lower()}.",
            "DateTabled": f"{tabled}T00:00:00", "Status": "Published", "StatusDate": f"{tabled}T00:00:00",
            "SponsorsCount": 2, "MemberId": mnis,
            "PrimarySponsor": {"MnisId": mnis, "Name": "Ann Example", "Party": "Labour"}, **extra}


DETAIL_SPONSORS = [
    {"MemberId": 4514, "Member": {"MnisId": 4514, "Name": "Ann Example", "Party": "Labour"}, "SponsoringOrder": 1,
     "CreatedWhen": "2024-01-10T09:00:00", "IsWithdrawn": False},
    {"MemberId": 172, "Member": {"MnisId": 172, "Name": "Bob Sample", "Party": "Conservative"},
     "SponsoringOrder": None, "CreatedWhen": "2024-01-11T09:00:00", "IsWithdrawn": True,
     "WithdrawnDate": "2024-01-12T09:00:00"},
]


def fake_api(motions: list[dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        if request.url.path == "/EarlyDayMotions/list":
            found = [m for m in motions
                     if (not p.get("parameters.tabledStartDate")
                         or p["parameters.tabledStartDate"] <= m["DateTabled"][:10] <= p["parameters.tabledEndDate"])
                     and m["StatusDate"][:10] >= p.get("parameters.currentStatusDateStart", "")]
            skip, take = int(p["parameters.skip"]), int(p["parameters.take"])
            return httpx.Response(200, json={"PagingInfo": {"Total": len(found)}, "Response": found[skip:skip + take]})
        eid = int(request.url.path.rsplit("/", 1)[1])
        m = next((m for m in motions if m["Id"] == eid), None)
        return httpx.Response(200, json={"Response": {**m, "Sponsors": DETAIL_SPONSORS}}) if m else httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.org")


def test_reading_the_api_tolerantly():
    e = normalise_edm({"id": 7, "title": "  Rail   fares ", "dateTabled": "2024-01-10T00:00:00",
                       "primarySponsor": {"mnisId": "9", "name": "X"}})
    assert e and e["id"] == 7 and e["title"] == "Rail fares" and e["date_tabled"] == "2024-01-10"
    assert e["primary_mnis"] == 9, "field names are matched whatever their case"
    assert normalise_edm({"Title": "no id"}) is None
    s = normalise_sponsors({"Sponsors": DETAIL_SPONSORS})
    assert [x["mnis"] for x in s] == [4514, 172] and s[1]["withdrawn"] == "2024-01-12" and s[0]["withdrawn"] is None
    assert normalise_sponsors({"Title": "list record without sponsors"}) is None


def test_sync_search_and_member_summary(tmp_path):
    motions = [motion(1, "Rail fares in the North", "2024-01-10"), motion(2, "Bus services", "2023-11-02", mnis=172),
               motion(3, "Amendment on rail", "2024-01-15", AmendmentToMotionId=1)]
    store = EdmStore(tmp_path / "edms.sqlite")
    stats = sync_edms(store, today=date(2024, 2, 1), months_per_run=6, sleep=NO_WAIT, client=fake_api(motions))
    assert stats["total"] == 3 and stats["signatures"] == 6 and stats["unrecognised"] == 0
    assert store.search("rail")[0] == 2 and store.search("buses services")[0] == 0
    total, rows = store.search("bus")
    assert total == 1 and rows[0]["title"] == "Bus services"
    one = store.get(1)
    assert one and [s["mnis"] for s in one["sponsors"]] == [4514, 172] and one["amendments"][0]["id"] == 3
    mine = store.member_summary(172)
    assert mine["tabled"] == 1 and mine["signed"] == 0, "a withdrawn signature does not count"
    # a second run: older months already done are not fetched again, and nothing is duplicated
    again = sync_edms(store, today=date(2024, 2, 1), months_per_run=6, sleep=NO_WAIT, client=fake_api(motions))
    assert again["total"] == 3 and store.search("rail")[0] == 2


def test_pages_and_api(indexed_settings, tmp_path):
    from fastapi.testclient import TestClient

    from hansard.sync import edms_path

    from .test_api import make_app

    path = edms_path(indexed_settings)
    try:
        app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
        client = TestClient(app)
        assert client.get("/api/edms").json()["available"] is False, "before the first sync"
        store = EdmStore(path)
        sync_edms(store, today=date(2024, 2, 1), months_per_run=3, sleep=NO_WAIT,
                  client=fake_api([motion(1, "Hospice funding", "2024-01-10")]))
        listing = client.get("/api/edms", params={"q": "hospice"}).json()
        assert listing["total"] == 1 and listing["items"][0]["sponsor"]["name"] == "Ann Example"
        one = client.get("/api/edms/1").json()
        assert one["signatures_known"] and len(one["sponsors"]) == 2
        assert one["party_counts"] == [["Labour", 1]], "withdrawn signatures are not counted"
        assert client.get("/edms/1").status_code == 200 and client.get("/edms/2").status_code == 404
        assert client.get("/api/edms/2").status_code == 404
        assert client.get("/api/edms", params={"person": 999999999}).status_code == 404
    finally:
        for suffix in ("", "-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)


def test_motions_join_the_evidence_for_a_briefing(indexed_settings):
    from hansard.embed.base import HashingEmbedder
    from hansard.engine import Engine, Scope
    from hansard.rag.pipeline import AskRequest, Pipeline
    from hansard.rag.query_parse import parse_offline
    from hansard.sync import edms_path

    path = edms_path(indexed_settings)
    try:
        sync_edms(EdmStore(path), today=date(2024, 2, 1), months_per_run=3, sleep=NO_WAIT,
                  client=fake_api([motion(1, "Hospice funding crisis", "2024-01-10"),
                                   motion(2, "Bus services", "2024-01-12")]))
        p = Pipeline(indexed_settings, Engine(indexed_settings, embedder=HashingEmbedder(64)))
        plan = parse_offline("What have MPs said about hospice funding?", indexed_settings.regions)
        _, _, sources = p.find(plan, AskRequest(question=plan.question))
        edms = [s for s in sources if s.kind == "edm"]
        assert [s.meta["edm_id"] for s in edms] == [1], "only the motion on the subject"
        text = "\n".join(edms[0].blocks)
        assert "Hospice funding crisis" in text and "Signed by 1 member" in text
        assert "not signing" not in text and "absence of a signature says nothing" in text
        _, _, none = p.find(plan, AskRequest(question=plan.question, scope=Scope(edms=False)))
        assert not [s for s in none if s.kind == "edm"], "the reader can leave motions out"
    finally:
        for suffix in ("", "-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)


def test_slowing_down_when_parliament_asks(tmp_path):
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/EarlyDayMotions/list":
            return httpx.Response(200, json={"PagingInfo": {"Total": 1},
                                             "Response": [motion(1, "Rail fares", "2024-01-10")]})
        return httpx.Response(429, headers={"Retry-After": "7"})

    store = EdmStore(tmp_path / "edms.sqlite")
    stats = sync_edms(store, today=date(2024, 2, 1), months_per_run=1, sleep=waits.append,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert stats["rate_limited"] and stats["total"] == 1, "the motions are kept; signatures wait for next time"
    assert 7 in waits, "the wait Parliament asks for is respected"
    assert stats["signatures_still_to_fetch"] == 1


def test_signature_count_follows_the_names(tmp_path):
    store = EdmStore(tmp_path / "edms.sqlite")
    sync_edms(store, today=date(2024, 2, 1), months_per_run=3, sleep=NO_WAIT,
              client=fake_api([motion(1, "Rail fares", "2024-01-10", SponsorsCount=0)]))
    assert store.get(1)["sponsors_count"] == 1, "one current signature; the withdrawn one is not counted"


def test_older_motions_count_signatures_from_the_names(tmp_path):
    old = motion(15377, "British Dependent Territories", "1998-01-29", SponsorsCount=0)
    edm = normalise_edm(old)
    assert edm["sponsors_count"] is None, "Parliament gives 0 for older motions: unknown, not none"
    store = EdmStore(tmp_path / "edms.sqlite")
    store.upsert(edm, old)
    store.set_sponsors(15377, normalise_sponsors({"Sponsors": DETAIL_SPONSORS}))
    store.upsert(normalise_edm(old), old)  # a later refresh of the motion keeps the count from the names
    assert store.get(15377)["sponsors_count"] == 1
