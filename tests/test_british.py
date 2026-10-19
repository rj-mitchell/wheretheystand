"""British spelling for model-written text, with quotations left exactly as recorded."""

from __future__ import annotations

from hansard.rag.british import britishise_segments, to_british
from hansard.rag.verify import Segment


def test_converts_american_spelling_in_the_models_own_words():
    text = "Ministers emphasized the program and its defense, analyzing labor costs of 5 percent toward the center."
    assert to_british(text) == (
        "Ministers emphasised the programme and its defence, analysing labour costs of 5 per cent towards the centre.")


def test_quotations_are_never_changed():
    text = 'She said "we will prioritize the program" and he criticized it.'
    assert to_british(text) == 'She said "we will prioritize the program" and he criticised it.'
    assert to_british("He called it “a modernization program” and organized a vote.") == (
        "He called it “a modernization program” and organised a vote.")


def test_quotation_spanning_segments_stays_verbatim():
    segs = [Segment('Ministers said "', status="connective"),
            Segment("we will organize the center", status="cited"),
            Segment('" and then organized a review.', status="connective")]
    britishise_segments(segs)
    assert [s.text for s in segs] == ['Ministers said "', "we will organize the center",
                                      '" and then organised a review.']


def test_names_and_lookalikes_are_left_alone():
    assert to_british("The World Health Organization and the Australian Labor Party were cited.") == (
        "The World Health Organization and the Australian Labor Party were cited.")
    assert to_british("The size of the prize was seized on; citizens noticed an oversized bill.") == (
        "The size of the prize was seized on; citizens noticed an oversized bill.")
    assert to_british("It was a computer program.") == "It was a computer program."


def test_capitals_are_kept_at_the_start_of_sentences_lines_and_labels():
    assert to_british("Labor shortages grew. Organizations objected.") == (
        "Labour shortages grew. Organisations objected.")
    assert to_british("**Center-right MPs**\n- Defense ministers said") == (
        "**Centre-right MPs**\n- Defence ministers said")


def test_british_text_is_unchanged():
    text = "Ministers recognised the programme's behaviour and travelled to the centre; 4 per cent of councils."
    assert to_british(text) == text


def test_loaded_words_are_flagged_only_outside_quotations():
    from hansard.rag.neutral import flag_loaded

    segs = [Segment("The minister admitted the delay and ", status="connective"),
            Segment('said "this was a scandal', status="cited"),
            Segment('" before benefit claims rose; MPs attacked it.', status="connective")]
    assert flag_loaded(segs) == 2
    words = [(i, f["word"]) for i, s in enumerate(segs) for f in s.loaded]
    assert words == [(0, "admitted"), (2, "attacked")]
    f = segs[0].loaded[0]
    assert segs[0].text[f["start"]:f["end"]] == "admitted"


def test_claimed_counts_only_as_a_verb_of_saying():
    from hansard.rag.neutral import LOADED, still_loaded

    assert not LOADED.search("He questioned the benefits claimed for the trial and the claimed savings.")
    assert LOADED.search("Jones claimed that the plan had failed.") and LOADED.search("as Smith claimed, ")
    saved = {"segments": [{"text": "He questioned the benefits claimed for the trial", "status": "connective",
                           "loaded": [{"start": 31, "end": 38, "word": "claimed"}]},
                          {"text": "Jones claimed that it failed", "status": "connective",
                           "loaded": [{"start": 6, "end": 13, "word": "claimed"}]}], "loaded_words": 2}
    out = still_loaded(saved)
    assert out["loaded_words"] == 1 and not out["segments"][0]["loaded"] and out["segments"][1]["loaded"]
