"""Mirror TheyWorkForYou / ParlParse scrapedxml files.

Two transports:

* **rsync** from ``data.theyworkforyou.com::parldata`` (TheyWorkForYou's
  recommended method; used on the server when port 873 is reachable);
* **HTTPS** from ``https://www.theyworkforyou.com/pwdata/scrapedxml`` (works
  through restrictive proxies). Directory listings give each file's size and
  modification time, so only new or changed files are downloaded.

File naming
-----------
Day files carry a revision letter that is bumped when Hansard is corrected:
``debates2025-12-18a.xml`` .. ``debates2025-12-18d.xml``. Earlier revisions are
superseded (they may contain only ``<gidredirect>`` elements). Some newer
written-answer and statement files have no letter and are rewritten in place,
which the size/mtime comparison catches. Only the latest revision of each
*key* (file name without the revision letter) is kept.
"""

from __future__ import annotations

import html
import logging
import re
import shutil
import subprocess
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# directory -> (house, record family). Order matters for the CLI listing only.
SOURCES: dict[str, tuple[str, str]] = {
    "debates": ("commons", "debate"),
    "westminhall": ("commons", "westminster_hall"),
    "lordspages": ("lords", "debate"),
    "wrans": ("commons", "written_answer"),
    "lordswrans": ("lords", "written_answer"),
    "wms": ("commons", "written_statement"),
    "lordswms": ("lords", "written_statement"),
    "standing": ("commons", "public_bill_committee"),
    "divisionsonly": ("commons", "division"),
    "lordsdivisionsonly": ("lords", "division"),
}

_NAME_RE = re.compile(r"^(?P<stem>.*?(?P<date>\d{4}-\d{2}-\d{2}))(?P<rev>[a-z]?)\.xml$")
_LISTING_RE = re.compile(
    r'href="(?P<name>[^"/]+\.xml)".*?</td><td[^>]*>\s*(?P<mtime>\d{4}-\d\d-\d\d \d\d:\d\d)\s*</td>'
    r'<td[^>]*>\s*(?P<size>[\d.]+[KMG]?)\s*<', re.S)


@dataclass(frozen=True)
class RemoteFile:
    directory: str
    name: str
    key: str          # file name without revision letter, e.g. "debates2025-12-18"
    revision: str     # "" for unlettered files
    file_date: date
    size_hint: str = ""
    mtime: str = ""

    @property
    def rel_path(self) -> str:
        return f"{self.directory}/{self.name}"


def parse_name(directory: str, name: str, size_hint: str = "", mtime: str = "") -> RemoteFile | None:
    m = _NAME_RE.match(name)
    if not m:
        return None
    try:
        d = date.fromisoformat(m.group("date"))
    except ValueError:
        return None
    return RemoteFile(directory, name, m.group("stem"), m.group("rev"), d, size_hint, mtime)


def latest_revisions(files: Iterable[RemoteFile]) -> list[RemoteFile]:
    """Keep only the highest revision letter for each key."""
    best: dict[tuple[str, str], RemoteFile] = {}
    for f in files:
        k = (f.directory, f.key)
        if k not in best or f.revision > best[k].revision:
            best[k] = f
    return sorted(best.values(), key=lambda f: (f.directory, f.file_date, f.key))


def parse_listing(directory: str, page: str) -> list[RemoteFile]:
    out = []
    for m in _LISTING_RE.finditer(page):
        rf = parse_name(directory, html.unescape(m.group("name")), m.group("size"), m.group("mtime"))
        if rf:
            out.append(rf)
    return out


class HttpsMirror:
    def __init__(self, base_url: str, dest: Path, *, concurrency: int = 4, timeout: float = 60.0,
                 user_agent: str = "WhereTheyStand (open-source research tool; +https://github.com/rj-mitchell/wheretheystand)"):
        self.base_url = base_url.rstrip("/")
        self.dest = dest
        self.concurrency = concurrency
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": user_agent}, follow_redirects=True)

    def list_directory(self, directory: str) -> list[RemoteFile]:
        resp = self._get(f"{self.base_url}/{directory}/")
        return parse_listing(directory, resp.text)

    def plan(self, directories: Iterable[str], since: date, until: date | None = None) -> list[RemoteFile]:
        wanted: list[RemoteFile] = []
        for d in directories:
            files = [f for f in self.list_directory(d) if f.file_date >= since and (until is None or f.file_date <= until)]
            wanted.extend(latest_revisions(files))
        return wanted

    def needs_download(self, f: RemoteFile) -> bool:
        local = self.dest / f.rel_path
        if not local.exists():
            return True
        marker = local.with_suffix(local.suffix + ".mtime")
        return not marker.exists() or marker.read_text().strip() != f.mtime

    def download(self, files: list[RemoteFile], progress_every: int = 500) -> list[RemoteFile]:
        todo = [f for f in files if self.needs_download(f)]
        done: list[RemoteFile] = []
        if not todo:
            return done
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            for i, f in enumerate(pool.map(self._fetch_one, todo), 1):
                if f is not None:
                    done.append(f)
                if i % progress_every == 0:
                    log.info("downloaded %d/%d files", i, len(todo))
        return done

    def _fetch_one(self, f: RemoteFile) -> RemoteFile | None:
        local = self.dest / f.rel_path
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_suffix(".part")
        try:
            resp = self._get(f"{self.base_url}/{f.rel_path}")
        except httpx.HTTPError as exc:
            log.warning("failed to fetch %s: %s", f.rel_path, exc)
            return None
        tmp.write_bytes(resp.content)
        tmp.replace(local)
        local.with_suffix(local.suffix + ".mtime").write_text(f.mtime)
        return f

    def _get(self, url: str, attempts: int = 4) -> httpx.Response:
        delay = 2.0
        for attempt in range(attempts):
            try:
                resp = self.client.get(url)
                resp.raise_for_status()
                return resp
            except httpx.HTTPError:
                if attempt == attempts - 1:
                    raise
                time.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")


def rsync_available(source: str) -> bool:
    if not shutil.which("rsync"):
        return False
    try:
        subprocess.run(["rsync", "--list-only", f"{source}/"], check=True, capture_output=True, timeout=30)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def rsync_directories(source: str, dest: Path, directories: Iterable[str], since_year: int) -> None:
    """rsync the given directories, limited to files from ``since_year`` onwards."""
    years = range(since_year, datetime.now().year + 1)
    for d in directories:
        target = dest / d
        target.mkdir(parents=True, exist_ok=True)
        includes = [arg for y in years for arg in ("--include", f"*{y}-*.xml")]
        cmd = ["rsync", "-az", "--delete-after", "--exclude", ".svn", "--exclude", "tmp/",
               *includes, "--exclude", "*", f"{source}/{d}/", f"{target}/"]
        log.info("rsync %s", d)
        subprocess.run(cmd, check=True)


def local_files(root: Path, directories: Iterable[str], since: date) -> list[RemoteFile]:
    """Latest local revision of each key under ``root`` (a scrapedxml mirror)."""
    found: list[RemoteFile] = []
    for d in directories:
        base = root / d
        if not base.exists():
            continue
        for p in base.glob("*.xml"):
            rf = parse_name(d, p.name)
            if rf and rf.file_date >= since:
                found.append(rf)
    return latest_revisions(found)
