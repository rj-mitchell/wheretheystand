"""Regenerate tests/fixtures from real parlparse and TheyWorkForYou data.

Usage:
    uv run python scripts/make_fixtures.py <parlparse members dir> <scrapedxml samples dir>

The samples dir holds real scrapedxml files laid out as <directory>/<file>.xml.
XML files are trimmed to their first few top-level elements (division vote
lists to a handful of voters); members data is trimmed to the people those
files mention plus the people the identity and temporal tests use.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"

# (directory, file, number of top-level elements to keep)
XML_FILES = [
    ("debates", "debates2012-03-07a.xml", 30),
    ("debates", "debates2026-09-15c.xml", 24),
    ("westminhall", "westminster2026-09-15b.xml", 12),
    ("lordspages", "daylord2012-03-07a.xml", 10),
    ("lordspages", "daylord2026-09-17b.xml", 12),
    ("wrans", "answers2012-03-07a.xml", 12),
    ("wrans", "answers2026-10-08.xml", 40),
    ("lordswrans", "lordswrans2026-10-08.xml", 9),
    ("wms", "ministerial2026-09-15.xml", 6),
    ("lordswms", "lordswms2026-09-16.xml", 10),
    ("standing", "standing5709_IMMIGRATION_02-0_2026-09-15b.xml", 40),
    ("divisionsonly", "divisions2012-03-07a.xml", 400),
    ("divisionsonly", "divisions2025-12-16c.xml", 400),
    ("lordsdivisionsonly", "lordsdivisions2025-12-10b.xml", 400),
]
VOTERS_PER_LIST = 6

EXTRA_PEOPLE = [
    "uk.org.publicwhip/person/10777",  # David Cameron: MP -> PM -> Lord, Foreign Secretary
    "uk.org.publicwhip/person/10669",  # Hilary Benn: shadow, committee chair, backbench, Cabinet
    "uk.org.publicwhip/person/11771",  # Justine Greening: concurrent Cabinet posts
    "uk.org.publicwhip/person/19284",  # target of a person-id redirect
]


def trim_xml(src: Path, dest: Path, keep: int, divisions_only: bool = False) -> set[str]:
    raw = src.read_bytes()
    head_end = raw.find(b"<publicwhip")
    header = raw[:head_end]
    encoding = "ISO-8859-1" if b"ISO-8859-1" in header else "UTF-8"
    parser = etree.XMLParser(huge_tree=True, recover=True)
    root = etree.fromstring(raw, parser)
    for div in root.iter("division"):
        for lst in list(div.iter("mplist", "lordlist")):
            for extra in list(lst)[VOTERS_PER_LIST:]:
                if extra.get("teller") != "yes":  # keep tellers, listed last
                    lst.remove(extra)
    kids = [c for c in root if isinstance(c.tag, str)]
    if divisions_only:
        # division files: keep headings (for division titles) and the divisions
        kids = [c for c in kids if c.tag == "division" or c.tag.endswith("heading")]
    kids = kids[:keep]
    attrs = " ".join(f'{k}="{v}"' for k, v in root.attrib.items())
    body = b"".join(etree.tostring(k, encoding=encoding).split(b"?>", 1)[-1]
                    if etree.tostring(k, encoding=encoding).startswith(b"<?xml") else etree.tostring(k, encoding=encoding)
                    for k in kids)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(header + f"<publicwhip {attrs}>\n".encode() + body + b"\n</publicwhip>\n")
    ids = set(re.findall(rb'(?:person_id|speakerid|id)="(uk\.org\.publicwhip/(?:person|member|lord)/\d+)"', dest.read_bytes()))
    return {i.decode() for i in ids}


def trim_members(members: Path, ids: set[str]) -> None:
    people = json.loads((members / "people.json").read_text())
    mem_by_id = {m["id"]: m for m in people["memberships"]}
    person_ids = {i for i in ids if "/person/" in i}
    person_ids |= {mem_by_id[i]["person_id"] for i in ids if i in mem_by_id and "person_id" in mem_by_id[i]}
    redirects = {p["id"]: p["redirect"] for p in people["persons"] if "redirect" in p}
    person_ids |= {redirects[i] for i in person_ids if i in redirects}
    persons = [p for p in people["persons"] if p["id"] in person_ids or p.get("redirect") in person_ids]
    memberships = [m for m in people["memberships"] if m.get("person_id") in person_ids]
    kept = {m["id"] for m in memberships}
    memberships += [m for m in people["memberships"] if "redirect" in m and m["redirect"] in kept]
    post_ids = {m.get("post_id") for m in memberships}
    out = {
        "memberships": memberships,
        "organizations": people["organizations"],
        "persons": persons,
        "posts": [p for p in people["posts"] if p["id"] in post_ids],
    }
    dest = FIX / "members"
    (dest / "posts").mkdir(parents=True, exist_ok=True)
    (dest / "people.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    for rel in ("ministers-2010.json", "posts/westminster-parliament-posts.json"):
        d = json.loads((members / rel).read_text())
        ms = [m for m in d["memberships"] if m.get("person_id") in person_ids]
        orgs = {m["organization_id"] for m in ms}
        (dest / rel).write_text(json.dumps(
            {"memberships": ms, "organizations": [o for o in d["organizations"] if o["id"] in orgs]},
            indent=1, ensure_ascii=False))
    print(f"members fixture: {len(persons)} persons, {len(memberships)} memberships")


def main(members: Path, samples: Path) -> None:
    ids: set[str] = set(EXTRA_PEOPLE)
    for directory, name, keep in XML_FILES:
        ids |= trim_xml(samples / directory / name, FIX / "xml" / directory / name, keep,
                        divisions_only="division" in directory)
    trim_members(members, ids)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
