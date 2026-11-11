from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from repo_ids import TEST_PREFIX

from prism.config import get_settings
from prism.store import GraphStore, InMemoryGraphStore, InMemoryVectorStore, VectorStore

# The fixture repos are sample code to parse, not tests to run.
collect_ignore_glob = ["fixtures/*"]

SAMPLE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


def _neo4j() -> GraphStore:
    from prism.store.neo4j_store import Neo4jGraphStore

    return Neo4jGraphStore.from_settings()


def _pgvector() -> VectorStore:
    from prism.store.pgvector_store import PgVectorStore

    return PgVectorStore.from_settings(embedder="test-embedder")


# Every store implementation runs through the same contract tests. Persistent
# ones need the Compose services, so they carry the smoke mark.
GRAPH_STORES: dict[str, Callable[[], GraphStore]] = {"memory": InMemoryGraphStore, "neo4j": _neo4j}
VECTOR_STORES: dict[str, Callable[[], VectorStore]] = {
    "memory": InMemoryVectorStore,
    "pgvector": _pgvector,
}
_PERSISTENT = {"neo4j", "pgvector"}


def _params(names: dict[str, object]) -> list[object]:
    return [
        pytest.param(n, marks=pytest.mark.smoke) if n in _PERSISTENT else pytest.param(n)
        for n in sorted(names)
    ]


@pytest.fixture(params=_params(dict(GRAPH_STORES)))
def graph_store(request: pytest.FixtureRequest) -> Iterator[GraphStore]:
    store = GRAPH_STORES[request.param]()
    try:
        yield store
    finally:
        for rid in store.repo_ids():
            if rid.startswith(TEST_PREFIX):
                store.delete_repo(rid)
        store.close()


@pytest.fixture(params=_params(dict(VECTOR_STORES)))
def vector_store(request: pytest.FixtureRequest) -> Iterator[VectorStore]:
    store = VECTOR_STORES[request.param]()
    try:
        yield store
    finally:
        for rid in store.repo_ids():
            if rid.startswith(TEST_PREFIX):
                store.delete_repo(rid)
        store.close()


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests see the default threshold unless they set PRISM_CALL_CONFIDENCE_THRESHOLD."""
    monkeypatch.delenv("PRISM_CALL_CONFIDENCE_THRESHOLD", raising=False)
    get_settings.cache_clear()


@pytest.fixture
def sample_repo() -> Path:
    return SAMPLE_REPO
