"""Composite soft ranking: boosts re-order results but never drop strong matches."""

from datetime import date

import pytest

from hansard.rank.composite import Candidate, RankParams, rank, recency_factor

TODAY = date(2026, 10, 1)


def params(**kw):
    return RankParams(today=TODAY, **kw)


def ids(scored):
    return [s.candidate.record_id for s in scored]


def test_no_decay_means_pure_relevance_order():
    cands = [Candidate("old-strong", date(2011, 1, 1), bm25=9.0), Candidate("new-weak", date(2026, 9, 1), bm25=3.0),
             Candidate("mid", date(2018, 1, 1), bm25=6.0)]
    assert ids(rank(cands, set(), params(recency=0.0, role_weight=1.0))) == ["old-strong", "mid", "new-weak"]


def test_decay_prefers_recent_when_relevance_is_similar():
    cands = [Candidate("old", date(2012, 1, 1), bm25=8.0), Candidate("new", date(2026, 6, 1), bm25=7.5)]
    assert ids(rank(cands, set(), params(recency=0.5, role_weight=1.0))) == ["new", "old"]


def test_aggressive_decay_never_drops_a_much_stronger_old_match():
    old = Candidate("old-strong", date(2010, 6, 1), bm25=10.0, cosine=0.8)
    new = [Candidate(f"new-weak-{i}", date(2026, 9, 1), bm25=2.0, cosine=0.2) for i in range(5)]
    scored = rank([old, *new], set(), params(recency=1.0, role_weight=1.0))
    assert len(scored) == 6  # nothing is excluded
    assert ids(scored)[0] == "old-strong"
    # The floor is what keeps it: a 16-year-old record keeps 35% of its weight.
    assert recency_factor(date(2010, 6, 1), params(recency=1.0)) == pytest.approx(0.35, abs=0.01)


def test_recency_factor_properties():
    p = params(recency=0.5)  # half-life 4 years
    assert recency_factor(TODAY, p) == pytest.approx(1.0)
    four_years = date(2022, 10, 1)
    assert recency_factor(four_years, p) == pytest.approx(0.35 + 0.65 * 0.5, abs=0.005)
    assert recency_factor(date(2012, 1, 1), params(recency=0.0)) == 1.0


def test_role_weight_one_leaves_ranking_unchanged():
    cands = [Candidate("minister", date(2020, 1, 1), bm25=5.0, is_gov=True, gov_portfolios=["transport"]),
             Candidate("backbencher", date(2020, 1, 1), bm25=6.0)]
    assert ids(rank(cands, {"transport"}, params(recency=0.0, role_weight=1.0))) == ["backbencher", "minister"]


def test_authority_order_at_equal_relevance():
    d = date(2020, 1, 1)
    cands = [
        Candidate("backbencher", d, bm25=5.0),
        Candidate("other-minister", d, bm25=5.0, is_gov=True, gov_portfolios=["health"]),
        Candidate("transport-minister", d, bm25=5.0, is_gov=True, gov_portfolios=["transport"]),
        Candidate("shadow-transport", d, bm25=5.0, is_shadow=True, shadow_portfolios=["transport"]),
        Candidate("transport-chair", d, bm25=5.0, is_chair=True, chair_portfolios=["transport"]),
    ]
    scored = rank(cands, {"transport"}, params(recency=0.0, role_weight=2.0))
    by = {s.candidate.record_id: s for s in scored}
    assert by["transport-minister"].authority == by["transport-chair"].authority == 2.0
    assert by["shadow-transport"].authority == pytest.approx(1.6)
    assert by["other-minister"].authority == pytest.approx(1.5)
    assert by["backbencher"].authority == 1.0
    assert ids(scored)[-1] == "backbencher"


def test_highly_relevant_backbencher_stays_on_top_against_weak_minister():
    d = date(2024, 1, 1)
    cands = [Candidate("backbencher", d, bm25=10.0, cosine=0.9),
             Candidate("minister", d, bm25=3.0, cosine=0.3, is_gov=True, gov_portfolios=["transport"])]
    assert ids(rank(cands, {"transport"}, params(recency=0.0, role_weight=2.0)))[0] == "backbencher"


def test_hybrid_relevance_combines_keyword_and_semantic():
    d = date(2024, 1, 1)
    cands = [Candidate("kw-only", d, bm25=10.0), Candidate("sem-only", d, cosine=0.8),
             Candidate("both", d, bm25=8.0, cosine=0.7)]
    scored = rank(cands, set(), params(recency=0.0, role_weight=1.0, semantic_floor=0.0, semantic_ceiling=0.8))
    assert ids(scored)[0] == "both"
    rel = {s.candidate.record_id: s.relevance for s in scored}
    assert rel["kw-only"] == pytest.approx(0.5) and rel["sem-only"] == pytest.approx(0.5)


def test_weak_semantic_matches_are_not_inflated():
    """A semantic-only hit near the model's 'unrelated' similarity must not outrank a strong keyword hit,
    even when it is the best semantic hit in the candidate set."""
    d = date(2024, 1, 1)
    cands = [Candidate("weak-semantic", d, cosine=0.52), Candidate("strong-keyword", d, bm25=15.0)]
    scored = rank(cands, set(), params(recency=0.0, role_weight=1.0, semantic_floor=0.5, semantic_ceiling=0.85))
    rel = {s.candidate.record_id: s.relevance for s in scored}
    assert ids(scored)[0] == "strong-keyword"
    assert rel["weak-semantic"] < 0.05


def test_sliders_are_clamped():
    p = RankParams.from_settings({"default_recency": 3, "default_role_weight": 9})
    assert p.recency == 1.0 and p.role_weight == 2.0


def test_ordinary_committee_member_on_the_subject_gets_a_small_boost():
    d = date(2025, 1, 1)
    cands = [Candidate("member", d, bm25=5.0, member_portfolios=["health", "science_tech"]),
             Candidate("other-member", d, bm25=5.0, member_portfolios=["defence"]),
             Candidate("backbencher", d, bm25=5.0)]
    by = {s.candidate.record_id: s for s in rank(cands, {"health"}, params(recency=0.0, role_weight=2.0))}
    assert by["member"].authority == pytest.approx(1.3)
    assert "committee member (health)" in by["member"].authority_reason
    assert by["other-member"].authority == by["backbencher"].authority == 1.0


def test_members_who_keep_raising_a_subject_are_included():
    from hansard.rank.evidence import evidence_summary, select_evidence

    town = {"house": "commons", "settlement": "town", "offices": []}
    minister = {"house": "commons", "settlement": "city", "offices": [{"kind": "gov"}]}
    peer = {"house": "lords", "offices": []}
    cands, profiles = [], {}

    def add(rid, person, prof, day, score):
        cands.append(Candidate(rid, day, bm25=score))
        profiles[rid] = {**prof, "person_id": person, "name": person}

    for i in range(6):  # a minister answering on six days: their job, not a sign of particular interest
        add(f"min-{i}", "minister", minister, date(2026, 1, 1 + i), 10.0 - i * 0.1)
    add("one-off-1", "one-off", town, date(2026, 3, 1), 9.0)  # a backbencher who spoke on it once, strongly
    add("one-off-2", "one-off", town, date(2026, 3, 1), 8.9)
    add("peer-1", "peer", peer, date(2026, 3, 2), 8.0)
    for i in range(3):  # a backbencher in the same group who raised it on three separate days
        add(f"wrigley-{i}", "wrigley", town, date(2026, 4, 1 + i), 6.0 - i * 0.1)

    scored = rank(cands, set(), params(recency=0.0))
    chosen = select_evidence(scored, profiles, 4, min_occasions=3, max_persistent=4)
    assert "wrigley-0" in ids(chosen)
    assert not any(s.candidate.data.get("occasions") for s in chosen if s.candidate.record_id.startswith("min-"))
    w = next(s for s in chosen if s.candidate.record_id == "wrigley-0")
    assert w.candidate.data["occasions"] == 3
    # without it, the town group's places go to the one-off speaker and the backbencher is left out
    assert "wrigley-0" not in ids(select_evidence(rank(cands, set(), params(recency=0.0)), profiles, 4,
                                                  min_occasions=99))
    records = [{"record_id": s.candidate.record_id, "date": s.candidate.date.isoformat(),
                "speaker": profiles[s.candidate.record_id], "occasions": s.candidate.data.get("occasions")}
               for s in chosen]
    assert evidence_summary(records)["raised_most"] == [{"name": "wrigley", "days": 3}]
