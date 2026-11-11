"""Rerankers score (query, document) pairs; higher means more relevant.

The real one is a cross-encoder run with ONNX Runtime through fastembed
(`Xenova/ms-marco-MiniLM-L-6-v2`, about 90 MB, downloaded once into
`PRISM_MODEL_CACHE_DIR`). No torch. ADR 0007.
"""

import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from prism.config import get_settings

if TYPE_CHECKING:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

_WORD = re.compile(r"[A-Za-z][a-z0-9]*|[A-Z]+(?![a-z])|\d+")
# The MiniLM cross-encoder reads 512 tokens; longer documents are cut.
_MAX_DOC_CHARS = 2000


class Reranker(Protocol):
    @property
    def name(self) -> str: ...

    def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


class CrossEncoderReranker:
    def __init__(self, model: str | None = None, cache_dir: Path | None = None) -> None:
        s = get_settings()
        self.model = model or s.reranker_model
        self._cache = cache_dir or Path(s.model_cache_dir).expanduser() / "fastembed"
        self._encoder: TextCrossEncoder | None = None

    @property
    def name(self) -> str:
        return f"cross-encoder:{self.model}"

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        if self._encoder is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._encoder = TextCrossEncoder(self.model, cache_dir=str(self._cache))
        docs = [d[:_MAX_DOC_CHARS] for d in documents]
        return [float(s) for s in self._encoder.rerank(query[:_MAX_DOC_CHARS], docs)]


def words(text: str) -> set[str]:
    """Lowercased word pieces, splitting snake_case and camelCase."""
    return {w.lower() for w in _WORD.findall(text)}


class OverlapReranker:
    """Deterministic stand-in for tests: share of the query's word pieces found in the document."""

    @property
    def name(self) -> str:
        return "overlap"

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        q = words(query)
        if not q:
            return [0.0] * len(documents)
        return [round(len(q & words(d)) / len(q), 6) for d in documents]


class NoReranker:
    """Every item scores 0, so items keep their collection order within a kind."""

    @property
    def name(self) -> str:
        return "none"

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        return [0.0] * len(documents)


def make_reranker(kind: str | None = None) -> Reranker:
    chosen = kind or get_settings().reranker
    if chosen == "cross-encoder":
        return CrossEncoderReranker()
    if chosen == "overlap":
        return OverlapReranker()
    return NoReranker()
