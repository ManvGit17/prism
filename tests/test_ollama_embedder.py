"""The real embedder against the host Ollama. Needs `ollama pull nomic-embed-text`."""

import math

import pytest

from prism.indexer.embedder import Embedder, OllamaEmbedder

pytestmark = pytest.mark.smoke

ADD = "def add_item(cart, item):\n    cart.items.append(item)\n    return cart"
APPEND = (
    "def put_in_basket(basket, product):\n    basket.products.append(product)\n    return basket"
)
UNRELATED = "def parse_config(path):\n    with open(path) as f:\n        return json.load(f)"


def _cos(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_vectors_have_the_declared_dimension_and_unit_length() -> None:
    embedder: Embedder = OllamaEmbedder(batch_size=2)
    vectors = embedder.embed([ADD, APPEND, UNRELATED])
    assert len(vectors) == 3
    assert all(len(v) == embedder.dimension for v in vectors)
    assert embedder.dimension == 768  # nomic-embed-text
    assert all(math.isclose(_cos(v, v), 1.0, rel_tol=1e-6) for v in vectors)
    assert embedder.name == "ollama:nomic-embed-text"


def test_similar_functions_score_closer() -> None:
    add, append, unrelated = OllamaEmbedder().embed([ADD, APPEND, UNRELATED])
    assert _cos(add, append) > _cos(add, unrelated)


def test_long_input_is_truncated_not_rejected() -> None:
    long_body = "def big():\n" + "    x = compute(x)\n" * 4000
    (vector,) = OllamaEmbedder().embed([long_body])
    assert len(vector) == 768
