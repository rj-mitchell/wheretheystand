"""The test set file is valid, scoring is right, and re-ranking reorders as intended."""

from datetime import date
from pathlib import Path

from hansard.api.usage import cost_usd
from hansard.engine import Filters
from hansard.evaluate import load_questions, parse_variant, plan_values, score_plan
from hansard.rag.query_parse import QueryPlan
from hansard.rank.composite import Candidate, Scored
from hansard.rank.rerank import rerank

ROOT = Path(__file__).resolve().parents[1]


def test_questions_file_is_valid():
    today, qs = load_questions(ROOT / "eval" / "questions.toml")
    assert today == date(2026, 10, 11)
    assert len(qs) >= 50
    fields = {"date_from", "date_to", "houses", "regions", "parties", "offices", "genders", "age_bands", "tenure",
              "majority_bands", "speakers", "group_by", "wants_votes", "portfolios"}
    for q in qs:
        assert {k.removesuffix("_any") for k in q.expect} <= fields, q.id


def plan(**kw) -> QueryPlan:
    speakers = kw.pop("speakers", [])
    p = QueryPlan("q", "text", Filters(**{k: v for k, v in kw.items() if k not in ("group_by", "portfolios")}),
                  portfolios=kw.get("portfolios", []), group_by=kw.get("group_by", "role"))
    p.unresolved_speakers = speakers
    return p


def test_scoring_counts_extra_filters_as_mistakes():
    got = plan_values(plan(parties=["labourco-operative"], houses=["commons"], portfolios=["education"]))
    s = score_plan({"parties": ["labour"], "houses": ["commons"], "portfolios": ["education", "treasury"]}, got)
    assert all(s.values())
    s = score_plan({"parties": ["labour"]}, got)
    assert s["parties"] and not s["houses"]  # houses was not asked for


def test_scoring_dates_and_alternatives():
    got = plan_values(plan(date_from=date(2024, 7, 4), speakers=["Hilary Benn"]))
    assert score_plan({"date_from": "2024-07-01", "speakers": ["hilary benn"], "portfolios_any": [[]]}, got)["date_from"]
    assert not score_plan({"date_from": "2023-01-01"}, got)["date_from"]
    s = score_plan({"date_from_any": ["", "2024-07-04"], "speakers": ["Hilary Benn"]}, got)
    assert s["date_from"] and s["speakers"] and s["portfolios"]


def test_variants():
    assert parse_variant("baseline").reranker == ""
    v = parse_variant("rerank:Xenova/ms-marco-MiniLM-L-6-v2:question:0.8")
    assert (v.reranker, v.rerank_query, v.rerank_weight) == ("Xenova/ms-marco-MiniLM-L-6-v2", "question", 0.8)


class FakeReranker:
    def scores(self, query, texts):
        return [0.9 if "wind" in t else 0.1 for t in texts]


def scored(rid: str, text: str, rel: float) -> Scored:
    c = Candidate(rid, date(2024, 1, 1), best_passage_id="p", data={"passages": {"p": {"text": text}}})
    return Scored(c, rel, 1.0, 1.0, 0.0, "", rel)


def test_rerank_promotes_the_closer_match():
    out = rerank([scored("a", "tribute to a colleague", 0.9), scored("b", "onshore wind farms", 0.6),
                  scored("c", "more on wind", 0.95)], "onshore wind", FakeReranker(), top=2, weight=0.6)
    assert [s.candidate.record_id for s in out] == ["b", "a", "c"]  # c was beyond the re-ranked set
    assert out[0].rerank == 0.9 and out[0].breakdown()["reranker"] == 0.9


def test_prices_per_model():
    prices = {"price_input_per_mtok": 4.0, "price_output_per_mtok": 20.0,
              "models": {"claude-haiku-5-5": {"price_input_per_mtok": 0.1, "price_output_per_mtok": 0.5}}}
    u = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert cost_usd(u, prices) == 24.0
    assert cost_usd(u, prices, "claude-haiku-5-5") == 0.6


def test_region_groups_are_expanded_not_dropped():
    from hansard.rag.query_parse import _open_end, expand_regions

    groups = {"northern": ["North East", "North West", "Yorkshire and The Humber"]}
    assert expand_regions(["northern"], groups) == ["North East", "North West", "Yorkshire and The Humber"]
    assert expand_regions(["scotland", "London", "Atlantis"], groups) == ["London", "Scotland"]
    assert _open_end(date(2026, 10, 11), date(2026, 10, 11)) is None
    assert _open_end(date(2020, 1, 1), date(2026, 10, 11)) == date(2020, 1, 1)
