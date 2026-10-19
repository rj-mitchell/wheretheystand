import shutil

import duckdb

from hansard.ingest import ingest
from hansard.store.parquet import attach_store
from hansard.store.state import StateDB


def _count(settings, sql):
    con = duckdb.connect()
    attach_store(con, settings.parquet_dir)
    return con.execute(sql).fetchall()


def test_ingest_writes_parquet_and_is_incremental(settings, xml_mirror):
    from datetime import date

    stats = ingest(settings, workers=2, since=date(2010, 1, 1))
    assert stats.errors == []
    assert stats.files_parsed == stats.files_seen == stats.files_new
    assert stats.records > 0 and stats.divisions == 5 and stats.votes > 0
    (n, unique), = _count(settings, "SELECT count(*), count(DISTINCT record_id) FROM records")
    assert n == unique == stats.records
    years = {y for (y,) in _count(settings, "SELECT DISTINCT year FROM records")}
    assert years == {2012, 2026}

    again = ingest(settings, workers=2, since=date(2010, 1, 1))
    assert again.files_parsed == 0 and again.files_seen == stats.files_seen


def test_new_revision_replaces_old_rows(settings, xml_mirror):
    from datetime import date

    ingest(settings, workers=1, since=date(2026, 1, 1), directories=["debates"])
    before = _count(settings, "SELECT count(*) FROM records WHERE source_key = 'debates2026-09-15'")[0][0]

    # Hansard issues a corrected revision "d" of the same sitting day.
    src = xml_mirror / "debates" / "debates2026-09-15c.xml"
    text = src.read_text(encoding="utf-8").replace("2026-09-15c.", "2026-09-15d.")
    text = text.replace("Trial by Jury", "Trial by Jury (corrected)")
    (xml_mirror / "debates" / "debates2026-09-15d.xml").write_text(text, encoding="utf-8")

    stats = ingest(settings, workers=1, since=date(2026, 1, 1), directories=["debates"])
    assert stats.files_revised == 1 and stats.files_parsed == 1
    rows = _count(settings, "SELECT count(*), count(DISTINCT record_id), min(revision), max(revision) "
                            "FROM records WHERE source_key = 'debates2026-09-15'")[0]
    assert rows == (before, before, "d", "d")
    headings = {h for (h,) in _count(settings, "SELECT DISTINCT minor_heading FROM records WHERE source_key = "
                                               "'debates2026-09-15'")}
    assert "Trial by Jury (corrected)" in headings and "Trial by Jury" not in headings
    manifest = StateDB(settings.state_db).manifest()
    assert manifest[("debates", "debates2026-09-15")]["revision"] == "d"


def test_unlettered_file_rewritten_in_place_is_reparsed(settings, xml_mirror):
    import os
    import time
    from datetime import date

    ingest(settings, workers=1, since=date(2026, 1, 1), directories=["wrans"])
    f = xml_mirror / "wrans" / "answers2026-10-08.xml"
    shutil.copy(f, f.with_suffix(".bak"))
    f.write_text(f.read_text(encoding="utf-8").replace("Freedom of Information", "FOI"), encoding="utf-8")
    os.utime(f, (time.time() + 5, time.time() + 5))
    stats = ingest(settings, workers=1, since=date(2026, 1, 1), directories=["wrans"])
    assert stats.files_revised == 1


def test_correction_to_an_already_compacted_day(settings, xml_mirror):
    from datetime import date

    from hansard.store.parquet import compact

    ingest(settings, workers=1, since=date(2010, 1, 1))
    total = _count(settings, "SELECT count(*) FROM records")[0][0]
    stats = compact(settings.parquet_dir, "2099-01-01")
    assert stats["files_merged"] > 0
    assert not list((settings.parquet_dir / "records").glob("**/debates2012-03-07.parquet"))
    assert _count(settings, "SELECT count(*) FROM records")[0][0] == total

    src = xml_mirror / "debates" / "debates2012-03-07a.xml"
    raw = src.read_bytes().replace(b"2012-03-07a.", b"2012-03-07b.")
    (xml_mirror / "debates" / "debates2012-03-07b.xml").write_bytes(raw)
    ingest(settings, workers=1, since=date(2010, 1, 1))
    rows = _count(settings, "SELECT count(*), count(DISTINCT record_id), min(revision), max(revision) "
                            "FROM records WHERE source_key = 'debates2012-03-07'")[0]
    n = rows[0]
    assert rows == (n, n, "b", "b") and n > 0
    assert _count(settings, "SELECT count(*) FROM records")[0][0] == total
