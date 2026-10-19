"""What each division (vote) was on, and what voting either way usually means.

A division's title often names only the bill or debate, so a vote can look like a vote on a whole policy when it
was on an amendment, a timetable or a motion to decline a bill. The record says more: the motion put to the House
just before the division ("Question put, That the Bill be now read a Second time") and the outcome just after
("Question accordingly negatived. Bill read a Second time."). Each division is classified from that wording, and
each kind of vote has a short, neutral note on what voting for or against it usually means.

Built from the stored record (no AI), cached on disk and rebuilt when the data changes.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

import duckdb

from hansard.config import Settings
from hansard.store.parquet import attach_store
from hansard.store.shared import shared

log = logging.getLogger(__name__)
RULES = 2  # raise when classify() changes, so the cached labels are rebuilt

# kind: (label, note on what a vote of this kind usually means)
KINDS: dict[str, tuple[str, str]] = {
    "second_reading": ("Second reading", "A vote on the general principles of the bill. The details can still be "
                       "changed at later stages."),
    "reasoned_amendment": ("Amendment to decline the bill a second reading", "An amendment giving reasons not to "
                           "pass the bill at this stage. Voting for it is a vote to stop the bill; voting against it "
                           "lets the bill continue, which is not necessarily support for all of it."),
    "third_reading": ("Third reading", "The final vote on the bill as amended, before it goes to the other House."),
    "new_clause": ("New clause", "A proposal to add a new section to the bill. Voting against may reflect the "
                   "wording, cost or timing as much as the aim."),
    "amendment": ("Amendment", "A proposal to change the wording of a bill or motion. Voting against an amendment "
                  "is not necessarily opposition to its aim: it may be rejected as unnecessary, badly drafted or "
                  "covered elsewhere."),
    "stand_part": ("Whether a clause stays in the bill", "A vote on keeping a section of the bill as it stands."),
    "lords_amendment": ("Lords amendment", "Whether the Commons accepts a change made by the House of Lords. "
                        "Bills pass back and forth until both Houses agree."),
    "commons_amendment": ("Commons amendment or reason", "Whether the Lords accepts, or insists on its own position "
                          "against, a change made by the Commons."),
    "programme": ("Programme motion", "Sets the timetable for the bill's later stages. A vote against may object to "
                  "the time allowed rather than to the bill."),
    "money": ("Money resolution", "Authorises spending needed for a bill. Usually a formality once the bill has "
              "passed second reading."),
    "ways_and_means": ("Ways and means resolution", "Authorises taxes or charges, usually those in a Budget or a "
                       "bill."),
    "closure": ("Closure motion", "A vote to end the debate and decide the matter now, not on the matter itself."),
    "ten_minute_rule": ("Ten minute rule motion", "Asks permission to introduce a backbench bill. Such bills rarely "
                        "become law; the vote is often about the principle being raised."),
    "statutory_instrument": ("Regulations or order", "Approves or rejects regulations made under an existing Act. "
                             "Parliament can only accept or reject them, not amend them."),
    "regret_motion": ("Regret motion", "Expresses concern about regulations but does not stop them."),
    "opposition_day": ("Opposition day motion", "A motion chosen by an opposition party. Governments often abstain, "
                       "or amend the motion, rather than vote it down; the result is not binding."),
    "estimates": ("Spending estimates", "Approves the spending plans of government departments."),
    "motion": ("Motion", "A proposal for the House to agree a view or take an action."),
    "other": ("Division", "The record does not make clear what kind of vote this was."),
}

_QUESTION = re.compile(r"Question put[^,\n]*,\s*(That\b[^\n]*)|Motion made, and Question put[^,\n]*,\s*(That\b[^\n]*)"
                       r"|^(That\b[^\n]*)", re.I | re.M)
_OUTCOME = re.compile(r"(Question accordingly (?:agreed to|negatived)\.?|(?:Amendment|Motion) [\w ]*?"
                      r"(?:agreed|disagreed|not agreed|withdrawn)\.?|(?:Lords|Commons) amendments? [\w ,]*?"
                      r"(?:agreed|disagreed)(?: to)?\.?)", re.I)
_CONSEQUENCE = re.compile(r"(Bill (?:accordingly )?read (?:a|the) (?:Second|Third) time(?: and passed)?\.?|"
                          r"Lords amendments? [\w ,]*?disagreed to\.?|Clause [\w ]*?(?:added|ordered to stand part)"
                          r"[\w ]*\.?)", re.I)


def _key(record_id: str) -> tuple[str, int, int]:
    prefix, col, n = record_id.rsplit(".", 2)
    return prefix, int(col) if col.isdigit() else 0, int(n) if n.isdigit() else 0


def _clip(text: str, n: int = 260) -> str:
    text = " ".join(text.split()).rstrip(".—-– ")
    text = re.sub(r"\s*—\s*\([^)]*\)$", "", text)  # "—(Greg Hands.)": who moved it
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def classify(before: str, after: str, major: str, minor: str, house: str) -> dict[str, Any]:
    """The kind of vote, the motion voted on (if recorded) and the outcome, from the wording around a division."""
    major, minor = major or "", minor or ""
    # the question is the last one put before the division (Commons), or named in the heading (Lords)
    questions = [next(g for g in m.groups() if g) for m in _QUESTION.finditer(before)]
    question = _clip(questions[-1]) if questions else ""
    q = " ".join(questions[-1].split()).lower() if questions else ""  # the full wording, not the clipped one
    ctx, head = (before + "\n" + after).lower(), f"{major} {minor}".lower()
    out_m = _OUTCOME.search(after) or _OUTCOME.search(before.split("divided")[-1])
    cons = _CONSEQUENCE.search(after)
    outcome = " ".join(x for x in ((out_m.group(1) if out_m else ""), (cons.group(1) if cons else "")) if x)

    after_l = after.lower()
    lords_amendment_head = house == "lords" and (re.search(r"\bamendments? \d", head) or
                                                 re.search(r"division on [^.\n]{0,80}amendment", ctx))
    # the outcome recorded for this division, before any next question is put
    settled = re.split(r"question put", after_l, maxsplit=1)[0]
    if re.search(r"lords amendment|lords in their amendment|lords reason", q) \
            or (house == "commons" and ("lords amendment" in head
                                        or ((not q or "amendment" in q) and "(programme" not in head
                                            and re.search(r"lords amendments? [\w ,()]*?(?:dis)?agreed", settled)))):
        kind = "lords_amendment"
    elif house == "lords" and ("commons amendment" in head or "commons reason" in head or "commons amendment" in ctx):
        kind = "commons_amendment"
    elif house == "lords" and "second reading" in head and ("amendment" in head or lords_amendment_head):
        kind = "reasoned_amendment"  # an amendment to the Lords second reading motion (to decline or delay it)
    elif lords_amendment_head:
        kind = "amendment"
    elif "question be now put" in q and "main question" not in ctx:
        kind = "closure"
    elif "be now read a second time" in q or re.search(r"\bbill be read a second time", q):  # (older wording)
        kind = "second_reading"
    elif "be now read the third time" in q or "be now read a third time" in q \
            or re.search(r"\bbill be read (?:the|a) third time", q) \
            or (not question and "read the third time and passed" in after_l):
        kind = "third_reading"
    elif "leave be given to bring in a bill" in q or "leave be given to bring in a bill" in ctx \
            or "motion for leave to bring in a bill" in ctx:
        kind = "ten_minute_rule"
    elif "clause be read a second time" in q or "clause be added" in q or "clause be added" in ctx \
            or "this new clause" in ctx or re.search(r"\bnew clause \d|\bafter clause \d", head):
        kind = "new_clause"
    elif "stand part" in q or "should stand part" in ctx:
        kind = "stand_part"
    elif "ways and means" in head or re.search(r"expedient to authorise[^.]{0,40}(?:imposition|levying|charge)", q):
        kind = "ways_and_means"
    elif "expedient to authorise" in q and "ways and means" not in head:
        kind = "money"
    elif "(programme" in head or "allocation of time" in head or q.startswith("that the following provisions shall apply"):
        kind = "programme"
    elif "(money" in head:
        kind = "money"
    elif "ways and means" in head or "provisional collection of taxes" in ctx:
        kind = "ways_and_means"
    elif (not question or "amendment be made" in q) and re.search(r"that the bill be now read a second time", after,
                                                                  re.I):
        kind = "reasoned_amendment"  # the amendment to the second reading motion, decided first
    elif re.search(r"motion (?:to|of) regret|regret motion", head) or "regrets" in q \
            or (house == "lords" and re.search(r"motion to regret|regrets that", ctx)):
        kind = "regret_motion"
    elif "motion to annul" in head or "motion to annul" in ctx or "prayer" in head \
            or re.search(r"\b(regulations|order) 20\d\d\b", q) \
            or ("be approved" in q and re.search(r"\border\b|regulations", q)):
        kind = "statutory_instrument"
    elif "amendment be made" in q or "amendment proposed" in ctx or re.search(r"\bamendment \d", head) \
            or re.search(r"amendment \w+ (?:agreed|disagreed)", ctx) or "amendment to the motion" in minor.lower() \
            or (re.search(r"\bbill\b", major, re.I) and re.match(r"\W*(clause|schedule|part) \w+", minor, re.I)):
        kind = "amendment"
    elif "opposition day" in head:
        kind = "opposition_day"
    elif "estimates" in head or "estimates" in q:
        kind = "estimates"
    elif q.startswith("that this house") or q.startswith("that ") or "main question" in ctx:
        kind = "motion"
    else:
        kind = "other"
    label, note = KINDS[kind]
    return {"kind": kind, "label": label, "note": note, "question": question, "outcome": outcome}


def build(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, Any]]:
    divs = con.execute("SELECT division_id, house, major_heading, minor_heading FROM divisions").fetchall()
    proc = con.execute("""SELECT record_id, left(text, 2000) FROM records
        WHERE xml_person_id IS NULL AND source IN ('debates', 'lordspages')
          AND date IN (SELECT DISTINCT date FROM divisions)""").fetchall()
    by_prefix: dict[str, list[tuple[tuple[int, int], str]]] = {}
    for rid, text in proc:
        p, c, n = _key(rid)
        by_prefix.setdefault(p, []).append(((c, n), text or ""))
    for v in by_prefix.values():
        v.sort()
    out = {}
    for did, house, major, minor in divs:
        p, c, n = _key(did)
        recs = by_prefix.get(p, [])
        before = [t for k, t in recs if k < (c, n)][-2:]
        after = [t for k, t in recs if k > (c, n)][:1]
        out[did] = classify("\n".join(before), after[0] if after else "", major, minor, house)
    return out


class VoteTypes:
    """Every division's kind of vote, built once per data update (a few seconds) and kept on disk."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = Path(settings.data_dir) / "vote_types.json"
        self.descriptions_path = Path(settings.data_dir) / "raw" / "twfy_divisions.parquet"
        self.whips_path = Path(settings.data_dir) / "whips.json"
        self._lock = threading.Lock()
        self._types: dict[str, dict[str, Any]] | None = None
        self._version: str | None = None
        self._con: duckdb.DuckDBPyConnection | None = None
        self._stores: set[str] = set()

    def _data_version(self) -> str:
        """Changes whenever divisions are added or replaced (a sync), so the cache follows the data."""
        if self._con is None:
            self._con = shared()
            self._stores = set(attach_store(self._con, self.settings.parquet_dir))
        if not {"records", "divisions"} <= self._stores:
            return "none"
        # (the identifiers too: reloading the 1997-2001 votes changed them but not the counts)
        n, last, ayes, ids = self._con.execute(
            "SELECT count(*), max(date), sum(ayes), bit_xor(hash(division_id)) FROM divisions").fetchone()
        desc = self.descriptions_path
        stamp = int(desc.stat().st_mtime) if desc.exists() else 0  # a new copy of the descriptions rebuilds too
        whips = int(self.whips_path.stat().st_mtime) if self.whips_path.exists() else 0
        return f"{n}|{last}|{ayes}|{ids}|{RULES}|{stamp}|{whips}"

    def all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            stamp = self._data_version()
            if self._types is not None and self._version == stamp:
                return self._types
            try:
                cached = json.loads(self.path.read_text())
                if cached.get("version") == stamp:
                    self._types, self._version = cached["types"], stamp
                    return self._types
            except (OSError, ValueError, KeyError):
                pass
            types = build(self._con) if stamp != "none" else {}
            from hansard.sources.twfy_votes import descriptions

            for did, d in descriptions(self.descriptions_path).items():  # plain-English descriptions, where written
                if did in types:
                    types[did] = {**types[did], **d}
            from hansard.sources.whips import reports, whip_text

            for did, found in reports(self.whips_path).items():  # how parties whipped, where reported
                if did in types:
                    types[did] = {**types[did], "whips": found, "whip_text": whip_text(found)}
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": stamp, "types": types}))
            tmp.replace(self.path)
            self._types, self._version = types, stamp
            log.info("vote types: %d divisions classified", len(types))
            return types

    def get(self, division_id: str) -> dict[str, Any] | None:
        return self.all().get(division_id)

    def annotate(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Adds ``vote_type`` (kind, label, note, question, outcome) to each item that has a ``division_id``."""
        try:
            types = self.all()
        except (OSError, duckdb.Error):  # never let the explanation break the page it explains
            log.exception("vote types unavailable")
            return items
        for it in items:
            vt = types.get(it.get("division_id") or "")
            if vt:
                it["vote_type"] = vt
        return items


_shared: dict[str, VoteTypes] = {}
_shared_lock = threading.Lock()


def vote_types(settings: Settings) -> VoteTypes:
    """One VoteTypes per data folder, shared by the bill pages, member pages, timelines and briefings."""
    key = str(Path(settings.data_dir).resolve())
    with _shared_lock:
        if key not in _shared:
            _shared[key] = VoteTypes(settings)
        return _shared[key]
