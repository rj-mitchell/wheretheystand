from datetime import date
from types import SimpleNamespace

from hansard.rag.interests import Match, Matches, check, held_on
from hansard.rag.sources import Source

GIFT = {"type": "3", "name": "Gifts, benefits and hospitality from UK sources", "items": [
    {"summary": "Betting & Gaming Council - £1,920.00", "details": [["Payment Description", "Tickets at Ascot"]],
     "registered": "02 July 2026", "children": []},
    {"summary": "Old gift - £500.00", "details": [], "registered": "01 March 2025", "children": []}]}
LAND = {"type": "6", "name": "Land and property (within or outside the UK)", "items": [
    {"summary": "Agricultural in County Fermanagh", "details": [], "registered": "10 January 2020", "children": []},
    {"summary": "Flat in London", "details": [], "registered": "10 October 2026", "children": []}]}
LORDS = {"type": "1", "name": "Remunerated employment etc.", "items": [
    {"summary": "Non-executive Director, Ofcom", "details": [], "registered_on": "2025-05-07", "children": []},
    {"summary": "Chair, a bank (interest ceased 30 April 2026)", "details": [], "registered_on": "2022-07-04",
     "children": []}]}


def test_entries_that_applied_on_the_day():
    held = held_on([GIFT, LAND], date(2026, 9, 10), lords=False)
    assert [h["summary"] for h in held] == ["Betting & Gaming Council - £1,920.00", "Agricultural in County Fermanagh"]
    # a gift more than a year before is left out; ongoing interests stay; anything registered later is left out
    assert "Ascot" in held[0]["text"]
    assert [h["summary"] for h in held_on([LORDS], date(2025, 5, 6), lords=True)] == [
        "Chair, a bank (interest ceased 30 April 2026)"]  # the Ofcom role was registered the next day
    assert [h["summary"] for h in held_on([LORDS], date(2025, 6, 1), lords=True)] == [
        "Non-executive Director, Ofcom", "Chair, a bank (interest ceased 30 April 2026)"]
    # an interest that had ended before the day does not count
    assert [h["summary"] for h in held_on([LORDS], date(2026, 6, 1), lords=True)] == ["Non-executive Director, Ofcom"]
    ended = {"type": "1.2", "name": "Ongoing paid employment", "items": [
        {"summary": "Adviser", "details": [["End Date", "2026-01-31"]], "registered": "01 May 2025", "children": []}]}
    assert held_on([ended], date(2026, 3, 1), lords=False) == []
    assert len(held_on([ended], date(2025, 12, 1), lords=False)) == 1


class Register:
    def __init__(self, people):
        self.people = people

    def for_person(self, pid):
        return {"categories": self.people.get(pid, [])}


class Lords:
    def for_person(self, pid):
        return [{"register": "lords", "categories": [LORDS]}] if pid == "uk.org.publicwhip/person/3" else []


class Client:
    def __init__(self, matches):
        self.matches = matches
        self.prompt = ""
        self.messages = self

    def parse(self, **kw):
        self.prompt = kw["messages"][0]["content"]
        return SimpleNamespace(parsed_output=Matches(matches=self.matches), usage=SimpleNamespace(input_tokens=1))


def source(n, pid, house, text, day="2026-09-10"):
    return Source(n, "record", "t", "u", [text], {"speaker": {"person_id": f"uk.org.publicwhip/person/{pid}",
                                                              "name": f"Member {pid}", "house": house},
                                                  "date": day, "heading": "Debate", "text": text})


def test_check_keeps_only_real_matches():
    commons = Register({"uk.org.publicwhip/person/1": [GIFT], "uk.org.publicwhip/person/2": [LAND]})
    sources = [source(1, 1, "commons", "Gambling advertising"), source(2, 2, "commons", "Farm tax"),
               source(3, 3, "lords", "Ofcom codes"), source(4, 9, "commons", "No register entries")]
    client = Client([Match(contribution=1, interest="A1", subjects="s", relevant=True, connection="Gift from the gambling industry; gambling."),
                     Match(contribution=2, interest="A1", subjects="s", relevant=True, connection="Another member's entry."),
                     Match(contribution=2, interest="Q7", subjects="s", relevant=True, connection="A label that does not exist."),
                     Match(contribution=3, interest="C1", subjects="s", relevant=True, connection="Ofcom role; Ofcom codes.")])
    flags, usage = check(client, "m", "question", sources, commons, Lords())
    assert usage is not None and "Member 9" not in client.prompt
    assert [(f["source"], f["name"], [i["summary"] for i in f["items"]]) for f in flags] == [
        (1, "Member 1", ["Betting & Gaming Council - £1,920.00"]),
        (3, "Member 3", ["Non-executive Director, Ofcom"])]
    assert flags[0]["link"] == "/people/1#h-interests" and flags[1]["register"] == "Register of Lords' Interests"


def test_no_model_call_without_entries():
    client = Client([])
    assert check(client, "m", "q", [source(1, 9, "commons", "x")], Register({}), Lords()) == ([], None)
    assert client.prompt == ""


class Donations:
    def for_person(self, pid):
        return {"personal": [
            {"donor": "Bawtry Farms Ltd", "value": 7500.0, "accepted": "2026-07-08", "donor_status": "Company",
             "url": "https://ec/1"},
            {"donor": "Betfair Ltd", "value": 2000.0, "accepted": "2026-01-02", "donor_status": "Company", "url": "https://ec/2"},
            {"donor": "Old Donor", "value": 100.0, "accepted": "2024-01-02", "url": "https://ec/3"}]}


def test_donations_count_once_and_only_within_a_year():
    from hansard.rag.interests import candidates

    registered = {"type": "2", "name": "Donations", "items": [
        {"summary": "Bawtry Farms Ltd - £7,500.00", "details": [], "registered": "10 July 2026", "children": []}]}
    commons = Register({"uk.org.publicwhip/person/1": [registered]})
    people = candidates([source(1, 1, "commons", "Farm tax", day="2026-07-20")], commons, Lords(), Donations())
    held = people["uk.org.publicwhip/person/1"]["contributions"][0]["held"]
    assert [(h["summary"], h["kind"], h["url"]) for h in held] == [
        ("Bawtry Farms Ltd - £7,500.00", "register", "https://ec/1"),
        ("Betfair Ltd - £2,000.00", "donation", "https://ec/2")]


def test_doubtful_connections_are_dropped():
    commons = Register({"uk.org.publicwhip/person/1": [GIFT]})
    client = Client([Match(contribution=1, interest="A1", subjects="gifts; gambling", relevant=True,
                           connection="The link is too weak, so this should not be listed.")])
    assert check(client, "m", "q", [source(1, 1, "commons", "Gambling")], commons, Lords())[0] == []


def test_posts_in_government_or_parliament_are_not_outside_interests():
    from hansard.rag.interests import public_office, without_public_office

    for summary in ("Financial Secretary to the Treasury", "Parliamentary Under-Secretary of State, Home Office",
                    "Lord in Waiting (Government Whip)", "Minister of State for Trade", "Shadow Secretary of State",
                    "Chairman of Committees", "Salary as Lord Speaker"):
        assert public_office({"summary": summary}), summary
    for summary in ("Adviser, Betting and Gaming Council", "Director, Whippet Racing Ltd", "Shareholding in BP plc",
                    "Trustee, Ministry of Stories (charity)"):
        assert not public_office({"summary": summary}), summary
    # flags saved before this change lose such entries when shown; a flag left empty is dropped
    flags = [{"source": 4, "items": [{"kind": "register", "summary": "Financial Secretary to the Treasury"}]},
             {"source": 5, "items": [{"kind": "register", "summary": "Financial Secretary to the Treasury"},
                                     {"kind": "register", "summary": "Director, Energy Co"}]}]
    assert without_public_office(flags) == [{"source": 5, "items": [{"kind": "register", "summary": "Director, Energy Co"}]}]


def test_held_on_leaves_out_public_office():
    from datetime import date

    from hansard.rag.interests import held_on

    cats = [{"type": "1", "name": "Remunerated employment, office, profession etc.",
             "items": [{"summary": "Financial Secretary to the Treasury", "registered": "2024-08-06", "details": [],
                        "children": []},
                       {"summary": "Adviser, Energy Co", "registered": "2024-08-06", "details": [], "children": []}]}]
    assert [h["summary"] for h in held_on(cats, date(2025, 7, 3), lords=True)] == ["Adviser, Energy Co"]


def test_an_mps_entries_as_they_stood_on_the_day_come_from_the_archive():
    from hansard.rag.interests import candidates

    old_job = {"type": "2", "name": "Remunerated employment, office, profession etc", "items": [
        {"summary": "Consultant, Fishing Federation", "details": [], "registered_on": "2004-02-01", "children": []}]}

    class Archived(Register):
        def categories_on(self, pid, on):
            return [old_job] if on.year < 2010 else None  # the archive knows 2008, not 2026

    commons = Archived({"uk.org.publicwhip/person/1": [GIFT]})
    people = candidates([source(1, 1, "commons", "Fishing quotas", day="2008-05-01"),
                         source(2, 1, "commons", "Racing", day="2026-07-20")], commons, Lords())
    held = {c["source"]: [h["summary"] for h in c["held"]] for c in people["uk.org.publicwhip/person/1"]["contributions"]}
    # 2008: the paid role then (registered years before, still counted: it was on the register at the time);
    # 2026: the latest edition, as before
    assert held == {1: ["Consultant, Fishing Federation"], 2: ["Betting & Gaming Council - £1,920.00"]}
