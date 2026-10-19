"""Registers of interests beyond the Commons: the Lords (Members API) and the devolved parliaments (invented data)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from hansard.sources.registers import (
    Registers,
    clean,
    lords_categories,
    refresh_devolved,
    refresh_lords,
)

FIXTURE = Path(__file__).parent / "fixtures" / "regmem" / "regmem2026-09-21.xml"
BENN = "uk.org.publicwhip/person/10669"


def api_value(*entries):
    """The Members API's shape: one category object per interest."""
    return [{"id": 1001, "name": name, "sortOrder": order, "interests": [
        {"id": iid, "interest": text, "createdWhen": when, "deletedWhen": None, "childInterests": []}]}
        for name, order, iid, text, when in entries]


def test_lords_entries_grouped_by_category_in_register_order():
    cats = lords_categories(api_value(
        ("Category 2: Shareholdings etc. (a)", 1043, 2, "Acme plc", "2019-01-02T10:00:00"),
        ("Category 1: Remunerated employment etc.", 1041, 1, "Director,\r\n Widgets Ltd", "2020-03-04T10:00:00"),
        ("Category 1: Remunerated employment etc.", 1041, 3, "Adviser, Gadgets Ltd", "2021-05-06T10:00:00")))
    assert [(c["type"], c["name"], len(c["items"])) for c in cats] == [
        ("1", "Remunerated employment etc.", 2), ("2", "Shareholdings etc. (a)", 1)]
    assert cats[0]["items"][0] == {"id": 1, "summary": "Director, Widgets Ltd", "details": [],
                                   "registered": "4 March 2020", "registered_on": "2020-03-04", "published": None,
                                   "children": []}


def test_placeholders_are_left_out():
    cats = [{"type": "gifts", "name": "Gifts", "items": [{"summary": "No registrable interests."}]},
            {"type": "shares", "name": "Shares", "items": [{"summary": "Acme plc"}]}]
    assert [c["name"] for c in clean(cats)] == ["Shares"]


def test_lords_refresh_keeps_failures_and_remembers_what_went(tmp_path):
    path = tmp_path / "regmem_lords.json"
    peers = [("p/1", "11", "Lord One"), ("p/2", "22", "Baroness Two")]
    entries = {"11": api_value(("Category 1: Remunerated employment etc.", 1, 7, "Director, Old Co", "2026-01-01")),
               "22": api_value(("Category 3: Land and property", 3, 8, "A farm", "2015-01-01"))}
    fail: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        mnis = request.url.path.split("/")[3]
        if mnis in fail:
            return httpx.Response(500)
        return httpx.Response(200, json={"value": entries[mnis]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert refresh_lords(path, peers, client=client, sleep=lambda _s: None)["peers"] == 2
    assert not refresh_lords(path, peers, client=client, sleep=lambda _s: None)["updated"], "weekly"

    entries["11"] = []  # Lord One's directorship ends; Baroness Two cannot be read this time
    fail.add("22")
    out = refresh_lords(path, peers, client=client, sleep=lambda _s: None, force=True)
    assert out == {"updated": True, "peers": 2, "failed": 1}
    data = json.loads(path.read_text())
    assert data["members"]["p/2"]["categories"][0]["items"][0]["summary"] == "A farm", "the last copy is kept"
    regs = Registers(tmp_path).for_person("p/1")
    assert regs[0]["register"] == "lords" and regs[0]["categories"] == []
    assert regs[0]["changes"]["removed"] == [{"category": "Remunerated employment etc.", "summary": "Director, Old Co"}]


def test_devolved_registers_from_theyworkforyou(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/"):
            return httpx.Response(200, text='<a href="regmem2025-09-01.xml"></a> <a href="regmem2026-09-21.xml"></a>')
        return httpx.Response(200, content=FIXTURE.read_bytes())

    out = refresh_devolved(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert set(out) == {"scottish-parliament", "senedd", "ni-assembly"}
    assert out["senedd"]["updated"] and out["senedd"]["previous"]["edition"] == "2025-09-01"
    regs = Registers(tmp_path).for_person(BENN)
    assert [r["register"] for r in regs] == ["scottish-parliament", "senedd", "ni-assembly"]
    assert regs[0]["member_word"] == "MSP" and regs[0]["categories"]
    assert regs[0]["changes"] == {"since": "2025-09-01", "added": [], "removed": []}
    assert Registers(tmp_path).for_person("uk.org.publicwhip/person/1") == []


def test_members_missing_from_a_partial_edition_are_kept(tmp_path):
    from lxml import etree

    root = etree.fromstring(FIXTURE.read_bytes())
    partial = etree.fromstring(FIXTURE.read_bytes())
    for rm in partial.findall("regmem"):
        if rm.get("personid") == BENN:
            partial.remove(rm)
    files = {"regmem2026-09-07.xml": etree.tostring(root), "regmem2026-09-21.xml": etree.tostring(partial)}
    listing = {"n": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[1]
        if not name:
            shown = sorted(files)[: listing["n"]]
            return httpx.Response(200, text=" ".join(f'<a href="{f}"></a>' for f in shown))
        return httpx.Response(200, content=files[name])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    refresh_devolved(tmp_path, client=client)
    listing["n"] = 2  # a newer, partial edition appears
    out = refresh_devolved(tmp_path, client=client)
    assert out["senedd"]["kept_from_earlier"] == 1
    assert [r["register"] for r in Registers(tmp_path).for_person(BENN)][:1] == ["scottish-parliament"]
