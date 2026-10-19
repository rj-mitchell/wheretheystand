from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from hansard.config import STATIC_DIR, load_settings
from hansard.reference.build import build_reference, write_reference
from hansard.reference.profiles import ProfileResolver
from hansard.sources.demographics import load_demographics
from hansard.sources.elections import load_elections

FIXTURES = Path(__file__).parent / "fixtures"
MEMBERS = FIXTURES / "members"
XML = FIXTURES / "xml"
GEO_CSV = STATIC_DIR / "geo" / "constituencies.csv"
DEMOGRAPHICS = FIXTURES / "demographics.json"   # real values for the fixture members, same format as the cache
ELECTIONS = FIXTURES / "elections.json"         # real seats and results for the fixture members

CAMERON = "uk.org.publicwhip/person/10777"
BENN = "uk.org.publicwhip/person/10669"
GREENING = "uk.org.publicwhip/person/11771"


@pytest.fixture(scope="session")
def reference(tmp_path_factory):
    ref = build_reference(MEMBERS, GEO_CSV, demographics=load_demographics(DEMOGRAPHICS),
                          elections=load_elections(ELECTIONS, snapshot=False))
    out = tmp_path_factory.mktemp("reference")
    write_reference(ref, out)
    return ref, out


@pytest.fixture(scope="session")
def reference_dir(reference) -> Path:
    return reference[1]


@pytest.fixture(scope="session")
def resolver(reference_dir) -> ProfileResolver:
    return ProfileResolver(reference_dir)


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    """Settings pointing at an empty temporary data directory."""
    monkeypatch.setenv("HANSARD_DATA_DIR", str(tmp_path / "data"))
    return load_settings(**{"embedding.backend": "hashing", "embedding.dim": 64})


@pytest.fixture(scope="session")
def indexed_settings(tmp_path_factory):
    """A complete, offline data directory built from the fixtures (hashing embedder)."""
    import dataclasses
    from datetime import date

    from hansard.embed.base import HashingEmbedder
    from hansard.index.build import Indexer, embed_pending
    from hansard.index.lance import SearchIndex
    from hansard.ingest import ingest
    from hansard.store.state import StateDB

    root = tmp_path_factory.mktemp("indexed")
    base = load_settings(**{"embedding.backend": "hashing", "embedding.dim": 64})
    s = dataclasses.replace(base, data_dir=root / "data")
    shutil.copytree(XML, s.raw_xml_dir)
    write_reference(build_reference(MEMBERS, GEO_CSV, demographics=load_demographics(DEMOGRAPHICS),
                                    elections=load_elections(ELECTIONS, snapshot=False)), s.reference_dir)
    state = StateDB(s.state_db)
    ingest(s, workers=1, since=date(2010, 1, 1), state=state)
    idx = SearchIndex(s.index_dir, 64)
    Indexer(s, idx, state).index_pending()
    idx.build_indices()
    embed_pending(s, idx, state, HashingEmbedder(64))
    return s


@pytest.fixture()
def xml_mirror(settings) -> Path:
    """Copy the XML fixtures into the settings' raw mirror directory."""
    dest = settings.raw_xml_dir
    shutil.copytree(XML, dest)
    return dest
