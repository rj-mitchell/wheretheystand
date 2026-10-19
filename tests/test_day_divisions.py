"""Divisions after TheyWorkForYou stopped publishing divisions-only files are read from the day's full report."""

import shutil
from pathlib import Path

import duckdb

from hansard.ingest import divisions_from_day_files
from hansard.sources.twfy import local_files
from hansard.store.parquet import attach_store
from hansard.store.state import StateDB

FIXTURE = Path(__file__).parent / "fixtures" / "xml" / "divisionsonly" / "divisions2025-12-16c.xml"


def test_divisions_read_from_day_reports_after_the_last_divisions_only_file(settings):
    raw = settings.raw_xml_dir
    (raw / "divisionsonly").mkdir(parents=True)
    (raw / "debates").mkdir(parents=True)
    shutil.copy(FIXTURE, raw / "divisionsonly" / "divisions2025-12-16c.xml")
    text = FIXTURE.read_text()
    # a day before the last divisions-only file (left to that file) and one after it (read from the report)
    (raw / "debates" / "debates2025-12-15a.xml").write_text(text.replace("2025-12-16c", "2025-12-15a")
                                                             .replace('divdate="2025-12-16"', 'divdate="2025-12-15"'))
    (raw / "debates" / "debates2026-02-03a.xml").write_text(text.replace("2025-12-16c", "2026-02-03a")
                                                             .replace('divdate="2025-12-16"', 'divdate="2026-02-03"'))
    state = StateDB(settings.state_db)
    files = local_files(raw, ["debates"], settings.archive_start)
    out = divisions_from_day_files(settings, state, files)
    assert out["files"] == 1 and out["divisions"] == 2 and out["votes"] > 0
    assert divisions_from_day_files(settings, state, files)["files"] == 0  # read once, until the report is revised

    con = duckdb.connect()
    attach_store(con, settings.parquet_dir)
    rows = con.execute("SELECT DISTINCT date, source_key FROM divisions").fetchall()
    assert [(str(d), k) for d, k in rows] == [("2026-02-03", "divisions2026-02-03")]
    assert (settings.parquet_dir / "divisions" / "house=commons" / "year=2026" / "divisions2026-02-03.parquet").exists()
