"""All-party parliamentary groups from mySociety's dataset (invented groups, real fixture members)."""

from __future__ import annotations

import json

import duckdb
import httpx

from hansard.sources.appgs import Groups, _tidy, build, refresh

from .conftest import BENN, CAMERON, GREENING

BENEFITS = json.dumps([
    {"Source": "Rail Co Ltd", "Value £s": "4,950", "Received": "01/10/2025", "Registered": "17/10/2025",
     "benefit_type": "Financial Benefits"},
    {"Source": "Policy Shop", "Description": "Policy Shop acts as secretariat", "Value £s In bands of £1,500":
     "12,001-13,500", "Received": "16/10/2025", "Registered": "16/02/2026", "benefit_type": "Benefits In Kind"}])


def write_dataset(folder):
    """register.parquet and members.parquet in the dataset's shape."""
    con = duckdb.connect()
    con.execute(f"""COPY (SELECT * FROM (VALUES
        ('rail', 'Rail All-Party Parliamentary Group', 'To promote railways.', 'Subject Group', 'uk', 'Financial',
         'https://example.org/rail.htm', 'Transport', 'A Person', '1 Secret Street', 'a@example.org', '', '',
         'Policy Shop acts as the group''s secretariat.https://policyshop.example/', '', 'register', ?, DATE '2026-01-20',
         true, '2025-26', NULL::DATE),
        ('ferries', 'Ferries Cross-Party Group', 'Ferries.', '', 'senedd-cy', '', '', '', '', '', '', '', '', '', '',
         'register', '', NULL::DATE, NULL::BOOLEAN, NULL, NULL::DATE))
        AS t(slug, title, purpose, category, parliament, registrable_benefits, source_url, categories,
             registered_contact_name, registered_contact_address, registered_contact_email, public_enquiry_point_name,
             public_enquiry_point_email, secretariat, website, website_status, detailed_benefits,
             date_of_most_recent_agm, published_income_expenditure_statement, reporting_year,
             next_reporting_deadline)) TO '{folder}/register.parquet' (FORMAT parquet)""", [BENEFITS])
    con.execute(f"""COPY (SELECT * FROM (VALUES
        ('Hilary Benn MP', 'Chair & Registered Contact', '{BENN}', NULL, 'Hilary Benn', 'uk', 'rail', true, 'mp',
         'parliament', DATE '2026-06-29', 'https://example.org/rail.htm', NULL::BOOLEAN),
        ('Justine Greening', NULL, NULL, NULL, NULL, 'uk', 'rail', false, 'mp', 'manual', DATE '2016-06-29',
         'https://rail.example/members', false),
        ('Acme Trains Ltd', NULL, NULL, NULL, NULL, 'uk', 'rail', false, 'other', 'manual', DATE '2016-06-29',
         'https://rail.example/members', false),
        ('Non-parliamentary members:', NULL, NULL, NULL, NULL, 'uk', 'rail', false, 'other', 'manual',
         DATE '2016-06-29', NULL, false),
        ('David Cameron', NULL, '{CAMERON}', NULL, NULL, 'uk', 'rail', false, 'mp', 'ai_search', DATE '2016-06-29',
         NULL, false))
        AS t(name, officer_role, twfy_id, mnis_id, canon_name, parliament, appg, is_officer, member_type, source,
             last_updated, url_source, removed)) TO '{folder}/members.parquet' (FORMAT parquet)""")


def test_tidy_secretariat_links():
    assert _tidy("X acts as the group's secretariat.https://x.example/andhttps://y.example/") == \
        "X acts as the group's secretariat. https://x.example/ and https://y.example/"


def test_build_links_members_and_leaves_out_ai_search(indexed_settings, tmp_path):
    from hansard.reference.profiles import attach_reference

    write_dataset(tmp_path)
    con = duckdb.connect()
    attach_reference(con, indexed_settings.reference_dir)
    path = tmp_path / "appgs.sqlite"
    stats = build(path, {"register": tmp_path / "register.parquet", "members": tmp_path / "members.parquet"}, con)
    assert stats["groups"] == 1, "the Welsh-language copy of the Senedd list is not counted twice"
    assert stats["left_out_ai_search"] == 1 and stats["members"] == 3 and stats["linked"] == 2
    g = Groups(path).group("rail")
    assert "Secret Street" not in json.dumps(g) and "a@example.org" not in json.dumps(g), "no contact details"
    by_name = {m["name"]: m for m in g["members"]}
    assert by_name["Hilary Benn MP"]["linked_by"] == "ParlParse ID" and by_name["Hilary Benn MP"]["is_officer"]
    assert by_name["Justine Greening"]["person_id"] == GREENING, "exact name of a member sitting then"
    assert by_name["Acme Trains Ltd"]["person_id"] is None
    assert [b["value_low"] for b in g["benefits"]] == [12001.0, 4950.0]
    assert Groups(path).for_person(CAMERON) == [], "AI-searched lists are left out"
    index = Groups(path).index()
    assert index["groups"][0]["benefits"] == 2 and index["funders"][0]["funder"] == "Policy Shop"
    assert Groups(path).index("railways")["groups"] and not Groups(path).index("buses")["groups"]
    assert len(Groups(path).funder("Rail Co Ltd")) == 1


def test_refresh_and_pages(indexed_settings, tmp_path):
    from fastapi.testclient import TestClient

    from hansard.reference.profiles import attach_reference
    from hansard.sync import appgs_path

    from .test_api import make_app

    write_dataset(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, content=(tmp_path / name).read_bytes())

    con = duckdb.connect()
    attach_reference(con, indexed_settings.reference_dir)
    path = appgs_path(indexed_settings)
    try:
        assert refresh(path, con, client=httpx.Client(transport=httpx.MockTransport(handler)))["updated"]
        assert not refresh(path, con, client=httpx.Client(transport=httpx.MockTransport(handler)))["updated"]
        client = TestClient(make_app(indexed_settings, tmp_path, auth=False)[0])
        g = client.get("/api/groups/uk/rail").json()
        assert g["members"][0]["member"]["name"] == "Hilary Benn"
        assert client.get("/api/groups").json()["groups"][0]["slug"] == "rail"
        assert client.get("/api/groups", params={"parliament": "nowhere"}).status_code == 404
        assert "/groups/uk/rail</loc>" in client.get("/sitemap-groups.xml").text
        assert client.get("/groups/uk/rail").status_code == 200 and client.get("/groups/uk/none").status_code == 404
        page = client.get(f"/api/people/{BENN.rsplit('/', 1)[1]}").json()
        assert page["groups"][0]["role"] == "Chair & Registered Contact"
    finally:
        path.unlink(missing_ok=True)
