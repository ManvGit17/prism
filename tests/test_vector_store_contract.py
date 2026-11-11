"""Contract tests every VectorStore implementation must pass, plus the fake embedder."""

from pathlib import Path

import pytest

from prism.indexer.embedder import Embedder, FakeEmbedder
from prism.indexer.pipeline import index_repository
from prism.store import InMemoryGraphStore, VectorItem, VectorStore

A = "repo-a"
B = "repo-b"


def _item(qn: str, path: str, vector: tuple[float, ...]) -> VectorItem:
    return VectorItem(qualified_name=qn, path=path, vector=vector)


def test_upsert_get_count_and_search(vector_store: VectorStore) -> None:
    vector_store.upsert(
        A,
        [
            _item("m.x", "m.py", (1.0, 0.0, 0.0)),
            _item("m.y", "m.py", (0.7, 0.7, 0.0)),
            _item("n.z", "n.py", (0.0, 0.0, 1.0)),
        ],
    )
    assert vector_store.count(A) == 3
    item = vector_store.get(A, "m.y")
    assert item is not None
    assert item.vector == (0.7, 0.7, 0.0)

    hits = vector_store.search(A, (1.0, 0.1, 0.0), k=2)
    assert [h.qualified_name for h in hits] == ["m.x", "m.y"]
    assert hits[0].score > hits[1].score
    assert hits[0].score == pytest.approx(0.995, abs=1e-3)
    assert [h.qualified_name for h in vector_store.search(A, (1.0, 0.1, 0.0), k=10)] == [
        "m.x",
        "m.y",
        "n.z",
    ]
    excluded = vector_store.search(A, (1.0, 0.1, 0.0), k=10, exclude_paths={"m.py"})
    assert [h.qualified_name for h in excluded] == ["n.z"]


def test_upsert_replaces_by_qualified_name(vector_store: VectorStore) -> None:
    vector_store.upsert(A, [_item("m.x", "m.py", (1.0, 0.0))])
    vector_store.upsert(A, [_item("m.x", "m.py", (0.0, 1.0))])
    assert vector_store.count(A) == 1
    assert vector_store.search(A, (0.0, 1.0), k=1)[0].score == pytest.approx(1.0)


def test_dimension_mismatch_is_rejected(vector_store: VectorStore) -> None:
    vector_store.upsert(A, [_item("m.x", "m.py", (1.0, 0.0))])
    with pytest.raises(ValueError):
        vector_store.upsert(A, [_item("m.y", "m.py", (1.0, 0.0, 0.0))])
    assert vector_store.count(A) == 1


def test_repo_isolation(vector_store: VectorStore) -> None:
    vector_store.upsert(A, [_item("m.x", "m.py", (1.0, 0.0))])
    vector_store.upsert(B, [_item("m.x", "m.py", (0.0, 1.0)), _item("m.y", "m.py", (1.0, 0.0))])
    hits = vector_store.search(A, (1.0, 0.0), k=10)
    assert [h.qualified_name for h in hits] == ["m.x"]
    a_item = vector_store.get(A, "m.x")
    assert a_item is not None
    assert a_item.vector == (1.0, 0.0)
    assert vector_store.search("nobody", (1.0, 0.0), k=10) == []
    vector_store.delete_repo(B)
    assert vector_store.count(B) == 0
    assert vector_store.count(A) == 1


def test_remove_files(vector_store: VectorStore) -> None:
    vector_store.upsert(A, [_item("m.x", "m.py", (1.0, 0.0)), _item("n.y", "n.py", (0.0, 1.0))])
    vector_store.remove_files(A, ["m.py"])
    assert vector_store.get(A, "m.x") is None
    assert vector_store.count(A) == 1


def test_fake_embedder_is_deterministic_and_normalised() -> None:
    embedder: Embedder = FakeEmbedder(dimension=32)
    first = embedder.embed(["def add_item(cart, item)", ""])
    second = FakeEmbedder(dimension=32).embed(["def add_item(cart, item)", ""])
    assert first == second
    assert len(first[0]) == embedder.dimension == 32
    assert sum(x * x for x in first[0]) == pytest.approx(1.0)
    assert first[1] == (0.0,) * 32


def test_fake_embedder_scores_shared_words_higher() -> None:
    embedder = FakeEmbedder(dimension=256)
    query, near, far = embedder.embed(
        ["compute cart total price", "cart total price with tax", "parse json config file"]
    )
    dot_near = sum(a * b for a, b in zip(query, near, strict=True))
    dot_far = sum(a * b for a, b in zip(query, far, strict=True))
    assert dot_near > dot_far


def test_index_repository_embeds_every_function(
    vector_store: VectorStore, sample_repo: Path
) -> None:
    graph = InMemoryGraphStore()
    embedder = FakeEmbedder(dimension=128)
    report = index_repository(sample_repo, A, graph, vectors=vector_store, embedder=embedder)
    assert report.vectors == graph.stats(A).functions == vector_store.count(A)
    query = embedder.embed(["slugify make a slug clean replace text"])[0]
    assert vector_store.search(A, query, k=1)[0].qualified_name == "shop.util.slugify"
