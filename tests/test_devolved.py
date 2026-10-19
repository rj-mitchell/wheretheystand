"""Devolved parliaments: reading their records, revisions, search and who spoke (invented data)."""

from __future__ import annotations

import json
from datetime import date

import httpx

from hansard.devolved import DevolvedStore, Members, parse_file, sync_devolved

SP = b"""<publicwhip>
<major-heading id="uk.org.publicwhip/spor/2026-09-17.0.0" nospeaker="True">General Question Time</major-heading>
<speech id="uk.org.publicwhip/spor/2026-09-17.0.1" speakername="The Presiding Officer (Test Officer)"
        person_id="uk.org.publicwhip/person/90001"><p>Good afternoon.</p></speech>
<minor-heading id="uk.org.publicwhip/spor/2026-09-17.0.2">Ferry Services</minor-heading>
<speech id="uk.org.publicwhip/spor/2026-09-17.0.3" speakername="1. Test Member (Testshire) (SNP)"
        person_id="uk.org.publicwhip/person/90002" url="https://example.org/or#1">
  <p>To ask the Scottish Government about ferry services to the islands.</p></speech>
<speech id="uk.org.publicwhip/spor/2026-09-17.0.4" speakername=""><p>  </p></speech>
</publicwhip>"""
NI_A = b"""<publicwhip><major-heading id="ni.0">Assembly Business</major-heading>
<speech id="ni.1" person_id="uk.org.publicwhip/person/90003" speakername="Test MLA"><p>First version.</p></speech>
</publicwhip>"""
NI_B = NI_A.replace(b"First version.", b"Corrected version about hospitals.")
PEOPLE = {
    "organizations": [{"id": "snp", "name": "Scottish National Party"}],
    "posts": [{"id": "post1", "organization_id": "scottish-parliament", "area": {"name": "Testshire"}}],
    "memberships": [{"person_id": "uk.org.publicwhip/person/90002", "post_id": "post1", "on_behalf_of_id": "snp",
                     "start_date": "2021-05-08"}],
    "persons": [{"id": "uk.org.publicwhip/person/90002",
                 "other_names": [{"note": "Main", "given_name": "Test", "family_name": "Member"}]}],
}


def fake_twfy(files: dict[str, bytes]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/"):
            names = [n.rsplit("/", 1)[1] for n in files if n.rsplit("/", 1)[0] + "/" in path]
            return httpx.Response(200, text=" ".join(f'<a href="{n}">' for n in names))
        key = next(k for k in files if path.endswith(k))
        return httpx.Response(200, content=files[key])

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_keeps_headings_and_skips_empty_speeches():
    rows = parse_file(SP)
    assert len(rows) == 2
    assert rows[1]["major"] == "General Question Time" and rows[1]["minor"] == "Ferry Services"
    assert rows[1]["person_id"] == "uk.org.publicwhip/person/90002"


def test_sync_revisions_search_and_members(tmp_path):
    store = DevolvedStore(tmp_path / "devolved.sqlite")
    files = {"meeting-of-the-parliament/2026-09-17_20233.xml": SP, "ni/ni2026-09-15.xml": NI_A,
             "ni/ni2026-01-01.xml": NI_A}
    stats = sync_devolved(store, since=date(2026, 6, 1), client=fake_twfy(files), sleep=lambda _s: None)
    assert stats["files"] == 2, "files before the start date are left out"
    assert store.search("ferry")[0] == 1 and store.search("ferry", "senedd")[0] == 0
    files["ni/ni2026-09-15a.xml"] = NI_B  # a revised file for the same day replaces the first
    again = sync_devolved(store, since=date(2026, 6, 1), client=fake_twfy(files), sleep=lambda _s: None)
    assert again["files"] == 1
    assert store.search("first version")[0] == 0 and store.search("hospitals")[0] == 1
    assert [d["key"] for d in store.days("ni-assembly")] == ["ni2026-09-15"]

    people = tmp_path / "people.json"
    people.write_text(json.dumps(PEOPLE))
    m = Members(people)
    assert m.on("uk.org.publicwhip/person/90002", "scottish-parliament", "2026-09-17") == {
        "name": "Test Member", "seat": "Testshire", "party": "Scottish National Party"}
    assert m.memberships("uk.org.publicwhip/person/90002")[0]["member"] == "MSP"
    assert m.on("uk.org.publicwhip/person/90002", "scottish-parliament", "2020-01-01")["seat"] is None


def test_pages(indexed_settings, tmp_path):
    from fastapi.testclient import TestClient

    from hansard.sync import devolved_path

    from .test_api import make_app

    path = devolved_path(indexed_settings)
    try:
        store = DevolvedStore(path)
        sync_devolved(store, since=date(2026, 6, 1), sleep=lambda _s: None,
                      client=fake_twfy({"meeting-of-the-parliament/2026-09-17_20233.xml": SP}))
        app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
        client = TestClient(app)
        index = {b["id"]: b for b in client.get("/api/devolved").json()["legislatures"]}
        assert index["scottish-parliament"]["days"][0]["key"] == "2026-09-17_20233"
        day = client.get("/api/devolved/scottish-parliament/2026-09-17_20233").json()
        said = [i for i in day["items"] if i["kind"] == "said"]
        assert said[0]["speaker"]["role"] == "The Presiding Officer" and len(said) == 2
        assert client.get("/api/devolved/search", params={"q": "ferry"}).json()["total"] == 1
        assert client.get("/devolved/scottish-parliament/2026-09-17_20233").status_code == 200
        assert client.get("/devolved/scottish-parliament/1999-01-01_1").status_code == 404
        assert client.get("/api/devolved/nowhere").status_code == 404
    finally:
        for suffix in ("", "-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)


def test_yearly_counts_for_the_cross_border_chart(tmp_path):
    store = DevolvedStore(tmp_path / "devolved.sqlite")
    sync_devolved(store, since=date(2026, 6, 1), sleep=lambda _s: None,
                  client=fake_twfy({"meeting-of-the-parliament/2026-09-17_20233.xml": SP}))
    assert store.yearly("ferry services") == {"scottish-parliament": {2026: (1, 2)}}, \
        "members' contributions only, matching the words as a phrase"
    assert store.yearly("services ferry")["scottish-parliament"][2026][0] == 0


def test_briefings_can_include_the_devolved_parliaments(indexed_settings):
    from hansard.embed.base import HashingEmbedder
    from hansard.engine import Engine, Scope
    from hansard.rag.pipeline import AskRequest, Pipeline
    from hansard.rag.query_parse import parse_offline
    from hansard.sync import devolved_path

    long_sp = SP.replace(b"about ferry services to the islands.",
                         b"about ferry services to the islands. " + b"The ferry timetable matters to islanders. " * 12)
    path = devolved_path(indexed_settings)
    try:
        sync_devolved(DevolvedStore(path), since=date(2026, 6, 1), sleep=lambda _s: None,
                      client=fake_twfy({"meeting-of-the-parliament/2026-09-17_20233.xml": long_sp}))
        p = Pipeline(indexed_settings, Engine(indexed_settings, embedder=HashingEmbedder(64)))
        plan = parse_offline("What has been said about ferry services?", indexed_settings.regions)
        _, _, off = p.find(plan, AskRequest(question=plan.question, scope=Scope(devolved=False)))
        assert not [s for s in off if s.kind == "devolved"], "left out when the reader unticks it"
        _, _, on = p.find(plan, AskRequest(question=plan.question))  # searched by default
        dev = [s for s in on if s.kind == "devolved"]
        assert len(dev) == 1 and dev[0].meta["group"] == "Scottish Parliament"
        assert "not of the UK Parliament" in dev[0].blocks[0]
    finally:
        for suffix in ("", "-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)


def test_early_holyrood_files_are_read_from_the_older_folder_with_speakers_mapped(tmp_path):
    old = b"""<publicwhip><major-heading id="sp.0">Transport</major-heading>
<speech id="uk.org.publicwhip/spor/2005-03-02.1" speakerid="uk.org.publicwhip/member/80121"
        speakername="Test Member"><p>About ferry fares in 2005.</p></speech></publicwhip>"""
    people = tmp_path / "people.json"
    people.write_text(json.dumps({**PEOPLE, "memberships": [*PEOPLE["memberships"], {
        "id": "uk.org.publicwhip/member/80121", "person_id": "uk.org.publicwhip/person/90002",
        "post_id": "post1", "start_date": "2003-05-01", "end_date": "2007-04-02"}]}))
    store = DevolvedStore(tmp_path / "devolved.sqlite")
    files = {"sp/sp2005-03-02.xml": old,
             "sp/sp2010-03-01.xml": old,  # also in the newer folder: left to it
             "meeting-of-the-parliament/2026-09-17_20233.xml": SP}
    stats = sync_devolved(store, since=date(1998, 7, 1), client=fake_twfy(files), members=Members(people),
                          sleep=lambda _s: None)
    assert stats["files"] == 2
    day = store.day("scottish-parliament", "sp2005-03-02")
    assert day and day[0]["person_id"] == "uk.org.publicwhip/person/90002", "membership ID mapped to the person"
