"""Oral evidence transcripts: parsing turns, fetching, search and the transcript page (invented data)."""

from __future__ import annotations

import base64
from datetime import date

import httpx

from hansard.sources.evidence import EvidenceStore, parse_transcript, sync_transcripts

DOC = """<html><body><div>
<p><img src="data:image/png;base64,AAAA" /></p>
<p><span>Transport Committee</span></p><p>Oral evidence: Rail fares</p><p>Tuesday 5 March 2024</p>
<p>Watch the meeting</p><p>Members present: Test Chair (Chair); Test Member.</p>
<p>Witnesses</p><p>Ann Expert, Chief Executive, Passenger Watch.</p><p>27</p>
<p>Examination of witnesses</p>
<p>Ann Expert and A Minister.</p>
<p>Q1 <b>The Chair:</b> Welcome. What has happened to fares?</p>
<p>Ann Expert: Fares have risen faster than wages.</p>
<p>Season tickets in particular.</p>
<p>Q2 Lord Jay of Ewelme: And what should change?</p>
<p>A Minister: We are reviewing it. Note the time: 10:30 am.</p>
</div></body></html>"""


def test_turns_are_read_with_continuations():
    header, turns = parse_transcript(DOC)
    assert header[0] == "Transport Committee" and "27" not in header and "Watch the meeting" not in header
    assert [(t["question"], t["speaker"]) for t in turns] == [("Q1", "The Chair"), (None, "Ann Expert"),
                                                              ("Q2", "Lord Jay of Ewelme"), (None, "A Minister")]
    assert turns[1]["text"] == "Fares have risen faster than wages.\n\nSeason tickets in particular."
    assert turns[3]["text"].endswith("10:30 am."), "a colon later in the text does not start a new turn"


def fake(calls: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "/77/" in request.url.path:
            return httpx.Response(200, json={"data": base64.b64encode(DOC.encode()).decode()})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sync_search_and_page(indexed_settings, tmp_path):
    from fastapi.testclient import TestClient

    from hansard.sources.committees import build
    from hansard.sync import committees_path, evidence_path

    from .test_api import make_app
    from .test_committees import fake_api

    store = EvidenceStore(tmp_path / "evidence.sqlite")
    calls: list[str] = []
    stats = sync_transcripts(store, [(77, "2024-03-05"), (78, "2024-04-05"), (5, "2015-01-01")],
                             since=date(2022, 1, 1), client=fake(calls), sleep=lambda _s: None)
    assert stats == {"fetched": 1, "turns": 4, "missing": 1, "still_to_fetch": 0}
    assert len(calls) == 2, "sessions before the start date are not fetched"
    again = sync_transcripts(store, [(77, "2024-03-05"), (78, "2024-04-05")], client=fake(calls),
                             sleep=lambda _s: None)
    assert again["fetched"] == 0 and len(calls) == 2, "nothing is fetched twice"
    assert store.search("season tickets")[0] == 1

    cpath, epath = committees_path(indexed_settings), evidence_path(indexed_settings)
    try:
        build(cpath, fake_api(), lambda _s: None)
        sync_transcripts(EvidenceStore(epath), [(77, "2024-03-05")], client=fake([]), sleep=lambda _s: None)
        client = TestClient(make_app(indexed_settings, tmp_path, auth=False)[0])
        t = client.get("/api/evidence/77").json()
        assert t["inquiry"] == "Rail fares" and len(t["turns"]) == 4 and t["witnesses"]
        found = client.get("/api/evidence", params={"q": "wages"}).json()
        assert found["total"] == 1 and found["items"][0]["committees"] == "Transport Committee"
        assert client.get("/api/inquiries/900").json()["sessions"][0]["transcript"] is True
        assert client.get("/evidence/77").status_code == 200 and client.get("/evidence/78").status_code == 404
        assert "/evidence/77</loc>" in client.get("/sitemap-committees.xml").text
    finally:
        for p in (cpath, epath):
            for suffix in ("", "-wal", "-shm"):
                p.with_name(p.name + suffix).unlink(missing_ok=True)


def test_witnesses_are_told_apart_from_members():
    from hansard.sources.evidence import witness_for

    listed = [{"name": "Professor Keith Bell", "organisation": "University"}, {"name": "Lord Young of Old Windsor"},
              {"name": "Dr Emily Wallace"}]
    assert witness_for("Professor Bell", listed)["organisation"] == "University"
    assert witness_for("Keith Bell", listed)["name"] == "Professor Keith Bell"
    assert witness_for("Lord Young of Old Windsor", listed)["name"] == "Lord Young of Old Windsor"
    assert witness_for("Emily Wallace", listed)
    assert witness_for("The Chair", listed) is None and witness_for("Chair", listed) is None
    assert witness_for("Lord Jay of Ewelme", listed) is None and witness_for("John Bell", listed) is None


def test_briefings_can_include_committee_evidence(indexed_settings):
    from hansard.embed.base import HashingEmbedder
    from hansard.engine import Engine, Scope
    from hansard.rag.pipeline import AskRequest, Pipeline
    from hansard.rag.query_parse import parse_offline
    from hansard.sources.committees import build
    from hansard.sync import committees_path, evidence_path

    from .test_committees import fake_api

    long_doc = DOC.replace("Fares have risen faster than wages.",
                           "Fares have risen faster than wages. " + "Rail fares hurt commuters every year. " * 12)
    paths = [evidence_path(indexed_settings), committees_path(indexed_settings)]
    try:
        build(paths[1], fake_api(), lambda _s: None)
        sync_transcripts(EvidenceStore(paths[0]), [(77, "2024-03-05")], since=date(2022, 1, 1), client=fake([]),
                         sleep=lambda _s: None)
        p = Pipeline(indexed_settings, Engine(indexed_settings, embedder=HashingEmbedder(64)))
        plan = parse_offline("What has been said about rail fares?", indexed_settings.regions)
        EvidenceStore(paths[0]).save(77, *parse_transcript(long_doc))
        _, _, off = p.find(plan, AskRequest(question=plan.question, scope=Scope(evidence=False)))
        assert not [s for s in off if s.kind == "evidence"], "left out when the reader unticks it"
        _, _, on = p.find(plan, AskRequest(question=plan.question))  # searched by default
        ev = [s for s in on if s.kind == "evidence"]
        assert len(ev) == 1, "the witness's answer only: not the chair's question"
        assert ev[0].meta["witness"]["organisation"] == "Passenger Watch"
        assert "not a view of Parliament" in ev[0].blocks[0] and "Transport Committee" in ev[0].title
        assert ev[0].url == "/evidence/77#t1"
    finally:
        for path in paths:
            for suffix in ("", "-wal", "-shm"):
                path.with_name(path.name + suffix).unlink(missing_ok=True)


def test_evidence_is_also_found_by_meaning(indexed_settings):
    """Answers are embedded and found by meaning: an answer without every word of the question still turns up."""
    import shutil

    from hansard.embed.base import HashingEmbedder
    from hansard.engine import Engine
    from hansard.index.evidence import EvidenceVectors, embed_evidence
    from hansard.rag.pipeline import Pipeline, merge_ranked
    from hansard.rag.query_parse import parse_offline
    from hansard.sync import evidence_index_path, evidence_path

    emb = HashingEmbedder(256)
    db, ix = evidence_path(indexed_settings), evidence_index_path(indexed_settings)
    try:
        store = EvidenceStore(db)
        fares = "Fares keep rising for commuters and season tickets cost more each year. " * 6
        other = "The weather and the harvest were discussed at some length today in the valley. " * 6
        store.save(1, ["Transport Committee"], [{"speaker": "Ann Expert", "question": None, "text": fares}])
        store.save(2, ["Farming Committee"], [{"speaker": "Bob Farmer", "question": None, "text": other}])
        vectors = EvidenceVectors(ix, emb.dim)
        assert embed_evidence(store, vectors, emb) == {"sessions": 2, "answers": 2, "sessions_remaining": 0}
        assert store.unembedded() == [] and vectors.count() == 2
        hits = vectors.search(emb.embed_query("rail fares for commuters"))
        assert hits[0][:2] == (1, 0) and hits[0][2] > hits[1][2]
        # a transcript fetched again is embedded again, replacing its old vectors
        store.save(1, ["Transport Committee"], [{"speaker": "Ann Expert", "question": None, "text": fares}])
        assert store.unembedded() == [1]
        embed_evidence(store, vectors, emb)
        assert vectors.count() == 2
        # "train fares" needs both words for the keyword search, which this answer lacks; meaning finds it
        assert store.relevant(["train", "fares"]) == []
        indexed_settings.raw.setdefault("committees", {})["evidence_min_similarity"] = 0.1
        p = Pipeline(indexed_settings, Engine(indexed_settings, embedder=emb))
        found = p.evidence_by_meaning(store, parse_offline("What has been said about train fares?",
                                                           indexed_settings.regions))
        assert [(t["session_id"], t["speaker"]) for t in found][:1] == [(1, "Ann Expert")]
    finally:
        indexed_settings.raw.get("committees", {}).pop("evidence_min_similarity", None)
        shutil.rmtree(ix, ignore_errors=True)
        for suffix in ("", "-wal", "-shm"):
            db.with_name(db.name + suffix).unlink(missing_ok=True)

    a, b, c = ({"session_id": i, "ord": 0} for i in (1, 2, 3))
    assert merge_ranked([a, b], [c, b]) == [b, a, c], "found both ways comes first"
