"""Company directorships of MPs and peers, from the Companies House register (its free public data API).

Companies House does not link its officers to Parliament, so each member is looked up by name in its officer
search, and an officer record is taken as theirs only when all of these agree:

* the month and year of birth (Companies House shows no more than that), against the member's birth date (from
  Wikidata or Wikipedia; members whose birth month is unknown are not looked up);
* the surname (any surname the member has had in Parliament, or a peer's title name such as "Cameron");
* a first or middle name (allowing short forms such as "Ed" for "Edward").

One person often has several officer records (one per spelling or address), so every record that passes is
kept. A record matched wrongly can be removed in ``data_static/directorships_exclude.csv``. Addresses, nationality
and dates of birth are never kept or shown; only the company, the role and the dates.

Members are checked a slice at a time at each update (current members first, then the longest unchecked), and
each is rechecked about every four weeks. Kept in ``data/directorships.json``. Needs a free API key in
``HANSARD_COMPANIES_HOUSE_KEY``; without one this does nothing.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx

from hansard.config import STATIC_DIR
from hansard.sources.demographics import USER_AGENT
from hansard.sources.donations import _same_first

log = logging.getLogger(__name__)

API = "https://api.company-information.service.gov.uk"
SITE = "https://find-and-update.company-information.service.gov.uk"
KEY_ENV = "HANSARD_COMPANIES_HOUSE_KEY"
EXCLUDE_CSV = STATIC_DIR / "directorships_exclude.csv"
SOURCE = "Companies House"
PER_RUN = 400  # members per update
RECHECK_DAYS = 28
PAUSE = 0.6  # Companies House allows 600 requests in five minutes; this stays well under
SEARCH_PAGES = 3  # 100 results a page; a member's own records come high for all but the commonest names
SINCE = "1997-05-01"

ROLES = {
    "director": "Director", "corporate-director": "Director", "nominee-director": "Nominee director",
    "secretary": "Secretary", "corporate-secretary": "Secretary", "nominee-secretary": "Nominee secretary",
    "llp-member": "LLP member", "llp-designated-member": "LLP designated member",
    "corporate-llp-member": "LLP member", "corporate-llp-designated-member": "LLP designated member",
    "cic-manager": "CIC manager", "judicial-factor": "Judicial factor",
    "general-partner-in-a-limited-partnership": "General partner",
    "limited-partner-in-a-limited-partnership": "Limited partner",
    "member-of-a-management-organ": "Member of a management organ",
    "member-of-a-supervisory-organ": "Member of a supervisory organ",
    "member-of-an-administrative-organ": "Member of an administrative organ",
    "managing-officer": "Managing officer", "manager-of-an-eeig": "Manager of an EEIG",
    "receiver-and-manager": "Receiver and manager",
}
STATUSES = {"active": "active", "dissolved": "dissolved", "liquidation": "in liquidation",
            "receivership": "in receivership", "administration": "in administration",
            "voluntary-arrangement": "in a voluntary arrangement", "converted-closed": "closed",
            "insolvency-proceedings": "in insolvency proceedings", "registered": "registered",
            "removed": "removed", "closed": "closed", "open": "open"}


# A company in one of these states has no current officers, though Companies House records no resignation
CLOSED = {"dissolved", "closed", "removed"}


def ended(a: dict[str, Any]) -> bool:
    """The role is over: resigned, or the company has closed."""
    return bool(a.get("until")) or a.get("status") in CLOSED


def api_key() -> str | None:
    return (os.environ.get(KEY_ENV) or "").strip() or None


def _key(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"['’`.]", "", s).replace("-", " ")
    return " ".join(re.findall(r"[a-z]+", s))


# ---------------------------------------------------------------------------------------------- the members


def members(reference_dir: Path, people_json: Path) -> list[dict[str, Any]]:
    """Everyone who has sat in the Commons or Lords since 1997 with a known birth month: their first names,
    surnames, birth month and year, and when they sat (to mark directorships held while a member)."""
    con = duckdb.connect()
    ref = Path(reference_dir).as_posix()
    rows = con.execute(f"""
        SELECT p.person_id, p.latest_name, p.birth_date, list(struct_pack(s := m.start, e := m."end")),
               max(m."end") = DATE '9999-12-31'
        FROM '{ref}/persons.parquet' p JOIN '{ref}/memberships.parquet' m USING (person_id)
        WHERE p.birth_date IS NOT NULL AND p.birth_precision IN ('day', 'month')
        GROUP BY ALL HAVING max(m."end") >= DATE '{SINCE}'""").fetchall()
    parts: dict[str, tuple[set[str], set[str]]] = {}
    try:
        for p in json.loads(Path(people_json).read_text(encoding="utf-8")).get("persons", []):
            firsts: set[str] = set()
            lasts: set[str] = set()
            for n in p.get("other_names", []):
                if n.get("note") != "Main":
                    continue
                if n.get("given_name"):
                    firsts.update(_key(n["given_name"]).split()[:1])
                for field in ("family_name", "lordname"):
                    if n.get(field):
                        lasts.add(_key(n[field]))
            parts[p["id"]] = (firsts, lasts)
    except (OSError, ValueError):
        log.warning("directorships: people.json not readable")
    out = []
    for pid, name, born, spans, current in rows:
        firsts, lasts = parts.get(pid, (set(), set()))
        firsts, lasts = {f for f in firsts if f}, {s for s in lasts if s}
        if not firsts or not lasts:
            continue
        out.append({"person_id": pid, "name": name, "born": (born.year, born.month), "firsts": sorted(firsts),
                    "surnames": sorted(lasts), "current": bool(current),
                    "spans": sorted((s["s"].isoformat(), s["e"].isoformat()) for s in spans)})
    return out


def _queries(m: dict[str, Any]) -> list[str]:
    return [f"{f} {s}" for s in m["surnames"] for f in m["firsts"][:2]][:4]


def _officer_id(item: dict[str, Any]) -> str | None:
    link = ((item.get("links") or {}).get("self") or "")
    hit = re.match(r"^/officers/([^/]+)/appointments", link)
    return hit.group(1) if hit else None


def _born_matches(item: dict[str, Any], born: tuple[int, int]) -> bool:
    dob = item.get("date_of_birth") or {}
    return (dob.get("year"), dob.get("month")) == born


def name_matches(elements: dict[str, Any], m: dict[str, Any]) -> bool:
    """The officer's surname is one of the member's, and one of its first or middle names is the member's
    first name (or a short form of it)."""
    surname = _key(elements.get("surname"))
    if not surname or not any(surname == s or surname.replace(" ", "") == s.replace(" ", "")
                              for s in m["surnames"]):
        return False
    given = _key(f"{elements.get('forename') or ''} {elements.get('other_forenames') or ''}").split()
    return any(_same_first(g, f) for g in given for f in m["firsts"])


# ---------------------------------------------------------------------------------------------- fetching


class Client:
    def __init__(self, key: str, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep):
        self.own = client is None
        self.http = client or httpx.Client(auth=(key, ""), timeout=60,
                                           headers={"User-Agent": USER_AGENT})
        self.sleep = sleep
        self.requests = 0

    def get(self, path: str, **params: Any) -> dict[str, Any] | None:
        for attempt in range(3):
            self.requests += 1
            resp = self.http.get(API + path, params=params)
            self.sleep(PAUSE)
            if resp.status_code == 429:  # over the rate limit: wait for the five-minute window to move on
                self.sleep(60 * (attempt + 1))
                continue
            if resp.status_code == 404:
                return None
            if resp.status_code in (401, 403):
                raise PermissionError(f"Companies House refused the API key ({resp.status_code}: "
                                      f"{resp.text[:200].strip()}); run `hansard directorships --check`")
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("Companies House rate limit")

    def close(self) -> None:
        if self.own:
            self.http.close()


SANDBOX = "https://api-sandbox.company-information.service.gov.uk"


def check_key(key: str | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    """Why a key is or isn't accepted, without revealing it: its shape, and one request each to the live service
    and to the test ("sandbox") service, which only accepts keys made for a Test application."""
    key = key if key is not None else os.environ.get(KEY_ENV)
    if not key:
        return {"ok": False, "problem": f"no key: {KEY_ENV} is not set inside the app (see docs/HOSTING.md)"}
    raw = key
    key = key.strip()
    out: dict[str, Any] = {
        "key_length": len(key),
        "looks_like_a_rest_key": bool(re.fullmatch(r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", key)),
        "spaces_or_quotes": raw != key or any(c in key for c in " \t\"'"),
    }
    http = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
    try:
        for name, base in (("live", API), ("test", SANDBOX)):
            try:
                resp = http.get(base + "/search/companies", params={"q": "tesco", "items_per_page": 1},
                                auth=(key, ""))
                out[name] = resp.status_code
                if resp.status_code != 200:
                    out[f"{name}_says"] = resp.text[:200].strip()
            except httpx.HTTPError as exc:
                out[name] = f"not reachable: {exc}"[:200]
    finally:
        if client is None:
            http.close()
    live, test = out.get("live"), out.get("test")
    out["ok"] = live == 200
    if live == 200:
        out["verdict"] = "The key works."
    elif test == 200:
        out["verdict"] = ("The key was made for a Test application: it works only on the test service. Make a new "
                          "application with Environment: Live, make a REST key in it, and put that on the server.")
    elif isinstance(live, str):
        out["verdict"] = "The server cannot reach Companies House: check its network."
    elif out["spaces_or_quotes"] or not out["looks_like_a_rest_key"]:
        out["verdict"] = ("The key does not look like a Companies House REST key (36 letters, digits and dashes): "
                          "it may have been cut short or pasted with extra characters, or be a key of another type "
                          "(choose REST, not Stream). Copy it again and put it on the server again.")
    else:
        out["verdict"] = ("Companies House refuses the key. If it was made in the last few minutes, wait ten minutes "
                          "and try again. Otherwise check, in the application on the Companies House developer site, "
                          "that the key type is REST and that 'Restricted IPs' is empty (or lists this server's IP "
                          "address), or make a new key.")
    return out


def _appointment(a: dict[str, Any], spans: list[tuple[str, str]]) -> dict[str, Any]:
    to = a.get("appointed_to") or {}
    start = a.get("appointed_on") or a.get("appointed_before")
    end = a.get("resigned_on")
    during = any((start or "0001") <= s_end and s_start <= (end or "9999-12-31") for s_start, s_end in spans)
    return {"company": to.get("company_name"), "number": to.get("company_number"),
            "status": STATUSES.get(to.get("company_status") or "", to.get("company_status")),
            "role": ROLES.get(a.get("officer_role") or "", (a.get("officer_role") or "").replace("-", " ")),
            "from": start, "from_before": bool(a.get("appointed_before") and not a.get("appointed_on")),
            "until": end, "while_member": during}


def officers_for(api: Client, m: dict[str, Any], excluded: set[str]) -> list[dict[str, Any]]:
    """The officer records that pass the checks above, with their appointments."""
    candidates: dict[str, str] = {}
    for q in _queries(m):
        for page in range(SEARCH_PAGES):
            data = api.get("/search/officers", q=q, items_per_page=100, start_index=page * 100)
            items = (data or {}).get("items") or []
            for item in items:
                oid = _officer_id(item)
                if oid and oid not in excluded and _born_matches(item, m["born"]):
                    candidates.setdefault(oid, item.get("title") or "")
            if len(items) < 100 or page * 100 + len(items) >= int((data or {}).get("total_results") or 0):
                break
    found = []
    for oid in candidates:
        apps: list[dict[str, Any]] = []
        elements: dict[str, Any] | None = None
        start = 0
        while True:
            data = api.get(f"/officers/{oid}/appointments", items_per_page=50, start_index=start)
            if not data or data.get("is_corporate_officer"):
                break
            if (data.get("date_of_birth") or {}) and not _born_matches(data, m["born"]):
                break
            items = data.get("items") or []
            for a in items:
                elements = elements or a.get("name_elements")
                apps.append(a)
            start += len(items)
            if not items or start >= int(data.get("total_results") or 0) or start >= 500:
                break
        if apps and elements and name_matches(elements, m):
            name = apps[0].get("name")
            found.append({"officer_id": oid, "name": name if isinstance(name, str) and name else candidates[oid],
                          "url": f"{SITE}/officers/{oid}/appointments",
                          "appointments": [_appointment(a, m["spans"]) for a in apps]})
    return found


def load_excluded(path: Path | None = None) -> dict[str, set[str]]:
    """{person_id: {officer_id}} of officer records wrongly matched (person_id may be '*': never match)."""
    out: dict[str, set[str]] = defaultdict(set)
    try:
        with open(path or EXCLUDE_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(r for r in f if not r.startswith("#")):
                if row.get("officer_id"):
                    out[(row.get("person_id") or "*").strip()].add(row["officer_id"].strip())
    except OSError:
        pass
    return out


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {"checked": {}, "people": {}}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def refresh(path: Path, reference_dir: Path, people_json: Path, *, per_run: int = PER_RUN,
            client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
            key: str | None = None, exclude_csv: Path | None = None) -> dict[str, Any]:
    """Look up the next ``per_run`` members: never-checked current members first, then other never-checked
    members, then those checked longest ago (once four weeks have passed)."""
    key = key or api_key()
    if not key and client is None:
        return {"skipped": f"no Companies House API key ({KEY_ENV})"}
    path = Path(path)
    data = _load(path)
    checked: dict[str, str] = data.setdefault("checked", {})
    people: dict[str, Any] = data.setdefault("people", {})
    everyone = members(reference_dir, people_json)
    today = datetime.now(UTC).date()
    never = sorted((m for m in everyone if m["person_id"] not in checked), key=lambda m: (not m["current"], m["name"]))
    due = sorted((m for m in everyone if m["person_id"] in checked
                  and (today - date.fromisoformat(checked[m["person_id"]])).days >= RECHECK_DAYS),
                 key=lambda m: checked[m["person_id"]])
    batch = (never + due)[:per_run]
    excluded = load_excluded(exclude_csv)
    api = Client(key or "", client, sleep)
    stats = {"checked": 0, "with_directorships": 0, "failed": 0}
    try:
        for i, m in enumerate(batch, 1):
            pid = m["person_id"]
            try:
                found = officers_for(api, m, excluded.get(pid, set()) | excluded.get("*", set()))
            except PermissionError:
                raise
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                log.warning("directorships: %s not checked: %s", m["name"], exc)
                stats["failed"] += 1
                if isinstance(exc, RuntimeError):
                    break
                continue
            checked[pid] = today.isoformat()
            stats["checked"] += 1
            if found:
                people[pid] = {"name": m["name"], "current": m["current"], "officers": found, "checked": today.isoformat()}
                stats["with_directorships"] += 1
            else:
                people.pop(pid, None)
            if i % 50 == 0:
                _save(path, data)
    finally:
        api.close()
    # an officer record excluded since it was matched is dropped at once, not at the next check
    for pid, rec in list(people.items()):
        rec["officers"] = [o for o in rec["officers"]
                           if o["officer_id"] not in excluded.get(pid, set()) | excluded.get("*", set())]
        if not rec["officers"]:
            people.pop(pid)
    data["last_run"] = datetime.now(UTC).isoformat(timespec="seconds")
    _save(path, data)
    stats.update({"requests": api.requests, "members_with_directorships": len(people),
                  "still_unchecked": sum(1 for m in everyone if m["person_id"] not in checked),
                  "members_looked_up": len(everyone)})
    return stats


# ---------------------------------------------------------------------------------------------- reading

_SUFFIX = {"the", "limited", "ltd", "plc", "llp", "lp", "company", "co", "uk", "cic", "group", "holdings", "and"}


def mark_declared(found: dict[str, Any] | None, register_text: str) -> dict[str, Any] | None:
    """Mark each company also named in the member's register of interests (``register_text``: its text)."""
    if not found:
        return found
    blob = f" {_key(register_text)} "
    for a in found["appointments"]:
        core = " ".join(w for w in _key(a["company"]).split() if w not in _SUFFIX)
        a["declared"] = len(core) >= 4 and f" {core} " in blob
    return found


class Directorships:
    """Reads data/directorships.json, reloading it when it changes."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._data: dict[str, Any] = {}

    def _get(self) -> dict[str, Any]:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return {}
        with self._lock:
            if mtime != self._mtime:
                self._data, self._mtime = _load(self.path), mtime
            return self._data

    def for_person(self, person_id: str) -> dict[str, Any] | None:
        data = self._get()
        rec = (data.get("people") or {}).get(person_id)
        if not rec:
            checked = (data.get("checked") or {}).get(person_id)
            return {"appointments": [], "officers": [], "checked": checked, "source": SOURCE} if checked else None
        apps = []
        seen = set()
        for o in rec["officers"]:
            for a in o["appointments"]:
                k = (a["number"], a["role"], a["from"])
                if k in seen:
                    continue
                seen.add(k)
                apps.append({**a, "company_url": f"{SITE}/company/{a['number']}" if a["number"] else None,
                             "current": not ended(a)})
        apps.sort(key=lambda a: (not a["current"], -(int((a["until"] or a["from"] or "0")[:4]) or 0),
                                 a["company"] or ""))
        return {"appointments": apps, "checked": rec["checked"], "source": SOURCE,
                "officers": [{"name": o["name"], "url": o["url"]} for o in rec["officers"]]}

    def index(self, q: str = "") -> dict[str, Any]:
        """Members' current appointments, by company (most members first); ``q`` filters by company or member."""
        from hansard.people import person_number, slug

        data = self._get()
        companies: dict[str, dict[str, Any]] = {}
        qk = _key(q)
        for pid, rec in (data.get("people") or {}).items():
            who = {"name": rec.get("name"), "number": person_number(pid), "slug": slug(rec.get("name") or ""),
                   "current": bool(rec.get("current"))}
            for o in rec["officers"]:
                for a in o["appointments"]:
                    if ended(a) or not a["number"]:
                        continue
                    if qk and qk not in _key(a["company"]) and qk not in _key(who["name"]):
                        continue
                    c = companies.setdefault(a["number"], {
                        "company": a["company"], "number": a["number"], "status": a["status"],
                        "url": f"{SITE}/company/{a['number']}", "members": []})
                    if not any(x["number"] == who["number"] and x["role"] == a["role"] for x in c["members"]):
                        c["members"].append({**who, "role": a["role"], "from": a["from"]})
        rows = sorted(companies.values(), key=lambda c: (-len(c["members"]), c["company"] or ""))
        return {"companies": rows[:500], "total_companies": len(rows),
                "members_checked": len(data.get("checked") or {}),
                "members_with_directorships": len(data.get("people") or {}),
                "last_run": data.get("last_run"), "source": SOURCE}
