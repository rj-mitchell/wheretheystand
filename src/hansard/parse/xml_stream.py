"""Streaming parser for ParlParse/TheyWorkForYou scrapedxml files.

Memory is bounded per file: ``lxml.etree.iterparse`` emits each top-level
element as it closes, the element is converted to a plain row, and then it and
every earlier sibling are freed. No file is ever held as a full DOM tree, so a
worker's footprint stays at a few MB whatever the file size.

One row is produced per intervention:

* ``speech``  (debates, Westminster Hall, Lords, Public Bill Committees, statements)
* ``question`` / ``answer`` (written questions ``<ques>`` and ``<reply>``)

Headings (``oral-heading`` / ``major-heading`` / ``minor-heading``) are carried
onto the rows that follow them. Divisions are parsed by :func:`parse_divisions`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from lxml import etree

from hansard.sources.twfy import SOURCES, parse_name

_WS = re.compile(r"\s+")
_TIME = re.compile(r"(\d{1,2}):(\d{2})(?::\d{2})?$")
_HEADINGS = {"oral-heading", "major-heading", "minor-heading"}

CHAMBER_LABELS = {
    "debates": "House of Commons",
    "westminhall": "Westminster Hall",
    "lordspages": "House of Lords",
    "wrans": "Commons written answers",
    "lordswrans": "Lords written answers",
    "wms": "Commons written statements",
    "lordswms": "Lords written statements",
    "standing": "Public Bill Committee",
}

# TheyWorkForYou permalink sections.
_TWFY_SECTION = {
    "debates": "debates",
    "westminhall": "whall",
    "lordspages": "lords",
    "wrans": "wrans",
    "lordswrans": "wrans",
    "wms": "wms",
    "lordswms": "wms",
    "standing": "pbc",
}


def clean(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


def norm_time(value: str | None) -> str | None:
    """'11:30:00' -> '11:30'; tolerates the malformed '020:43:00' seen in 2012 files."""
    if not value:
        return None
    m = _TIME.search(value.strip())
    if not m:
        return None
    h, mnt = int(m.group(1)) % 24, int(m.group(2))
    return f"{h:02d}:{mnt:02d}"


def element_text(el: etree._Element) -> str:
    """Paragraph-preserving text of an intervention; tables become ' | '-joined rows."""
    parts: list[str] = []
    for child in el:
        tag = child.tag if isinstance(child.tag, str) else ""
        if tag == "table":
            for row in child.iter("tr"):
                cells = [clean("".join(c.itertext())) for c in row if isinstance(c.tag, str)]
                if any(cells):
                    parts.append(" | ".join(cells))
        elif tag in {"ul", "ol"}:
            parts.extend(f"• {clean(''.join(li.itertext()))}" for li in child.iter("li"))
        elif tag:
            t = clean("".join(child.itertext()))
            if t:
                parts.append(t)
    if not parts:  # text directly inside the element
        t = clean("".join(el.itertext()))
        if t:
            parts.append(t)
    return "\n\n".join(parts)


def _free(el: etree._Element) -> None:
    el.clear(keep_tail=False)
    parent = el.getparent()
    if parent is not None:
        while el.getprevious() is not None:
            del parent[0]


def gid_suffix(xml_id: str) -> str:
    """'uk.org.publicwhip/debate/2026-09-15c.1557.4' -> '2026-09-15c.1557.4'."""
    return xml_id.split("/", 2)[-1] if xml_id.count("/") >= 2 else xml_id


def twfy_url(directory: str, xml_id: str, bill: str | None = None, session: str | None = None) -> str:
    section = _TWFY_SECTION.get(directory, "debates")
    suffix = gid_suffix(xml_id)
    if directory == "standing" and bill and session:
        # e.g. /pbc/2026-27/Immigration_and_Asylum_Bill/02-0_2026-09-15b.35.5
        tail = suffix.split("_", 2)[-1] if suffix.startswith("standing") else suffix
        return f"https://www.theyworkforyou.com/pbc/{session}/{bill.replace(' ', '_')}/{tail}"
    return f"https://www.theyworkforyou.com/{section}/?id={suffix}"


def official_url(directory: str, url_attr: str | None, on: date) -> tuple[str | None, str]:
    """(url, kind): the primary Parliament link available for a record.

    kind is 'record' when the source XML links the exact record (older Hansard
    pages, written questions/statements site), else 'sitting' for the
    official Hansard page of that sitting day.
    """
    if url_attr and url_attr.startswith("http"):
        return url_attr, "record"
    house = "Lords" if directory in {"lordspages", "lordswrans", "lordswms"} else "Commons"
    if directory in {"debates", "westminhall", "lordspages", "standing"}:
        return f"https://hansard.parliament.uk/{house}/{on.isoformat()}", "sitting"
    return None, "none"


def parse_file(path: Path, directory: str | None = None) -> Iterator[dict[str, Any]]:
    """Yield one row per intervention in a scrapedxml file."""
    path = Path(path)
    directory = directory or path.parent.name
    meta = parse_name(directory, path.name)
    if meta is None:
        raise ValueError(f"Unrecognised file name: {path.name}")
    house, family = SOURCES[directory]
    oral = major = minor = None
    bill = session = None
    pending_question: dict[str, Any] | None = None
    context = etree.iterparse(str(path), events=("end",), huge_tree=True, recover=True,
                              remove_comments=True, remove_pis=True)
    for _, el in context:
        tag = el.tag if isinstance(el.tag, str) else ""
        parent = el.getparent()
        if parent is None or parent.getparent() is not None:
            continue  # only top-level children of <publicwhip>
        if tag in _HEADINGS:
            text = clean("".join(el.itertext()))
            if tag == "oral-heading":
                oral = text
            elif tag == "major-heading":
                major, minor = text, None
            else:
                minor = text
        elif tag == "bill":
            bill = clean(el.get("title") or "".join(el.itertext()))
            session = el.get("session")
        elif tag in {"speech", "ques", "reply"}:
            row = _row(el, tag, directory, house, family, meta, oral, major, minor, bill, session)
            if row is not None:
                if tag == "ques":
                    pending_question = row
                elif tag == "reply" and pending_question and pending_question["thread_id"] == row["thread_id"]:
                    row["context"] = pending_question["text"]
                    row["official_url"] = row["official_url"] or pending_question["official_url"]
                    row["url_kind"] = pending_question["url_kind"] if not row["official_url_own"] else row["url_kind"]
                    row["uin"] = row["uin"] or pending_question["uin"]
                row.pop("official_url_own", None)
                yield row
        _free(el)


def _row(el, tag, directory, house, family, meta, oral, major, minor, bill, session) -> dict[str, Any] | None:
    xml_id = el.get("id")
    if not xml_id:
        return None
    text = element_text(el)
    if not text:
        return None
    nospeaker = el.get("nospeaker") == "true"
    xml_person = None if nospeaker else (el.get("person_id") or el.get("speakerid"))
    if xml_person in {"unknown", ""}:
        xml_person = None
    record_type = {"ques": "question", "reply": "answer"}.get(tag)
    if record_type is None:
        if family == "written_statement":
            record_type = "statement"
        elif xml_person is None:
            record_type = "procedural"
        else:
            record_type = "speech"
    url_attr = el.get("url")
    off_url, url_kind = official_url(directory, url_attr, meta.file_date)
    uin = None
    if tag in {"ques", "reply"}:
        first_p = el.find("p")
        uin = first_p.get("qnum") if first_p is not None else None
    elif family == "written_statement":
        uin = gid_suffix(xml_id).split(".")[1] if "." in gid_suffix(xml_id) else None
    else:
        uin = el.get("oral-qnum")
    thread_id = re.sub(r"\.[qr]\d+$", "", xml_id) if tag in {"ques", "reply"} else None
    return {
        "record_id": xml_id,
        "source": directory,
        "source_key": meta.key,
        "revision": meta.revision,
        "house": house,
        "chamber": CHAMBER_LABELS[directory],
        "record_type": record_type,
        "speech_type": el.get("type"),
        "date": meta.file_date,
        "time": norm_time(el.get("time")),
        "colnum": el.get("colnum"),
        "oral_heading": oral,
        "major_heading": major,
        "minor_heading": minor,
        "bill": bill,
        "bill_session": session,
        "xml_person_id": xml_person,
        "speaker_name_raw": clean(el.get("speakername")) or None,
        "uin": uin,
        "thread_id": thread_id,
        "text": text,
        "context": None,
        "official_url": off_url,
        "official_url_own": bool(url_attr and url_attr.startswith("http")),
        "url_kind": url_kind,
        "twfy_url": twfy_url(directory, xml_id, bill, session),
    }


# --------------------------------------------------------------------------- divisions


def parse_divisions(path: Path, directory: str | None = None) -> tuple[list[dict], list[dict]]:
    """Parse a divisions-only file into (divisions, votes)."""
    path = Path(path)
    directory = directory or path.parent.name
    meta = parse_name(directory, path.name)
    if meta is None:
        raise ValueError(f"Unrecognised file name: {path.name}")
    house = SOURCES[directory][0]
    divisions: list[dict] = []
    votes: list[dict] = []
    major = minor = None
    for _, el in etree.iterparse(str(path), events=("end",), huge_tree=True, recover=True,
                                 remove_comments=True, remove_pis=True):
        tag = el.tag if isinstance(el.tag, str) else ""
        parent = el.getparent()
        if parent is None or parent.getparent() is not None:
            continue
        if tag == "major-heading":
            major, minor = clean("".join(el.itertext())), None
        elif tag == "minor-heading":
            minor = clean("".join(el.itertext()))
        elif tag == "division":
            div_id = el.get("id")
            count = el.find("divisioncount")
            counts = dict(count.attrib) if count is not None else {}
            divisions.append({
                "division_id": div_id,
                "source_key": meta.key,
                "house": house,
                "date": date.fromisoformat(el.get("divdate")) if el.get("divdate") else meta.file_date,
                "number": int(el.get("divnumber") or 0),
                "time": norm_time(el.get("time")),
                "colnum": el.get("colnum"),
                "major_heading": major,
                "minor_heading": minor,
                "ayes": _count(counts.get("ayes", counts.get("content"))),
                "noes": _count(counts.get("noes", counts.get("not-content"))),
                "official_url": official_url("debates" if house == "commons" else "lordspages",
                                             el.get("url"), meta.file_date)[0],
                "twfy_url": twfy_url("debates" if house == "commons" else "lordspages", div_id),
            })
            div_votes_start = len(votes)
            for voter in el.iter("mpname", "lord"):
                pid = voter.get("person_id") or voter.get("id")
                if not pid:
                    continue
                raw_vote = voter.get("vote") or ""
                votes.append({
                    "division_id": div_id,
                    "date": divisions[-1]["date"],
                    "house": house,
                    "xml_person_id": pid,
                    "vote": {"content": "aye", "not-content": "no"}.get(raw_vote, raw_vote),
                    "teller": voter.get("teller") == "yes",
                    "proxy_person_id": voter.get("proxy"),
                })
            # A few files carry malformed counts (e.g. noes="21\n14", or two deferred
            # divisions run together as ayes="538482"); fall back to the vote lists.
            for side, key in (("aye", "ayes"), ("no", "noes")):
                if divisions[-1][key] is None or divisions[-1][key] > MAX_PLAUSIBLE_VOTES:
                    divisions[-1][key] = sum(1 for v in votes[div_votes_start:]
                                             if v["vote"] == side and not v["teller"])
        _free(el)
    return divisions, votes


MAX_PLAUSIBLE_VOTES = 1000  # more than either House's membership


def _count(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value.isdigit() else None
