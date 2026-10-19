"""The public search API: no AI, ranked results with speakers, clear errors."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .test_api import make_app


def test_search_api(indexed_settings, tmp_path):
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    r = client.get("/api/v1/search", params={"q": "the", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(body["results"]) <= 5 and "Open Parliament Licence" in body["licence"]
    for x in body["results"]:
        assert x["record_id"] and x["date"] and x["house"] in ("commons", "lords") and "name" in x["speaker"]
    lords = client.get("/api/v1/search", params={"q": "the", "house": "lords"}).json()["results"]
    assert all(x["house"] == "lords" for x in lords)
    assert client.get("/api/v1/search", params={"q": "x"}).status_code == 400
    assert client.get("/api/v1/search", params={"q": "the", "date_from": "last week"}).status_code == 400
    assert client.get("/developers").status_code in (200, 404), "the docs page is static (built separately)"
