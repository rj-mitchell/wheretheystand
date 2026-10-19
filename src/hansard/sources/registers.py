"""Registers of interests beyond the House of Commons: the House of Lords, the Scottish Parliament, the Senedd and
the Northern Ireland Assembly.

* **Lords.** Not in TheyWorkForYou's files, so each sitting peer's entries are read from the UK Parliament Members
  API (matched by their UK Parliament ID), one polite request at a time, weekly. Each entry carries the date it was
  registered; entries that disappear between weekly reads are remembered with the date they were last seen, so a
  peer's page can show what has changed over the past year.
* **Devolved parliaments.** TheyWorkForYou publishes each register in the same format as the Commons one, matched
  to members by ParlParse ID; read with :mod:`hansard.sources.regmem`, with the edition from a year earlier kept to
  show what has changed.

A registered interest is a declaration, not a finding of wrongdoing. Addresses are left out as for the Commons.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from hansard.sources import regmem
from hansard.sources.demographics import USER_AGENT

log = logging.getLogger(__name__)

MEMBERS_API = "https://members-api.parliament.uk/api/Members/{mnis}/RegisteredInterests"
TWFY = "https://www.theyworkforyou.com/pwdata/scrapedxml/"
REFRESH = timedelta(days=7)
PAUSE = 0.4

REGISTERS: dict[str, dict[str, str]] = {
    "lords": {"name": "Register of Lords' Interests", "member": "peer", "body": "House of Lords",
              "official": "https://members.parliament.uk/members/lords/interests/register-of-lords-interests",
              "file": "regmem_lords.json"},
    "scottish-parliament": {"name": "Register of Interests of Members of the Scottish Parliament", "member": "MSP",
                            "body": "Scottish Parliament", "base": TWFY + "regmem-scotparl/",
                            "official": "https://www.parliament.scot/msps/register-of-interests",
                            "file": "regmem_scottish-parliament.json"},
    "senedd": {"name": "Senedd Register of Members' Interests", "member": "MS", "body": "Senedd",
               "base": TWFY + "regmem-senedd-en/", "official": "https://senedd.wales/",
               "file": "regmem_senedd.json"},
    "ni-assembly": {"name": "Northern Ireland Assembly Register of Members' Interests", "member": "MLA",
                    "body": "Northern Ireland Assembly", "base": TWFY + "regmem-ni/",
                    "official": "https://www.niassembly.gov.uk/your-mlas/register-of-interests/",
                    "file": "regmem_ni-assembly.json"},
}
_NOTHING = re.compile(r"^\s*no (registrable|registered|relevant)? ?interests?\.?\s*$", re.I)
_CATEGORY = re.compile(r"^\s*(?:Category\s+)?(\d+[A-Za-z]?)(?:[.:])?\s*(.*)$")


def path_for(data_dir: Path, key: str) -> Path:
    return Path(data_dir) / REGISTERS[key]["file"]


def clean(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Leave out "No registrable interests." placeholders, and categories left empty."""
    out = []
    for c in categories:
        items = [i for i in c["items"] if not _NOTHING.match(i.get("summary") or "")]
        if items:
            out.append({**c, "items": items})
    return out


# --------------------------------------------------------------------------- the Lords


def _when(v: str | None) -> str | None:
    if not v:
        return None
    try:
        d = date.fromisoformat(v[:10])
    except ValueError:
        return None
    return f"{d.day} {d.strftime('%B %Y')}"


def _lords_item(i: dict[str, Any]) -> dict[str, Any]:
    text = " ".join((i.get("interest") or "").split())
    return {"id": i.get("id"), "summary": text, "details": [], "registered": _when(i.get("createdWhen")),
            "registered_on": (i.get("createdWhen") or "")[:10] or None, "published": None,
            "children": [_lords_item(c) for c in i.get("childInterests") or [] if not c.get("deletedWhen")]}


def lords_categories(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The Members API's list (one entry per category per interest) -> categories in the register's order."""
    cats: dict[str, dict[str, Any]] = {}
    order: dict[str, int] = {}
    for c in value or []:
        name = " ".join((c.get("name") or "").split())
        m = _CATEGORY.match(name)
        number, label = (m.group(1), m.group(2) or name) if m else ("", name)
        cat = cats.setdefault(name, {"type": number, "name": label, "items": []})
        order.setdefault(name, c.get("sortOrder") or 0)
        cat["items"] += [_lords_item(i) for i in c.get("interests") or [] if not i.get("deletedWhen")]
    return [cats[k] for k in sorted(cats, key=lambda k: (order[k], k)) if cats[k]["items"]]


def sitting_peers(con: Any, on: date | None = None) -> list[tuple[str, str, str]]:
    """(person id, UK Parliament ID, name) of every peer sitting on ``on`` (DuckDB, reference attached)."""
    on = on or date.today()
    return con.execute("""
        SELECT DISTINCT p.person_id, p.datadotparl_id, p.latest_name FROM ref_memberships m
        JOIN ref_persons p ON p.person_id = m.person_id
        WHERE m.house = 'lords' AND m.start <= ? AND m."end" >= ? AND p.datadotparl_id IS NOT NULL
        ORDER BY p.person_id""", [on, on]).fetchall()


def refresh_lords(path: Path, peers: list[tuple[str, str, str]], *, client: httpx.Client | None = None,
                  force: bool = False, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Read every sitting peer's entries (weekly unless ``force``), keeping what was known for any that fail."""
    path = Path(path)
    old: dict[str, Any] = {}
    if path.exists():
        try:
            old = json.loads(path.read_text())
        except (OSError, ValueError):
            old = {}
    if not force and old.get("fetched") and \
            datetime.now(UTC) - datetime.fromisoformat(old["fetched"]) < REFRESH:
        return {"updated": False, "fetched": old["fetched"]}
    own = client is None
    client = client or httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    members: dict[str, Any] = {}
    failed = 0
    today = date.today().isoformat()
    try:
        for pid, mnis, name in peers:
            try:
                resp = client.get(MEMBERS_API.format(mnis=mnis))
                if resp.status_code == 429:
                    retry = resp.headers.get("Retry-After", "")
                    sleep(min(int(retry) if retry.isdigit() else 60, 300))
                    resp = client.get(MEMBERS_API.format(mnis=mnis))
                resp.raise_for_status()
                members[pid] = {"name": name, "categories": lords_categories(resp.json().get("value") or [])}
            except (httpx.HTTPError, ValueError) as exc:
                failed += 1
                log.warning("Lords interests for %s not read (%s); keeping the last copy", name, exc)
                if pid in (old.get("members") or {}):
                    members[pid] = old["members"][pid]
            sleep(PAUSE)
    finally:
        if own:
            client.close()
    if not members:
        raise ValueError("no peers' interests could be read")
    # remember entries that have gone, with the date they were last seen
    gone = dict(old.get("gone") or {})
    now_ids = {str(i["id"]) for m in members.values() for c in m["categories"] for i in c["items"]}
    for pid, m in (old.get("members") or {}).items():
        if pid not in members:
            continue
        for c in m["categories"]:
            for i in c["items"]:
                if str(i["id"]) not in now_ids:
                    gone[str(i["id"])] = {"person_id": pid, "category": c["name"], "summary": i["summary"],
                                          "last_seen": old.get("fetched", today)[:10]}
    cutoff = (date.today() - timedelta(days=400)).isoformat()
    gone = {k: v for k, v in gone.items() if v["last_seen"] >= cutoff}
    data = {"edition": today, "fetched": datetime.now(UTC).isoformat(timespec="seconds"),
            "source": "UK Parliament Members API", "members": members, "gone": gone,
            "tracked_from": old.get("tracked_from") or today}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)
    return {"updated": True, "peers": len(members), "failed": failed}


def lords_changes(data: dict[str, Any], person_id: str, days: int = 365) -> dict[str, Any] | None:
    m = (data.get("members") or {}).get(person_id)
    if m is None:
        return None
    since = (date.today() - timedelta(days=days)).isoformat()
    added = [{"category": c["name"], "summary": i["summary"]} for c in m["categories"] for i in c["items"]
             if (i.get("registered_on") or "") >= since]
    removed = [{"category": g["category"], "summary": g["summary"]} for g in (data.get("gone") or {}).values()
               if g["person_id"] == person_id and g["last_seen"] >= since]
    return {"since": since, "added": added, "removed": removed, "removed_tracked_from": data.get("tracked_from")}


# --------------------------------------------------------------------------- the devolved registers


def refresh_devolved(data_dir: Path, *, client: httpx.Client | None = None) -> dict[str, Any]:
    out = {}
    for key, cfg in REGISTERS.items():
        if "base" not in cfg:
            continue
        try:
            path = path_for(data_dir, key)
            try:
                before = json.loads(path.read_text()).get("members") or {} if path.exists() else {}
            except (OSError, ValueError):
                before = {}
            stats = regmem.refresh(path, client=client, base=cfg["base"])
            if stats.get("updated") and before:
                # some editions list only some members: keep the others as last published (each has its date)
                data = json.loads(path.read_text())
                kept = {pid: m for pid, m in before.items() if pid not in data["members"]}
                if kept:
                    data["members"].update(kept)
                    stats["kept_from_earlier"] = len(kept)
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(data))
                    tmp.replace(path)
            stats["previous"] = regmem.refresh_previous(path.with_name(path.stem + "_previous.json"),
                                                        stats.get("edition"), client=client, base=cfg["base"])
            out[key] = stats
        except Exception as exc:  # one register failing does not stop the others
            log.warning("%s not updated: %s", cfg["name"], exc)
            out[key] = {"error": str(exc)[:200]}
    return out


# --------------------------------------------------------------------------- reading


class Registers:
    """Every register a person is in, reloaded when the files change."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def _load(self, name: str) -> dict[str, Any]:
        p = self.data_dir / name
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return {}
        hit = self._cache.get(name)
        if hit is None or hit[0] != mtime:
            try:
                self._cache[name] = (mtime, json.loads(p.read_text()))
            except (OSError, ValueError):
                return hit[1] if hit else {}
        return self._cache[name][1]

    def for_person(self, person_id: str) -> list[dict[str, Any]]:
        out = []
        for key, cfg in REGISTERS.items():
            data = self._load(cfg["file"])
            m = (data.get("members") or {}).get(person_id)
            if m is None:
                continue
            entry = {"register": key, "register_name": cfg["name"], "member_word": cfg["member"],
                     "body": cfg["body"], "edition": m.get("date") or data.get("edition"),
                     "source": data.get("source"),
                     "official": cfg["official"], "listed": True, "former": False,
                     "categories": clean(m["categories"])}
            if key == "lords":
                entry["changes"] = lords_changes(data, person_id)
            else:
                prev = self._load(Path(cfg["file"]).stem + "_previous.json")
                was = (prev.get("members") or {}).get(person_id)
                if was is not None and prev.get("edition") != data.get("edition"):
                    entry["changes"] = {"since": prev.get("edition"),
                                        **regmem.changes(entry["categories"], clean(was["categories"]))}
            out.append(entry)
        return out
