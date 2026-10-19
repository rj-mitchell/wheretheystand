"""Bill pages: bills found in the record, their stages and votes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hansard.bills import bill_key, parse_heading, sentence, stage_of

from .test_api import make_app


def test_headings_and_names():
    assert parse_heading("Safety of Rwanda (Asylum and Immigration) Bill - Committee (1st Day)") == (
        "Safety of Rwanda (Asylum and Immigration) Bill", "Committee (1st Day)")
    assert parse_heading("SAFETY OF RWANDA (ASYLUM AND IMMIGRATION) BILL (PROGRAMME)")[1] == "(PROGRAMME)"
    assert parse_heading("Bill of Rights") is None and parse_heading("Prisons") is None
    assert bill_key("Finance (No. 2) Bill") == bill_key("Finance No. 2 Bill")
    assert bill_key("Victims and Prisoners Bill [HL]") == bill_key("Victims and Prisoners Bill")
    assert sentence("Consideration of Commons Amendments") == "Consideration of Commons amendments"


def sitting(**kw):
    base = {"source": "debates", "house": "commons", "minors": [], "raw_is_presented": False, "second": False,
            "third": False, "lords_amendments": False, "commons_amendments": False}
    return {**base, **kw}


def test_stages_from_the_wording_of_motions():
    assert stage_of(sitting(second=True), "") == ["Second reading"]
    assert stage_of(sitting(minors=["[1st Allocated Day]", "Clause 2"], third=True), "") == [
        "Committee of the whole House", "Third reading"]
    assert stage_of(sitting(lords_amendments=True), "") == ["Consideration of Lords amendments"]
    assert stage_of(sitting(), "(PROGRAMME)") == ["Programme motion"]
    assert stage_of(sitting(house="lords", source="lordspages"), "Report (2nd Day) (Continued)") == ["Report (2nd day)"]
    assert stage_of(sitting(source="standing"), "") == ["Public Bill Committee"]
    assert stage_of(sitting(), "") == ["Debate"]


def test_bills_api_and_pages(indexed_settings, tmp_path):
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    bills = client.get("/api/bills").json()["bills"]
    assert bills, "the fixtures include bill debates"
    b = bills[0]
    detail = client.get(f"/api/bills/{b['slug']}").json()
    assert detail["name"] == b["name"] and detail["sittings"]
    assert [s["date"] for s in detail["sittings"]] == sorted(s["date"] for s in detail["sittings"])
    page = client.get(f"/bills/{b['slug']}")
    assert page.status_code == 200 and b["name"] in page.text and 'name="robots"' not in page.text
    assert client.get("/api/bills/no-such-bill-2020").status_code == 404
    assert client.get("/bills/no-such-bill-2020").status_code == 404
    assert f"/bills/{b['slug']}" in client.get("/sitemap-bills.xml").text
