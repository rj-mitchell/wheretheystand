"""Comparisons: each side searched separately, with an equal share of the sources, labelled by side."""

from __future__ import annotations

from hansard.compare import instruction, resolve_sides, side_filters, sides_from_question
from hansard.engine import Filters
from hansard.rag.pipeline import AskRequest, answer_once, plan_note

from .test_rag import FakeClient, pipeline

PARTIES = {"labour": "Labour", "conservative": "Conservative", "liberal-democrat": "Liberal Democrat"}


def test_sides():
    names = {"uk.org.publicwhip/person/10669": "Hilary Benn"}.get
    sides = resolve_sides([{"kind": "party", "id": "labour"}, {"kind": "person", "id": "10669"},
                           {"kind": "nation", "id": "wales"}, {"kind": "party", "id": "x"}], PARTIES, names)
    assert [s["label"] for s in sides] == ["Labour", "Hilary Benn", "MPs for seats in Wales"]
    assert resolve_sides([{"kind": "party", "id": "labour"}], PARTIES, names) == []  # one side is no comparison
    assert resolve_sides([{"kind": "party", "id": "labour"}] * 2, PARTIES, names) == []
    f = side_filters(Filters(parties=["green"], houses=["lords"]), {"kind": "nation", "id": "wales"})
    assert f.regions == ["Wales"] and f.houses == ["commons"] and f.parties == ["green"]
    q = sides_from_question("How do Labour and the Conservatives compare on housing?",
                            Filters(parties=["labourco-operative", "conservative"]), [], PARTIES)
    assert [s["id"] for s in q] == ["labour", "conservative"]
    assert sides_from_question("What have Labour and Conservative MPs said about housing?",
                               Filters(parties=["labour", "conservative"]), [], PARTIES) == []
    assert "Labour and Conservative" in instruction(q) and "Do not judge" in instruction(q)


def test_comparison_briefing_has_balanced_labelled_sources(indexed_settings):
    p = pipeline(indexed_settings, FakeClient())
    parties = [pid for pid, _ in p.parties() if pid in ("labour", "conservative")]
    assert len(parties) == 2
    req = AskRequest("What has been said about trial by jury?",
                     compare=[{"kind": "party", "id": x} for x in parties])
    out = answer_once(p, req)
    plan = out["plan"]["plan"]
    assert [s["id"] for s in plan["compare"]] == parties and not plan["filters"]["parties"]
    groups = [s["group"] for s in out["sources"]["sources"] if s["kind"] == "record"]
    assert set(groups) <= {"Labour", "Conservative"} and groups
    sent = p.client.calls[-1]["messages"][0]["content"]
    assert "Each search result title begins with the side" in sent[-1]["text"]
    assert any(b.get("title", "").startswith(("Labour", "Conservative")) for b in sent if b["type"] == "search_result")
    assert out["final"]["answer"]
    from hansard.rag.query_parse import QueryPlan

    names = " and ".join(s["label"] for s in plan["compare"])
    assert f"comparing {names}, each searched separately" in plan_note(QueryPlan.from_dict(plan), req.scope)
