"""``hansard`` command-line interface."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import date
from pathlib import Path

import typer

from hansard.config import STATIC_DIR, Settings, load_settings

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Where They Stand: sourced briefings on the record of the UK Parliament.")
user_app = typer.Typer(no_args_is_help=True, help="Manage invite-only user accounts.")
app.add_typer(user_app, name="user")
topics_app = typer.Typer(no_args_is_help=True, help="Topic briefings (config/topics.toml).")
app.add_typer(topics_app, name="topics")
eval_app = typer.Typer(no_args_is_help=True, help="Score the site against the test set (eval/questions.toml).")
app.add_typer(eval_app, name="eval")

log = logging.getLogger("hansard")


def _settings() -> Settings:
    return load_settings()


@contextmanager
def _one_at_a_time() -> Iterator[None]:
    """For the long one-off fetches: not while an update is running (they write the same files), and an update
    due meanwhile is skipped until the next one."""
    from hansard.sync import SyncRunning, update_lock

    with ExitStack() as stack:
        try:
            stack.enter_context(update_lock(_settings()))
        except SyncRunning:
            typer.echo("An update is running. Try again when it has finished "
                       "(`systemctl is-active hansard-sync` says inactive).", err=True)
            raise typer.Exit(1) from None
        yield


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "urllib3", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


# --------------------------------------------------------------------------- reference


def build_reference_data(settings: Settings, pull: bool = True, members_dir: Path | None = None) -> dict:
    from hansard.reference.build import build_reference
    from hansard.sources import parlparse
    from hansard.store.state import StateDB
    from hansard.sync import install_reference, refresh_demographics_data, refresh_elections_data

    state = StateDB(settings.state_db)
    sha = None
    live = members_dir is None  # the real member data (not the sample fixtures): fetch the extras too
    demographics, demo_status = refresh_demographics_data(settings, fetch=live)
    if members_dir is None:
        if pull:
            sha = parlparse.ensure_checkout(settings.sources["parlparse_repo"], settings.parlparse_dir,
                                            settings.sources.get("parlparse_branch", "master"))
        else:
            sha = parlparse.head_sha(settings.parlparse_dir)
        members_dir = parlparse.members_dir(settings.parlparse_dir)
    elections, elec_status = refresh_elections_data(settings, members_dir, fetch=live)
    ref = build_reference(members_dir, STATIC_DIR / "geo" / "constituencies.csv",
                          Path(settings.config_path).parent / "overrides" / "constituency_names.csv",
                          demographics, elections)
    people = install_reference(settings, state, ref, sha)
    # the index's speaker columns for these people are refreshed by the next `hansard sync`
    return {**ref.stats, "parlparse_sha": sha, "people_changed": len(people), "demographics": demo_status,
            "elections": elec_status}


@app.command()
def reference(pull: bool = typer.Option(True, help="git pull parlparse members/ first"),
              members: Path = typer.Option(None, help="use this members/ directory instead of the checkout")):
    """Build reference tables (people, seats, offices, regions) from parlparse."""
    _setup_logging()
    stats = build_reference_data(_settings(), pull=pull, members_dir=members)
    unmatched = stats.pop("unmatched_seats")
    typer.echo(json.dumps(stats, indent=2, default=str))
    if unmatched:
        typer.echo(f"WARNING: {len(unmatched)} seats since 1997 without a region: {unmatched[:10]}")


# --------------------------------------------------------------------------- data


@app.command()
def fetch(since: str = typer.Option(None, help="first date (default: archive start)"),
          transport: str = typer.Option("auto", help="auto | rsync | https")):
    """Mirror new and corrected scrapedxml files from TheyWorkForYou."""
    _setup_logging()
    typer.echo(json.dumps(fetch_xml(_settings(), _date(since), transport), indent=2))


def fetch_xml(settings: Settings, since: date | None = None, transport: str = "auto") -> dict:
    from hansard.ingest import DIVISION_DIRS
    from hansard.sources.twfy import HttpsMirror, rsync_available, rsync_directories

    since = since or settings.archive_start
    dirs = [*settings.record_dirs, *sorted(DIVISION_DIRS)]
    src = settings.sources
    if transport in ("auto", "rsync") and rsync_available(src["twfy_rsync"]):
        rsync_directories(src["twfy_rsync"], settings.raw_xml_dir, dirs, since.year)
        return {"transport": "rsync"}
    if transport == "rsync":
        raise typer.Exit("rsync to TheyWorkForYou is not reachable")
    mirror = HttpsMirror(src["twfy_https"], settings.raw_xml_dir)
    plan = mirror.plan(dirs, since)
    done = mirror.download(plan)
    return {"transport": "https", "planned": len(plan), "downloaded": len(done)}


@app.command()
def ingest(workers: int = typer.Option(3, help="parser processes (each streams one file)"),
           since: str = typer.Option(None), until: str = typer.Option(None)):
    """Parse new or corrected XML files into the Parquet store."""
    _setup_logging()
    from hansard.ingest import ingest as run

    stats = run(_settings(), workers=workers, since=_date(since), until=_date(until))
    typer.echo(json.dumps(stats.as_dict(), indent=2, default=str))


@app.command()
def index(rebuild_fts: bool = typer.Option(True, help="(re)build the BM25 index afterwards"),
          dedupe: bool = typer.Option(False, help="one-off: drop older copies of republished "
                                                  "records left by earlier versions of the indexer")):
    """Add newly ingested records to the keyword search index."""
    _setup_logging()
    settings = _settings()
    typer.echo(json.dumps(index_pending(settings, rebuild_fts, dedupe), indent=2))


def index_pending(settings: Settings, rebuild_fts: bool = True, dedupe: bool = False) -> dict:
    from hansard.index.build import Indexer
    from hansard.index.lance import SearchIndex
    from hansard.store.state import StateDB

    idx = SearchIndex(settings.index_dir, int(settings.embedding.get("dim", 384)))
    t = time.time()
    indexer = Indexer(settings, idx, StateDB(settings.state_db))
    out = indexer.dedupe() if dedupe else {}
    n = indexer.index_pending()
    if rebuild_fts and (n or dedupe):
        idx.build_indices()
    elif n:
        idx.optimize()
    return {"passages_written": n, **out, "seconds": round(time.time() - t, 1), **idx.stats()}


@app.command()
def embed(max_files: int = typer.Option(None, help="stop after this many files"),
          max_minutes: float = typer.Option(None, help="stop after this many minutes (resumable)")):
    """Embed passages for semantic search, newest first (resumable)."""
    _setup_logging()
    settings = _settings()
    typer.echo(json.dumps(embed_pending_cmd(settings, max_files, max_minutes), indent=2))


def embed_pending_cmd(settings: Settings, max_files=None, max_minutes=None) -> dict:
    from hansard.embed.base import get_embedder, model_fingerprint
    from hansard.index.build import embed_pending
    from hansard.index.lance import SearchIndex
    from hansard.store.state import StateDB

    state = StateDB(settings.state_db)
    idx = SearchIndex(settings.index_dir, int(settings.embedding.get("dim", 384)))
    embedder = get_embedder(settings)
    if settings.embedding.get("backend") == "fastembed":
        fp = model_fingerprint(settings.data_dir / "models")
        prev = state.get("embedding_model_sha256")
        if prev and prev != fp:
            raise typer.Exit("embedding model weights changed since the index was built; "
                             "rebuild vectors or restore the pinned model")
        state.set("embedding_model_sha256", fp)
        state.set("embedding_model", embedder.name)
    t = time.time()
    n = embed_pending(settings, idx, state, embedder, max_files=max_files,
                      max_seconds=max_minutes * 60 if max_minutes else None)
    if n and idx.stats()["vectors"] >= 10_000:
        idx.build_indices()
    return {"passages_embedded": n, "seconds": round(time.time() - t, 1), **idx.stats()}


@app.command("reindex-vectors")
def reindex_vectors(rows_per_partition: int = typer.Option(8192, help="vectors per index partition"),
                    tolerance: float = typer.Option(0.02, help="keep the new index if some setting finds the "
                                                              "nearest passages within this share of the old one"),
                    force: bool = typer.Option(False, help="keep the new index whatever the comparison shows")):
    """Rebuild the meaning-search index for the vectors there are now (faster searches), checking the results.

    Takes a while on the full record (tens of minutes, most of it comparing every vector to measure how many of the
    nearest passages each index finds); the site keeps working on the old index meanwhile, and the twice-daily
    update waits for it to finish."""
    from hansard.embed.base import get_embedder
    from hansard.index.lance import SearchIndex
    from hansard.index.retune import rebuild
    from hansard.sync import update_lock

    _setup_logging()
    settings = _settings()
    idx = SearchIndex(settings.index_dir, int(settings.embedding.get("dim", 384)))
    with update_lock(settings):
        current = {"nprobes": settings.ranking.get("vector_nprobes"),
                   "refine_factor": settings.ranking.get("vector_refine_factor")}
        out = rebuild(idx, get_embedder(settings), rows_per_partition, current=current, tolerance=tolerance,
                      force=force)
    if out.get("recommended"):
        from hansard.config import save_local_settings

        rec = out["recommended"]
        out["settings_file"] = str(save_local_settings(settings.data_dir, {"ranking": {
            "vector_nprobes": rec["nprobes"], "vector_refine_factor": rec["refine_factor"] or 0}}))
    typer.echo(json.dumps(out, indent=2))
    if out.get("kept"):
        typer.echo("\nRestart the app to search with the new index settings (bash deploy/update.sh does this).")


@app.command()
def status():
    """Show data, index and sync status."""
    from hansard.index.lance import SearchIndex
    from hansard.store.state import StateDB

    settings = _settings()
    state = StateDB(settings.state_db)
    manifest = state.manifest()
    idx = SearchIndex(settings.index_dir, int(settings.embedding.get("dim", 384)))
    out = {
        "data_dir": str(settings.data_dir),
        "files_ingested": len(manifest),
        "files_unindexed": len(state.unindexed()),
        "files_unembedded": len(state.unembedded()),
        "index": idx.stats(),
        "parlparse_sha": state.get("parlparse_sha"),
        "embedding_model": state.get("embedding_model"),
        "last_runs": state.runs(5),
    }
    typer.echo(json.dumps(out, indent=2, default=str))


# --------------------------------------------------------------------------- search


@app.command()
def search(query: str, date_from: str = typer.Option(None, "--from"), date_to: str = typer.Option(None, "--to"),
           region: list[str] = typer.Option(None), house: list[str] = typer.Option(None),
           portfolio: list[str] = typer.Option(None), office: list[str] = typer.Option(None),
           recency: float = typer.Option(None), role_weight: float = typer.Option(None),
           limit: int = typer.Option(10), no_written: bool = typer.Option(False)):
    """Run the deterministic retrieval pipeline and print ranked sources."""
    from hansard.engine import Engine, Filters, Scope

    settings = _settings()
    t = time.time()
    eng = Engine(settings)
    res = eng.search(query, Filters(date_from=_date(date_from), date_to=_date(date_to), regions=region or [],
                                    houses=house or [], offices=office or []),
                     Scope(written=not no_written), portfolio or [], recency, role_weight, limit)
    typer.echo(f"{res.candidates} candidates, semantic={'on' if res.semantic_available else 'off'}, "
               f"{time.time() - t:.2f}s")
    for i, r in enumerate(res.records, 1):
        sp = r["speaker"] or {}
        offices = "; ".join(o["title"] for o in sp.get("offices", [])) or "backbench"
        typer.echo(f"\n[{i}] {r['date']} {r['chamber']} col {r['colnum'] or r['uin'] or '-'} | "
                   f"{sp.get('name') or r['speaker_name_raw']} ({sp.get('party')}, {sp.get('constituency') or sp.get('house')})"
                   f" | {offices}\n    {r['heading']}\n    {r['excerpt'][:300]}...\n    {r['ranking']}")


# --------------------------------------------------------------------------- sync and bootstrap


@app.command()
def sync(embed_minutes: float = typer.Option(60.0, help="time budget for embedding new passages"),
         fetch_data: bool = typer.Option(True, "--fetch/--no-fetch")):
    """Daily incremental update: member data, new Hansard, index, embeddings."""
    _setup_logging()
    from hansard.sync import SyncRunning, run_sync

    try:
        stats = run_sync(_settings(), fetch=fetch_data, embed_minutes=embed_minutes)
    except SyncRunning as exc:
        raise typer.Exit(f"Not started: {exc}.") from None
    typer.echo(json.dumps(stats, indent=2, default=str))
    if stats.get("error"):
        raise typer.Exit(1)


@app.command()
def devolved(files: int = typer.Option(200, help="files to fetch in this run"),
             since: str = typer.Option(None, help="first date (default: the first sittings, 1998-99)")):
    """Fetch the Scottish Parliament, Senedd and Northern Ireland Assembly records (also done by every sync)."""
    _setup_logging()
    with _one_at_a_time():
        from hansard.devolved import SINCE, DevolvedStore, Members, sync_devolved
        from hansard.sync import devolved_path

        settings = _settings()
        stats = sync_devolved(DevolvedStore(devolved_path(settings)), max_files=files,
                              since=date.fromisoformat(since) if since else SINCE,
                              members=Members(settings.parlparse_dir / "members" / "people.json"))
        typer.echo(json.dumps(stats, indent=2))


@app.command()
def committees(force: bool = typer.Option(True, help="rebuild even if the copy is less than a week old")):
    """Fetch select committees, their reports and evidence sessions (also done weekly by the sync)."""
    _setup_logging()
    from hansard.sources.committees import refresh
    from hansard.sync import committees_path

    typer.echo(json.dumps(refresh(committees_path(_settings()), force=force), indent=2))


@app.command()
def evidence(sessions: int = typer.Option(10000, help="transcripts to fetch in this run (about 8,000 since 2014)"),
             embed_minutes: float = typer.Option(180, help="then embed answers for meaning-based search, for up to "
                                                           "this long (0 to skip)")):
    """Fetch committee evidence transcripts and embed them now, rather than a few hundred per update."""
    _setup_logging()
    with _one_at_a_time():
        import sqlite3

        from hansard.embed.base import get_embedder
        from hansard.index.evidence import EvidenceVectors, embed_evidence
        from hansard.sources.evidence import SINCE, EvidenceStore, sync_transcripts
        from hansard.sync import committees_path, evidence_index_path, evidence_path

        settings = _settings()
        with sqlite3.connect(committees_path(settings)) as cc:
            listed = cc.execute("SELECT id, date FROM sessions").fetchall()
        store = EvidenceStore(evidence_path(settings))
        out = {"transcripts": sync_transcripts(store, listed, since=SINCE, max_sessions=sessions)}
        if embed_minutes:
            emb = get_embedder(settings)
            out["embedded"] = embed_evidence(store, EvidenceVectors(evidence_index_path(settings), int(emb.dim)), emb,
                                             max_seconds=embed_minutes * 60)
        typer.echo(json.dumps(out, indent=2))


@app.command()
def interests(lords: bool = typer.Option(False, help="Read every peer's entries now, even if read this week"),
              archive: int = typer.Option(0, help="also read this many past Commons editions into the archive "
                                                  "(about 420 since 2000; sync reads 40 a run)")):
    """Fetch the newest registers of interests: Commons, Lords and the devolved parliaments (also done by sync)."""
    _setup_logging()
    with _one_at_a_time():
        if archive:
            from hansard.sources.regmem_archive import RegisterArchive, refresh_archive
            from hansard.sync import regmem_path

            typer.echo(json.dumps(refresh_archive(RegisterArchive(regmem_path(_settings()).with_name(
                "regmem_archive.sqlite")), max_editions=archive), indent=2))
        import duckdb

        from hansard.reference.profiles import attach_reference
        from hansard.sources.registers import refresh_devolved, refresh_lords, sitting_peers
        from hansard.sources.regmem import refresh
        from hansard.sync import regmem_path

        settings = _settings()
        out: dict = {"commons": refresh(regmem_path(settings))}
        con = duckdb.connect()
        attach_reference(con, settings.reference_dir)
        out["lords"] = refresh_lords(settings.data_dir / "regmem_lords.json", sitting_peers(con), force=lords)
        out["devolved"] = refresh_devolved(settings.data_dir)
        typer.echo(json.dumps(out, indent=2))


@app.command()
def whips(count: int = typer.Option(1500, help="votes to check in this run")):
    """Check votes for whip reports published by TheyWorkForYou Votes (also done by sync, a slice at a time)."""
    _setup_logging()
    from hansard.sources.whips import refresh

    settings = _settings()
    typer.echo(json.dumps(refresh(settings.data_dir / "whips.json", settings.data_dir / "raw" / "twfy_divisions.parquet",
                                  per_run=count, force=True), indent=2))


@app.command()
def directorships(count: int = typer.Option(400, help="members to look up in this run"),
                  show: str = typer.Option("", help="print what is held for members whose name contains this"),
                  check: bool = typer.Option(False, "--check", help="check the API key (never prints it)")):
    """Look up MPs' and peers' company directorships at Companies House (also done by sync, a slice at a time).
    Needs HANSARD_COMPANIES_HOUSE_KEY."""
    _setup_logging()
    from hansard.sources import parlparse
    from hansard.sources.companies import Directorships, refresh
    from hansard.sync import directorships_path

    if check:
        from hansard.sources.companies import check_key

        typer.echo(json.dumps(check_key(), indent=2))
        return
    settings = _settings()
    path = directorships_path(settings)
    if show:
        data = json.loads(path.read_text()) if path.exists() else {}
        for pid, rec in (data.get("people") or {}).items():
            if show.lower() in (rec.get("name") or "").lower():
                d = Directorships(path).for_person(pid) or {}
                typer.echo(f"{rec['name']} ({len(d.get('officers', []))} Companies House record(s))")
                for a in d.get("appointments", []):
                    typer.echo(f"  {a['role']}, {a['company']} ({a['status']}), {a['from'] or '?'} to "
                               f"{a['until'] or ('the company closed' if not a['current'] else 'now')}")
        return
    try:
        out = refresh(path, settings.reference_dir, parlparse.members_dir(settings.parlparse_dir) / "people.json",
                      per_run=count)
    except PermissionError as exc:
        raise typer.Exit(f"{exc}\nRun: docker compose exec app hansard directorships --check") from None
    typer.echo(json.dumps(out, indent=2))


@app.command()
def groups(force: bool = typer.Option(True, help="Download again even if the copy is less than a week old")):
    """Download the all-party parliamentary groups dataset and rebuild (also done weekly by sync)."""
    _setup_logging()
    import duckdb

    from hansard.reference.profiles import attach_reference
    from hansard.sources.appgs import refresh
    from hansard.sync import appgs_path

    settings = _settings()
    con = duckdb.connect()
    attach_reference(con, settings.reference_dir)
    typer.echo(json.dumps(refresh(appgs_path(settings), con, force=force), indent=2))


@app.command()
def donations(force: bool = typer.Option(True, help="Download again even if the copy is less than a week old")):
    """Download the Electoral Commission's register of donations and match it to MPs (also done weekly by sync)."""
    _setup_logging()
    import duckdb

    from hansard.reference.profiles import attach_reference
    from hansard.sources.donations import refresh
    from hansard.sync import donations_path

    settings = _settings()
    con = duckdb.connect()
    attach_reference(con, settings.reference_dir)
    typer.echo(json.dumps(refresh(donations_path(settings), con, force=force), indent=2))


@app.command()
def edms(check: bool = typer.Option(False, help="show what the API returns now, to confirm the fields read"),
         months: int = typer.Option(12, help="older months to fetch in this run"),
         signatures: int = typer.Option(1000, help="motions whose signatures to fetch in this run"),
         minutes: float = typer.Option(None, help="stop fetching signatures after this many minutes (default: when "
                                                   "done; the twice-daily update stops after 15)")):
    """Fetch early day motions (also done by every sync)."""
    _setup_logging()
    with _one_at_a_time():
        from hansard.sources.edms import EdmStore, sync_edms
        from hansard.sources.edms import check as edm_check
        from hansard.sync import edms_path

        if check:
            typer.echo(edm_check())
            return
        stats = sync_edms(EdmStore(edms_path(_settings())), months_per_run=months, sponsors_per_run=signatures,
                          sponsors_seconds=minutes * 60 if minutes else float("inf"))
        typer.echo(json.dumps(stats, indent=2, default=str))


@app.command()
def bootstrap(sample: bool = typer.Option(False, help="offline demo using bundled fixtures"),
              since: str = typer.Option(None, "--from", help="first date to load (default: archive start)"),
              embed_minutes: float = typer.Option(None, help="limit initial embedding time (resumable later)"),
              keyword_only: bool = typer.Option(False, help="skip meaning-based search (smaller server; remembered)"),
              workers: int = typer.Option(3)):
    """One-command setup: reference data, Hansard download, ingest, index, embeddings, smoke test."""
    _setup_logging()
    from hansard.bootstrap import run_bootstrap

    stats = run_bootstrap(_settings(), sample=sample, since=_date(since), embed_minutes=embed_minutes,
                          workers=workers, keyword_only=keyword_only)
    typer.echo(json.dumps(stats, indent=2, default=str))


# --------------------------------------------------------------------------- web


@app.command("set-key")
def set_key(key: str = typer.Option(..., prompt="Paste your Anthropic API key (it will not be shown)",
                                    hide_input=True)):
    """Save the Anthropic API key in the data folder (handy when running on your own computer)."""
    import os

    from hansard.rag.answer import KEY_FILE, make_client

    key = key.strip()
    if not key.startswith("sk-ant-"):
        raise typer.BadParameter("that doesn't look like an Anthropic API key (they start with sk-ant-)")
    path = _settings().data_dir / KEY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    os.chmod(path, 0o600)
    typer.echo(f"Saved ({len(key)} characters). Checking it with Anthropic...")
    import anthropic

    try:
        os.environ["HANSARD_ANTHROPIC_API_KEY"] = key
        make_client().models.list(limit=1)
    except anthropic.AuthenticationError:
        path.unlink()
        typer.echo("Anthropic did not accept that key, so it was not kept. Check it and run this again.")
        raise typer.Exit(1) from None
    except Exception as e:  # noqa: BLE001 - network trouble etc.: keep the key, say what happened
        typer.echo(f"Kept the key, but could not check it just now ({type(e).__name__}).")
        return
    typer.echo("The key works. Written briefings are switched on.")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False,
          behind_proxy: bool = typer.Option(False, help="trust X-Forwarded-* headers (e.g. behind Caddy)"),
          access_log: bool = typer.Option(False, help="log every request, with visitors' IP addresses (off by "
                                                      "default: the site keeps its own cookie-free counts)")):
    """Run the web application."""
    import uvicorn

    _setup_logging()
    os.environ.setdefault("HANSARD_WARM_UP", "1")  # load the search models at start, not on the first question
    uvicorn.run("hansard.api.app:create_app", host=host, port=port, factory=True, reload=reload,
                proxy_headers=behind_proxy, forwarded_allow_ips="*" if behind_proxy else None,
                access_log=access_log)


@app.command()
def backup(to: Path = typer.Option(None, help="folder for backups (default: <data>/backups)"),
           keep: int = typer.Option(14, help="how many daily backups to keep")):
    """Back up what cannot be rebuilt: saved briefings, reports, feedback, usage and visit counts."""
    from hansard.backup import make_backup
    from hansard.offsite import OffsiteError, send

    _setup_logging()
    archive = make_backup(_settings(), to, keep)
    typer.echo(f"Backed up to {archive}")
    try:  # and a copy off the server, when a Backblaze B2 bucket is set up
        out = send(archive)
    except OffsiteError as exc:
        typer.echo(f"The off-site copy failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo("Off-site copy: " + (f"stored in {out['bucket']} ({out['stored']} kept there)"
                                    if out.get("ok") else out["offsite"]))


@app.command()
def offsite():
    """Check the off-site backup bucket: whether the key works, and how many backups are stored there."""
    from hansard.offsite import check

    _setup_logging()
    typer.echo(json.dumps(check(), indent=2))


@app.command()
def restore(archive: Path = typer.Argument(..., exists=True, dir_okay=False),
            yes: bool = typer.Option(False, "--yes", help="do not ask for confirmation")):
    """Put a backup back (stop the site first). Replaces the current saved briefings, reports and usage."""
    from hansard.backup import restore_backup

    if not yes:
        typer.confirm("This replaces the current saved briefings, reports and usage records. Continue?", abort=True)
    restore_backup(_settings(), archive)
    typer.echo("Restored. Start the site again.")


@user_app.command("add")
def user_add(email: str, admin: bool = typer.Option(False),
             password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True)):
    """Invite a user (create an account)."""
    from hansard.api.auth import create_user
    from hansard.store.state import StateDB

    create_user(StateDB(_settings().state_db), email, password, admin)
    typer.echo(f"created {email}")


@user_app.command("list")
def user_list():
    from hansard.store.state import StateDB

    with StateDB(_settings().state_db).connect() as con:
        for r in con.execute("SELECT email, is_admin, disabled, created_at FROM users ORDER BY email"):
            typer.echo(f"{r[0]}  admin={bool(r[1])}  disabled={bool(r[2])}  since {r[3]}")


@user_app.command("disable")
def user_disable(email: str):
    from hansard.store.state import StateDB

    with StateDB(_settings().state_db).connect() as con:
        con.execute("UPDATE users SET disabled = 1 WHERE email = ?", (email,))
        con.execute("DELETE FROM sessions WHERE user_id IN (SELECT user_id FROM users WHERE email = ?)", (email,))
    typer.echo(f"disabled {email}")


# ---- topic briefings ----------------------------------------------------------------------------------------------


@topics_app.command("list")
def topics_list():
    """Each topic, its current version and when it was last checked."""
    from hansard.store.state import StateDB
    from hansard.topics import topic_listing

    for t in topic_listing(_settings(), StateDB(_settings().state_db)):
        cur = t["current"]
        chk = t["last_check"]
        typer.echo(f"{t['slug']:32} {('written ' + cur['prepared_at'][:10]) if cur else 'not written yet':22} "
                   f"{t['versions']} version(s)  {('last check: ' + chk['outcome']) if chk else ''}")


@topics_app.command("refresh")
def topics_refresh(only: list[str] = typer.Option(None, help="just these topics (slugs)"),
                   force: bool = typer.Option(False, help="rewrite even if not due or the evidence is unchanged"),
                   limit: int = typer.Option(None, help="at most this many rewrites (default: max_per_run)")):
    """Rewrite the topic briefings that are due. Each rewrite costs about 11p."""
    from hansard.rag.pipeline import Pipeline
    from hansard.store.state import StateDB
    from hansard.topics import refresh_topics

    settings = _settings()
    _setup_logging()
    res = refresh_topics(settings, StateDB(settings.state_db), Pipeline(settings), only=only or None, force=force,
                         limit=limit, progress=typer.echo)
    typer.echo(f"{sum(1 for r in res if r['outcome'].startswith('rewritten'))} rewritten")


# ---- the test set ------------------------------------------------------------------------------------------------

EVAL_DIR = Path("eval")


def _eval_setup(questions: Path, only: str | None):
    from hansard.evaluate import load_questions

    today, qs = load_questions(questions)
    if only:
        keep = set(only.split(","))
        qs = [q for q in qs if q.id in keep]
    return today, qs


def _client():
    from hansard.rag.answer import make_client

    return make_client()


def _load_plans(path: Path) -> dict[str, dict]:
    run = json.loads(path.read_text(encoding="utf-8"))
    return {r["id"]: r["plan"] for r in run["rows"] if r.get("plan")}


@eval_app.command("plans")
def eval_plans(model: str = typer.Option(None, help="model that reads the questions (default: the configured one)"),
               effort: str = typer.Option(None, help="low, medium or high"),
               questions: Path = typer.Option(EVAL_DIR / "questions.toml"), only: str = typer.Option(None),
               out: Path = typer.Option(EVAL_DIR / "runs")):
    """Score how each question is read (the filters chosen). Costs a little: one model call per question."""
    from hansard.evaluate import run_plans, save
    from hansard.rag.pipeline import Pipeline

    settings = _settings()
    today, qs = _eval_setup(questions, only)
    parties = Pipeline(settings).parties()
    res = run_plans(settings, qs, today, _client(), parties, model, effort)
    path = save(out, f"plans-{res['model']}", res)
    typer.echo(json.dumps(res["summary"], indent=1))
    typer.echo(f"saved {path}")


@eval_app.command("sources")
def eval_sources(plans: Path = typer.Option(..., help="a saved plans run: the filters each question was read as"),
                 variant: list[str] = typer.Option(["baseline"], help="baseline or rerank:<model>[:question|keywords|both[:weight]]"),
                 judge: str = typer.Option("claude-haiku-5-5", help="model that grades relevance"),
                 questions: Path = typer.Option(EVAL_DIR / "questions.toml"), only: str = typer.Option(None),
                 out: Path = typer.Option(EVAL_DIR / "runs"),
                 judgments: Path = typer.Option(EVAL_DIR / "judgments.jsonl")):
    """Compare ranking variants on the relevance and balance of the sources they choose."""
    from hansard.evaluate import (
        judge_sources,
        judgments_load,
        parse_variant,
        run_sources,
        save,
        summarise_sources,
    )

    settings = _settings()
    today, qs = _eval_setup(questions, only)
    plan_map = _load_plans(plans)
    runs = []
    for v in variant:
        typer.echo(f"{v}:")
        run = run_sources(settings, plan_map, qs, today, parse_variant(v))
        save(out, f"sources-{v.replace(':', '_').replace('/', '-')}", run)
        runs.append(run)
    graded = judge_sources(settings, _client(), qs, runs, judgments, judge)
    known = judgments_load(judgments)
    summaries = [summarise_sources(r, known) for r in runs]
    for sm in summaries:
        typer.echo(json.dumps({k: v for k, v in sm.items() if k != "per_question"}))
    typer.echo(f"grading cost ${graded['cost_usd']} ({graded['graded']} passages graded)")
    save(out, "sources-summary", {"summaries": summaries, "grading": graded})


@eval_app.command("briefings")
def eval_briefings(plans: Path = typer.Option(...), model: list[str] = typer.Option(["claude-opus-5-5"]),
                   judge: str = typer.Option("claude-opus-5-5", help="model for the side-by-side judgement"),
                   questions: Path = typer.Option(EVAL_DIR / "questions.toml"), only: str = typer.Option(None),
                   out: Path = typer.Option(EVAL_DIR / "runs")):
    """Write briefings with each model (about $0.06-0.15 each) and compare them; the first model is the baseline."""
    from hansard.evaluate import compare_briefings, run_briefings, save

    settings = _settings()
    today, qs = _eval_setup(questions, only)
    plan_map, client = _load_plans(plans), _client()
    runs = []
    for m in model:
        typer.echo(f"{m}:")
        run = run_briefings(settings, plan_map, qs, today, client, m)
        save(out, f"briefings-{m}", run)
        typer.echo(json.dumps(run["summary"]))
        runs.append(run)
    for other in runs[1:]:
        cmp = compare_briefings(settings, client, runs[0], other, judge)
        save(out, f"compare-{runs[0]['model']}-{other['model']}", cmp)
        typer.echo(json.dumps({k: cmp[k] for k in ("a", "b", "tally", "cost_usd")}))


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())

__all__ = ["app", "shutil"]
