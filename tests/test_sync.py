"""Incremental daily sync: new sittings, corrections and member-data changes."""

import json
import shutil
from datetime import date, timedelta

import pyarrow as pa
import pyarrow.compute  # noqa: F401  (pa.compute)
import pytest

from hansard.embed.base import HashingEmbedder
from hansard.index import lance
from hansard.index.build import Indexer
from hansard.index.lance import SearchIndex, sql_str
from hansard.store.state import StateDB
from hansard.sync import run_sync

from .conftest import MEMBERS

BENN = "uk.org.publicwhip/person/10669"


def _passages(settings, where):
    idx = SearchIndex(settings.index_dir, 64)
    return idx.passages().search().where(where).limit(None).to_list()


def test_sync_end_to_end_and_member_reshuffle(settings, xml_mirror, tmp_path):
    members = tmp_path / "members"
    shutil.copytree(MEMBERS, members)
    first = run_sync(settings, fetch=False, members_dir=members, embedder=HashingEmbedder(64), embed_minutes=None,
                     workers=1, today=date(2026, 10, 1))
    assert "error" not in first, first
    assert first["ingest"]["files_new"] > 0 and first["index"]["passages_written"] > 0
    assert first["embed"]["files_remaining"] == 0
    vectors_before = SearchIndex(settings.index_dir, 64).stats()["vectors"]
    assert vectors_before > 0

    # Pick a speaker who appears in the fixture debates and give them a new government post.
    rows = _passages(settings, "source = 'debates' AND person_id IS NOT NULL AND date >= DATE '2026-01-01'")
    target = rows[0]["person_id"]
    others = {r["person_id"] for r in rows} - {target}
    f = members / "ministers-2010.json"
    data = json.loads(f.read_text())
    data["memberships"].append({
        "id": "test/new-post", "source": "datadotparl/governmentpost", "organization_id": "house-of-commons",
        "person_id": target, "role": "Minister of State (Department for Transport)",
        "start_date": "2026-01-01",
    })
    f.write_text(json.dumps(data))

    second = run_sync(settings, fetch=False, members_dir=members, embedder=HashingEmbedder(64), embed_minutes=None,
                      workers=1, today=date(2026, 10, 1))
    assert "error" not in second, second
    assert second["reference"]["people_changed"] == [target]
    assert second["ingest"]["files_parsed"] == 0          # no Hansard re-parsing
    assert second["index"]["passages_reenriched"] >= 1
    after = _passages(settings, f"person_id = {sql_str(target)} AND date >= DATE '2026-01-01'")
    assert after and all("transport" in r["gov_portfolios"] for r in after)
    # other speakers untouched, embeddings kept (text unchanged)
    assert second["embed"]["passages_embedded"] == 0
    assert SearchIndex(settings.index_dir, 64).stats()["vectors"] == vectors_before
    vt = SearchIndex(settings.index_dir, 64).vectors()
    vec_rows = vt.search().where(f"person_id = {sql_str(target)} AND date >= DATE '2026-01-01'").limit(None).to_list()
    assert vec_rows and all(r["is_gov"] for r in vec_rows)
    assert others  # sanity: fixture has several speakers

    runs = StateDB(settings.state_db).runs()
    assert len(runs) == 2 and all(r["status"] == "ok" for r in runs)
    assert runs[0]["stats"]["reference"]["people_changed"] == [target]


def test_sync_applies_a_hansard_correction(settings, xml_mirror, tmp_path):
    members = tmp_path / "members"
    shutil.copytree(MEMBERS, members)
    run_sync(settings, fetch=False, members_dir=members, embedder=HashingEmbedder(64), embed_minutes=None, workers=1,
             today=date(2026, 10, 1))
    before = _passages(settings, "source_key = 'debates2026-09-15'")
    src = xml_mirror / "debates" / "debates2026-09-15c.xml"
    text = src.read_text(encoding="utf-8").replace("2026-09-15c.", "2026-09-15d.").replace(
        "Trial by Jury", "Trial by Jury (corrected)")
    (xml_mirror / "debates" / "debates2026-09-15d.xml").write_text(text, encoding="utf-8")

    stats = run_sync(settings, fetch=False, members_dir=members, embedder=HashingEmbedder(64), embed_minutes=None,
                     workers=1, today=date(2026, 10, 1))
    assert stats["ingest"]["files_revised"] == 1 and stats["ingest"]["files_parsed"] == 1
    after = _passages(settings, "source_key = 'debates2026-09-15'")
    assert len(after) == len(before)
    assert all("2026-09-15d." in r["record_id"] for r in after)
    assert any("Trial by Jury (corrected)" in r["heading"] for r in after)
    assert stats["embed"]["passages_embedded"] == len(after)  # corrected text is re-embedded


def test_sync_records_failures(settings, tmp_path):
    stats = run_sync(settings, fetch=False, members_dir=tmp_path / "missing", embed_minutes=0)
    assert "error" in stats
    assert StateDB(settings.state_db).runs()[0]["status"] == "failed"


ANSWER = "uk.org.publicwhip/wrans/2026-09-11.29242.r0"
QUESTION = "uk.org.publicwhip/wrans/2026-09-11.29242.q0"


def _republish_answer(xml_mirror):
    """TWFY republishes a corrected written answer in a later day's file under the same IDs."""
    lines = (xml_mirror / "wrans" / "answers2026-10-08.xml").read_text(encoding="utf-8").splitlines()
    ques = "\n".join(lines[9:12])
    body = "\n".join([*lines[:12], ques, *lines[12:16]])  # the question appears twice in the new file
    (xml_mirror / "wrans" / "answers2026-10-12.xml").write_text(
        body.replace("over 8,700 requests", "over 8,900 requests") + "\n</publicwhip>\n", encoding="utf-8")


def _copies(settings, table, record_id):
    idx = SearchIndex(settings.index_dir, 64)
    tbl = idx.passages() if table == "passages" else idx.vectors()
    return tbl.search().where(f"record_id = {sql_str(record_id)}").limit(None).to_list()


def test_republished_written_answer_replaces_the_older_copy(settings, xml_mirror, tmp_path):
    members = tmp_path / "members"
    shutil.copytree(MEMBERS, members)

    def sync():
        out = run_sync(settings, fetch=False, members_dir=members, embedder=HashingEmbedder(64), embed_minutes=None,
                       workers=1, today=date(2026, 10, 13))
        assert "error" not in out, out
        return out

    sync()
    assert {r["source_key"] for r in _copies(settings, "passages", ANSWER)} == {"answers2026-10-08"}
    _republish_answer(xml_mirror)
    sync()

    for table in ("passages", "vectors"):
        answer = _copies(settings, table, ANSWER)
        assert answer and {r["source_key"] for r in answer} == {"answers2026-10-12"}
        assert len({r["passage_id"] for r in answer}) == len(answer)
        question = _copies(settings, table, QUESTION)
        assert len(question) == 1 and question[0]["source_key"] == "answers2026-10-12"
    assert all("8,900" in r["text"] for r in _copies(settings, "passages", ANSWER))
    # the rest of the older file is still indexed
    assert _passages(settings, "source_key = 'answers2026-10-08'")
    # passage IDs are unique again, so in-place speaker updates work
    idx = SearchIndex(settings.index_dir, 64)
    who = "uk.org.publicwhip/person/25673"
    indexer = Indexer(settings, idx, StateDB(settings.state_db))
    assert indexer.reenrich_people({who}) == 0  # nothing changed: nothing rewritten
    idx.passages().update(where=f"person_id = '{who}'", values={"party": "Stale"})
    assert indexer.reenrich_people({who}) > 0
    assert "Stale" not in {r["party"] for r in _passages(settings, f"person_id = '{who}'")}
    # in small groups and small writes (as for a big reshuffle on a small server) the outcome is the same
    everyone = {r["person_id"] for r in _passages(settings, "person_id IS NOT NULL")}
    idx.passages().update(where="person_id IS NOT NULL", values={"party": "Stale"})
    assert indexer.reenrich_people(everyone, max_passages=2, write_rows=1) > 0
    assert "Stale" not in {r["party"] for r in _passages(settings, "person_id IS NOT NULL")}


def test_dedupe_cleans_an_index_built_with_both_copies(settings, xml_mirror, tmp_path):
    members = tmp_path / "members"
    shutil.copytree(MEMBERS, members)
    _republish_answer(xml_mirror)
    run_sync(settings, fetch=False, members_dir=members, embedder=HashingEmbedder(64), embed_minutes=None,
             workers=1, today=date(2026, 10, 13))
    # indexing both files at once already keeps only the newer copy
    assert {r["source_key"] for r in _copies(settings, "passages", ANSWER)} == {"answers2026-10-12"}

    # simulate an index built by the old indexer: an older copy and a repeated question
    idx = SearchIndex(settings.index_dir, 64)
    tbl = idx.passages()
    newer = tbl.search().where(f"record_id IN ({sql_str(ANSWER)}, {sql_str(QUESTION)})").limit(None).to_arrow()
    older = newer.set_column(newer.schema.get_field_index("source_key"), "source_key",
                             pa.array(["answers2026-10-08"] * newer.num_rows))
    tbl.add(older)
    tbl.add(newer.filter(pa.compute.equal(newer.column("record_id"), QUESTION)))
    assert len(_copies(settings, "passages", QUESTION)) == 3

    state = StateDB(settings.state_db)
    out = Indexer(settings, idx, state).dedupe()
    assert out == {"stale_copies_deleted": 2, "files_reindexed": 1}
    for rid in (ANSWER, QUESTION):
        assert {r["source_key"] for r in _copies(settings, "passages", rid)} == {"answers2026-10-12"}
    assert len(_copies(settings, "passages", QUESTION)) == 1
    assert ("wrans", "answers2026-10-12") in state.unembedded()  # re-indexed file is queued for embedding


def test_member_changes_survive_a_failed_sync(settings, xml_mirror, tmp_path, monkeypatch):
    members = tmp_path / "members"
    shutil.copytree(MEMBERS, members)

    def sync():
        return run_sync(settings, fetch=False, members_dir=members, embedder=HashingEmbedder(64), embed_minutes=None,
                        workers=1, today=date(2026, 10, 1))

    sync()
    target = _passages(settings, "source = 'debates' AND person_id IS NOT NULL AND date >= DATE '2026-01-01'")[0][
        "person_id"]
    f = members / "ministers-2010.json"
    data = json.loads(f.read_text())
    data["memberships"].append({
        "id": "test/new-post", "source": "datadotparl/governmentpost", "organization_id": "house-of-commons",
        "person_id": target, "role": "Minister of State (Department for Transport)", "start_date": "2026-01-01"})
    f.write_text(json.dumps(data))

    def broken(self, person_ids, batch=50):
        raise RuntimeError("disk full")

    with monkeypatch.context() as m:
        m.setattr(Indexer, "reenrich_people", broken)
        failed = sync()
    assert "error" in failed and failed["reference"]["people_changed"] == [target]

    retry = sync()  # member data now looks unchanged, but the queued person is still refreshed
    assert "error" not in retry and retry["reference"]["people_changed"] == []
    assert retry["index"]["people_reenriched"] == 1 and retry["index"]["passages_reenriched"] >= 1
    after = _passages(settings, f"person_id = {sql_str(target)} AND date >= DATE '2026-01-01'")
    assert after and all("transport" in r["gov_portfolios"] for r in after)
    assert sync()["index"]["people_reenriched"] == 0


@pytest.mark.filterwarnings("ignore:optimize.cleanup_older_than=0")
def test_superseded_index_versions_are_deleted(settings, xml_mirror, tmp_path, monkeypatch):
    members = tmp_path / "members"
    shutil.copytree(MEMBERS, members)
    run_sync(settings, fetch=False, members_dir=members, embedder=HashingEmbedder(64), embed_minutes=None, workers=1,
             today=date(2026, 10, 1))
    _republish_answer(xml_mirror)
    idx = SearchIndex(settings.index_dir, 64)
    Indexer(settings, idx, StateDB(settings.state_db)).index_pending()
    before = len(idx.passages().list_versions())
    assert before > 2
    monkeypatch.setattr(lance, "KEEP_OLD_VERSIONS", timedelta(0))
    idx.optimize()
    assert len(idx.passages().list_versions()) < before
    assert _copies(settings, "passages", ANSWER)  # the current data is intact


def test_display_only_member_fields_do_not_trigger_reindexing(reference_dir):
    from hansard.reference.build import build_reference
    from hansard.sync import changed_people

    from .conftest import GEO_CSV

    fresh = build_reference(MEMBERS, GEO_CSV)
    for m in fresh.memberships:
        m["settlement"], m["urban_area"] = "city", "Somewhere new"
    assert changed_people(reference_dir, fresh) == set()


def test_keyword_only_installations_skip_meaning_search(settings, xml_mirror, tmp_path):
    import dataclasses

    members = tmp_path / "members"
    shutil.copytree(MEMBERS, members)
    s = dataclasses.replace(settings, raw={**settings.raw, "embedding": {**settings.embedding, "enabled": False}})
    stats = run_sync(s, fetch=False, members_dir=members, embedder=HashingEmbedder(64), embed_minutes=None,
                     workers=1, today=date(2026, 10, 1))
    assert "embed" not in stats
    assert SearchIndex(s.index_dir, 64).stats()["vectors"] == 0


def test_bootstrap_choices_are_remembered_for_later_syncs(tmp_path, monkeypatch):
    from hansard.config import load_settings, save_local_settings

    monkeypatch.setenv("HANSARD_DATA_DIR", str(tmp_path))
    save_local_settings(tmp_path, {"archive": {"start_date": "2025-01-01"}})
    save_local_settings(tmp_path, {"embedding": {"enabled": False}})  # merged, not replaced
    s = load_settings()
    assert s.archive_start == date(2025, 1, 1)
    assert s.embedding["enabled"] is False and s.embedding["dim"]  # other embedding settings kept


def test_backup_is_a_checked_copy_and_restores(settings, tmp_path):
    import tarfile

    from hansard.backup import make_backup, restore_backup
    from hansard.store.state import StateDB

    state = StateDB(settings.state_db)
    state.set("marker", "before")
    (settings.data_dir / "settings.local.toml").write_text('[archive]\nstart_date = "2015-01-01"\n')
    dest = tmp_path / "backups"
    first = make_backup(settings, dest, keep=2, day="2026-10-01")
    with tarfile.open(first) as tar:
        assert sorted(tar.getnames()) == ["settings.local.toml", "state.sqlite"]
    make_backup(settings, dest, keep=2, day="2026-10-02")
    make_backup(settings, dest, keep=2, day="2026-10-03")
    assert sorted(p.name for p in dest.iterdir()) == ["where-they-stand-2026-10-02.tar.gz",
                                                       "where-they-stand-2026-10-03.tar.gz"]

    state.set("marker", "after")
    restore_backup(settings, dest / "where-they-stand-2026-10-03.tar.gz")
    assert StateDB(settings.state_db).get("marker") == "before"


def test_an_update_killed_before_merging_is_finished_by_the_next(settings, xml_mirror, tmp_path):
    members = tmp_path / "members"
    shutil.copytree(MEMBERS, members)
    kw = dict(fetch=False, members_dir=members, embedder=HashingEmbedder(64), embed_minutes=None, workers=1,
              today=date(2026, 10, 1))
    run_sync(settings, **kw)
    # an update that added passages and was killed before merging them into the keyword index
    _republish_answer(xml_mirror)
    idx = SearchIndex(settings.index_dir, 64)
    state = StateDB(settings.state_db)
    state.start_run("sync")
    from hansard.ingest import ingest

    ingest(settings, workers=1, state=state)
    Indexer(settings, idx, state).index_pending()
    assert idx.unmerged() > 0
    out = run_sync(settings, **kw)
    assert out["index"]["passages_written"] == 0  # nothing new to add: only the merge was left
    assert idx.unmerged() == 0
    killed = state.runs(5)[1]
    assert killed["status"] == "failed" and "stopped before finishing" in killed["error"]


def test_only_one_update_runs_at_a_time(settings):
    import fcntl

    from hansard.sync import SyncRunning

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with open(settings.data_dir / "sync.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SyncRunning):
            run_sync(settings, fetch=False, embed_minutes=0)


def test_the_vectors_table_is_indexed_by_passage(settings, xml_mirror, tmp_path):
    """Without it, updating speaker details on a few passages reads every vector into memory."""
    members = tmp_path / "members"
    shutil.copytree(MEMBERS, members)
    run_sync(settings, fetch=False, members_dir=members, embedder=HashingEmbedder(64), embed_minutes=None, workers=1,
             today=date(2026, 10, 1))
    idx = SearchIndex(settings.index_dir, 64)
    assert any("passage_id" in (ix.columns or []) for ix in idx.vectors().list_indices())
    assert idx.ensure_vector_ids() is False  # already there


def test_one_off_fetches_wait_for_a_running_update(settings, monkeypatch):
    from typer.testing import CliRunner

    from hansard import cli
    from hansard.sync import update_lock

    monkeypatch.setattr(cli, "_settings", lambda: settings)
    with update_lock(settings):
        out = CliRunner().invoke(cli.app, ["devolved", "--files", "0"])
    assert out.exit_code == 1 and "An update is running" in out.output
