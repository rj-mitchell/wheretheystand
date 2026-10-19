"""Daily incremental synchronisation.

1. Member data: ``git pull`` parlparse ``members/``; if the commit changed,
   rebuild the reference tables and work out exactly which people changed
   (offices, seats, parties, names). Only their passages are re-enriched, and
   their embeddings are kept because the text has not changed.
2. Hansard: mirror new and corrected scrapedxml files (rsync or HTTPS).
3. Ingest new/changed files into Parquet (corrections replace their day).
4. Index the changed files for keyword search; merge into the BM25 index.
5. Embed new passages within a time budget (the rest continues next run).
6. Compact Parquet days older than the correction window.
7. Record timings and counts in ``sync_log``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import time
import traceback
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from hansard.config import STATIC_DIR, Settings
from hansard.index.build import Indexer, embed_pending
from hansard.index.lance import SearchIndex
from hansard.ingest import ingest
from hansard.reference.build import (
    REFERENCE_VERSION,
    ReferenceData,
    build_reference,
    write_reference,
)
from hansard.store.parquet import compact
from hansard.store.state import StateDB

log = logging.getLogger(__name__)

COMPACT_AFTER_DAYS = 90
PERSON_TABLES = ("names", "memberships", "offices")
PENDING_PEOPLE = "reenrich_pending_people"


# Shown on source cards (profiled live from the reference tables), never stored in the index.
DISPLAY_ONLY = {"settlement", "urban_area", "urban_area_pop"}


def _person_fingerprints(rows_by_table: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    acc: dict[str, list[str]] = defaultdict(list)
    for table, rows in rows_by_table.items():
        for r in rows:
            pid = r.get("person_id")
            if pid:
                kept = {k: v for k, v in r.items() if k not in DISPLAY_ONLY}
                acc[pid].append(table + json.dumps(kept, sort_keys=True, default=str))
    return {pid: hashlib.sha256("\n".join(sorted(parts)).encode()).hexdigest() for pid, parts in acc.items()}


def changed_people(old_dir: Path, new: ReferenceData) -> set[str]:
    """People whose names, memberships or offices differ between two reference builds."""
    old_rows: dict[str, list[dict[str, Any]]] = {}
    for t in PERSON_TABLES:
        f = old_dir / f"{t}.parquet"
        old_rows[t] = pq.read_table(f).to_pylist() if f.exists() else []
    if not any(old_rows.values()):
        return set()  # first build: everything is new and will be indexed anyway
    new_rows = {t: getattr(new, t) for t in PERSON_TABLES}
    a, b = _person_fingerprints(old_rows), _person_fingerprints(new_rows)
    return {p for p in a.keys() | b.keys() if a.get(p) != b.get(p)}


def pending_people(state: StateDB) -> set[str]:
    return set(json.loads(state.get(PENDING_PEOPLE) or "[]"))


def demographics_path(settings: Settings) -> Path:
    return settings.data_dir / "raw" / "demographics.json"


def refresh_demographics_data(settings: Settings, fetch: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    """Gender and date-of-birth data (refreshed weekly when ``fetch``), and what happened."""
    from hansard.sources.demographics import load_demographics, refresh_demographics

    status: dict[str, Any] = {"status": "not fetched"}
    if fetch and settings.section("demographics").get("enabled", True):
        try:
            status = refresh_demographics(demographics_path(settings))
        except Exception as exc:  # never let this stop a sync
            log.warning("gender and date-of-birth refresh failed: %s", exc)
            status = {"status": "failed", "error": repr(exc)}
    return load_demographics(demographics_path(settings)), status


def evidence_path(settings: Settings) -> Path:
    return settings.data_dir / "evidence.sqlite"


def evidence_index_path(settings: Settings) -> Path:
    return settings.data_dir / "evidence_index"


def devolved_path(settings: Settings) -> Path:
    return settings.data_dir / "devolved.sqlite"


def committees_path(settings: Settings) -> Path:
    return settings.data_dir / "committees.sqlite"


def regmem_path(settings: Settings) -> Path:
    return settings.data_dir / "regmem.json"


def appgs_path(settings: Settings) -> Path:
    return settings.data_dir / "appgs.sqlite"


def directorships_path(settings: Settings) -> Path:
    return settings.data_dir / "directorships.json"


def donations_path(settings: Settings) -> Path:
    return settings.data_dir / "donations.sqlite"


def edms_path(settings: Settings) -> Path:
    return settings.data_dir / "edms.sqlite"


def elections_path(settings: Settings) -> Path:
    return settings.data_dir / "raw" / "elections.json"


def refresh_elections_data(settings: Settings, members_dir: Path, fetch: bool = True
                           ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Seats and election results for every MP since 2010 (election results start then) (refreshed weekly when ``fetch``), and what happened."""
    from hansard.sources.elections import commons_member_ids, load_elections, refresh_elections

    status: dict[str, Any] = {"status": "not fetched"}
    if fetch and settings.section("elections").get("enabled", True):
        try:
            status = refresh_elections(elections_path(settings), commons_member_ids(members_dir / "people.json"))
        except Exception as exc:  # never let this stop a sync
            log.warning("election results refresh failed: %s", exc)
            status = {"status": "failed", "error": repr(exc)}
    return load_elections(elections_path(settings)), status


def refresh_reference(settings: Settings, state: StateDB, members_dir: Path | None) -> dict[str, Any]:
    from hansard.sources import parlparse

    sha = None
    fetch = members_dir is None
    demographics, demo_status = refresh_demographics_data(settings, fetch=fetch)
    if members_dir is None:
        sha = parlparse.ensure_checkout(settings.sources["parlparse_repo"], settings.parlparse_dir,
                                        settings.sources.get("parlparse_branch", "master"))
        members_dir = parlparse.members_dir(settings.parlparse_dir)
    elections, elec_status = refresh_elections_data(settings, members_dir, fetch=fetch)
    unchanged = (fetch and sha == state.get("parlparse_sha") and state.get("reference_version") == REFERENCE_VERSION
                 and (settings.reference_dir / "persons.parquet").exists()
                 and (settings.reference_dir / "election_results.parquet").exists()
                 and demo_status.get("status") not in ("updated", "partial")
                 and elec_status.get("status") != "updated")
    if unchanged:
        return {"parlparse_sha": sha, "reference_changed": False, "people_changed": [],
                "demographics": demo_status, "elections": elec_status}
    ref = build_reference(members_dir, STATIC_DIR / "geo" / "constituencies.csv",
                          Path(settings.config_path).parent / "overrides" / "constituency_names.csv",
                          demographics, elections)
    people = install_reference(settings, state, ref, sha)
    return {"parlparse_sha": sha, "reference_changed": True, "people_changed": sorted(people),
            "unmatched_seats": ref.stats.get("unmatched_seats", []), "demographics": demo_status,
            "elections": elec_status}


def install_reference(settings: Settings, state: StateDB, ref: ReferenceData, sha: str | None) -> set[str]:
    """Swap in a new reference build; returns the people whose details changed."""
    people = changed_people(settings.reference_dir, ref)
    # Queue the changed people before the swap: if this sync fails later, the next one
    # still refreshes their passages even though the reference will then look unchanged.
    state.set(PENDING_PEOPLE, json.dumps(sorted(pending_people(state) | people)))
    # write to a temporary directory, then swap, so readers never see a half-written set
    tmp = Path(tempfile.mkdtemp(prefix=".reference-", dir=settings.reference_dir.parent))
    write_reference(ref, tmp)
    if settings.reference_dir.exists():
        old = settings.reference_dir.with_name(".reference-old")
        shutil.rmtree(old, ignore_errors=True)
        settings.reference_dir.rename(old)
        tmp.rename(settings.reference_dir)
        shutil.rmtree(old, ignore_errors=True)
    else:
        tmp.rename(settings.reference_dir)
    if sha:
        state.set("parlparse_sha", sha)
    state.set("reference_version", REFERENCE_VERSION)
    return people


def refresh_topics_safely(settings: Settings, state: StateDB) -> list[dict[str, Any]] | str:
    """Refresh the topic briefings that are due; a failure here never fails the sync."""
    try:
        from hansard.rag.pipeline import Pipeline
        from hansard.topics import refresh_topics

        return refresh_topics(settings, state, Pipeline(settings))
    except Exception as exc:
        log.warning("topic briefings were not refreshed: %s", exc)
        return f"error: {type(exc).__name__}: {exc}"


class SyncRunning(RuntimeError):
    """Another update holds the lock."""


@contextmanager
def update_lock(settings: Settings) -> Iterator[None]:
    """One update at a time: a second one started meanwhile (by hand, or a timer firing during a long run) stops
    straight away rather than writing the same files at once. The lock is released when the process ends, even if
    it is killed."""
    import fcntl

    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(settings.data_dir) / "sync.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncRunning("another update is already running") from None
        yield


def run_sync(settings: Settings, **kwargs: Any) -> dict[str, Any]:
    with update_lock(settings):
        return _run_sync(settings, **kwargs)


def _run_sync(settings: Settings, *, fetch: bool = True, embed_minutes: float | None = 60.0,
              members_dir: Path | None = None, embedder=None, workers: int = 3,
              today: date | None = None) -> dict[str, Any]:
    state = StateDB(settings.state_db)
    stopped = state.close_unfinished_runs()
    if stopped:
        log.warning("%d earlier update(s) stopped before finishing (the server may have run out of memory)", stopped)
    run_id = state.start_run("sync")
    started = time.time()
    stats: dict[str, Any] = {}
    try:
        t = time.time()
        stats["reference"] = refresh_reference(settings, state, members_dir)
        stats["reference"]["seconds"] = round(time.time() - t, 1)

        if fetch:
            from hansard.cli import fetch_xml

            t = time.time()
            stats["fetch"] = {**fetch_xml(settings), "seconds": round(time.time() - t, 1)}

        log.info("checking for new and corrected files")
        ing = ingest(settings, workers=workers, state=state)
        stats["ingest"] = ing.as_dict()
        try:  # Commons votes of 1997-2001 (Public Whip): loaded once
            from hansard.sources.publicwhip import load_votes_1997

            stats["votes_1997"] = load_votes_1997(settings.parquet_dir, state)
        except Exception as exc:  # the rest of the update carries on
            log.exception("1997-2001 votes not loaded")
            stats["votes_1997"] = {"error": repr(exc)}

        idx = SearchIndex(settings.index_dir, int(settings.embedding.get("dim", 384)))
        indexer = Indexer(settings, idx, state)
        t = time.time()
        first_build = not idx.has_data()
        passages = indexer.index_pending()
        if idx.ensure_vector_ids():
            log.info("built the vectors table's passage index")
        log.info("checking for passages waiting to be merged into the search indices")
        waiting = 0 if first_build else idx.unmerged()  # includes rows an earlier, stopped update added
        if first_build and passages:
            idx.build_indices()
        elif passages or waiting:
            # before updating speaker details below: those updates find passages through these indices
            log.info("merging %d new passages into the search indices", waiting or passages)
            t_merge = time.time()
            idx.optimize()  # merges new rows into the BM25 and scalar indices incrementally
            log.info("merged in %.0fs", time.time() - t_merge)
        people = pending_people(state)
        reenriched = 0
        if people and not first_build:
            log.info("updating speaker details (party, seat, roles) for %d member(s) whose records changed", len(people))
            reenriched = indexer.reenrich_people(people)
            log.info("speaker details changed on %d passages", reenriched)
            if reenriched:
                idx.optimize()
        state.set(PENDING_PEOPLE, "[]")
        stats["index"] = {"passages_written": passages, "passages_reenriched": reenriched,
                          "people_reenriched": len(people), "seconds": round(time.time() - t, 1), **idx.stats()}

        t = time.time()
        if fetch and settings.section("interests").get("enabled", True):
            try:  # the Register of Members' Financial Interests: a new edition every two weeks or so
                import duckdb

                from hansard.reference.profiles import attach_reference
                from hansard.sources.regmem import (
                    former_members,
                    refresh,
                    refresh_history,
                    refresh_previous,
                )

                stats["interests"] = refresh(regmem_path(settings))
                # the edition from a year earlier, to show what changed
                stats["interests"]["previous"] = refresh_previous(
                    regmem_path(settings).with_name("regmem_previous.json"), stats["interests"].get("edition"))
                con = duckdb.connect()
                attach_reference(con, settings.reference_dir)
                # former MPs: the last edition before they left, a few old editions per run
                stats["interests"]["history"] = refresh_history(regmem_path(settings).with_name("regmem_history.json"),
                                                                former_members(con))
                # every edition since 2000, a few dozen per run, so interests can be checked as they stood on the day
                from hansard.sources.regmem_archive import RegisterArchive, refresh_archive

                stats["interests"]["archive"] = refresh_archive(
                    RegisterArchive(regmem_path(settings).with_name("regmem_archive.sqlite")),
                    max_editions=int(settings.section("interests").get("archive_editions_per_run", 40)))
                # the Lords (each sitting peer, weekly) and the devolved parliaments' registers
                from hansard.sources.registers import refresh_devolved, refresh_lords, sitting_peers

                try:
                    stats["interests"]["lords"] = refresh_lords(settings.data_dir / "regmem_lords.json",
                                                                sitting_peers(con))
                except Exception as exc:
                    log.warning("Lords interests not updated: %s", exc)
                    stats["interests"]["lords"] = {"error": str(exc)[:200]}
                stats["interests"]["devolved"] = refresh_devolved(settings.data_dir)
                stats["interests"]["seconds"] = round(time.time() - t, 1)
            except Exception as exc:  # never let an outside service stop the sync
                log.warning("register of interests not updated: %s", exc)
                stats["interests"] = {"error": str(exc)[:200]}

        t = time.time()
        if fetch and settings.section("appgs").get("enabled", True):
            try:  # all-party parliamentary groups, from mySociety's dataset (weekly)
                import duckdb

                from hansard.reference.profiles import attach_reference
                from hansard.sources.appgs import refresh as refresh_appgs

                con = duckdb.connect()
                attach_reference(con, settings.reference_dir)
                stats["appgs"] = refresh_appgs(appgs_path(settings), con)
                stats["appgs"]["seconds"] = round(time.time() - t, 1)
            except Exception as exc:  # never let an outside service stop the sync
                log.warning("all-party groups not updated: %s", exc)
                stats["appgs"] = {"error": str(exc)[:200]}

        t = time.time()
        if fetch and settings.section("donations").get("enabled", True):
            try:  # the Electoral Commission's register of donations, matched to MPs (weekly)
                import duckdb

                from hansard.reference.profiles import attach_reference
                from hansard.sources.donations import refresh as refresh_donations

                con = duckdb.connect()
                attach_reference(con, settings.reference_dir)
                stats["donations"] = refresh_donations(donations_path(settings), con)
                stats["donations"]["seconds"] = round(time.time() - t, 1)
            except Exception as exc:  # never let an outside service stop the sync
                log.warning("donations not updated: %s", exc)
                stats["donations"] = {"error": str(exc)[:200]}

        t = time.time()
        if fetch and settings.section("devolved").get("enabled", True):
            try:  # the Scottish Parliament, the Senedd and the Northern Ireland Assembly: a few dozen files a run
                from hansard.devolved import SINCE, DevolvedStore, Members, sync_devolved

                since = settings.section("devolved").get("since")
                stats["devolved"] = {**sync_devolved(DevolvedStore(devolved_path(settings)),
                                                     since=date.fromisoformat(since) if since else SINCE,
                                                     members=Members(settings.parlparse_dir / "members" / "people.json"),
                                                     max_files=int(settings.section("devolved").get("files_per_run", 60))),
                                     "seconds": round(time.time() - t, 1)}
            except Exception as exc:  # never let an outside service stop the sync
                log.warning("devolved parliaments not updated: %s", exc)
                stats["devolved"] = {"error": str(exc)[:200]}

        t = time.time()
        if fetch and settings.section("committees").get("enabled", True):
            try:  # select committees, their reports and who gave evidence: rebuilt about once a week
                from hansard.sources.committees import refresh as refresh_committees

                stats["committees"] = {**refresh_committees(committees_path(settings)),
                                       "seconds": round(time.time() - t, 1)}
            except Exception as exc:  # never let an outside service stop the sync
                log.warning("committees not updated: %s", exc)
                stats["committees"] = {"error": str(exc)[:200]}
            t = time.time()
            try:  # oral evidence transcripts, a few hundred per run (needs the committee data above)
                import sqlite3

                from hansard.sources.evidence import SINCE as EVIDENCE_SINCE
                from hansard.sources.evidence import EvidenceStore, sync_transcripts

                if committees_path(settings).exists():
                    with sqlite3.connect(committees_path(settings)) as cc:
                        sessions = cc.execute("SELECT id, date FROM sessions").fetchall()
                    since = settings.section("committees").get("transcripts_since")
                    stats["transcripts"] = {**sync_transcripts(
                        EvidenceStore(evidence_path(settings)), sessions,
                        since=date.fromisoformat(since) if since else EVIDENCE_SINCE,
                        max_sessions=int(settings.section("committees").get("transcripts_per_run", 500))),
                        "seconds": round(time.time() - t, 1)}
            except Exception as exc:
                log.warning("committee transcripts not updated: %s", exc)
                stats["transcripts"] = {"error": str(exc)[:200]}

        t = time.time()
        if fetch and settings.section("edms").get("enabled", True):
            try:  # early day motions: recent changes, some older months, and who signed
                from hansard.sources.edms import EdmStore, sync_edms

                stats["edms"] = {**sync_edms(EdmStore(edms_path(settings)), today=today),
                                 "seconds": round(time.time() - t, 1)}
            except Exception as exc:  # never let an outside service stop the sync
                log.warning("early day motions not updated: %s", exc)
                stats["edms"] = {"error": str(exc)[:200]}

        t = time.time()
        if fetch:
            try:  # plain-English descriptions of some votes, from TheyWorkForYou Votes (weekly)
                from hansard.sources.twfy_votes import refresh as refresh_descriptions

                stats["vote_descriptions"] = refresh_descriptions(settings.data_dir / "raw" / "twfy_divisions.parquet")
            except Exception as exc:
                log.warning("vote descriptions not updated: %s", exc)
            try:  # how parties whipped, where TheyWorkForYou Votes has reported it (a slice of votes per update)
                from hansard.sources.whips import refresh as refresh_whips

                stats["whips"] = refresh_whips(settings.data_dir / "whips.json",
                                               settings.data_dir / "raw" / "twfy_divisions.parquet")
            except Exception as exc:
                log.warning("whip reports not updated: %s", exc)
            if settings.section("directorships").get("enabled", True):
                try:  # company directorships from Companies House (a slice of members per update; needs a key)
                    from hansard.sources import parlparse
                    from hansard.sources.companies import refresh as refresh_directorships

                    t = time.time()
                    stats["directorships"] = refresh_directorships(
                        directorships_path(settings), settings.reference_dir,
                        parlparse.members_dir(settings.parlparse_dir) / "people.json")
                    stats["directorships"]["seconds"] = round(time.time() - t, 1)
                except Exception as exc:  # never let an outside service stop the sync
                    log.warning("directorships not updated: %s", exc)
                    stats["directorships"] = {"error": str(exc)[:200]}
        try:  # what each vote was on (amendment, second reading and so on), used across the site
            from hansard.vote_types import vote_types

            stats["vote_types"] = {"divisions": len(vote_types(settings).all()), "seconds": round(time.time() - t, 1)}
        except Exception as exc:
            log.warning("vote types not rebuilt: %s", exc)

        t = time.time()
        try:  # the bill pages' index, ready before anyone asks for it
            from hansard.bills import Bills

            stats["bills"] = {"bills": len(Bills(settings).all(state.latest_record_date())),
                              "seconds": round(time.time() - t, 1)}
        except Exception as exc:
            log.warning("bill index not rebuilt: %s", exc)

        t = time.time()
        try:  # the totals every chart is read against, ready before anyone asks
            from hansard.trends import BY, Trends

            trends = Trends(settings)
            for by in BY:
                trends.totals(by, str(state.latest_record_date()))
            stats["chart_totals_seconds"] = round(time.time() - t, 1)
        except Exception as exc:
            log.warning("chart totals not rebuilt: %s", exc)

        from hansard.rank.rerank import ensure_reranker

        # before the embedder loads: once a model is on disk, downloads are switched off for this process
        stats["reranker"] = ensure_reranker(settings)

        if settings.embedding.get("enabled", True) and (embed_minutes is None or embed_minutes > 0):
            from hansard.embed.base import get_embedder

            t = time.time()
            emb = embedder or get_embedder(settings)
            n = embed_pending(settings, idx, state, emb,
                              max_seconds=embed_minutes * 60 if embed_minutes else None)
            stats["embed"] = {"passages_embedded": n, "files_remaining": len(state.unembedded()),
                              "seconds": round(time.time() - t, 1)}
            if n:  # index the new vectors now, so later look-ups by passage never read them all into memory
                idx.ensure_vector_ids()
                log.info("indexing %d new vectors", n)
                idx.optimize()

            t = time.time()
            try:  # witnesses' answers to committees, for meaning-based search, within their own time allowance
                from hansard.index.evidence import EvidenceVectors, embed_evidence
                from hansard.sources.evidence import EvidenceStore

                if evidence_path(settings).exists():
                    minutes = float(settings.section("committees").get("evidence_embed_minutes", 30))
                    stats["evidence_embed"] = {**embed_evidence(
                        EvidenceStore(evidence_path(settings)),
                        EvidenceVectors(evidence_index_path(settings), int(getattr(emb, "dim", 384))),
                        emb, max_seconds=minutes * 60), "seconds": round(time.time() - t, 1)}
            except Exception as exc:  # never let this stop the sync
                log.warning("committee evidence not embedded: %s", exc)
                stats["evidence_embed"] = {"error": str(exc)[:200]}

        if settings.section("topics").get("enabled", True) and fetch:
            t = time.time()
            stats["topics"] = refresh_topics_safely(settings, state)
            stats["topics_seconds"] = round(time.time() - t, 1)

        t = time.time()
        cutoff = ((today or date.today()) - timedelta(days=COMPACT_AFTER_DAYS)).isoformat()
        stats["compact"] = {**compact(settings.parquet_dir, cutoff), "seconds": round(time.time() - t, 1)}
        stats["seconds"] = round(time.time() - started, 1)
        state.finish_run(run_id, stats)
    except Exception as exc:
        stats["seconds"] = round(time.time() - started, 1)
        stats["error"] = f"{type(exc).__name__}: {exc}"
        log.error("sync failed: %s", traceback.format_exc())
        state.finish_run(run_id, stats, error=stats["error"])
    return stats
