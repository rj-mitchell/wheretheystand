"""Save UK Parliament IDs for MPs since 1997 that parlparse has none for, with their seats and election results.

About 400 MPs who sat after 1997 have no Parliament ID ("datadotparl_id") in parlparse's people.json, so the site
could not show their election results, age or gender. Each is found in the Members API by surname and accepted only
when one of their seats and its dates match a seat parlparse has for them (see
scripts/snapshot_committees_before_2010.py). The IDs go to data_static/parliament_ids.json; the seats they held and
those seats' results are added to data_static/elections/results_snapshot.json, which the server reads for members its
own copy lacks. Run once (answers are cached in .cache/).

    uv run python scripts/snapshot_parliament_ids.py data/raw/parlparse/members
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import snapshot_committees_before_2010 as members_api  # noqa: E402

from hansard.config import STATIC_DIR  # noqa: E402
from hansard.sources.demographics import USER_AGENT  # noqa: E402
from hansard.sources.elections import SNAPSHOT, fetch_representations, fetch_results  # noqa: E402

OUT = STATIC_DIR / "parliament_ids.json"


def main(members_dir: Path) -> None:
    if members_api.CACHE_FILE.exists():
        members_api.CACHE.update(json.loads(members_api.CACHE_FILE.read_text()))
    people = json.loads((members_dir / "people.json").read_text(encoding="utf-8"))
    posts = {p["id"]: p for p in people.get("posts", [])}
    seats: dict[str, list[tuple[str, str, str]]] = {}
    for m in people["memberships"]:
        post = posts.get(m.get("post_id", ""), {})
        if m.get("person_id") and post.get("organization_id") == "house-of-commons" \
                and (m.get("end_date") or "9999") >= "1997-05-01":
            seats.setdefault(m["person_id"], []).append(((post.get("area") or {}).get("name") or "",
                                                         (m.get("start_date") or "0000")[:10],
                                                         (m.get("end_date") or "9999")[:10]))
    family = {p["id"]: next((n.get("family_name") for n in p.get("other_names", []) if n.get("note") == "Main"
                             and n.get("family_name")), None) for p in people["persons"]}
    have = {p["id"] for p in people["persons"] for i in p.get("identifiers", []) if i.get("scheme") == "datadotparl_id"}
    todo = sorted(pid for pid in seats if pid not in have and family.get(pid))
    print(f"{len(todo)} MPs since 1997 without a Parliament ID", flush=True)
    found: dict[str, str] = {}
    snapshot = json.loads(SNAPSHOT.read_text())
    reps, results = snapshot["representations"], snapshot["results"]
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30) as client:
        for k, pid in enumerate(todo):
            mid = members_api.parliament_id(client, family[pid], seats[pid])
            if mid is None:
                continue
            if mid in found.values():  # two parlparse people on one Parliament ID: trust neither
                found = {p: m for p, m in found.items() if m != mid}
                continue
            found[pid] = mid
            if not reps.get(mid):
                reps[mid] = fetch_representations(client, mid)
            for r in reps[mid]:
                if str(r["id"]) not in results:
                    results[str(r["id"])] = fetch_results(client, r["id"])
            if k % 25 == 0:
                print(f"  {k}/{len(todo)}: {len(found)} found", flush=True)
    members_api.CACHE_FILE.write_text(json.dumps(members_api.CACHE))
    OUT.write_text(json.dumps({"source": "UK Parliament Members API (matched by surname, seat and dates)",
                               "ids": dict(sorted(found.items()))}, indent=1) + "\n")
    SNAPSHOT.write_text(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
    print(f"{len(found)} of {len(todo)} matched; {OUT} and {SNAPSHOT} written")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
