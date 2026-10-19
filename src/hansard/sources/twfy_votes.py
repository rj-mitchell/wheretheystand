"""Plain-English descriptions of votes, from TheyWorkForYou Votes (mySociety).

For some Commons and Lords divisions, TheyWorkForYou Votes publishes a clearer title and a sentence on what voting
aye (and sometimes no) meant, written by mySociety or taken from Parliament's own description. They are published as
``pwdata/votes/divisions.parquet``, keyed by the same division identifiers this site uses. Fetched about once a week
(1–2 MB); if it cannot be reached the previous copy is kept.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import httpx

from hansard.sources.demographics import USER_AGENT

log = logging.getLogger(__name__)

URL = "https://www.theyworkforyou.com/pwdata/votes/divisions.parquet"
REFRESH = timedelta(days=6)
CREDIT = "TheyWorkForYou Votes (mySociety)"


def refresh(path: Path, client: httpx.Client | None = None, force: bool = False) -> dict[str, Any]:
    path = Path(path)
    if not force and path.exists():
        age = datetime.now(UTC) - datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if age < REFRESH:
            return {"updated": False, "age_days": age.days}
    own = client is None
    client = client or httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    try:
        resp = client.get(URL)
        resp.raise_for_status()
    finally:
        if own:
            client.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(resp.content)
    n = duckdb.connect().execute(f"SELECT count(*) FROM read_parquet('{tmp.as_posix()}')").fetchone()[0]
    tmp.replace(path)
    return {"updated": True, "divisions": n}


def descriptions(path: Path) -> dict[str, dict[str, str]]:
    """{division id: {"title", "aye_means", "no_means"}} for Westminster divisions with a description or better title."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        rows = duckdb.connect().execute(f"""
            SELECT source_gid, division_title, title_priority, yes_text, no_text
            FROM read_parquet('{path.as_posix()}')
            WHERE chamber IN ('commons', 'lords') AND coalesce(language, 'en') = 'en'
              AND (coalesce(yes_text, '') <> '' OR title_priority IN ('MANUAL', 'PARLIAMENT_DESCRIBED'))""").fetchall()
    except duckdb.Error:
        log.exception("vote descriptions unreadable")
        return {}
    out = {}
    for gid, title, priority, yes, no in rows:
        d = {}
        if priority in ("MANUAL", "PARLIAMENT_DESCRIBED") and title:
            d["title"] = " ".join(html.unescape(re.sub(r"<[^>]+>", "", title)).split())
        if yes and yes.strip():
            d["aye_means"] = _sentence(yes)
        if no and no.strip():
            d["no_means"] = _sentence(no)
        if d and gid:
            out[gid] = d
    return out


def _sentence(text: str) -> str:
    t = " ".join(html.unescape(re.sub(r"<[^>]+>", "", text)).split()).rstrip(" .")
    return t[:1].lower() + t[1:] + "." if t else ""
