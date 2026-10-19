"""Room for the rest of the record in a briefing by how well it answers the question, not by fixed shares.

Early day motions, the devolved parliaments and witnesses' evidence to committees are each found by their own search,
whose scores cannot be compared with each other or with the main search. To put them on one scale, the start of each
(about the length of a passage in the main index) is compared in meaning with the question by the same embedding
model the main search uses, which already gives every Westminster record such a score.

Then:

* anything below the floor of related text is dropped, so a weak match never takes a place just because its search
  found something;
* the best of the rest take up to ``budget`` places beside the Westminster records (no more than ``caps`` of each
  kind, of each devolved parliament, and three from any one devolved debate);
* a further one closer to the question than the weakest Westminster record takes that record's place, down to
  ``keep`` Westminster records. A record that is the only one from its party, or that comes from a member who
  keeps raising the subject, keeps its place, so the balance chosen for the Westminster records survives.

So a subject argued mainly in Holyrood, or in committee, brings in more of that record, and one that is not leaves
room for Westminster. The number of sources, and so the cost of a briefing, stays as before.
"""

from __future__ import annotations

from typing import Any

import numpy as np

WINDOW = 1500  # characters compared at a time: about a passage of the main index
WINDOWS = 2    # the first two windows of a long contribution
PER_DEBATE = 3  # devolved contributions from any one debate


def windows(text: str) -> list[str]:
    """The opening of a contribution in passage-sized pieces, broken at paragraphs where possible."""
    out, cur = [], ""
    for para in (text or "").split("\n\n"):
        if cur and len(cur) + len(para) > WINDOW:
            out.append(cur[:WINDOW])
            if len(out) == WINDOWS:
                return out
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur and len(out) < WINDOWS:
        out.append(cur[:WINDOW])
    return out or [""]


def similarities(embedder, query: str, texts: list[str]) -> list[float]:
    """Each text's closeness in meaning to the query: the best of its windows."""
    if not texts:
        return []
    pieces = [windows(t) for t in texts]
    flat = [w for ws in pieces for w in ws]
    sims = embedder.embed_documents(flat) @ np.asarray(embedder.embed_query(query))
    out, i = [], 0
    for ws in pieces:
        out.append(float(max(sims[i:i + len(ws)])))
        i += len(ws)
    return out


def side_text(kind: str, item: dict[str, Any]) -> str:
    if kind == "edms":
        return f"{item.get('title') or ''}. {item.get('text') or ''}"
    if kind == "devolved":
        heading = " — ".join(x for x in (item.get("major"), item.get("minor")) if x)
        return f"{heading}. {item.get('text') or ''}" if heading else item.get("text") or ""
    return item.get("text") or ""


def blend(records: list[dict[str, Any]], record_sims: list[float | None],
          side: list[tuple[str, dict[str, Any]]], side_sims: list[float], *, floor: float, budget: int,
          keep: int, caps: dict[str, int], persistent: int = 3) -> tuple[list[dict[str, Any]], dict[str, list]]:
    """(Westminster records kept, in their order; {kind: items kept, in their search's order})."""
    cap_of = {"edms": caps.get("edms", 0), "evidence": caps.get("evidence", 0)}
    taken: dict[str, int] = {}
    ranked = sorted((i for i, s in enumerate(side_sims) if s >= floor), key=lambda i: -side_sims[i])
    chosen: set[int] = set()
    dropped: set[int] = set()

    def bucket(i: int) -> str:
        kind, item = side[i]
        return f"devolved:{item.get('legislature')}" if kind == "devolved" else kind

    def removable() -> int | None:
        """The weakest Westminster record that can give up its place, if any."""
        parties: dict[str, int] = {}
        for j, r in enumerate(records):
            if j not in dropped:
                p = ((r.get("speaker") or {}).get("party")) or ""
                parties[p] = parties.get(p, 0) + 1
        best = None
        for j, r in enumerate(records):
            if j in dropped or record_sims[j] is None:
                continue
            if parties.get(((r.get("speaker") or {}).get("party")) or "", 0) <= 1:
                continue  # the only one from their party
            if (r.get("occasions") or 0) >= persistent:
                continue  # a member who keeps raising the subject
            if best is None or record_sims[j] < record_sims[best]:
                best = j
        return best

    for i in ranked:
        b = bucket(i)
        limit = caps.get("devolved", 0) if b.startswith("devolved:") else cap_of.get(b, 0)
        if taken.get(b, 0) >= limit:
            continue
        kind, item = side[i]
        debate = (item.get("legislature"), item.get("key"), item.get("major"), item.get("minor"))
        if kind == "devolved" and taken.get(debate, 0) >= PER_DEBATE:
            continue  # one afternoon's argument is not the whole of a parliament's
        if len(chosen) < budget:
            chosen.add(i)
        else:
            if len(records) - len(dropped) <= keep:
                break
            j = removable()
            if j is None or record_sims[j] >= side_sims[i]:
                break  # nothing left that this one (or anything weaker) should replace
            dropped.add(j)
            chosen.add(i)
        taken[b] = taken.get(b, 0) + 1
        if kind == "devolved":
            taken[debate] = taken.get(debate, 0) + 1
    kept_side: dict[str, list] = {"edms": [], "devolved": [], "evidence": []}
    for i, (kind, item) in enumerate(side):
        if i in chosen:
            kept_side[kind].append(item)
    return [r for j, r in enumerate(records) if j not in dropped], kept_side
