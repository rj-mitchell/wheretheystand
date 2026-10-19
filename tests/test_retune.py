from datetime import date

import numpy as np
import pytest

from hansard.index.lance import SearchIndex
from hansard.index.retune import SAMPLE_QUERIES, rebuild, recall

DIM = 16


class FakeEmbedder:
    def embed_query(self, text: str) -> np.ndarray:
        rng = np.random.default_rng(abs(hash(text)) % 2**32)
        v = rng.normal(size=DIM)
        return v / np.linalg.norm(v)


@pytest.fixture
def idx(tmp_path):
    idx = SearchIndex(tmp_path / "index", DIM)
    rng = np.random.default_rng(1)
    n = 12_000
    rows = [{"passage_id": f"p{i}", "record_id": f"r{i}", "source": "debates", "source_key": "k", "record_type": "speech",
             "house": "commons" if i % 2 else "lords", "date": date(2021, 1, 1), "year": 2021, "person_id": None,
             "party_id": None, "region": None, "is_gov": bool(i % 3 == 0), "is_shadow": False, "is_chair": False,
             "gov_portfolios": [], "shadow_portfolios": [], "chair_portfolios": []} for i in range(n)]
    vecs = rng.normal(size=(n, DIM))
    idx.add_vectors([], rows, vecs / np.linalg.norm(vecs, axis=1, keepdims=True))
    idx.rebuild_vector_index(rows_per_partition=4000)  # the "old" index: few large partitions
    return idx


def test_recall():
    assert recall([["a", "b"], ["c", "d"]], [["a", "x"], ["c", "d"]]) == 0.75
    assert recall([[]], [[]]) == 1.0


def test_rebuild_keeps_an_index_that_finds_the_same_passages(idx):
    out = rebuild(idx, FakeEmbedder(), rows_per_partition=1000, probes=(20, 40), refines=(None, 5), limit=50,
                  tolerance=1.0)
    assert out["kept"] and out["recommended"]["nprobes"] in (20, 40)
    assert 0 <= out["before"]["recall"] <= 1
    assert [(t["nprobes"], t["refine_factor"]) for t in out["trials"]] == [(20, None), (20, 5), (40, None), (40, 5)]
    assert len(idx.vector(FakeEmbedder().embed_query(SAMPLE_QUERIES[0][0]), None, 10)) == 10


def test_rebuild_puts_the_old_index_back_when_results_differ(idx):
    version = idx.vectors().version
    out = rebuild(idx, FakeEmbedder(), rows_per_partition=1000, probes=(1,), refines=(None,), limit=50,
                  tolerance=-1.0)
    assert not out["kept"] and "put back" in out["note"]
    assert idx.vectors().version > version  # restoring writes a new version equal to the old one
    assert len(idx.vector(FakeEmbedder().embed_query("x"), None, 10)) == 10
