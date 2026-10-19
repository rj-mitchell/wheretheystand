"""One person, many names: identity is keyed on the parlparse person id."""

from datetime import date

from .conftest import CAMERON


def test_cameron_as_mp_and_prime_minister(resolver):
    p = resolver.profile(CAMERON, date(2012, 3, 1))
    assert p.person_id == CAMERON
    assert p.name == "David Cameron"
    assert (p.house, p.constituency, p.region) == ("commons", "Witney", "South East")
    assert p.party == "Conservative"
    assert "The Prime Minister" in p.office_titles


def test_cameron_between_commons_and_lords_has_no_seat(resolver):
    p = resolver.profile(CAMERON, date(2020, 3, 1))
    assert p.person_id == CAMERON
    assert p.house is None and p.constituency is None and p.region is None
    assert p.offices == []


def test_cameron_as_peer_and_foreign_secretary(resolver):
    p = resolver.profile(CAMERON, date(2024, 2, 1))
    assert p.person_id == CAMERON
    assert p.name == "Lord Cameron of Chipping Norton"
    assert p.house == "lords"
    assert p.constituency is None and p.region is None
    # The post is filed against house-of-commons in the source; lookups are house-agnostic.
    gov = [o for o in p.offices if o["kind"] == "gov"]
    assert len(gov) == 1 and "foreign" in gov[0]["portfolios"]
    assert "David Cameron" in p.other_names


def test_legacy_commons_member_id_resolves_to_the_same_person(resolver):
    assert resolver.canonical_person("uk.org.publicwhip/member/40665") == CAMERON
    p = resolver.profile("uk.org.publicwhip/member/40665", date(2012, 3, 1))
    assert p.person_id == CAMERON and p.name == "David Cameron"


def test_redirected_person_id_resolves(resolver):
    assert resolver.canonical_person("uk.org.publicwhip/person/12975") == "uk.org.publicwhip/person/19284"


def test_alternate_names_are_never_used_for_identity(resolver, reference):
    ref, _ = reference
    # parlparse gives Cameron an open-ended Alternate name "The Prime Minister";
    # it must not appear among display names or be resolvable as an id.
    assert all(n["name"] != "The Prime Minister" for n in ref.names)
    assert resolver.canonical_person("The Prime Minister") is None
    # A record with no speaker id stays unattributed, whatever its speaker name says.
    p = resolver.profiles([("r1", None, date(2018, 1, 1))])["r1"]
    assert p.person_id is None and p.name is None
