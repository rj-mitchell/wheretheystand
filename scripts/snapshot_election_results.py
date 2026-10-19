"""Save every MP's seats and every seat's election results from Parliament's Members API as a fallback copy shipped
with the code.

The server fetches results itself, a few minutes' worth per update; if Parliament's service is unreachable from it
or answers empty, the pages would show no results at all. This copy fills any member or seat the server's own copy
lacks.

    uv run python scripts/snapshot_election_results.py data/raw/elections.json
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from hansard.sources.demographics import USER_AGENT
from hansard.sources.elections import SNAPSHOT, SOURCE, fetch_results


def main(cache: Path) -> None:
    reps = json.loads(cache.read_text())["representations"]
    seats = sorted({str(r["id"]) for rs in reps.values() for r in rs}, key=int)
    out: dict[str, list] = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True) as client:
        for k, cid in enumerate(seats):
            res = fetch_results(client, int(cid))
            if res:
                out[cid] = res
            if k % 200 == 0:
                print(f"{k}/{len(seats)} seats, {len(out)} with results", flush=True)
    SNAPSHOT.write_text(json.dumps({"fetched_at": datetime.now(UTC).isoformat(timespec="seconds"), "source": SOURCE,
                                    "representations": {k: v for k, v in reps.items() if v}, "results": out}, sort_keys=True, separators=(",", ":")))
    print(f"saved {len(out)} seats ({sum(len(v) for v in out.values())} results) to {SNAPSHOT}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw/elections.json"))
