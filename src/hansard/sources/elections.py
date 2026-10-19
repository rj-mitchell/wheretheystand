"""Election results for every MP's seat, from the UK Parliament Members API (Open Parliament Licence).

For each MP since 1997 (matched by UK Parliament ID, never by name) the API lists the constituencies they have
represented, with dates. For each of those constituencies it gives every general election and by-election result:
the majority, votes cast, electorate and outcome. From these the site works out an MP's majority at the time they
spoke (the result of the most recent election in their seat before that day) and now.

Cached in the data folder and refreshed weekly (by-elections happen); if the API cannot be reached, the previous
copy is kept.

The API has results from 2010 only. For the general elections of 1997, 2001 and 2005 the House of Commons Library's
results by constituency (CBP-8647, Open Parliament Licence) are used, kept in data_static/elections (see
scripts/hocl_election_results.py); for the by-elections of 2001 to 2010 its research papers 05/34 and 10/50 (see
scripts/library_by_elections.py), and for those of 1997 to 2001 the House of Commons Information Office's Factsheet
M16 (see scripts/factsheet_by_elections.py; it gives no electorate). They are attached to a seat by its name, for the
elections in which an MP the API lists sat for it.
"""

from __future__ import annotations

import contextlib
import csv
import json
import logging
import re
import time
import unicodedata
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from hansard.config import STATIC_DIR
from hansard.sources.demographics import USER_AGENT

log = logging.getLogger(__name__)

API = "https://members-api.parliament.uk/api"
REFRESH = timedelta(days=7)
SOURCE = "UK Parliament Members API (constituency election results)"
LIBRARY_CSV = STATIC_DIR / "elections" / "general_elections_1997_2005.csv"
BY_ELECTIONS_CSV = STATIC_DIR / "elections" / "by_elections_2001_2010.csv"
BY_ELECTIONS_1997_CSV = STATIC_DIR / "elections" / "by_elections_1997_2001.csv"
# every MP's seats and every seat's results from the Members API, saved with the code
# (scripts/snapshot_election_results.py): fills any member or seat the server's own copy lacks, so results show even
# if Parliament's service cannot be reached from the server
SNAPSHOT = STATIC_DIR / "elections" / "results_snapshot.json"
LIBRARY_SOURCE = "House of Commons Library, general election results by constituency (CBP-8647)"
BY_ELECTIONS_SOURCE = "House of Commons Library, research papers 05/34 and 10/50 (by-elections 2001-2010)"
BY_ELECTIONS_1997_SOURCE = "House of Commons Information Office, Factsheet M16 (by-elections 1997-2001)"

# Majority as a share of votes cast: (key, label, upper bound, exclusive)
MAJORITY_BANDS = [
    ("marginal", "marginal (majority under 10%)", 10.0),
    ("middle", "majority 10% to 20%", 20.0),
    ("safe", "safe (majority over 20%)", 1000.0),
]
MAJORITY_BAND_LABEL = {k: label for k, label, _ in MAJORITY_BANDS}
TENURE_LABEL = {"first-term": "first-term MPs", "returning": "MPs re-elected at least once"}

# UK general elections since 1945. Later ones are also read from the results, so this never goes stale.
GENERAL_ELECTIONS = [date.fromisoformat(d) for d in (
    "1945-07-05", "1950-02-23", "1951-10-25", "1955-05-26", "1959-10-08", "1964-10-15", "1966-03-31",
    "1970-06-18", "1974-02-28", "1974-10-10", "1979-05-03", "1983-06-09", "1987-06-11", "1992-04-09",
    "1997-05-01", "2001-06-07", "2005-05-05", "2010-05-06", "2015-05-07", "2017-06-08", "2019-12-12",
    "2024-07-04")]


PAUSE = 0.2           # seconds between requests: one at a time, as Parliament's services ask
BUDGET_SECONDS = 300  # per update; whatever is left is fetched at the next one


class Throttled(Exception):
    """Parliament asked us to slow down (429): stop now, keep what was fetched, carry on next update."""


def _get(client: httpx.Client, path: str, tries: int = 3, sleep=time.sleep) -> Any:
    for attempt in range(tries):
        try:
            resp = client.get(f"{API}{path}")
            if resp.status_code == 429:
                raise Throttled(path)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            body = resp.json()
            sleep(PAUSE)
            return body
        except httpx.HTTPError:
            if attempt == tries - 1:
                raise
            sleep(1 + attempt * 2)
    return None


def fetch_representations(client: httpx.Client, member_id: str, sleep=time.sleep) -> list[dict[str, Any]]:
    """The Commons seats a member has held: [{"id", "name", "start", "end"}]."""
    body = _get(client, f"/Members/{member_id}/Biography", sleep=sleep)
    reps = ((body or {}).get("value") or {}).get("representations") or []
    return [{"id": r["id"], "name": r["name"], "start": r["startDate"][:10],
             "end": (r.get("endDate") or "")[:10] or None} for r in reps if r.get("house") == 1 and r.get("id")]


def fetch_results(client: httpx.Client, constituency_id: int, sleep=time.sleep) -> list[dict[str, Any]]:
    """Every real (not notional) election result for a constituency, oldest first."""
    body = _get(client, f"/Location/Constituency/{constituency_id}/ElectionResults", sleep=sleep)
    out = []
    for r in (body or {}).get("value") or []:
        if r.get("isNotional"):
            continue
        out.append({"date": r["electionDate"][:10], "title": r.get("electionTitle"),
                    "general": bool(r.get("isGeneralElection")), "majority": r.get("majority"),
                    "votes": r.get("turnout"), "electorate": r.get("electorate"), "result": r.get("result"),
                    "party": ((r.get("winningParty") or {}).get("name"))})
    return sorted(out, key=lambda x: x["date"])


def refresh_elections(cache: Path, member_ids: list[str], *, force: bool = False, client: httpx.Client | None = None,
                      budget_seconds: float = BUDGET_SECONDS, sleep=time.sleep, now=None) -> dict[str, Any]:
    """Bring the cached seats and results up to date, a few minutes' worth per update.

    First anyone the copy has never looked up (the record grew), then, once a week, the sitting MPs and their seats
    (by-elections and general elections change those; history does not). Requests go one at a time; on the first
    request to slow down, or when the time is up, what was fetched is saved and the next update carries on."""
    now = now or (lambda: datetime.now(UTC))
    old: dict[str, Any] = {}
    if cache.exists():
        try:
            old = json.loads(cache.read_text())
        except (OSError, ValueError):
            old = {}
    reps: dict[str, list] = dict(old.get("representations") or {})
    results: dict[str, list] = dict(old.get("results") or {})
    requested = set(old.get("requested") or [])
    # seats whose results came back empty (Parliament's service busy, say): asked again once a week
    empty_checked: dict[str, str] = dict(old.get("empty_checked") or {})
    try:
        fetched = datetime.fromisoformat(old["fetched_at"])
    except (KeyError, TypeError, ValueError):
        fetched = None
    cycle = old.get("cycle")  # the weekly refresh in progress: {"members": [...], "seats": [...]} still to do
    if cycle is None and (force or fetched is None or now() - fetched >= REFRESH):
        sitting = sorted(m for m, rs in reps.items() if m in set(member_ids) and any(r.get("end") is None for r in rs))
        cycle = {"members": sitting, "seats": sorted({str(r["id"]) for m in sitting for r in reps[m]
                                                       if r.get("end") is None})}
    new_members = [m for m in member_ids if m not in requested]
    stale_empty = any(not rs and empty_checked.get(c, "") < (now() - REFRESH).isoformat() for c, rs in results.items())
    if not new_members and not cycle and not stale_empty:
        return {"status": "fresh"}

    own = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
    started, done, stopped = time.monotonic(), 0, None

    def time_up() -> bool:
        return time.monotonic() - started > budget_seconds

    try:
        for m in new_members:
            if time_up():
                break
            reps[m] = fetch_representations(client, m, sleep)
            requested.add(m)
            done += 1
        for c in sorted({str(r["id"]) for rs in reps.values() for r in rs} - set(results)):  # seats never fetched
            if time_up():
                break
            results[c] = fetch_results(client, int(c), sleep)
            done += 1
        week_ago = (now() - REFRESH).isoformat()
        for c in sorted(c for c, rs in results.items() if not rs and empty_checked.get(c, "") < week_ago):
            if time_up():
                break
            results[c] = fetch_results(client, int(c), sleep)
            if not results[c]:
                empty_checked[c] = now().isoformat(timespec="seconds")
            done += 1
        while cycle and cycle["members"] and not time_up():
            m = cycle["members"][0]
            reps[m] = fetch_representations(client, m, sleep)
            cycle["members"].pop(0)
            done += 1
        while cycle and cycle["seats"] and not time_up():
            c = cycle["seats"][0]
            results[c] = fetch_results(client, int(c), sleep)
            cycle["seats"].pop(0)
            done += 1
    except Throttled as exc:
        stopped = f"Parliament asked us to slow down ({exc})"
    except Exception as exc:  # network trouble: keep what we have
        stopped = repr(exc)
    finally:
        if own:
            client.close()
    if done == 0 and stopped:
        log.warning("could not refresh election results (%s); keeping the previous copy", stopped)
        return {"status": "failed", "error": stopped}
    finished_cycle = cycle is not None and not cycle["members"] and not cycle["seats"]
    remaining = (len([m for m in member_ids if m not in requested])
                 + len({str(r["id"]) for rs in reps.values() for r in rs} - set(results))
                 + (len(cycle["members"]) + len(cycle["seats"]) if cycle else 0))
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "fetched_at": now().isoformat(timespec="seconds") if finished_cycle or fetched is None
        else old.get("fetched_at"),
        "source": SOURCE, "requested": sorted(requested), "empty_checked": empty_checked,
        "representations": {k: v for k, v in reps.items() if v}, "results": results,
        **({"cycle": cycle} if cycle and not finished_cycle else {})}, sort_keys=True))
    tmp.replace(cache)
    out = {"status": "updated" if not remaining else "partial", "requests": done, "remaining": remaining,
           "seats": len(results), "results": sum(len(v) for v in results.values())}
    if stopped:
        log.info("election results: %d fetched, %d left for the next update (%s)", done, remaining, stopped)
        out["stopped"] = stopped
    return out


def seat_key(name: str) -> str:
    """'LIVERPOOL, WEST DERBY', 'Liverpool West Derby' and 'Bedfordshire Mid' / 'Mid Bedfordshire' alike."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower().replace("&", " and ")
    text = re.sub(r"\([^)]*\)", " ", text)  # "Richmond (Yorks)" is "RICHMOND" in the Library's file
    words = re.findall(r"[a-z]+", text)
    return " ".join(sorted(w for w in words if w != "and"))


def library_results(path: Path | None = None, by_elections: Path | None = None, by_elections_1997: Path | None = None
                    ) -> dict[tuple[str, str], dict[str, Any]]:
    """{(seat key, election date): result} from the Commons Library and Information Office files (general elections
    1997 to 2005, by-elections 1997 to 2010)."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for file, general, source in ((path or LIBRARY_CSV, True, LIBRARY_SOURCE),
                                  (by_elections or BY_ELECTIONS_CSV, False, BY_ELECTIONS_SOURCE),
                                  (by_elections_1997 or BY_ELECTIONS_1997_CSV, False, BY_ELECTIONS_1997_SOURCE)):
        try:
            with open(file, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    out[(seat_key(r["constituency"]), r["date"])] = {
                        "date": r["date"], "title": f"{r['year']} {'General Election' if general else 'By-election'}",
                        "general": general, "majority": int(r["majority"]) if r["majority"] else None,
                        "votes": int(r["votes"]) if r["votes"] else None,
                        "electorate": int(r["electorate"]) if r["electorate"] else None,
                        "result": r.get("result") or None, "party": r["winner"] if r["winner"] != "Other" else None,
                        "source": source}
        except OSError:
            pass
    return out


def add_library_results(representations: dict[str, list[dict[str, Any]]], results: dict[str, list[dict[str, Any]]],
                        library: dict[tuple[str, str], dict[str, Any]]) -> int:
    """Fill in the 1997, 2001 and 2005 general elections and the 1997-2010 by-elections for seats the API has no
    result for then: each election in which one of the MPs listed sat for the seat, matched by the seat's name.
    Returns the number added."""
    spans: dict[str, list[tuple[str, str, str]]] = {}
    for seats in representations.values():
        for r in seats:
            spans.setdefault(str(r["id"]), []).append((r["start"], r["end"] or "9999-12-31", r["name"]))  # end: excl.
    dates = sorted({d for _, d in library})
    added = 0
    for cid, ss in spans.items():
        have = {r["date"] for r in results.get(cid, [])}
        for d in dates:
            if d in have:
                continue
            hit = next((library.get((seat_key(name), d)) for start, end, name in ss if start <= d < end
                        and (seat_key(name), d) in library), None)
            if hit:
                results.setdefault(cid, []).append(dict(hit))
                added += 1
        if cid in results:
            results[cid].sort(key=lambda x: x["date"])
    return added


def saved_snapshot(path: Path | None = None) -> dict[str, Any]:
    """The seats and results saved with the code (see SNAPSHOT); empty if there is none."""
    try:
        return json.loads(Path(path or SNAPSHOT).read_text())
    except (OSError, ValueError):
        return {}


def snapshot_results(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    return saved_snapshot(path).get("results") or {}


def load_elections(cache: Path, library: Path | None = None, snapshot: bool = True) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    # no copy of its own yet (Parliament's service unreachable from the server, say): the saved one serves
    with contextlib.suppress(OSError, ValueError):
        raw = json.loads(cache.read_text())
    reps, results = raw.get("representations", {}), raw.get("results", {})
    saved = saved_snapshot() if snapshot else {}
    for mid, rs in (saved.get("representations") or {}).items():  # members the server's own copy lacks
        if not reps.get(mid):
            reps[mid] = rs
    for cid, rs in (saved.get("results") or {}).items():  # seats the server's own copy lacks
        if not results.get(cid):
            results[cid] = rs
    add_library_results(reps, results, library_results(library))
    return {"representations": reps, "results": results}


def commons_member_ids(people_json: Path, since: str = "1997-05-01") -> list[str]:
    """UK Parliament IDs of everyone who has sat in the Commons since ``since`` (from parlparse people.json, and the
    IDs saved for members it has none for)."""
    from hansard.reference.build import saved_parliament_ids

    data = json.loads(Path(people_json).read_text(encoding="utf-8"))
    ids = saved_parliament_ids()
    ids.update({p["id"]: str(i["identifier"]) for p in data["persons"] for i in p.get("identifiers", [])
                if i.get("scheme") == "datadotparl_id"})
    out = {ids[m["person_id"]] for m in data.get("memberships", [])
           if m.get("post_id") and m.get("person_id") in ids and (m.get("end_date") or "9999") >= since}
    return sorted(out)


# --------------------------------------------------------------------------- calculations


def majority_band(pct: float | None) -> str | None:
    if pct is None:
        return None
    return next(k for k, _, upper in MAJORITY_BANDS if pct < upper)


def general_elections(results: dict[str, list[dict[str, Any]]] | None = None) -> list[date]:
    seen = set(GENERAL_ELECTIONS)
    for rs in (results or {}).values():
        seen.update(date.fromisoformat(r["date"]) for r in rs if r.get("general"))
    return sorted(seen)


def first_term(first_elected: date | None, on: date, elections: list[date]) -> bool | None:
    """True while an MP is still in the Parliament they were first elected to (including by-election entrants)."""
    if first_elected is None:
        return None
    return not any(first_elected < g <= on for g in elections)


def election_label(result: dict[str, Any]) -> str:
    year = result["date"][:4]
    return f"{year} general election" if result.get("general") else f"{year} by-election"
