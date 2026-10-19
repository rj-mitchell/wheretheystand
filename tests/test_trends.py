"""Charts: whole-word counts by year, with every group's total contributions as context."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hansard.trends import clean_phrase

from .test_api import make_app


def test_phrases_are_checked():
    assert clean_phrase('  "Onshore   Wind" ') == "onshore wind"
    for bad in ("", "a", "x" * 70, "drop table; --"):
        with pytest.raises(ValueError):
            clean_phrase(bad)


def test_chart_api(indexed_settings, tmp_path):
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    body = client.get("/api/trends?q=jury&by=house").json()
    assert body["years"] and body["series"] and body["total"] >= 1
    for s in body["series"]:
        assert len(s["counts"]) == len(s["totals"]) == len(body["years"])
        assert all(c <= t for c, t in zip(s["counts"], s["totals"], strict=True))  # a part of its own total
    party = client.get("/api/trends?q=jury&by=party").json()
    assert sum(sum(s["counts"]) for s in party["series"]) == body["total"]  # every contribution counted once
    both = client.get("/api/trends?q=jury,wind&by=party").json()
    assert both["by"] == "all" and {s["phrase"] for s in both["series"]} <= {"jury", "wind"}
    # whole words only: "jur" is not counted inside "jury"
    assert client.get("/api/trends?q=jur").json()["total"] == 0
    assert client.get("/api/trends?q=x").status_code == 400
    assert client.get("/api/trends?q=jury&by=colour").status_code == 400
