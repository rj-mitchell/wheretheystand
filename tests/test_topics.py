"""Topic briefings (refreshed only when due and when the evidence has changed) and featured briefings."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from hansard.api.app import create_app
from hansard.config import REPO_ROOT
from hansard.embed.base import HashingEmbedder
from hansard.engine import Engine
from hansard.rag.pipeline import Pipeline
from hansard.rag.query_parse import ParsedQuery
from hansard.store.state import StateDB
from hansard.topics import is_due, load_topics, refresh_topics, versions

from .test_rag import TODAY, FakeClient

TOKEN = {"Authorization": "Bearer s3cret-token"}

TOPICS = """
[[topic]]
slug = "trial-by-jury"
title = "Trial by jury"
area = "Justice"
question = "trial by jury"
description = "Jury trials and the courts."
"""


class ReadingClient(FakeClient):
    """Like FakeClient, but reads the question successfully (the topic words become the search text)."""

    def _parse(self, **kw):
        text = kw["messages"][0]["content"].removeprefix("Question: ")
        return NS(stop_reason="end_turn", usage=None, parsed_output=ParsedQuery(
            search_text=text, date_from=None, date_to=None, houses=[], regions=[], parties=[], speakers=[],
            offices=[], genders=[], age_bands=[], tenure=[], majority_bands=[], group_by="role", portfolios=[],
            wants_votes=False, interpretation="All records."))


@pytest.fixture()
def site(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", "s3cret-token")
    topics = tmp_path / "topics.toml"
    topics.write_text(TOPICS)
    s = dataclasses.replace(indexed_settings, raw={**indexed_settings.raw,
                                                   "topics": {"file": str(topics), "max_per_run": 4}})
    pipe = Pipeline(s, Engine(s, embedder=HashingEmbedder(64), today=TODAY), client=ReadingClient(), today=TODAY)
    state = StateDB(tmp_path / "state.sqlite")
    return s, pipe, state, TestClient(create_app(s, pipe, state))


def test_the_real_topic_list_is_valid_and_neutral():
    topics = load_topics(REPO_ROOT / "config" / "topics.toml")
    assert len(topics) >= 10
    for t in topics:
        q = t.question.lower()
        assert "?" in t.question and not q.startswith(("why", "should", "isn't", "is it right")), t.slug
        assert "past twelve months" in q, t.slug


def test_topic_is_written_then_not_rewritten_until_due_and_changed(site):
    s, pipe, state, client = site
    res = refresh_topics(s, state, pipe, progress=lambda m: None)
    assert res[0]["outcome"].startswith("rewritten")
    first = versions(state, "trial-by-jury")[0]["briefing_id"]

    # straight away: not due
    assert refresh_topics(s, state, pipe, progress=lambda m: None)[0]["outcome"].startswith("written 0 days ago")

    # a week on, with new records: due, but the evidence is the same, so not rewritten (no cost)
    week_ago = (datetime.now(UTC) - timedelta(days=8)).isoformat(timespec="seconds")
    with state.connect() as con:
        con.execute("UPDATE topic_versions SET records_up_to = '2000-01-01', prepared_at = ?", (week_ago,))
    out = refresh_topics(s, state, pipe, progress=lambda m: None)[0]["outcome"]
    assert out.startswith("evidence unchanged") and len(versions(state, "trial-by-jury")) == 1
    topic = load_topics(s.config_path.parent / s.raw["topics"]["file"])[0]
    assert is_due(topic, state, "2026-10-01")[1] == "checked recently: evidence unchanged"
    assert is_due(topic, state, "2026-10-01", datetime.now(UTC) + timedelta(days=1))[0]  # checked again next day

    # forced: a second version; the first stays at its address
    assert refresh_topics(s, state, pipe, force=True, progress=lambda m: None)[0]["outcome"].startswith("rewritten")
    vs = versions(state, "trial-by-jury")
    assert len(vs) == 2 and vs[1]["briefing_id"] == first

    detail = client.get("/api/topics/trial-by-jury").json()
    assert detail["briefing"]["id"] == vs[0]["briefing_id"] and len(detail["versions"]) == 2
    page = client.get("/topics/trial-by-jury")
    assert page.status_code == 200 and "Trial by jury: where MPs and peers stand" in page.text
    assert 'name="robots"' not in page.text  # topic pages are for search engines to find
    assert client.get("/topics/no-such-topic").status_code == 404
    listing = client.get("/api/topics").json()
    assert listing["topics"][0]["current"]["briefing_id"] == vs[0]["briefing_id"]
    assert "/topics/trial-by-jury" in client.get("/sitemap-briefings.xml").text


def test_topics_stop_at_the_budget_and_without_a_key(site):
    s, pipe, state, _ = site
    broke = dataclasses.replace(s, raw={**s.raw, "usage": {**s.raw["usage"], "daily_spend_cap_usd": 0}})
    assert refresh_topics(broke, state, pipe, progress=lambda m: None)[0]["outcome"].startswith("skipped:")
    keyless = Pipeline(s, pipe.engine, client=None, today=TODAY)
    keyless.has_model = lambda: False
    assert refresh_topics(s, state, keyless, progress=lambda m: None)[0]["outcome"] == "skipped: no API key"
    assert versions(state, "trial-by-jury") == []


def test_featuring_a_briefing(site):
    s, pipe, state, client = site
    refresh_topics(s, state, pipe, progress=lambda m: None)
    bid = versions(state, "trial-by-jury")[0]["briefing_id"]
    assert 'name="robots" content="noindex"' in client.get(f"/b/{bid}").text  # not featured: hidden from search
    assert client.post("/api/admin/featured", json={"briefing": f"https://wherempsstand.com/b/{bid}"}).status_code == 403
    r = client.post("/api/admin/featured", json={"briefing": f"https://wherempsstand.com/b/{bid}",
                                                  "title": "Juries"}, headers=TOKEN)
    assert r.status_code == 200
    assert client.post("/api/admin/featured", json={"briefing": "bbbbbbbbbb"}, headers=TOKEN).status_code == 404
    featured = client.get("/api/topics").json()["featured"]
    assert featured[0]["id"] == bid and featured[0]["title"] == "Juries" and featured[0]["blurb"]
    assert 'name="robots"' not in client.get(f"/b/{bid}").text
    assert f"/b/{bid}" in client.get("/sitemap-briefings.xml").text
    assert client.delete(f"/api/admin/featured/{bid}", headers=TOKEN).status_code == 200
    assert client.get("/api/topics").json()["featured"] == []
