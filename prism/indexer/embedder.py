"""Embedder interface, the Ollama embedder, and a deterministic fake for tests."""

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

import httpx

from prism.config import get_settings
from prism.indexer.model import FunctionNode

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+")


class Embedder(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """One unit-length vector per text, in order."""
        ...


class FakeEmbedder:
    """Feature hashing over lowercased word pieces. Texts sharing words score higher.

    Deterministic across processes (no use of `hash()`). Not a code model.
    """

    def __init__(self, dimension: int = 64) -> None:
        if dimension < 1:
            raise ValueError("dimension must be positive")
        self._dimension = dimension

    @property
    def name(self) -> str:
        return f"fake-hash-{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> tuple[float, ...]:
        vec = [0.0] * self._dimension
        for token in _TOKEN.findall(text):
            digest = hashlib.blake2b(token.lower().encode(), digest_size=8).digest()
            value = int.from_bytes(digest, "little")
            sign = 1.0 if value & 1 else -1.0
            vec[(value >> 1) % self._dimension] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0.0:
            return tuple(vec)
        return tuple(x / norm for x in vec)


def function_text(node: FunctionNode, source: bytes) -> str:
    """What gets embedded for a function: signature, docstring and body.

    The docstring is the first statement of the body, so the body carries it.
    Embedders truncate to their own input limit.
    """
    start, end = node.body_span
    body = source[start:end].decode("utf-8", "replace")
    return f"{node.signature}:\n    {body}"


class OllamaEmbedder:
    """Embeddings from the host Ollama `/api/embed` endpoint (ADR 0005).

    Texts are cut to `max_chars` and Ollama truncates anything still over the
    model's context. Vectors are normalised to unit length.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        url: str | None = None,
        batch_size: int | None = None,
        max_chars: int | None = None,
        prefix: str | None = None,
        timeout: float = 120.0,
        client: httpx.Client | None = None,
    ) -> None:
        s = get_settings()
        self.model = model or s.embedding_model
        self._url = (url or s.ollama_url).rstrip("/") + "/api/embed"
        self._batch = batch_size or s.embedding_batch_size
        self._max_chars = max_chars or s.embedding_max_chars
        self._prefix = s.embedding_document_prefix if prefix is None else prefix
        self._client = client or httpx.Client(timeout=timeout)
        self._dimension: int | None = None

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._dimension = len(self.embed(["dimension probe"])[0])
        return self._dimension

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        out: list[tuple[float, ...]] = []
        for i in range(0, len(texts), self._batch):
            chunk = [self._prefix + t[: self._max_chars] for t in texts[i : i + self._batch]]
            response = self._client.post(
                self._url,
                # keep_alive holds the model between batches and between index runs.
                json={"model": self.model, "input": chunk, "truncate": True, "keep_alive": "10m"},
            )
            response.raise_for_status()
            vectors = response.json()["embeddings"]
            if len(vectors) != len(chunk):
                raise RuntimeError(f"Ollama returned {len(vectors)} vectors for {len(chunk)} texts")
            out.extend(_unit(v) for v in vectors)
        if out and self._dimension is None:
            self._dimension = len(out[0])
        return out

    def close(self) -> None:
        self._client.close()


def _unit(values: Sequence[float]) -> tuple[float, ...]:
    norm = math.sqrt(sum(float(x) * float(x) for x in values))
    if norm == 0.0:
        return tuple(float(x) for x in values)
    return tuple(float(x) / norm for x in values)
