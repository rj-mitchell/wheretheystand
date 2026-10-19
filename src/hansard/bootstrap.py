"""One-command setup (``hansard bootstrap``).

Full mode:   parlparse member data -> Hansard XML since the archive start ->
             Parquet -> keyword index -> embeddings (optionally time-limited,
             resumable by ``hansard embed`` / the daily sync) -> smoke queries.
Sample mode: everything offline from the bundled test fixtures (real Hansard
             excerpts and member data), with the dependency-free hashing
             embedder, so the app can be tried in minutes without downloads.
"""

from __future__ import annotations

import dataclasses
import logging
import shutil
import time
from datetime import date
from typing import Any

from hansard.config import REPO_ROOT, Settings, load_settings, save_local_settings
from hansard.store.parquet import compact

log = logging.getLogger(__name__)

SMOKE_QUERIES = [
    ("How have ministers justified delays to HS2?", {"offices": ["gov"]}, ["transport"]),
    ("agricultural planning and land use", {"regions": ["North East", "North West", "Yorkshire and The Humber"]},
     ["environment"]),
    ("trial by jury", {}, ["justice"]),
]


def smoke_test(settings: Settings) -> list[dict[str, Any]]:
    from hansard.engine import Engine, Filters

    eng = Engine(settings)
    out = []
    for q, f, ports in SMOKE_QUERIES:
        t = time.time()
        res = eng.search(q, Filters(**f), portfolios=ports, limit=5)
        top = res.records[0] if res.records else None
        out.append({
            "query": q,
            "seconds": round(time.time() - t, 3),
            "results": len(res.records),
            "top": None if not top else {
                "date": top["date"], "speaker": (top["speaker"] or {}).get("name"),
                "roles": [o["title"] for o in (top["speaker"] or {}).get("offices", [])],
                "heading": top["heading"],
            },
        })
    return out


def run_bootstrap(settings: Settings, *, sample: bool = False, since: date | None = None,
                  embed_minutes: float | None = None, workers: int = 3,
                  keyword_only: bool = False) -> dict[str, Any]:
    from hansard.cli import build_reference_data, embed_pending_cmd, fetch_xml, index_pending
    from hansard.ingest import ingest

    stats: dict[str, Any] = {"mode": "sample" if sample else "full"}
    t0 = time.time()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if sample:
        fixtures = REPO_ROOT / "tests" / "fixtures"
        (settings.data_dir / "settings.local.toml").write_text(
            '# Written by `hansard bootstrap --sample`: offline demo settings.\n'
            '[embedding]\nbackend = "hashing"\ndim = 64\n\n[auth]\nrequired = false\n')
        settings = load_settings()  # pick up the local overrides
        shutil.copytree(fixtures / "xml", settings.raw_xml_dir, dirs_exist_ok=True)
        shutil.copy(fixtures / "demographics.json", settings.data_dir / "raw" / "demographics.json")
        shutil.copy(fixtures / "elections.json", settings.data_dir / "raw" / "elections.json")
        stats["reference"] = build_reference_data(settings, members_dir=fixtures / "members")
        since = date(2010, 1, 1)
    else:
        # Remember the choices, so the daily sync keeps to them (it would otherwise fetch from 2010
        # and build meaning-based search for everything).
        local: dict[str, dict[str, Any]] = {}
        if since and since > settings.archive_start:
            local["archive"] = {"start_date": since.isoformat()}
        if keyword_only:
            local["embedding"] = {"enabled": False}
        if local:
            save_local_settings(settings.data_dir, local)
            settings = load_settings()
        stats["reference"] = build_reference_data(settings, pull=True)
        stats["fetch"] = fetch_xml(settings, since)
    stats["reference"].pop("unmatched_seats", None)
    stats["ingest"] = ingest(dataclasses.replace(settings), workers=workers, since=since).as_dict()
    stats["index"] = index_pending(settings, rebuild_fts=True)
    if settings.embedding.get("enabled", True):
        stats["embed"] = embed_pending_cmd(settings, max_minutes=embed_minutes)
    else:
        stats["embed"] = "skipped: keyword search only ([embedding] enabled = false)"
    cutoff = date.fromordinal(date.today().toordinal() - 90).isoformat()
    stats["compact"] = compact(settings.parquet_dir, cutoff)
    stats["smoke_test"] = smoke_test(settings)
    stats["seconds"] = round(time.time() - t0, 1)
    if sample:
        stats["next"] = "Run `hansard serve` and open http://127.0.0.1:8000 (sign-in disabled for the sample)."
    else:
        stats["next"] = ("Run `hansard serve` (or `docker compose up -d`) and open http://localhost:8000. "
                         "If embedding was time-limited, `hansard embed` (or the daily sync) continues it.")
    return stats
