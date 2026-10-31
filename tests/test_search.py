"""Deterministic retrieval over the fixture index."""

from datetime import date

from hansard.embed.base import HashingEmbedder
from hansard.engine import Engine, Filters, Scope, build_where

TODAY = date(2026, 10, 1)


def engine(settings):
    return Engine(settings, embedder=HashingEmbedder(64), today=TODAY)


def test_keyword_and_semantic_search_find_the_topic(indexed_settings):
    res = engine(indexed_settings).search("trial by jury courts", limit=5)
    assert res.records, "expected results"
    assert res.semantic_available
    top = res.records[0]
    assert "jury" in (top["heading"] + top["text"]).lower()
    assert top["speaker"]["name"] and top["ranking"]["relevance"] > 0
    assert top["official_url"] and top["twfy_url"].startswith("https://www.theyworkforyou.com/")


def test_results_are_deterministic(indexed_settings):
    e = engine(indexed_settings)
    a = [r["record_id"] for r in e.search("security Northern Ireland", limit=8).records]
    b = [r["record_id"] for r in e.search("security Northern Ireland", limit=8).records]
    assert a == b and a


def test_house_and_scope_filters(indexed_settings):
    e = engine(indexed_settings)
    lords = e.search("government", Filters(houses=["lords"]), limit=20).records
    assert lords and {r["house"] for r in lords} == {"lords"}
    no_written = e.search("Home Office", scope=Scope(written=False), limit=30).records
    assert all(r["source"] not in {"wrans", "lordswrans", "wms", "lordswms"} for r in no_written)


def test_date_and_region_filters(indexed_settings):
    e = engine(indexed_settings)
    recs = e.search("security", Filters(date_from=date(2012, 1, 1), date_to=date(2012, 12, 31)), limit=20).records
    assert recs and all(r["date"].startswith("2012") for r in recs)
    regional = e.search("question", Filters(regions=["South East"]), limit=20).records
    assert all((r["speaker"] or {}).get("region") == "South East" for r in regional)


def test_office_filter_and_role_boost(indexed_settings):
    e = engine(indexed_settings)
    gov = e.search("jury trial courts", Filters(offices=["gov"]), limit=10).records
    assert gov and all(any(o["kind"] == "gov" for o in r["speaker"]["offices"]) for r in gov)
    flat = e.search("jury trial courts", portfolios=["justice"], role_weight=1.0, recency=0.0, limit=10).records
    boosted = e.search("jury trial courts", portfolios=["justice"], role_weight=2.0, recency=0.0, limit=10).records
    assert {r["record_id"] for r in flat} <= {r["record_id"] for r in e.search(
        "jury trial courts", portfolios=["justice"], role_weight=2.0, recency=0.0, limit=50).records}
    assert all(r["ranking"]["authority"] == 1.0 for r in flat)
    assert any(r["ranking"]["authority"] > 1.0 for r in boosted)


def test_where_clause_escapes_values():
    w = build_where(Filters(regions=["O'Brien land"]), Scope())
    assert "'O''Brien land'" in w


def test_the_frontbench_for_the_subject_is_searched_on_its_own(indexed_settings):
    """Speeches by the ministers and shadow ministers for the subject are found even when the busiest debates fill
    the main shortlist (here cut to a handful of passages)."""
    import dataclasses

    raw = {**indexed_settings.raw, "ranking": {**indexed_settings.ranking, "candidates_bm25": 3,
                                               "candidates_vector": 3, "rerank": False}}
    narrow = engine(dataclasses.replace(indexed_settings, raw=raw))

    def frontbench(res):
        return [r for r in res.records if any(o["kind"] in ("gov", "shadow") and "northern_ireland" in
                                              (o.get("portfolios") or []) for o in r["speaker"]["offices"])]

    without = narrow.search("security", limit=10, recency=0.0)
    found = narrow.search("security", portfolios=["northern_ireland"], limit=10, recency=0.0)
    assert len(found.records) > len(without.records)
    assert len(frontbench(found)) > len(frontbench(without))
    # their closeness in meaning is read from their stored vectors, though the search by meaning did not reach them
    assert all(r["ranking"]["semantic"] is not None for r in frontbench(found))
