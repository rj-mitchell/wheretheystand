"""Keep a sparse git checkout of mysociety/parlparse ``members/`` up to date.

mySociety commits "Automatic data update" changes to the members data most
days; ``git pull`` brings in ministerial reshuffles, committee changes, new
peers and by-election winners. The commit SHA is recorded with every build so
any answer can be traced to the exact member-data version used.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

SPARSE_PATHS = ["members"]
# a clean environment, apart from network settings a proxied machine needs to reach GitHub
_PASS_THROUGH = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy",
                 "SSL_CERT_FILE", "GIT_SSL_CAINFO")


def _git(*args: str, cwd: Path | None = None) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                         env={"GIT_LFS_SKIP_SMUDGE": "1", "PATH": "/usr/bin:/bin:/usr/local/bin",
                              **{k: v for k in _PASS_THROUGH if (v := os.environ.get(k))}})
    return out.stdout.strip()


def ensure_checkout(repo_url: str, dest: Path, branch: str = "master") -> str:
    """Clone (first run) or fast-forward the sparse checkout. Returns HEAD SHA."""
    dest = Path(dest)
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        log.info("cloning %s (members/ only)", repo_url)
        _git("clone", "--depth", "1", "--filter=blob:none", "--sparse", "--branch", branch, repo_url, str(dest))
        _git("sparse-checkout", "set", *SPARSE_PATHS, cwd=dest)
    else:
        _git("fetch", "--depth", "1", "origin", branch, cwd=dest)
        _git("reset", "--hard", f"origin/{branch}", cwd=dest)
    return head_sha(dest)


def head_sha(dest: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=dest)


def members_dir(dest: Path) -> Path:
    return Path(dest) / "members"
