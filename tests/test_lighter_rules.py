from hansard.lighter import heading, light, similar


def moment(text):
    return {"text": text}


def row(major="Business of the House", minor=None, rid="uk.org.publicwhip/debate/x.1"):
    return {"major_heading": major, "minor_heading": minor, "record_id": rid}


def test_light_moments_only():
    none = (set(), [])
    joke = "I said in my maiden speech that bus stops were the window to the soul of buses, and I stand by it"
    assert light(moment(joke), row(), none)
    assert not light(moment(joke), row("Death of Ann Widdecombe"), none)  # tributes
    assert not light(moment("…decades of pain and hurt for the islanders, which no apology can mend"), row(), none)
    assert not light(moment("My Lords, I am very grateful to all noble Lords who have taken part in this debate today"),
                     row(), none)
    assert not light(moment("I think the Minister has got the message."), row(), none)  # too short, and formulaic
    assert not light(moment(joke), row(rid="uk.org.publicwhip/debate/bad"), ({"uk.org.publicwhip/debate/bad"}, []))
    assert not light(moment(joke), row(), (set(), ["window to the soul"]))


def test_one_title_without_the_chair():
    assert heading("School Funding: Stockport", "Nuclear Energy: Scotland") == "Nuclear Energy: Scotland"
    assert heading("Treasury", "Topical Questions") == "Treasury — Topical Questions"
    assert heading("Bus Services — [Pete Wishart in the Chair]", None) == "Bus Services"


def test_near_identical_motions():
    assert similar("100th birthday of Sir David Attenborough", "Sir David Attenborough's 100th birthday")
    assert not similar("Maurice the toll booth cat", "Wyke Farms at the 2026 Global Cheese Awards")
