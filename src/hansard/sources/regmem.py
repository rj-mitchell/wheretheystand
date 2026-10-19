"""The Register of Members' Financial Interests, from TheyWorkForYou's copy of each edition (Open Parliament Licence).

MPs must register jobs and earnings, donations, gifts and hospitality, visits abroad, land and property,
shareholdings and some other interests. Each edition of the register is published as XML by TheyWorkForYou
(``pwdata/scrapedxml/regmem``), with every member identified by their parlparse person ID, so no matching by name
is needed. The site keeps the latest edition and shows each current MP's entries on their page, as registered.

Addresses are left out: the register publishes donors' addresses, but repeating private individuals' addresses adds
nothing to understanding a member's interests.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from lxml import etree

from hansard.sources.demographics import USER_AGENT

log = logging.getLogger(__name__)

BASE = "https://www.theyworkforyou.com/pwdata/scrapedxml/regmem/"
OFFICIAL = "https://www.parliament.uk/mps-lords-and-offices/standards-and-financial-interests/parliamentary-commissioner-for-standards/registers-of-interests/register-of-members-financial-interests/"
_FILE = re.compile(r'href="(regmem(\d{4}-\d{2}-\d{2})\.xml)"')
_SKIP = re.compile(r"address|identifier source|^is until further notice$", re.I)


def _text(el: etree._Element | None) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def _item(div: etree._Element) -> dict[str, Any]:
    summary = div.find("./h4") if div.find("./h4") is not None else div.find("./h6")
    ul = div.find("./ul")
    details, registered, published = [], None, None
    for li in (ul if ul is not None else []):
        cls = li.get("class") or ""
        if cls == "interest-detail":
            name = _text(li.find("./span[@class='interest-detail-name']")).rstrip(": ")
            value = _text(li.find("./span[@class='interest-detail-value']"))
            if name and value and not _SKIP.search(name) and value != _text(summary) \
                    and value not in (v for _, v in details):  # "Donor Company Name" often repeats "Donor Name"
                details.append([name, value])
        elif cls == "registration-date":
            registered = _text(li).split(":", 1)[-1].strip()
        elif cls == "published-date":
            published = _text(li).split(":", 1)[-1].strip()
    children = [_item(c) for c in div.findall("./div[@class='interest-child-items']/div[@class='interest-item']")]
    return {"summary": _text(summary), "details": details, "registered": registered, "published": published,
            "children": children}


_REGISTERED = re.compile(r"\s*\(Registered ([^)]+)\)\s*$")


def _plain_item(item: etree._Element) -> dict[str, Any]:
    """An entry from an older edition: a first line, then "Label: value" lines (addresses left out)."""
    lines = [" ".join((item.text or "").split())]
    for br in item.findall("./br"):
        lines += [" ".join("".join(br.itertext()).split()), " ".join((br.tail or "").split())]
    lines = [x for x in lines if x]
    summary, details = (lines[0] if lines else ""), []
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep and len(name) < 60 and value.strip():
            if not _SKIP.search(name):
                details.append([name.strip(), value.strip()])
        else:
            summary = f"{summary} {line}".strip()
    if summary.lower().startswith("address") and details:  # the first line was itself an address
        summary = details.pop(0)[1]
    registered = None
    m = _REGISTERED.search(summary)
    if m:
        summary, registered = summary[:m.start()].strip(), m.group(1)
    return {"summary": summary, "details": details, "registered": registered, "published": None, "children": []}


_REGISTERED_LINE = re.compile(r"^\(Registered ([^)]+)\)$")
_LABEL = re.compile(r"^([^:]{2,60}):\s*(.+)$")
_NAMES = re.compile(r"name|donor|company|organisation|employer|payer|client|source", re.I)


def _loose_items(lines: list[str]) -> list[dict[str, Any]]:
    """Entries from the oldest editions (to about 2015), where each line is an <item> of its own: a sentence is an
    entry; "Label: value" lines belong to the entry before them; "(Registered 21 July 2013)" ends one."""
    entries: list[list[str]] = []
    registered: list[str | None] = []
    current: list[str] | None = None
    for line in (" ".join(x.split()) for x in lines):
        if not line:
            continue
        m = _REGISTERED_LINE.match(line)
        if m:
            if current:
                registered[-1] = m.group(1)
                current = None
            continue
        if current is None or not _LABEL.match(line):
            current = [line]
            entries.append(current)
            registered.append(None)
        else:
            current.append(line)
        tail = _REGISTERED.search(line)
        if tail:  # "House in Kent. (Registered 3 May 2005)"
            current[-1] = line[:tail.start()].strip()
            registered[-1] = tail.group(1)
            current = None
    out = []
    for lines_, when in zip(entries, registered, strict=True):
        summary, details = lines_[0], []
        for line in lines_[1:]:
            name, value = _LABEL.match(line).groups()
            if not _SKIP.search(name):
                details.append([name.strip(), value.strip()])
        first = _LABEL.match(summary)
        if first and not _SKIP.search(first.group(1)):  # an entry that starts with a label: name it by its value
            details.insert(0, [first.group(1).strip(), first.group(2).strip()])
            summary = first.group(2).strip()
        elif summary.endswith(":") and details:  # "Support in the capacity as an MP:" then the donor's details
            named = next((v for k, v in details if _NAMES.search(k)), details[0][1])
            details.insert(0, ["Kind", summary.rstrip(":")])
            summary = named
        if summary:
            out.append({"summary": summary, "details": details, "registered": when, "published": None,
                        "children": []})
    return out


def _category_key(t: str) -> tuple[float, str]:
    try:
        return float(t), t
    except ValueError:
        return 99.0, t


def parse(xml: bytes) -> dict[str, Any]:
    """One edition: {"edition": date, "members": {person_id: {"name", "categories": [...]}}}."""
    root = etree.fromstring(xml, parser=etree.XMLParser(recover=True, huge_tree=True))
    members: dict[str, Any] = {}
    edition = None
    for rm in root.iter("regmem"):
        pid = rm.get("personid")
        if not pid:
            continue
        edition = edition or rm.get("date")
        cats = []
        for cat in rm.findall("./category"):
            items = []
            if cat.find("./record") is None:  # the oldest editions: lines straight under the category
                items = _loose_items([_text(i) for i in cat.findall("./item")])
            for rec in cat.findall("./record"):
                for item in rec.findall("./item"):
                    for div in item.findall("./div[@class='interest-item']"):
                        items.append(_item(div))
                    if item.find("./div[@class='interest-item']") is None and _text(item):
                        items.append(_plain_item(item))  # older editions: plain text, lines split by <br>
            if items:
                cats.append({"type": cat.get("type") or "", "name": cat.get("name") or "", "items": items})
        cats.sort(key=lambda c: _category_key(c["type"]))
        members[pid] = {"name": rm.get("membername"), "categories": cats, "date": rm.get("date")}
    return {"edition": edition, "members": members}


def latest_edition(client: httpx.Client, base: str = BASE) -> tuple[str, str] | None:
    """(file name, date) of the newest edition listed."""
    resp = client.get(base)
    resp.raise_for_status()
    found = sorted(_FILE.findall(resp.text), key=lambda x: x[1])
    return found[-1] if found else None


def refresh(path: Path, client: httpx.Client | None = None, base: str = BASE) -> dict[str, Any]:
    """Fetch the newest edition if it is newer than the copy on disk. Keeps the old copy on any failure."""
    path = Path(path)
    current = None
    if path.exists():
        try:
            current = json.loads(path.read_text()).get("edition")
        except (OSError, ValueError):
            current = None
    own = client is None
    client = client or httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    try:
        latest = latest_edition(client, base)
        if latest is None:
            return {"edition": current, "updated": False, "error": "no editions listed"}
        name, day = latest
        if current == day:
            return {"edition": day, "updated": False}
        resp = client.get(base + name)
        resp.raise_for_status()
        data = parse(resp.content)
        if not data["members"]:
            return {"edition": current, "updated": False, "error": f"{name}: no members found"}
        data["edition"] = day
        data["fetched"] = datetime.now(UTC).isoformat(timespec="seconds")
        data["source"] = base + name
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
        return {"edition": day, "updated": True, "members": len(data["members"])}
    finally:
        if own:
            client.close()


def editions(client: httpx.Client, base: str = BASE) -> list[tuple[str, str]]:
    """Every edition listed, oldest first: (file name, date)."""
    resp = client.get(base)
    resp.raise_for_status()
    return sorted(set(_FILE.findall(resp.text)), key=lambda x: x[1])


def former_members(con: Any) -> dict[str, str]:
    """Commons members since 2010 who are no longer MPs, with the date they left (DuckDB, reference attached)."""
    rows = con.execute("""
        SELECT person_id, max("end") AS left_on FROM ref_memberships WHERE house = 'commons' GROUP BY person_id
        HAVING max("end") >= DATE '2010-05-06' AND max("end") < DATE '9000-01-01'""").fetchall()
    return {pid: d.isoformat() for pid, d in rows}


def refresh_history(path: Path, leavers: dict[str, str], *, client: httpx.Client | None = None,
                    max_editions: int = 15, sleep=time.sleep) -> dict[str, Any]:
    """For each former MP, their entries in the last edition published before they left (kept in ``path``).

    Spread over runs: at most ``max_editions`` old editions are downloaded each time. A member missing from the
    chosen edition is tried in the one before, up to three times."""
    path = Path(path)
    try:
        history: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        history = {}
    todo = {pid: left for pid, left in leavers.items()
            if (history.get(pid) or {}).get("left") != left or (history.get(pid) or {}).get("retry")}
    if not todo:
        return {"former_members": len(history), "editions_fetched": 0}
    own = client is None
    client = client or httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    fetched = 0
    try:
        listed = editions(client)
        dates = [d for _, d in listed]
        names = dict((d, n) for n, d in listed)

        def choose(left: str, skip: int) -> str | None:
            before = [d for d in dates if d <= left]
            return before[-1 - skip] if len(before) > skip else None

        wanted: dict[str, list[str]] = {}
        for pid, left in todo.items():
            ed = choose(left, (history.get(pid) or {}).get("tries", 0))
            if ed:
                wanted.setdefault(ed, []).append(pid)
        for ed in sorted(wanted, reverse=True)[:max_editions]:
            resp = client.get(BASE + names[ed])
            resp.raise_for_status()
            members = parse(resp.content)["members"]
            fetched += 1
            for pid in wanted[ed]:
                tries = (history.get(pid) or {}).get("tries", 0) + 1
                if pid in members:
                    history[pid] = {"left": todo[pid], "edition": ed, "categories": members[pid]["categories"]}
                else:  # not in that edition (left just before it was compiled?): try the one before next time
                    history[pid] = {"left": todo[pid], "edition": None, "categories": [], "tries": tries,
                                    "retry": tries < 3}
            sleep(1.0)
    finally:
        if own:
            client.close()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(history))
    tmp.replace(path)
    waiting = sum(1 for pid in leavers if (history.get(pid) or {}).get("left") != leavers[pid]
                  or (history.get(pid) or {}).get("retry"))
    return {"former_members": sum(1 for h in history.values() if h.get("edition")), "editions_fetched": fetched,
            "still_to_fetch": waiting}


def refresh_previous(path: Path, latest: str | None, *, client: httpx.Client | None = None,
                     days: int = 365, base: str = BASE) -> dict[str, Any]:
    """Keep the edition from about a year before ``latest`` (the newest published on or before that day), so a
    member's page can show what is new in the register and what is no longer listed."""
    path = Path(path)
    if not latest:
        return {"edition": None, "updated": False}
    target = (date.fromisoformat(latest) - timedelta(days=days)).isoformat()
    current = None
    if path.exists():
        try:
            current = json.loads(path.read_text()).get("edition")
        except (OSError, ValueError):
            current = None
    own = client is None
    client = client or httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    try:
        before = [(n, d) for n, d in editions(client, base) if d <= target]
        if not before:
            return {"edition": current, "updated": False}
        name, day = before[-1]
        if current == day:
            return {"edition": day, "updated": False}
        resp = client.get(base + name)
        resp.raise_for_status()
        data = parse(resp.content)
        if not data["members"]:
            return {"edition": current, "updated": False, "error": f"{name}: no members found"}
        data["edition"] = day
    finally:
        if own:
            client.close()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)
    return {"edition": day, "updated": True}


def _entries(categories: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    return {(c["type"], i["summary"]): c["name"] for c in categories for i in c["items"] if i.get("summary")}


def changes(now: list[dict[str, Any]], before: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """Entries listed now but not in the earlier edition, and the reverse, compared by category and wording."""
    a, b = _entries(now), _entries(before)
    return {"added": [{"category": a[k], "summary": k[1]} for k in a if k not in b],
            "removed": [{"category": b[k], "summary": k[1]} for k in b if k not in a]}


class Register:
    """The latest edition on disk, reloaded when the file changes."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, Any] | None = None
        self._mtime: float | None = None
        self._sides: dict[str, tuple[float, dict[str, Any]]] = {}

    def _load(self) -> dict[str, Any]:
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime
            except OSError:
                return {}
            if self._data is None or mtime != self._mtime:
                try:
                    self._data, self._mtime = json.loads(self.path.read_text()), mtime
                except (OSError, ValueError):
                    return self._data or {}
            return self._data

    def _side(self, name: str) -> dict[str, Any]:
        """A companion file (former members, or the edition a year back), reloaded when it changes."""
        spath = self.path.with_name(name)
        with self._lock:
            try:
                mtime = spath.stat().st_mtime
            except OSError:
                return {}
            cached = self._sides.get(name)
            if cached is None or cached[0] != mtime:
                try:
                    self._sides[name] = (mtime, json.loads(spath.read_text()))
                except (OSError, ValueError):
                    return cached[1] if cached else {}
            return self._sides[name][1]

    def _history(self) -> dict[str, Any]:
        return self._side("regmem_history.json")

    def _archive(self):
        """Every edition since 2000 (hansard.sources.regmem_archive), once it has been built."""
        apath = self.path.with_name("regmem_archive.sqlite")
        if not apath.exists():
            return None
        with self._lock:
            if getattr(self, "_arch", None) is None:
                from hansard.sources.regmem_archive import RegisterArchive

                self._arch = RegisterArchive(apath)
            return self._arch

    def categories_on(self, person_id: str, on) -> list[dict[str, Any]] | None:
        """The entries a member had registered on a given day, from the archive of editions (None without it)."""
        archive = self._archive()
        return archive.categories_on(person_id, on) if archive else None

    def _previous(self) -> dict[str, Any]:
        return self._side("regmem_previous.json")

    def for_person(self, person_id: str) -> dict[str, Any] | None:
        data = self._load()
        m = (data.get("members") or {}).get(person_id)
        if m is None:  # a former MP: their entries in the last edition before they left
            h = self._history().get(person_id)
            if h and h.get("edition"):
                return {"edition": h["edition"], "source": BASE, "official": OFFICIAL, "former": True,
                        "categories": h["categories"], "listed": True}
            archive = self._archive()  # one who left before 2010: the last edition that listed them
            last = archive.last_listed(person_id) if archive else None
            if last:
                return {"edition": last["edition"], "source": BASE, "official": OFFICIAL, "former": True,
                        "categories": last["categories"], "listed": True}
        if not data:
            return None
        out = {"edition": data.get("edition"), "source": data.get("source"), "official": OFFICIAL,
               "categories": (m or {}).get("categories", []), "listed": m is not None, "former": False}
        earlier = self._previous()
        was = (earlier.get("members") or {}).get(person_id)
        if m is not None and was is not None and earlier.get("edition") != data.get("edition"):
            out["changes"] = {"since": earlier.get("edition"), **changes(m["categories"], was["categories"])}
        return out
