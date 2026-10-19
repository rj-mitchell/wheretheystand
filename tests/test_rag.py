"""Answer generation guardrails, with a fake Claude client (no network)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace as NS

from hansard.embed.base import HashingEmbedder
from hansard.engine import Engine, Filters, Scope
from hansard.rag.pipeline import AskRequest, Pipeline, answer_once
from hansard.rag.prompts import NO_RECORDS
from hansard.rag.query_parse import parse_offline
from hansard.rag.sources import Source
from hansard.rag.verify import verify

TODAY = date(2026, 10, 1)
GROUPS = {"northern": ["North East", "North West", "Yorkshire and The Humber"]}


# --------------------------------------------------------------------------- query parsing (offline)


def test_offline_parse_hs2_example():
    plan = parse_offline("How have ministers justified delays to HS2?", GROUPS, TODAY)
    assert plan.filters.offices == ["gov"]
    assert plan.portfolios == ["transport"]
    assert not plan.wants_votes and plan.filters.date_from is None


def test_offline_parse_northern_dissent_example():
    plan = parse_offline("Did northern MPs dissent on agricultural zoning bills between 2018 and 2022?", GROUPS, TODAY)
    f = plan.filters
    assert f.regions == GROUPS["northern"]
    assert f.houses == ["commons"]
    assert (f.date_from, f.date_to) == (date(2018, 1, 1), date(2022, 12, 31))
    assert plan.wants_votes and "environment" in plan.portfolios


# --------------------------------------------------------------------------- verification


def src(n, *blocks):
    return Source(n, "record", f"Source {n}", f"https://example/{n}", list(blocks))


def test_verify_marks_cited_unsupported_and_invalid():
    sources = [src(1, "The line to Crewe is delayed by two years.", "Costs rose sharply."), src(2, "We oppose it.")]
    blocks = [
        {"text": "Ministers said ", "citations": []},
        {"text": "the Crewe line is delayed by two years", "citations": [
            {"search_result_index": 0, "cited_text": "The line to Crewe is delayed by two years."}]},
        {"text": ". The project was also cancelled in 2023 because of a scandal.", "citations": []},
        {"text": "Opponents disagreed", "citations": [{"search_result_index": 1, "cited_text": "Text not in source"}]},
    ]
    v = verify(blocks, sources)
    statuses = [s.status for s in v.segments]
    assert statuses == ["connective", "cited", "unsupported", "invalid"]
    assert v.citations_total == 2 and v.citations_invalid == 1
    assert "[Source 1]" in v.markdown() and "[Source 2]" not in v.markdown()
    assert 0 < v.grounded_ratio < 1
    assert not v.no_records


def test_verify_separates_lead_ins_and_evidence_notes_from_uncited_claims():
    sources = [src(1, "We will deliver the line to Manchester.")]
    cite = {"search_result_index": 0, "cited_text": "We will deliver the line to Manchester."}
    blocks = [
        {"text": "## Labour MPs\n\n**Shadow ministers**\n\n"
                 "Shadow ministers attacked the decision to cancel the northern legs.\n"
                 "- Louise Haigh (Sheffield Heeley, Lab), 4 October 2023, said ", "citations": []},
        {"text": "the line to Manchester would be delivered", "citations": [cite]},
        {"text": ".\n\nNo division records were supplied, so votes cannot be reported.\n"
                 "- The retrieved records contain no revised opening date.\n"
                 "- Two results on prison building were not relevant and have been excluded.\n"
                 "- The records are dominated by compensation. Little was found on the system itself.", "citations": []},
    ]
    v = verify(blocks, sources)
    by_status = {}
    for s in v.segments:
        by_status.setdefault(s.status, []).append(s.text)
    unsupported = "".join(by_status["unsupported"])
    assert "attacked the decision" in unsupported
    assert "Louise Haigh" not in unsupported and "Labour MPs" not in unsupported
    assert "No division records" not in unsupported
    assert "opening date" not in unsupported and "prison" not in unsupported and "dominated" not in unsupported
    assert v.to_dict()["unsupported_segments"] == 1


def test_verify_detects_no_records_answer():
    v = verify([{"text": f"{NO_RECORDS} The records cover only fisheries.", "citations": []}], [src(1, "x")])
    assert v.no_records


# --------------------------------------------------------------------------- pipeline with a fake client


class FakeStream:
    def __init__(self, blocks, stop_reason="end_turn"):
        self.blocks, self.stop_reason = blocks, stop_reason

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for b in self.blocks:
            yield NS(type="content_block_delta", delta=NS(type="text_delta", text=b["text"]))
            for c in b.get("citations", []):
                yield NS(type="content_block_delta", delta=NS(type="citations_delta", citation=NS(**c)))

    def get_final_message(self):
        content = [NS(type="text", text=b["text"], citations=[NS(type="search_result_location", **c)
                                                              for c in b.get("citations", [])])
                   for b in self.blocks]
        return NS(content=content, stop_reason=self.stop_reason, model="claude-sonnet-5-5",
                  usage=NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0))


class FakeClient:
    """Answers by citing the first sentence of the first source it is given."""

    def __init__(self, refuse=False):
        self.calls = []
        self.refuse = refuse
        self.beta = NS(messages=NS(stream=self._stream))
        self.messages = NS(stream=self._stream, parse=self._parse)

    def _parse(self, **kw):
        raise RuntimeError("force offline parse in tests")

    def _stream(self, **kw):
        self.calls.append(kw)
        if self.refuse:
            return FakeStream([], stop_reason="refusal")
        first = next(b for b in kw["messages"][0]["content"] if b["type"] == "search_result")
        quote = first["content"][0]["text"]
        return FakeStream([
            {"text": "According to the record, "},
            {"text": quote[:80], "citations": [{"search_result_index": 0, "cited_text": quote, "source": "x"}]},
        ])


def pipeline(settings, client):
    return Pipeline(settings, Engine(settings, embedder=HashingEmbedder(64), today=TODAY), client=client, today=TODAY)


def test_pipeline_end_to_end_is_grounded(indexed_settings):
    client = FakeClient()
    out = answer_once(pipeline(indexed_settings, client), AskRequest("What has been said about trial by jury?"))
    assert out["plan"]["plan"]["search_text"]
    cards = out["sources"]["sources"]
    assert cards and cards[0]["n"] == 1 and cards[0]["text"]
    final = out["final"]["answer"]
    assert final["citations_total"] == 1 and final["citations_invalid"] == 0
    assert "[Source 1]" in final["markdown"]
    # The model only ever sees our sources (as search results) and the grounding rules.
    call = client.calls[0]
    blocks = call["messages"][0]["content"]
    assert [b["type"] for b in blocks[:-1]] == ["search_result"] * len(cards)
    system = call["system"][0]
    assert "ONLY" in system["text"] and NO_RECORDS in system["text"]
    assert system["cache_control"] == {"type": "ephemeral"}  # the shared instructions are cached
    assert call["fallbacks"] == "default" and call["model"] == "claude-sonnet-5-5"


def test_pipeline_returns_no_records_without_calling_the_model(indexed_settings):
    client = FakeClient()
    req = AskRequest("anything", plan={"search_text": "jury", "filters": {
        "date_from": "2011-01-01", "date_to": "2011-01-02"}})
    out = answer_once(pipeline(indexed_settings, client), req)
    assert out["final"]["answer"]["no_records"] is True
    assert client.calls == []


def test_pipeline_reports_refusals_instead_of_answering(indexed_settings):
    out = answer_once(pipeline(indexed_settings, FakeClient(refuse=True)), AskRequest("trial by jury"))
    assert "error" in out and "final" not in out


def test_edited_plan_from_ui_is_used_verbatim(indexed_settings):
    client = FakeClient()
    req = AskRequest("q", plan={"search_text": "security", "filters": {"houses": ["lords"]}},
                     scope=Scope(written=False, votes=False))
    out = answer_once(pipeline(indexed_settings, client), req)
    assert out["plan"]["plan"]["filters"]["houses"] == ["lords"]
    assert {c["chamber"] for c in out["sources"]["sources"]} <= {"House of Lords"}


def test_votes_evidence_is_offered_for_dissent_questions(indexed_settings):
    p = pipeline(indexed_settings, FakeClient())
    divs = p.votes.find("Finance Bill", Filters())
    assert divs and divs[0]["major_heading"].startswith("Finance")
    assert "party_votes" in divs[0] and divs[0]["voters_in_scope"] > 0


def test_offline_parse_northern_labour_mps():
    plan = parse_offline("What have northern Labour MPs said about HS2?", GROUPS, TODAY)
    assert plan.filters.parties == ["labour"]
    assert plan.filters.regions == GROUPS["northern"] and plan.filters.houses == ["commons"]
    assert plan.search_text == "HS2" and plan.portfolios == ["transport"]


def test_api_key_falls_back_to_the_key_file_in_the_data_folder(tmp_path, monkeypatch):
    from hansard.rag.answer import KEY_FILE, api_key

    for name in ("HANSARD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HANSARD_DATA_DIR", str(tmp_path))
    assert api_key() is None
    (tmp_path / KEY_FILE).write_text("sk-ant-from-file\n")
    assert api_key() == "sk-ant-from-file"
    monkeypatch.setenv("HANSARD_ANTHROPIC_API_KEY", "sk-ant-from-env")
    assert api_key() == "sk-ant-from-env"


def test_briefing_instructions_require_neutral_wording():
    from hansard.rag.prompts import system_prompt

    text = system_prompt(TODAY)
    assert "Report in neutral words" in text
    for loaded in ("lied", "misled", "U-turn", "admitted"):
        assert loaded in text  # named as forbidden in the model's own voice
    assert "exact, cited quotation" in text


def test_quotations_are_checked_word_for_word():
    from hansard.rag.sources import Source
    from hansard.rag.verify import Segment, Verification, check_quotes

    src = [Source(1, "record", "t", "u", ["I very much support renewable energy, but we now have our fair share of "
                                          "onshore wind. I warmly welcome the hon. and learned Gentleman's point."], {})]
    segs = [Segment('Lamont said "I very much support renewable energy" ', [], status="connective"),
            Segment('and "we now have our fair share … onshore wind."', [1]),
            Segment(' He "warmly welcome[d] the hon. and learned Gentleman".', [1]),
            Segment(' He also said "wind farms are a blight on the landscape".', [1]),
            Segment(' The term "net zero" is not a quotation.', [], status="connective")]
    report = Verification(segs, False, 3, 0, 0, 10)
    assert check_quotes(report, src) == 1
    assert (report.quotes_total, report.quotes_unverified) == (4, 1)
    assert segs[3].misquotes == [{"start": 14, "end": 56, "text": "wind farms are a blight on the landscape"}]
    assert report.to_dict()["quotes_unverified"] == 1


def test_pipeline_reports_checked_quotations(indexed_settings):
    out = answer_once(pipeline(indexed_settings, FakeClient()), AskRequest("What has been said about trial by jury?"))
    assert out["final"]["answer"]["quotes_unverified"] == 0


def test_the_closing_note_about_the_evidence_is_not_a_claim():
    from hansard.rag.verify import _classify_uncited

    note = _classify_uncited("\n**About the evidence:** the records span 2013 to 2026 and include no divisions.\n", False)
    assert [s.status for s in note] == ["connective"]
    note = _classify_uncited("\nAbout the evidence: the records show what was said, but nothing said by Hilary Benn "
                             "before 2026.\n", False)
    assert [s.status for s in note] == ["connective"]
    claim = _classify_uncited("\nAbout the evidence: Jane Smith said the policy had failed families across the "
                              "country.\n", False)
    assert "unsupported" in [s.status for s in claim]  # a claim about what someone said still needs a citation


def test_a_lead_in_after_an_uncited_sentence_is_not_flagged_but_the_sentence_is():
    from hansard.rag.verify import _classify_uncited

    segs = _classify_uncited("Labour MPs criticised the government. Jane Smith said on 4 May that ", True)
    assert [(s.status, s.text) for s in segs] == [("unsupported", "Labour MPs criticised the government. "),
                                                   ("connective", "Jane Smith said on 4 May that ")]
    assert [s.status for s in _classify_uncited("- Keir Mather (Selby), 10 August 2026, said ", True)] == ["connective"]


def test_openers_about_the_records_are_dropped():
    from hansard.rag.verify import Segment, drop_preambles

    segs = [Segment("Across the records retrieved, ministers defended the policy."),
            Segment("**In brief**\n- In the records supplied, Labour MPs criticised it."),
            Segment("In 2019 the minister said so.")]
    assert drop_preambles(segs) == 2
    assert [s.text for s in segs] == ["Ministers defended the policy.", "**In brief**\n- Labour MPs criticised it.",
                                      "In 2019 the minister said so."]


def test_contributions_about_one_place_are_recognised():
    from hansard.rank.places import Places

    p = Places({"Cornwall", "Workington", "Cockermouth", "Manchester", "Leeds", "Liverpool"})
    assert p.is_local("Bus Services: Cornwall", "")
    assert p.is_local("Bus Services", "The new X5 in my constituency of Workington links Cockermouth.")
    assert not p.is_local("Bus Services", "Franchising will be open to every local authority in England.")
    assert not p.is_local("Transport", "In Manchester, Liverpool and Leeds franchising has improved services.")
    assert not p.is_local("Free Bus Travel: Over-60s — [Leeds in the Chair]", "")  # the chair, not the subject


def test_local_contributions_are_scored_down_not_dropped():
    from datetime import date

    from hansard.rank.composite import Candidate, RankParams, rank

    params = RankParams(today=date(2026, 1, 1))
    national, local = Candidate("a", date(2025, 1, 1), bm25=1.0), Candidate("b", date(2025, 1, 1), bm25=1.0, local=True)
    scored = rank([local, national], set(), params)
    assert [s.candidate.record_id for s in scored] == ["a", "b"]
    assert scored[1].score == scored[0].score * params.local_factor
