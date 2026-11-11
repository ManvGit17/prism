import pytest

from prism.retrieval.rerank import CrossEncoderReranker, NoReranker, OverlapReranker, Reranker

QUERY = "def add_item(cart, item):\n    cart.items.append(item)"
RELEVANT = "class Cart:\n    def add(self, item):\n        self.items.append(item)"
NEAR = "def put_in_basket(basket, product):\n    basket.products.append(product)"
UNRELATED = "def parse_config(path):\n    with open(path) as f:\n        return json.load(f)"


def test_overlap_reranker_is_deterministic() -> None:
    reranker: Reranker = OverlapReranker()
    scores = reranker.score(QUERY, [RELEVANT, UNRELATED])
    assert scores == reranker.score(QUERY, [RELEVANT, UNRELATED])
    assert scores[0] > scores[1]
    assert NoReranker().score(QUERY, [RELEVANT, UNRELATED]) == [0.0, 0.0]


@pytest.mark.download
def test_cross_encoder_ranks_relevant_code_first() -> None:
    relevant, near, unrelated = CrossEncoderReranker().score(QUERY, [RELEVANT, NEAR, UNRELATED])
    assert relevant > unrelated
    assert near > unrelated
