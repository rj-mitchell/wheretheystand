"""How parties whipped a vote, where TheyWorkForYou Votes (mySociety) has published it.

mySociety adds "whip reports" to some divisions when a party's instruction has become public: a free vote, or
which way the party told its members to vote and how firmly. Only a small share of votes have one, and they are
added over time, so each Commons and Lords division is checked one at a time, politely, a slice per weekly update:
never-checked divisions first (newest first), then those checked longest ago. Kept in ``data/whips.json``.

A whip report is shown only as reported, credited to TheyWorkForYou Votes; the site never infers a whip.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx

from hansard.sources.demographics import USER_AGENT

log = logging.getLogger(__name__)

URL = "https://votes.theyworkforyou.com/decisions/division/{chamber}/{date}/{number}.json"
PER_RUN = 1500
PAUSE = 0.4
CREDIT = "TheyWorkForYou Votes (mySociety)"
DIRECTIONS = {"for": "to vote for", "against": "to vote against", "abstain": "to abstain", "free": "a free vote"}


def divisions(parquet: Path) -> list[tuple[str, str, str, int]]:
    """(source gid, chamber, date, number) for every Commons and Lords division TheyWorkForYou Votes lists."""
    if not Path(parquet).exists():
        return []
    rows = duckdb.connect().execute(f"""
        SELECT DISTINCT source_gid, chamber, division_date, division_number FROM read_parquet('{Path(parquet).as_posix()}')
        WHERE chamber IN ('commons', 'lords') AND source_gid IS NOT NULL""").fetchall()
    return [(gid, ch, d.isoformat() if isinstance(d, date) else str(d), int(n)) for gid, ch, d, n in rows]


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {"checked": {}, "reports": {}}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def refresh(path: Path, parquet: Path, *, per_run: int = PER_RUN, client: httpx.Client | None = None,
            sleep: Callable[[float], None] = time.sleep, force: bool = False) -> dict[str, Any]:
    """Check the next ``per_run`` divisions for whip reports: at every update until every division has been
    checked once, then once a week (each pass rechecks the divisions checked longest ago)."""
    path = Path(path)
    data = _load(path)
    checked: dict[str, str] = data.setdefault("checked", {})
    reports: dict[str, Any] = data.setdefault("reports", {})
    todo = divisions(parquet)
    never = sorted((d for d in todo if d[0] not in checked), key=lambda d: d[2], reverse=True)
    last = data.get("last_run")
    if not force and not never and last and (datetime.now(UTC) - datetime.fromisoformat(last)).days < 6:
        return {"updated": False, "last_run": last, "votes_with_reports": len(reports)}
    again = sorted((d for d in todo if d[0] in checked), key=lambda d: checked[d[0]])
    batch = (never + again)[:per_run]
    own = client is None
    client = client or httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    stats = {"checked": 0, "with_reports": 0, "failed": 0}
    today = datetime.now(UTC).date().isoformat()
    try:
        for gid, chamber, day, number in batch:
            try:
                resp = client.get(URL.format(chamber=chamber, date=day, number=number))
                if resp.status_code == 429:
                    log.warning("whip reports: rate limited; carrying on at the next update")
                    break
                if resp.status_code == 404:
                    checked[gid] = today
                    continue
                resp.raise_for_status()
                found = [{"party": r.get("party"), "direction": r.get("whip_direction"),
                          "priority": r.get("whip_priority")}
                         for r in resp.json().get("whip_reports") or [] if r.get("party")]
            except (httpx.HTTPError, ValueError):
                stats["failed"] += 1
                continue
            finally:
                sleep(PAUSE)
            checked[gid] = today
            stats["checked"] += 1
            if found:
                reports[gid] = found
                stats["with_reports"] += 1
            else:
                reports.pop(gid, None)
            if stats["checked"] % 200 == 0:  # keep progress if a long run is interrupted
                _save(path, data)
    finally:
        if own:
            client.close()
    data["last_run"] = datetime.now(UTC).isoformat(timespec="seconds")
    _save(path, data)
    stats["still_unchecked"] = sum(1 for d in todo if d[0] not in checked)
    stats["votes_with_reports"] = len(reports)
    return stats


def whip_text(reports: list[dict[str, Any]]) -> str:
    """'Labour: a free vote; Conservative: told to vote against (three-line whip)'."""
    parts = []
    for r in reports:
        direction = DIRECTIONS.get(r.get("direction") or "", r.get("direction") or "not stated")
        if r.get("direction") == "free":
            parts.append(f"{r['party']}: a free vote")
        else:
            prio = r.get("priority")
            how = f" ({prio.replace('_', ' ')} whip)" if prio and prio not in ("free", r.get("direction")) else ""
            parts.append(f"{r['party']}: told {direction}{how}")
    return "; ".join(parts)


def reports(path: Path) -> dict[str, list[dict[str, Any]]]:
    return _load(path).get("reports") or {}
