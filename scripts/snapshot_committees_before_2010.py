"""Save the select committee memberships (and chairs) of members who left Parliament before 2010, from the UK
Parliament Members API, as a copy shipped with the code (data_static/committees_before_2010.json).

parlparse's ministers-2010.json has Parliament's committee history for everyone who sat after 2010, back to their
first committee; members who left before then are not in it. Their histories do not change, so they are fetched once
here rather than by every server. Same shape as ministers-2010.json's committee rows. Most of these members have no
Parliament ID in parlparse: they are found by surname and accepted only when a seat and its dates also match.

    uv run python scripts/snapshot_committees_before_2010.py data/raw/parlparse/members
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

from hansard.config import STATIC_DIR
from hansard.sources.demographics import USER_AGENT

API = "https://members-api.parliament.uk/api/Members/{}/Biography"
OUT = STATIC_DIR / "committees_before_2010.json"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def seat_key(name: str) -> str:
    return " ".join(sorted(w for w in re.findall(r"[a-z]+", (name or "").lower().replace("&", " and ")) if w != "and"))


def parliament_id(client: httpx.Client, family: str, seats: list[tuple[str, str, str]]) -> str | None:
    """The member's Parliament ID, found by surname and accepted only when one of their seats and its dates match
    a seat parlparse has for them (so two members with the same surname are never confused)."""
    resp = get(client, "https://members-api.parliament.uk/api/Members/Search",
               {"Name": family, "House": 1, "take": 20})
    found = []
    for item in (resp.json().get("items") or []) if resp is not None else []:
        mid = item["value"]["id"]
        bio = get(client, API.format(mid))
        reps = ((bio.json().get("value") or {}).get("representations") or []) if bio is not None else []
        if any(seat_key(r.get("name")) == seat_key(area) and (r.get("startDate") or "")[:10] <= end
               and (r.get("endDate") or "9999")[:10] >= start for r in reps for area, start, end in seats):
            found.append(str(mid))
    return found[0] if len(found) == 1 else None


CACHE: dict[str, dict] = {}
CACHE_FILE = Path(".cache") / "members_api_biographies.json"


class _Cached:
    def __init__(self, body: dict):
        self._body = body

    def json(self) -> dict:
        return self._body


def get(client: httpx.Client, url: str, params: dict | None = None):
    """A Members API answer, kept on disk so a rerun does not ask again."""
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    if key in CACHE:
        return _Cached(CACHE[key])
    for attempt in range(3):
        resp = client.get(url, params=params)
        if resp.status_code == 429:
            time.sleep(10 * (attempt + 1))
            continue
        time.sleep(0.2)
        if resp.status_code != 200:
            return None
        CACHE[key] = resp.json()
        if len(CACHE) % 50 == 0:
            CACHE_FILE.parent.mkdir(exist_ok=True)
            CACHE_FILE.write_text(json.dumps(CACHE))
        return resp
    return None


def main(members_dir: Path) -> None:
    if CACHE_FILE.exists():
        CACHE.update(json.loads(CACHE_FILE.read_text()))
    people = json.loads((members_dir / "people.json").read_text(encoding="utf-8"))
    later = json.loads((members_dir / "ministers-2010.json").read_text(encoding="utf-8"))
    covered = {m["person_id"] for m in later["memberships"] if m.get("source", "").endswith("/committee")}
    covered |= {m["person_id"] for m in people["memberships"]
                if (m.get("end_date") or "9999") >= "2010-05-06" and m.get("person_id")}
    posts = {p["id"]: p for p in people.get("posts", [])}
    seats: dict[str, list[tuple[str, str, str]]] = {}
    for m in people["memberships"]:
        post = posts.get(m.get("post_id", ""), {})
        if m.get("person_id") and post.get("organization_id") == "house-of-commons" \
                and (m.get("end_date") or "9999") >= "1997-05-01" and (m.get("start_date") or "0000") < "2010-05-06":
            seats.setdefault(m["person_id"], []).append(((post.get("area") or {}).get("name") or "",
                                                         (m.get("start_date") or "0000")[:10],
                                                         (m.get("end_date") or "9999")[:10]))
    family = {p["id"]: next((n.get("family_name") for n in p.get("other_names", []) if n.get("note") == "Main"
                             and n.get("family_name")), None) for p in people["persons"]}
    ids = {p["id"]: str(i["identifier"]) for p in people["persons"] for i in p.get("identifiers", [])
           if i.get("scheme") == "datadotparl_id"}
    todo = sorted(pid for pid in seats if pid not in covered)
    print(f"{len(todo)} MPs who left before 2010", flush=True)
    rows, orgs, unmatched = [], {}, 0
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30) as client:
        for k, pid in enumerate(todo):
            mid = ids.get(pid) or (parliament_id(client, family[pid], seats[pid]) if family.get(pid) else None)
            if mid is None:
                unmatched += 1
                continue
            bio = get(client, API.format(mid))
            for c in ((bio.json().get("value") or {}).get("committeeMemberships") or []) if bio is not None else []:
                if not c.get("name") or not c.get("startDate"):
                    continue
                oid = slug(c["name"])
                orgs[oid] = c["name"]
                role = "Chair" if (c.get("additionalInfo") or "").strip().lower() == "chair" else "Member"
                rows.append({"id": f"uk.parliament.data/Member/{mid}/Committee/{c.get('id')}/{c['startDate'][:10]}",
                             "source": "datadotparl/committee", "person_id": pid, "organization_id": oid, "role": role,
                             "start_date": c["startDate"][:10],
                             **({"end_date": c["endDate"][:10]} if c.get("endDate") else {})})
            if k % 50 == 0:
                print(f"{k}/{len(todo)} MPs, {len(rows)} committee spells, {unmatched} not matched", flush=True)
    OUT.write_text(json.dumps({"source": "UK Parliament Members API (committee memberships)",
                               "organizations": [{"id": k, "name": v} for k, v in sorted(orgs.items())],
                               "memberships": rows}, indent=0, sort_keys=True))
    print(f"saved {len(rows)} committee spells for {len({r['person_id'] for r in rows})} MPs to {OUT}; "
          f"{unmatched} could not be matched to Parliament's records")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw/parlparse/members"))
