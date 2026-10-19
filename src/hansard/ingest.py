"""Parse the local XML mirror into the Parquet store, incrementally.

Only files whose latest revision, size or mtime differ from the manifest are
parsed. Work is spread over a bounded process pool; each worker streams one
file at a time, and the pool is replaced after every ``TASKS_PER_WORKER`` files per
worker, so peak memory is (workers x one file's working set), far below the 4 GB budget.
(Recycling is done by hand: ``max_tasks_per_child`` can deadlock on some Python 3.12 releases.)
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hansard.config import Settings
from hansard.parse.xml_stream import parse_divisions, parse_file
from hansard.sources.twfy import SOURCES, RemoteFile, local_files
from hansard.store.parquet import remove_keys_from_compacted, write_divisions, write_records
from hansard.store.state import StateDB, utcnow

log = logging.getLogger(__name__)
TASKS_PER_WORKER = 200

DIVISION_DIRS = {"divisionsonly", "lordsdivisionsonly"}
# TheyWorkForYou stopped publishing the divisions-only files in January 2026; the day's full report still carries
# every division, so after the last divisions-only file the divisions are read from it.
DAY_FILES = {"debates": "divisionsonly", "lordspages": "lordsdivisionsonly"}
DIVISION_PREFIX = {"divisionsonly": "divisions", "lordsdivisionsonly": "lordsdivisions"}


@dataclass
class IngestStats:
    files_seen: int = 0
    files_parsed: int = 0
    files_new: int = 0
    files_revised: int = 0
    records: int = 0
    divisions: int = 0
    votes: int = 0
    errors: list[str] = field(default_factory=list)
    changed_keys: list[tuple[str, str]] = field(default_factory=list)
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["changed_keys"] = len(self.changed_keys)
        return d


def _parse_one(path_str: str, directory: str, out_root: str) -> dict[str, Any]:
    path, out = Path(path_str), Path(out_root)
    f = local_meta(path, directory)
    if directory in DIVISION_DIRS:
        divisions, votes = parse_divisions(path, directory)
        write_divisions(out, SOURCES[directory][0], f.key, f.file_date.year, divisions, votes)
        return {"rows": len(divisions), "divisions": len(divisions), "votes": len(votes)}
    rows = list(parse_file(path, directory))
    write_records(out, directory, f.key, f.file_date.year, rows)
    return {"rows": len(rows)}


def _divisions_from_day(path_str: str, directory: str, key: str, out_root: str) -> dict[str, Any]:
    """The divisions in a day's full report, stored as that day's divisions-only file would be (same key), so a
    divisions-only file for the day, if one ever appears, replaces them rather than adding a second copy."""
    path, out = Path(path_str), Path(out_root)
    f = local_meta(path, directory)
    divisions, votes = parse_divisions(path, directory)
    for d in divisions:
        d["source_key"] = key
    write_divisions(out, SOURCES[directory][0], key, f.file_date.year, divisions, votes)
    return {"divisions": len(divisions), "votes": len(votes)}


def divisions_from_day_files(settings: Settings, state: StateDB, files: list[RemoteFile]) -> dict[str, int]:
    """Divisions for the days after the last divisions-only file, read from each day's full report (re-read when the
    report is revised)."""
    out = {"files": 0, "divisions": 0, "votes": 0}
    manifest = state.manifest()
    last = {}
    for d in DIVISION_DIRS:
        dates = [f.file_date for f in local_files(settings.raw_xml_dir, [d], settings.archive_start)]
        last[d] = max(dates) if dates else None
    for f in files:
        only = DAY_FILES.get(f.directory)
        if only is None or (last[only] is not None and f.file_date <= last[only]):
            continue
        p = settings.raw_xml_dir / f.rel_path
        st = p.stat()
        source = f"{f.directory}-divisions"
        prev = manifest.get((source, f.key))
        if prev and prev["revision"] == f.revision and prev["size"] == st.st_size and prev["mtime_ns"] == st.st_mtime_ns:
            continue
        key = f"{DIVISION_PREFIX[only]}{f.file_date.isoformat()}"
        try:
            res = _divisions_from_day(str(p), f.directory, key, str(settings.parquet_dir))
        except Exception as exc:  # keep going; the next update tries again
            log.warning("divisions not read from %s: %r", f.rel_path, exc)
            continue
        house = SOURCES[f.directory][0]
        for table in ("votes", "divisions"):
            remove_keys_from_compacted(settings.parquet_dir, table, house, f.file_date.year, {key})
        state.upsert_manifest({"source": source, "key": f.key, "revision": f.revision, "file_name": f.name,
                               "size": st.st_size, "mtime_ns": st.st_mtime_ns, "rows": res["divisions"],
                               "parsed_at": utcnow()})
        out["files"] += 1
        out["divisions"] += res["divisions"]
        out["votes"] += res["votes"]
    return out


def local_meta(path: Path, directory: str) -> RemoteFile:
    from hansard.sources.twfy import parse_name

    meta = parse_name(directory, path.name)
    if meta is None:
        raise ValueError(f"unrecognised file {path}")
    return meta


def ingest(settings: Settings, *, workers: int = 3, since=None, until=None,
           directories: list[str] | None = None, state: StateDB | None = None) -> IngestStats:
    started = time.time()
    state = state or StateDB(settings.state_db)
    dirs = directories or [*settings.record_dirs, *sorted(DIVISION_DIRS)]
    files = local_files(settings.raw_xml_dir, dirs, since or settings.archive_start)
    if until is not None:
        files = [f for f in files if f.file_date <= until]
    manifest = state.manifest()
    stats = IngestStats(files_seen=len(files))
    todo: list[tuple[RemoteFile, Path, int, int]] = []
    for f in files:
        p = settings.raw_xml_dir / f.rel_path
        st = p.stat()
        prev = manifest.get((f.directory, f.key))
        if prev and prev["revision"] == f.revision and prev["size"] == st.st_size and prev["mtime_ns"] == st.st_mtime_ns:
            continue
        todo.append((f, p, st.st_size, st.st_mtime_ns))
        if prev is None:
            stats.files_new += 1
        else:
            stats.files_revised += 1

    if todo:
        ctx = mp.get_context("spawn")
        n_workers = max(1, workers)
        chunk = n_workers * TASKS_PER_WORKER
        done = 0
        for start in range(0, len(todo), chunk):
            with ProcessPoolExecutor(max_workers=n_workers, mp_context=ctx) as pool:
                futures = {pool.submit(_parse_one, str(p), f.directory, str(settings.parquet_dir)): (f, size, mtime)
                           for f, p, size, mtime in todo[start:start + chunk]}
                for fut in as_completed(futures):
                    done += 1
                    f, size, mtime = futures[fut]
                    try:
                        res = fut.result()
                    except Exception as exc:  # keep going; record the failure
                        stats.errors.append(f"{f.rel_path}: {exc!r}")
                        log.warning("parse failed for %s: %r", f.rel_path, exc)
                        continue
                    stats.files_parsed += 1
                    if f.directory in DIVISION_DIRS:
                        stats.divisions += res["divisions"]
                        stats.votes += res["votes"]
                    else:
                        stats.records += res["rows"]
                        stats.changed_keys.append((f.directory, f.key))
                    state.upsert_manifest({
                        "source": f.directory, "key": f.key, "revision": f.revision, "file_name": f.name,
                        "size": size, "mtime_ns": mtime, "rows": res["rows"], "parsed_at": utcnow(),
                    })
                    if done % 500 == 0:
                        log.info("parsed %d/%d files", done, len(todo))
    # A correction to an already-compacted day: drop that day's old rows from the
    # compacted file now that its new per-key file exists (serially, in this process).
    by_partition: dict[tuple[str, str, int], set[str]] = {}
    for f, _, _, _ in todo:
        if f.directory in DIVISION_DIRS:
            house = SOURCES[f.directory][0]
            by_partition.setdefault(("divisions", house, f.file_date.year), set()).add(f.key)
            by_partition.setdefault(("votes", house, f.file_date.year), set()).add(f.key)
        else:
            by_partition.setdefault(("records", f.directory, f.file_date.year), set()).add(f.key)
    for (table, part, year), keys in sorted(by_partition.items(), key=lambda kv: kv[0][0] != "votes"):
        remove_keys_from_compacted(settings.parquet_dir, table, part, year, keys)
    from_days = divisions_from_day_files(settings, state, files)
    stats.divisions += from_days["divisions"]
    stats.votes += from_days["votes"]
    if from_days["files"]:
        log.info("read %d divisions from %d day reports (no divisions-only files for those days)",
                 from_days["divisions"], from_days["files"])
    stats.seconds = round(time.time() - started, 1)
    return stats
