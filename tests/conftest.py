from collections.abc import Callable
from pathlib import Path

import pytest

from prism.config import get_settings
from prism.store import GraphStore, InMemoryGraphStore, InMemoryVectorStore, VectorStore

# The fixture repo is sample code to parse, not tests to run.
collect_ignore_glob = ["fixtures/*"]

SAMPLE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"

# Every store implementation runs through the same contract tests. The Neo4j and
# pgvector stores join these lists with a `smoke` mark when they exist.
GRAPH_STORES: dict[str, Callable[[], GraphStore]] = {"memory": InMemoryGraphStore}
VECTOR_STORES: dict[str, Callable[[], VectorStore]] = {"memory": InMemoryVectorStore}


@pytest.fixture(params=sorted(GRAPH_STORES))
def graph_store(request: pytest.FixtureRequest) -> GraphStore:
    factory = GRAPH_STORES[request.param]
    return factory()


@pytest.fixture(params=sorted(VECTOR_STORES))
def vector_store(request: pytest.FixtureRequest) -> VectorStore:
    factory = VECTOR_STORES[request.param]
    return factory()


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests see the default threshold unless they set PRISM_CALL_CONFIDENCE_THRESHOLD."""
    monkeypatch.delenv("PRISM_CALL_CONFIDENCE_THRESHOLD", raising=False)
    get_settings.cache_clear()


@pytest.fixture
def sample_repo() -> Path:
    return SAMPLE_REPO
