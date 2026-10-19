"""Company roles from Companies House: who counts as the member, and what is kept."""

from __future__ import annotations

import httpx

from hansard.sources.companies import Directorships, mark_declared, members, name_matches, refresh

from .conftest import BENN, CAMERON, MEMBERS

PEOPLE = MEMBERS / "people.json"


def officer(oid, title, month, year):
    return {"title": title, "date_of_birth": {"month": month, "year": year},
            "links": {"self": f"/officers/{oid}/appointments"}}


def appointment(name, company, number, role="director", start="2017-01-05", end=None, status="active"):
    full = " ".join(filter(None, [name.get("forename"), name.get("other_forenames"), name["surname"].upper()]))
    a = {"name": f"{name['surname'].upper()}, {full.rsplit(' ', 1)[0]}", "name_elements": name, "officer_role": role, "appointed_on": start,
         "appointed_to": {"company_name": company, "company_number": number, "company_status": status}}
    if end:
        a["resigned_on"] = end
    return a


CAMERON_NAME = {"forename": "David", "other_forenames": "William Donald", "surname": "CAMERON"}
OTHER_DAVID = {"forename": "David", "surname": "CAMERON-SMITH"}


def handler(seen):
    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        path = request.url.path
        if path == "/search/officers":
            q = request.url.params["q"]
            items = [officer("good1", "David William Donald CAMERON", 10, 1966),  # him
                     officer("good2", "David CAMERON", 10, 1966),                  # him again, another record
                     officer("wrongdob", "David CAMERON", 3, 1966),                # born another month
                     officer("wrongname", "David CAMERON-SMITH", 10, 1966),        # another surname
                     officer("excluded", "David CAMERON", 10, 1966)] if "cameron" in q.lower() else []
            return httpx.Response(200, json={"items": items, "total_results": len(items)})
        apps = {
            "good1": [appointment(CAMERON_NAME, "Wiltshire Widgets Limited", "01234567"),
                      appointment(CAMERON_NAME, "Gone Ltd", "05555555", status="dissolved"),  # no resignation
                      appointment(CAMERON_NAME, "Old Co Ltd", "07654321", start="2003-01-01", end="2009-02-01", status="dissolved")],
            "good2": [appointment({"forename": "David", "surname": "Cameron"}, "Old Co Ltd", "07654321", start="2003-01-01",
                                  end="2009-02-01", status="dissolved"),
                      appointment({"forename": "David", "surname": "Cameron"}, "Witney Trust", "SC000001",
                                  role="secretary", start="1995-01-01", end="1999-01-01")],
            "wrongname": [appointment(OTHER_DAVID, "Elsewhere Limited", "00000001")],
            "excluded": [appointment(CAMERON_NAME, "Not His Limited", "00000002")],
        }
        oid = path.split("/")[2]
        items = apps.get(oid, [])
        return httpx.Response(200, json={"items": items, "total_results": len(items),
                                         "date_of_birth": {"month": 10, "year": 1966}})
    return respond


def test_members_need_a_birth_month_and_name_parts(reference_dir):
    found = {m["person_id"]: m for m in members(reference_dir, PEOPLE)}
    cam = found[CAMERON]
    assert cam["born"] == (1966, 10) and "david" in cam["firsts"] and "cameron" in cam["surnames"]
    assert found[BENN]["surnames"] == ["benn"]


def test_name_check():
    m = {"firsts": ["ed"], "surnames": ["davey"]}
    assert name_matches({"forename": "Edward", "other_forenames": "Jonathan", "surname": "DAVEY"}, m)
    assert not name_matches({"forename": "Mark", "surname": "DAVEY"}, m)
    assert name_matches({"forename": "Alexander", "other_forenames": "Boris De Pfeffel", "surname": "JOHNSON"},
                        {"firsts": ["boris"], "surnames": ["johnson"]})
    assert name_matches({"forename": "Rebecca", "surname": "LONG-BAILEY"}, {"firsts": ["rebecca"],
                                                                          "surnames": ["long bailey"]})


def test_refresh_matches_only_name_and_birth_month(tmp_path, reference_dir):
    seen: list[str] = []
    exclude = tmp_path / "exclude.csv"
    exclude.write_text(f"# comment\nperson_id,officer_id,reason\n{CAMERON},excluded,another David Cameron\n")
    path = tmp_path / "directorships.json"
    out = refresh(path, reference_dir, PEOPLE, per_run=500, key="k", exclude_csv=exclude,
                  client=httpx.Client(transport=httpx.MockTransport(handler(seen))),
                  sleep=lambda _s: None)
    assert out["checked"] == out["members_looked_up"] and out["members_with_directorships"] == 1
    assert not any("/officers/wrongdob/" in u for u in seen), "born another month: never fetched"
    assert not any("/officers/excluded/" in u for u in seen)
    d = Directorships(path).for_person(CAMERON)
    assert [o["url"].split("/")[-2] for o in d["officers"]] == ["good1", "good2"]
    assert [(a["company"], a["until"], a["current"]) for a in d["appointments"]] == [
        ("Wiltshire Widgets Limited", None, True), ("Gone Ltd", None, False), ("Old Co Ltd", "2009-02-01", False),
        ("Witney Trust", "1999-01-01", False)], "a dissolved company's directors are no longer directors"
    widgets, _gone, old, trust = d["appointments"]
    assert widgets["role"] == "Director" and widgets["while_member"] and widgets["company_url"].endswith("/01234567")
    assert old["status"] == "dissolved" and old["while_member"]
    assert trust["role"] == "Secretary" and not trust["while_member"], "before he was elected in 2001"
    assert "address" not in str(d) and "date_of_birth" not in str(d)
    assert d["officers"][0]["name"] == "CAMERON, David William Donald"
    assert Directorships(path).for_person(BENN)["appointments"] == []  # checked, nothing found

    mark_declared(d, "Director of Wiltshire Widgets Ltd (unpaid)")
    assert d["appointments"][0]["declared"] and not d["appointments"][1]["declared"]

    idx = Directorships(path).index()
    assert [c["company"] for c in idx["companies"]] == ["Wiltshire Widgets Limited"]
    assert idx["companies"][0]["members"][0]["name"] == "Lord Cameron of Chipping Norton"
    assert Directorships(path).index("witney")["companies"] == []  # current roles only
    assert Directorships(path).index("chipping")["total_companies"] == 1

    # an officer record excluded later is dropped at the next run, before any recheck
    exclude.write_text(f"person_id,officer_id,reason\n{CAMERON},good1,x\n{CAMERON},good2,x\n")
    refresh(path, reference_dir, PEOPLE, per_run=0, key="k", exclude_csv=exclude,
            client=httpx.Client(transport=httpx.MockTransport(handler(seen))), sleep=lambda _s: None)
    assert Directorships(path).for_person(CAMERON)["appointments"] == []


def test_no_key_does_nothing(tmp_path, reference_dir, monkeypatch):
    monkeypatch.delenv("HANSARD_COMPANIES_HOUSE_KEY", raising=False)
    assert "skipped" in refresh(tmp_path / "d.json", reference_dir, PEOPLE)
    assert not (tmp_path / "d.json").exists()


def test_member_page_and_index(indexed_settings, tmp_path):
    from fastapi.testclient import TestClient

    from hansard.sync import directorships_path

    from .test_api import make_app

    path = directorships_path(indexed_settings)
    try:
        refresh(path, indexed_settings.reference_dir, PEOPLE, per_run=500, key="k", exclude_csv=tmp_path / "none.csv",
                client=httpx.Client(transport=httpx.MockTransport(handler([]))), sleep=lambda _s: None)
        client = TestClient(make_app(indexed_settings, tmp_path, auth=False)[0])
        page = client.get(f"/api/people/{CAMERON.rsplit('/', 1)[1]}").json()
        assert "Wiltshire Widgets Limited" in {a["company"] for a in page["directorships"]["appointments"]}
        assert client.get("/api/directorships", params={"q": "widgets"}).json()["total_companies"] == 1
    finally:
        path.unlink(missing_ok=True)


def test_key_check_says_why_without_showing_the_key():
    from hansard.sources.companies import check_key

    good = "0123abcd-0123-4567-89ab-0123456789ab"

    def client(live, test):
        def respond(request: httpx.Request) -> httpx.Response:
            code = test if "sandbox" in request.url.host else live
            return httpx.Response(code, json={"error": "Invalid Authorization"} if code != 200 else {"items": []})
        return httpx.Client(transport=httpx.MockTransport(respond))

    ok = check_key(good, client(200, 401))
    assert ok["ok"] and ok["verdict"] == "The key works." and good not in str(ok)
    test_app = check_key(good, client(401, 200))
    assert not test_app["ok"] and "Test application" in test_app["verdict"]
    pasted = check_key(f'"{good}"', client(401, 401))
    assert pasted["spaces_or_quotes"] and "cut short" in pasted["verdict"]
    refused = check_key(good, client(401, 401))
    assert "Restricted IPs" in refused["verdict"] and refused["live_says"].startswith("{")
    assert "not set" in check_key("", client(200, 200))["problem"]
