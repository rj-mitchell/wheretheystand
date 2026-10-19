"""Devolution notes: matched from the question's words, worded correctly, and not set off by look-alike words."""

from hansard.devolution import SUBJECTS, notes_for


def subjects(q: str) -> list[str]:
    return [n["subject"] for n in notes_for(q)]


def test_devolved_subjects_are_noted():
    assert subjects("What have MPs said about NHS waiting lists?") == ["health"]
    assert subjects("Teacher pay and school funding") == ["education"]
    assert subjects("Planning permission for onshore wind") == ["housing", "energy"]
    note = notes_for("Police funding")[0]
    assert note["westminster_for"] == ["England", "Wales"] and note["devolved_in"] == ["Scotland", "Northern Ireland"]
    assert "decide their own" in note["text"]
    assert "decides its own" in notes_for("zero-hours contracts")[0]["text"]


def test_look_alike_words_do_not_trigger_notes():
    for q in ("Air strikes in Yemen", "What did MPs say about the International Court of Justice?",
              "Health and safety at work", "The benefits of free trade", "Defence spending", "Immigration"):
        assert subjects(q) == [], q
    assert subjects("Animal welfare on farms") == ["farming"]


def test_every_subject_is_well_formed():
    for s in SUBJECTS:
        assert set(s.devolved) <= {"Scotland", "Wales", "Northern Ireland"}
        assert s.devolved or s.detail, f"{s.key}: a subject devolved nowhere needs a detail explaining the split"
