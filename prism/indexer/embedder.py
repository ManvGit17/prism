"""Embedder interface and a deterministic fake for tests.

The real code embedder (PLAN.md Phase 1) comes in a later stage and implements
the same `Embedder` protocol.
"""

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

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
    """What gets embedded for a function: its qualified name and full source.

    The source covers decorators, signature, docstring and body.
    """
    start, end = node.span
    return f"{node.qualified_name}\n{source[start:end].decode('utf-8', 'replace')}"
