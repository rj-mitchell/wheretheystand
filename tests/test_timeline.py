"""Timelines: a member's or a party's contributions and votes on a topic, oldest first (free: no AI)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hansard.embed.base import HashingEmbedder
from hansard.engine import Engine
from hansard.people import person_number

from .test_api import make_app


def test_member_and_party_timelines(indexed_settings, tmp_path):
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    hit = Engine(indexed_settings, embedder=HashingEmbedder(64)).search("trial by jury", limit=5).records[0]
    number, party = person_number(hit["speaker"]["person_id"]), hit["speaker"]["party_id"]

    body = client.get(f"/api/timeline?person={number}&q=trial+by+jury").json()
    dates = [i["date"] for i in body["items"]]
    assert body["said"] >= 1 and dates == sorted(dates)
    said = next(i for i in body["items"] if i["kind"] == "said")
    assert said["excerpt"] and said["url"] and said["heading"]
    if body["votes"]:  # a member's votes on the topic, summed up
        s = body["vote_summary"]
        assert s["aye"] + s["no"] <= body["votes"] and s["against_party"] <= body["votes"]
    else:
        assert body["vote_summary"] is None

    body = client.get(f"/api/timeline?party={party}&q=trial+by+jury").json()
    assert body["said"] >= 1 and all(i.get("speaker") for i in body["items"] if i["kind"] == "said")

    assert client.get("/api/timeline?q=trial+by+jury").status_code == 400            # nobody chosen
    assert client.get(f"/api/timeline?person={number}&q=ab").status_code == 400     # topic too short
    assert client.get("/api/timeline?person=99999999&q=jury").status_code == 404
    assert client.get("/api/timeline?party=no-such-party&q=jury").status_code == 404
