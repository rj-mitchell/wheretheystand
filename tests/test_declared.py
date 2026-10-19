"""A speaker's own declaration of an interest, picked out of what they said (never about anyone else)."""

from __future__ import annotations

from hansard.rag.sources import declared_interest


def test_declarations_in_the_speakers_own_words():
    assert declared_interest("Thank you. I refer the House to my entry in the Register of Members’ Financial "
                             "Interests. On the Bill itself...") == \
        "I refer the House to my entry in the Register of Members’ Financial Interests."
    assert declared_interest("My Lords, I declare my interests as set out in the register.")
    assert declared_interest("I declare an interest as a member of Unite.")
    assert declared_interest("I wish to draw attention to my entry in the Register of Members’ Financial Interests.")


def test_not_a_declaration():
    assert declared_interest("The Minister should declare an interest in this.") is None
    assert declared_interest("We have an interest in reform and I refer to the register later.") is None
    assert declared_interest("I am interested in this, and the public interest matters.") is None
    assert declared_interest("") is None
