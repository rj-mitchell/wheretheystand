"""Donations from the Electoral Commission's register, matched to MPs (invented donations, real fixture members)."""

from __future__ import annotations

from datetime import date

import httpx

from hansard.sources.donations import (
    Donations,
    Matcher,
    Spell,
    match_all,
    name_tokens,
    parse_csv,
    refresh,
    unit_key,
)

HEADER = ("ECRef,RegulatedEntityName,RegulatedEntityType,Value,AcceptedDate,AccountingUnitName,DonorName,"
          "AccountingUnitsAsCentralParty,IsSponsorship,DonorStatus,RegulatedDoneeType,CompanyRegistrationNumber,"
          "Postcode,DonationType,NatureOfDonation,PurposeOfVisit,DonationAction,ReceivedDate,ReportedDate,"
          "IsReportedPrePoll,ReportingPeriodName,IsBequest,IsAggregation,RegulatedEntityId,AccountingUnitId,DonorId,"
          "CampaigningName,RegisterName,IsIrishSource\n")


def row(ref, recipient, when, donor="Acme Ltd", value="£5,000.00", kind="MP - Member of Parliament", unit="",
        entity="Regulated Donee", rid="1", central="False", dtype="Cash", purpose=""):
    return (f'{ref},{recipient},{entity},"{value}",{when},{unit},{donor},{central},False,Company,{kind},123,'
            f'SW1A 1AA,{dtype},,{purpose},,{when},{when},,Q,False,False,{rid},,9,,Great Britain,False\n')


def spell(pid, name, start, end, party="conservative", seat="Witney"):
    return Spell(pid, date.fromisoformat(start), date.fromisoformat(end), party, seat, [name_tokens(n) for n in name])


SPELLS = [
    spell("p/griffiths-a", ["Andrew Griffiths"], "2010-05-06", "2019-11-06", seat="Burton"),
    spell("p/griffiths-k", ["Kate Griffiths"], "2019-12-12", "9999-12-31", seat="Burton and Uttoxeter"),
    spell("p/tugendhat", ["Tom Tugendhat"], "2015-05-07", "9999-12-31", seat="Tonbridge and Malling"),
    spell("p/smith-1", ["Angela Smith"], "2005-05-05", "2010-04-12", party="labour", seat="Basildon"),
    spell("p/smith-2", ["Angela Smith"], "2005-05-05", "2019-11-06", party="labour", seat="Penistone and Stocksbridge"),
    spell("p/ottaway", ["Richard Ottaway"], "1992-04-09", "2015-03-30", seat="Croydon South"),
    spell("p/long", ["Rebecca Long-Bailey"], "2015-05-07", "2024-05-30", party="labour", seat="Salford and Eccles"),
    spell("p/greening", ["Justine Greening"], "2005-05-05", "2019-11-06", seat="Putney"),
]


def test_reading_the_register_leaves_out_addresses():
    rows = parse_csv(HEADER + row("C1", "Mr Tom Tugendhat MP", "17/08/2020", value="£12,345.67"))
    r = rows[0]
    assert r["accepted"] == "2020-08-17" and r["value"] == 12345.67 and r["recipient"] == "Mr Tom Tugendhat MP"
    assert "SW1A" not in str(r), "postcodes are not kept"


def test_names_match_only_sitting_mps_and_only_one():
    m = Matcher(SPELLS)
    on = date(2020, 6, 1)
    assert m.person("The Rt Hon Thomas Tugendhat MP", on, "mp") == ("p/tugendhat", "first and last name (short form)")
    assert m.person("Rebecca Long Bailey", on, "mp")[0] == "p/long", "hyphens do not matter"
    assert m.person("Mr Richard Ottoway", date(2010, 1, 1), "mp")[1] == "first and last name, one letter different"
    # a former MP's name, after they left: never moved onto the MP of the same surname who followed them
    assert m.person("Mr Andrew Griffiths MP", date(2023, 5, 1), "mp") is None
    assert m.person("Mr A Griffiths", date(2015, 5, 1), "mp")[0] == "p/griffiths-a"
    # two sitting MPs with the same name: left out
    assert m.person("Angela Smith MP", date(2008, 1, 1), "mp") is None
    assert m.person("Angela Smith MP", date(2016, 1, 1), "mp")[0] == "p/smith-2"
    # leadership candidates need a first name that fits, not a surname alone
    assert m.person("Griffiths", date(2015, 5, 1), "leadership") is None
    assert m.person("Griffiths", date(2015, 5, 1), "mp")[0] == "p/griffiths-a"


def test_local_parties_named_after_the_seat_and_of_the_mps_party():
    assert unit_key("Stoke-On-Trent North CLP") == "stoke on trent north"
    assert unit_key("Putney Conservative Association") == "putney"
    m = Matcher(SPELLS)
    on = date(2015, 6, 1)
    assert m.seat_mp("Putney", "Conservative and Unionist Party", on).person_id == "p/greening"
    assert m.seat_mp("Putney", "Liberal Democrats", on) is None, "another party's local party"
    assert m.seat_mp("Twickenham and Putney", "Conservative and Unionist Party", on) is None
    assert m.seat_mp("Putney", "Conservative and Unionist Party", date(2021, 1, 1)) is None, "after she left"


def test_every_donation_to_a_recipient_must_point_to_the_same_mp():
    rd = parse_csv(HEADER + row("C1", "Mr Andrew Griffiths MP", "01/06/2015", rid="7")
                   + row("C2", "Mr Andrew Griffiths MP", "01/06/2023", rid="7")
                   + row("C3", "Kate Griffiths", "01/06/2023", rid="8", kind="Leadership Candidate")
                   + row("C4", "Someone Else", "01/06/2015", rid="9"))
    pp = parse_csv(HEADER + row("C5", "Conservative and Unionist Party", "01/06/2015", entity="Political Party",
                                kind="", unit="Putney", rid="20")
                   + row("C6", "Conservative and Unionist Party", "01/06/2015", entity="Political Party",
                         kind="", unit="Central Party", rid="20", central="True"))
    rows, stats = match_all(rd, pp, Matcher(SPELLS))
    by = {r["ec_ref"]: r for r in rows}
    assert by["C1"]["person_id"] == "p/griffiths-a"
    assert by["C2"]["person_id"] is None, "kept unlinked: he was no longer an MP"
    assert by["C3"]["person_id"] == "p/griffiths-k" and by["C3"]["kind"] == "leadership"
    assert by["C4"]["person_id"] is None
    assert by["C5"]["kind"] == "local_party" and by["C5"]["person_id"] == "p/greening"
    assert "C6" not in by, "central party donations are not linked to anyone"
    assert stats["mp_donations"] == 3 and stats["mp_matched"] == 1 and stats["local_party_matched"] == 1


def test_refresh_store_and_the_mp_page(indexed_settings, tmp_path):
    import duckdb
    from fastapi.testclient import TestClient

    from hansard.reference.profiles import attach_reference
    from hansard.sync import donations_path

    from .conftest import GREENING
    from .test_api import make_app

    rd = HEADER + row("C1", "Ms Justine Greening MP", "02/03/2012", donor="Acme Ltd", value="£2,500.00") \
        + row("C2", "Justine Greening", "02/03/2013", donor="Mr A Donor", value="£1,000.00", dtype="Visit",
              purpose="Fact finding")
    pp = HEADER + row("C3", "Conservative and Unionist Party", "05/04/2012", entity="Political Party", kind="",
                      unit="Putney", donor="Acme Ltd", value="£10,000.00")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["et"])
        return httpx.Response(200, text=rd if request.url.params["et"] == "rd" else pp)

    path = donations_path(indexed_settings)
    try:
        con = duckdb.connect()
        attach_reference(con, indexed_settings.reference_dir)
        out = refresh(path, con, client=httpx.Client(transport=httpx.MockTransport(handler)))
        assert out["updated"] and out["mp_matched"] == 2 and out["local_party_matched"] == 1
        assert not refresh(path, con, client=httpx.Client(transport=httpx.MockTransport(handler)))["updated"], \
            "weekly"
        mine = Donations(path).for_person(GREENING)
        assert mine["totals"] == {"personal": 3500.0, "personal_count": 2, "local": 10000.0, "local_count": 1}
        assert mine["personal"][0]["url"].endswith("/Donations/C2")

        client = TestClient(make_app(indexed_settings, tmp_path, auth=False)[0])
        page = client.get(f"/api/people/{GREENING.rsplit('/', 1)[1]}").json()
        assert page["donations"]["totals"]["personal_count"] == 2
        index = client.get("/api/donations").json()
        assert index["top_donors"] == [], "the biggest donors are of the last five years"
        assert index["recent"][0]["member"]["name"] == "Justine Greening"
        assert client.get("/api/donations/donors", params={"q": "acme"}).json()["donors"][0]["n"] == 2
        assert len(client.get("/api/donations/donor", params={"name": "Acme Ltd"}).json()["donations"]) == 2
    finally:
        path.unlink(missing_ok=True)
