"""One place that decides which stores a process uses (`PRISM_STORE`)."""

from dataclasses import dataclass
from typing import Literal

from prism.config import Settings, get_settings
from prism.indexer.embedder import Embedder, OllamaEmbedder
from prism.store.base import GraphStore, VectorStore
from prism.store.ledger import IndexLedger
from prism.store.memory import InMemoryGraphStore, InMemoryVectorStore

StoreKind = Literal["memory", "persistent"]


@dataclass
class Stores:
    kind: StoreKind
    graph: GraphStore
    vectors: VectorStore | None = None
    ledger: IndexLedger | None = None
    embedder: Embedder | None = None

    def close(self) -> None:
        self.graph.close()
        if self.vectors is not None:
            self.vectors.close()
        if self.ledger is not None:
            self.ledger.close()
        close = getattr(self.embedder, "close", None)
        if callable(close):
            close()


class StoreUnavailable(RuntimeError):
    """The persistent stores could not be reached."""


def open_stores(
    kind: StoreKind | None = None,
    *,
    settings: Settings | None = None,
    embedder: Embedder | None = None,
    with_vectors: bool = True,
) -> Stores:
    """Open the stores. "memory" gives a fresh in-memory graph and no ledger or vectors
    unless an embedder is passed. "persistent" gives Neo4j, pgvector and the ledger, and
    raises StoreUnavailable if Neo4j or Postgres cannot be reached.
    """
    s = settings or get_settings()
    chosen: StoreKind = kind or s.store
    if chosen == "memory":
        vectors = InMemoryVectorStore() if embedder is not None else None
        return Stores(kind="memory", graph=InMemoryGraphStore(), vectors=vectors, embedder=embedder)

    # Imported here so the in-memory path never needs the drivers to connect.
    from prism.store.neo4j_store import Neo4jGraphStore
    from prism.store.pgvector_store import PgVectorStore

    try:
        graph = Neo4jGraphStore.from_settings(s)
    except Exception as exc:
        raise StoreUnavailable(f"Neo4j at {s.neo4j_uri}: {exc}") from exc
    try:
        ledger = IndexLedger.from_settings(s)
        emb: Embedder | None = None
        vectors_store: VectorStore | None = None
        if with_vectors:
            emb = embedder or OllamaEmbedder()
            vectors_store = PgVectorStore.from_settings(s, embedder=emb.name)
    except Exception as exc:
        graph.close()
        raise StoreUnavailable(f"Postgres at {s.postgres_host}:{s.postgres_port}: {exc}") from exc
    return Stores(
        kind="persistent", graph=graph, vectors=vectors_store, ledger=ledger, embedder=emb
    )
