from hansard.embed.base import HashingEmbedder
from hansard.rag.blend import blend, similarities, windows

CAPS = {"edms": 3, "evidence": 6, "devolved": 8}


def rec(party, occasions=None):
    return {"speaker": {"party": party}, "occasions": occasions}


def holyrood(n, debate=None):
    return ("devolved", {"legislature": "scottish-parliament", "n": n, "key": debate or f"day{n}"})


def test_weak_matches_are_left_out_and_strong_ones_fill_the_places():
    records = [rec("Labour"), rec("Labour"), rec("Conservative")]
    side = [("edms", {"n": 1}), ("evidence", {"n": 2}), holyrood(3)]
    kept, chosen = blend(records, [0.7, 0.7, 0.7], side, [0.5, 0.8, 0.75], floor=0.64, budget=10, keep=2, caps=CAPS)
    assert kept == records, "room to spare: no Westminster record gives way"
    assert chosen == {"edms": [], "evidence": [{"n": 2}], "devolved": [holyrood(3)[1]]}


def test_a_subject_argued_in_holyrood_takes_the_places_of_weaker_westminster_records():
    records = [rec("Labour"), rec("Labour"), rec("Labour"), rec("Conservative"), rec("Conservative")]
    sims = [0.66, 0.9, 0.67, 0.65, 0.88]
    side = [holyrood(i) for i in range(6)]
    kept, chosen = blend(records, sims, side, [0.8] * 6, floor=0.6, budget=2, keep=2, caps=CAPS)
    assert len(chosen["devolved"]) == 5, "two places, then three weaker Westminster records replaced"
    # the weakest go first, down to two, but never the last of a party: the stronger Conservative stays
    assert kept == [records[1], records[4]]
    # when every Westminster record is closer to the question, none gives way
    kept, chosen = blend(records, [0.9] * 5, side, [0.8] * 6, floor=0.6, budget=2, keep=2, caps=CAPS)
    assert kept == records and len(chosen["devolved"]) == 2


def test_persistent_members_and_the_last_of_a_party_keep_their_places():
    records = [rec("Labour", occasions=4), rec("Labour"), rec("Green")]
    kept, chosen = blend(records, [0.61, 0.62, 0.6], [holyrood(1), holyrood(2), holyrood(3)], [0.9, 0.9, 0.9],
                         floor=0.6, budget=1, keep=1, caps=CAPS)
    assert kept == [records[0], records[2]] and len(chosen["devolved"]) == 2


def test_caps_hold_for_each_kind_and_each_parliament():
    side = [holyrood(i) for i in range(12)] + [("devolved", {"legislature": "senedd", "n": 1})]
    _, chosen = blend([], [], side, [0.9] * 12 + [0.7], floor=0.6, budget=20, keep=0, caps=CAPS)
    assert sum(d["legislature"] == "scottish-parliament" for d in chosen["devolved"]) == 8
    assert any(d["legislature"] == "senedd" for d in chosen["devolved"])


def test_similarity_uses_the_closest_window_of_a_long_contribution():
    long = "\n\n".join(["Procedure and thanks to the Presiding Officer."] * 40 + ["minimum unit pricing of alcohol"])
    assert len(windows(long)) == 2
    e = HashingEmbedder()
    near, far = similarities(e, "minimum unit pricing alcohol", ["minimum unit pricing of alcohol", "bus timetables"])
    assert near > far


def test_no_more_than_three_from_one_devolved_debate():
    side = [holyrood(i, debate="2019-06-13") for i in range(6)] + [holyrood(9)]
    _, chosen = blend([], [], side, [0.9] * 6 + [0.7], floor=0.6, budget=10, keep=0, caps=CAPS)
    assert [d["n"] for d in chosen["devolved"]] == [0, 1, 2, 9]
