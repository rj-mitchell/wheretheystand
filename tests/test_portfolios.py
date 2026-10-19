import json
import os
from pathlib import Path

import pytest

from hansard.reference.portfolios import (
    KIND_CHAIR,
    KIND_GOV,
    KIND_PARLIAMENTARY,
    KIND_PARTY,
    KIND_SHADOW,
    KIND_WHIP,
    PORTFOLIOS,
    classify_committee,
    classify_post,
    is_unspecified_title,
    portfolios_for_query,
)

from .conftest import MEMBERS

GOV = "datadotparl/governmentpost"
OPP = "datadotparl/oppositionpost"


@pytest.mark.parametrize("role,expected", [
    ("Parliamentary Under-Secretary (Department for Transport)", {"transport"}),
    ("Foreign Secretary, Foreign, Commonwealth and Development Office", {"foreign", "international_development"}),
    ("The Secretary of State for Northern Ireland", {"northern_ireland"}),
    ("The Secretary of State for Business, Energy and Industrial Strategy", {"business", "energy_climate"}),
    ("The Secretary of State for Energy Security and Net Zero", {"energy_climate"}),
    ("The Secretary of State for Energy and Climate Change", {"energy_climate"}),
    ("The Chancellor of the Exchequer", {"treasury"}),
    ("The Prime Minister", {"pm"}),
    ("The Lord Chancellor and Secretary of State for Justice", {"justice"}),
    ("Lords Spokesperson (HM Treasury) (Whip)", {"treasury"}),
])
def test_government_posts_map_to_portfolios(role, expected):
    rc = classify_post(GOV, role)
    assert rc.kind == KIND_GOV
    assert set(rc.portfolios) == expected


def test_shadow_and_opposition_posts():
    assert classify_post(OPP, "Shadow Foreign Secretary").portfolios == ("foreign",)
    assert classify_post(OPP, "Shadow Minister (Transport)").kind == KIND_SHADOW
    # Climate justice is an energy brief, not the Ministry of Justice.
    rc = classify_post(OPP, "Shadow Minister (Business, Energy and Industrial Strategy) Climate Justice and Green Jobs")
    assert "justice" not in rc.portfolios and "energy_climate" in rc.portfolios
    assert classify_post(OPP, "Leader of HM Official Opposition").portfolios == ("pm",)


@pytest.mark.parametrize("source,role,kind", [
    (GOV, "The Lord Commissioner of HM Treasury", KIND_WHIP),
    (GOV, "Assistant Whip (HM Treasury)", KIND_WHIP),
    (GOV, "The Parliamentary Secretary to the Treasury", KIND_WHIP),
    (GOV, "Lord in Waiting (HM Household) (Whip)", KIND_WHIP),
    (OPP, "Opposition Whip (Commons)", KIND_WHIP),
    (OPP, "Campaigns and Elections Chair", KIND_PARTY),
    ("datadotparl/parliamentarypost", "Deputy Chairman of Committees", KIND_PARLIAMENTARY),
])
def test_whips_party_and_parliamentary_posts_carry_no_portfolio(source, role, kind):
    rc = classify_post(source, role)
    assert rc.kind == kind and rc.portfolios == ()


def test_committees():
    rc = classify_committee("Transport Committee", "Chair")
    assert (rc.kind, rc.portfolios) == (KIND_CHAIR, ("transport",))
    rc = classify_committee("Committee on the Future Relationship with the European Union", "Chair")
    assert rc.kind == KIND_CHAIR and "exiting_eu" in rc.portfolios
    assert classify_committee("Liaison Committee (Commons)", "Chair").portfolios == ()
    assert classify_committee("Health and Social Care Committee", "").kind == "committee_member"


def test_query_topics():
    assert portfolios_for_query("How have ministers justified delays to HS2?") == ("transport",)
    assert "environment" in portfolios_for_query("agricultural zoning bills")


def test_every_portfolio_has_a_description():
    assert all(PORTFOLIOS.values())


def _role_strings(members: Path):
    for rel in ("ministers-2010.json", "posts/westminster-parliament-posts.json"):
        f = members / rel
        if f.exists():
            for m in json.loads(f.read_text())["memberships"]:
                if m["source"] != "datadotparl/committee" and (m.get("end_date") or "9999") >= "2010-05-06":
                    yield m["source"], m.get("role", "")


def _assert_all_mapped(members: Path):
    unmapped = sorted({
        role for source, role in _role_strings(members)
        if (rc := classify_post(source, role)).kind in (KIND_GOV, KIND_SHADOW)
        and not rc.portfolios and not is_unspecified_title(role)
    })
    assert unmapped == []


def test_every_fixture_role_maps_to_a_portfolio_or_explicit_none():
    _assert_all_mapped(MEMBERS)


@pytest.mark.skipif(not os.environ.get("PARLPARSE_MEMBERS"), reason="set PARLPARSE_MEMBERS to a live members/ dir")
def test_every_live_role_maps_to_a_portfolio_or_explicit_none():
    _assert_all_mapped(Path(os.environ["PARLPARSE_MEMBERS"]))
