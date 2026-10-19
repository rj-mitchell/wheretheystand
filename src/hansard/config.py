"""Settings loaded from config/settings.toml, with environment overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "settings.toml"
STATIC_DIR = REPO_ROOT / "data_static"


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]
    data_dir: Path
    config_path: Path
    overrides: dict[str, Any] = field(default_factory=dict)

    def section(self, name: str) -> dict[str, Any]:
        return self.raw.get(name, {})

    # Convenience accessors -------------------------------------------------
    @property
    def archive_start(self) -> date:
        return date.fromisoformat(self.section("archive").get("start_date", "1997-05-01"))

    @property
    def record_dirs(self) -> list[str]:
        return list(self.section("archive").get("record_dirs", []))

    @property
    def ranking(self) -> dict[str, Any]:
        return self.section("ranking")

    @property
    def answer(self) -> dict[str, Any]:
        return self.section("answer")

    @property
    def embedding(self) -> dict[str, Any]:
        return self.section("embedding")

    @property
    def regions(self) -> dict[str, list[str]]:
        return self.section("regions")

    @property
    def usage(self) -> dict[str, Any]:
        return self.section("usage")

    @property
    def sources(self) -> dict[str, Any]:
        return self.section("sources")

    # Data layout -------------------------------------------------------------
    @property
    def raw_xml_dir(self) -> Path:
        return self.data_dir / "raw" / "scrapedxml"

    @property
    def parlparse_dir(self) -> Path:
        return self.data_dir / "raw" / "parlparse"

    @property
    def reference_dir(self) -> Path:
        return self.data_dir / "reference"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    @property
    def state_db(self) -> Path:
        # SQLite: shared safely by the web app and the sync job (WAL mode).
        return self.data_dir / "state.sqlite"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def load_settings(config_path: str | Path | None = None, **overrides: Any) -> Settings:
    path = _resolve(config_path or os.environ.get("HANSARD_CONFIG") or DEFAULT_CONFIG)
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    data_dir = _resolve(os.environ.get("HANSARD_DATA_DIR") or raw.get("paths", {}).get("data_dir", "data"))
    # Per-installation overrides written by `hansard bootstrap` (e.g. the offline sample demo).
    local = data_dir / "settings.local.toml"
    if local.exists():
        with open(local, "rb") as fh:
            for section, values in tomllib.load(fh).items():
                raw.setdefault(section, {}).update(values)
    for dotted, value in overrides.items():
        section, _, key = dotted.partition(".")
        raw.setdefault(section, {})[key] = value
    return Settings(raw=raw, data_dir=data_dir, config_path=path, overrides=overrides)


def save_local_settings(data_dir: Path, updates: dict[str, dict[str, Any]]) -> Path:
    """Merge simple values into <data_dir>/settings.local.toml, so later commands (sync, serve) use them too."""
    path = data_dir / "settings.local.toml"
    current: dict[str, dict[str, Any]] = {}
    if path.exists():
        with open(path, "rb") as fh:
            current = tomllib.load(fh)
    for section, values in updates.items():
        current.setdefault(section, {}).update(values)

    def lit(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = ["# Per-installation settings written by `hansard bootstrap`; they override config/settings.toml."]
    for section, values in current.items():
        lines += ["", f"[{section}]", *(f"{k} = {lit(v)}" for k, v in values.items())]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
