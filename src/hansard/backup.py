"""Daily backup of the one thing that cannot be rebuilt from public sources: the state database.

It holds the saved briefings, reports from readers, feedback, usage and spending, and visit counts. Everything else
(Hansard, member data, the search index) is downloaded or rebuilt by ``hansard bootstrap``.

A backup is a consistent copy made with SQLite's online backup (safe while the site is running), checked, then
packed with the local settings into ``where-they-stand-YYYY-MM-DD.tar.gz``. The newest ``keep`` are kept.
"""

from __future__ import annotations

import sqlite3
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from hansard.config import Settings

PREFIX = "where-they-stand-"


def make_backup(settings: Settings, dest: Path | None = None, keep: int = 14, day: str | None = None) -> Path:
    dest = Path(dest or settings.data_dir / "backups")
    dest.mkdir(parents=True, exist_ok=True)
    day = day or datetime.now(UTC).strftime("%Y-%m-%d")
    out = dest / f"{PREFIX}{day}.tar.gz"
    with tempfile.TemporaryDirectory(dir=dest) as tmp:
        copy = Path(tmp) / "state.sqlite"
        src = sqlite3.connect(settings.state_db)
        dst = sqlite3.connect(copy)
        try:
            src.backup(dst)
            ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
            src.close()
        if ok != "ok":
            raise RuntimeError(f"the backup copy failed its integrity check: {ok}")
        partial = Path(tmp) / out.name
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(copy, arcname="state.sqlite")
            local = settings.data_dir / "settings.local.toml"
            if local.exists():
                tar.add(local, arcname="settings.local.toml")
        partial.replace(out)
    for old in sorted(dest.glob(f"{PREFIX}*.tar.gz"))[:-keep] if keep > 0 else []:
        old.unlink()
    return out


def restore_backup(settings: Settings, archive: Path) -> None:
    """Put a backup's state database (and local settings) back. Stop the site first."""
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(archive) as tar:
        tar.extractall(tmp, filter="data")
        db = Path(tmp) / "state.sqlite"
        con = sqlite3.connect(db)
        try:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("the backup's database failed its integrity check")
        finally:
            con.close()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(f"{settings.state_db}{suffix}").unlink(missing_ok=True)
        db.replace(settings.state_db)
        local = Path(tmp) / "settings.local.toml"
        if local.exists():
            local.replace(settings.data_dir / "settings.local.toml")
