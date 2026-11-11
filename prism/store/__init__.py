"""Graph and vector stores behind one interface (PLAN.md Phase 1, revision 3 item 3)."""

from prism.store.base import (
    GraphReader,
    GraphStats,
    GraphStore,
    Neighbour,
    Related,
    VectorHit,
    VectorItem,
    VectorReader,
    VectorStore,
)
from prism.store.memory import InMemoryGraphStore, InMemoryVectorStore
from prism.store.overlay import OverlayGraph, OverlayVectors

__all__ = [
    "GraphReader",
    "GraphStats",
    "GraphStore",
    "InMemoryGraphStore",
    "InMemoryVectorStore",
    "Neighbour",
    "OverlayGraph",
    "OverlayVectors",
    "Related",
    "VectorHit",
    "VectorItem",
    "VectorReader",
    "VectorStore",
]
