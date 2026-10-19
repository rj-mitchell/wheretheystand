"""Boundary invariance: 2010-2024 and post-2024 seats both resolve to ITL1 regions."""

from collections import Counter
from datetime import date

from hansard.reference.build import (
    PRE2010_CSV,
    load_constituencies,
    load_pre2010_regions,
    normalise_place,
)

from .conftest import BENN, CAMERON, GEO_CSV

ITL1 = {
    "North East", "North West", "Yorkshire and The Humber", "East Midlands", "West Midlands",
    "East of England", "London", "South East", "South West", "Scotland", "Wales", "Northern Ireland",
}


def seats():
    return load_constituencies(GEO_CSV)


def test_both_boundary_sets_are_complete():
    counts = Counter(s["boundary_set"] for s in seats())
    assert counts == {"2010": 650, "2024": 650}
    assert len({(s["gss_code"], s["boundary_set"]) for s in seats()}) == 1300
    # Five unchanged Scottish seats keep their ONS code across the 2024 review.
    shared = {s["gss_code"] for s in seats() if s["boundary_set"] == "2010"} & {
        s["gss_code"] for s in seats() if s["boundary_set"] == "2024"}
    assert len(shared) == 5 and all(code.startswith("S14") for code in shared)


def test_every_seat_has_an_itl1_region_and_valid_interval():
    for s in seats():
        assert s["region"] in ITL1, s
        assert s["valid_from"] <= s["valid_to"]
    by_set = {}
    for s in seats():
        by_set.setdefault(s["boundary_set"], set()).add((s["valid_from"], s["valid_to"]))
    assert by_set["2010"] == {(date(2010, 5, 6), date(2024, 7, 3))}
    assert by_set["2024"] == {(date(2024, 7, 4), date(9999, 12, 31))}


def test_abolished_and_successor_seats_resolve_to_the_same_region():
    idx = {(normalise_place(s["name"]), s["boundary_set"]): s for s in seats()}
    assert idx[("wakefield", "2010")]["region"] == "Yorkshire and The Humber"
    assert idx[("wakefield and rothwell", "2024")]["region"] == "Yorkshire and The Humber"
    assert ("wakefield", "2024") not in idx  # abolished in 2024


def test_nations_resolve_to_themselves():
    regions = {(s["nation"], s["region"]) for s in seats() if s["nation"] != "England"}
    assert regions == {("Scotland", "Scotland"), ("Wales", "Wales"), ("Northern Ireland", "Northern Ireland")}
    counts = Counter((s["boundary_set"], s["nation"]) for s in seats())
    assert counts[("2010", "Scotland")] == 59 and counts[("2024", "Scotland")] == 57
    assert counts[("2010", "Wales")] == 40 and counts[("2024", "Wales")] == 32
    assert counts[("2010", "Northern Ireland")] == 18 == counts[("2024", "Northern Ireland")]


def test_members_resolve_to_regions_across_the_2024_change(resolver):
    old = resolver.profile(BENN, date(2015, 10, 14))
    new = resolver.profile(BENN, date(2025, 3, 1))
    assert old.gss_code != new.gss_code
    assert old.region == new.region == "Yorkshire and The Humber"
    assert resolver.profile(CAMERON, date(2012, 3, 1)).region == "South East"


def test_seats_before_2010_take_the_region_of_their_namesake_or_the_curated_table(resolver):
    before = resolver.profile(CAMERON, date(2005, 6, 1))
    assert before.constituency == "Witney" and before.region == "South East" and before.gss_code is None
    pre = load_pre2010_regions(PRE2010_CSV)
    assert pre[normalise_place("Tyne Bridge")]["region"] == "North East"
    assert all(r["region"] in ITL1 for r in pre.values())


def test_all_fixture_commons_memberships_since_1997_have_regions(reference):
    ref, _ = reference
    missing = [m for m in ref.memberships
               if m["house"] == "commons" and m["end"] >= date(1997, 5, 1) and not m["region"]]
    assert missing == []
    assert ref.stats["unmatched_seats"] == []


def test_name_normalisation():
    assert normalise_place("Airdrie & Shotts") == normalise_place("Airdrie and Shotts")
    assert normalise_place("Aldridge - Brownhills") == normalise_place("Aldridge-Brownhills")
    assert normalise_place("Ynys Môn") == "ynys mon"


def _seat(name, boundary="2024"):
    return next(s for s in seats() if s["name"] == name and s["boundary_set"] == boundary)


def test_seat_type_follows_urban_area_size_not_city_status():
    for s in seats():
        assert s["settlement"] in {"city", "town", "rural"}, s
        if s["settlement"] == "city":
            assert s["urban_area"] and s["urban_area_pop"] >= 200_000, s
        if s["settlement"] == "town" and s["urban_area_pop"]:
            assert s["urban_area_pop"] < 200_000, s
    # small places with city status
    assert _seat("Wells and Mendip Hills")["settlement"] == "rural"
    assert _seat("Ely and East Cambridgeshire")["settlement"] == "rural"
    assert {_seat(n)["settlement"] for n in ("Bath", "Canterbury", "Lichfield", "Hereford and South Herefordshire")} \
        == {"town"}
    # large places without it
    assert {_seat(n)["settlement"] for n in ("Reading Central", "Milton Keynes Central", "Northampton North")} \
        == {"city"}
    # a town inside a conurbation takes the conurbation's size; a separate town keeps its own
    bolton = _seat("Bolton North East")
    assert (bolton["settlement"], bolton["urban_area"]) == ("city", "Greater Manchester")
    assert bolton["urban_area_pop"] > 2_000_000
    assert _seat("Wigan")["settlement"] == "town"
    # ...but a conurbation stops at its county: London's commuter towns are towns
    assert {_seat(n)["settlement"] for n in ("Watford", "St Albans", "Woking", "Guildford", "Tatton")} == {"town"}
    assert _seat("Watford")["urban_area"] == "Watford"
    assert _seat("Leeds Central", "2010")["urban_area"] == "West Yorkshire"
    assert {_seat(n)["settlement"] for n in ("Belfast East", "Belfast West")} == {"city"}
    assert _seat("Paisley and Renfrewshire South")["urban_area"] == "Greater Glasgow"  # NRS settlement


def test_seat_types_are_balanced_enough_to_compare():
    for boundary in ("2010", "2024"):
        counts = Counter(s["settlement"] for s in seats() if s["boundary_set"] == boundary)
        assert all(100 <= n <= 350 for n in counts.values()), counts
