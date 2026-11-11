"""Per-PR overlays over a base store. Runs against every GraphStore implementation."""

from pathlib import Path

from prism.indexer.embedder import FakeEmbedder
from prism.indexer.model import Resolution
from prism.indexer.pipeline import (
    build_overlay,
    embed_functions,
    index_repository,
    parse_sources,
)
from prism.store import (
    GraphReader,
    GraphStore,
    InMemoryGraphStore,
    InMemoryVectorStore,
    OverlayVectors,
)

R = "r"
OTHER = "other"

# Two different head commits of shop/pricing.py.
PRICING_A = b'''"""Prices and tax, version A."""

TAX = 0.2


def with_tax(amount):
    return apply_rounding(amount * (1 + TAX))


def apply_rounding(value):
    return int(value * 100) / 100
'''

PRICING_B = b'''"""Prices and tax, version B."""

from shop.models import apply_discount

TAX = 0.25


def with_tax(amount):
    discounted = apply_discount(amount)
    return round_price(discounted * (1 + TAX))


def round_price(value):
    return round(value, 2)
'''


def _callees(g: GraphReader, qn: str) -> set[str]:
    return {r.node.qualified_name for r in g.callees(R, qn)}


def _callers(g: GraphReader, qn: str) -> set[str]:
    return {r.node.qualified_name for r in g.callers(R, qn)}


def _base(graph_store: GraphStore, sample_repo: Path) -> GraphStore:
    index_repository(sample_repo, R, graph_store)
    index_repository(sample_repo, OTHER, graph_store)
    return graph_store


def test_two_overlays_answer_differently_at_the_same_time(
    graph_store: GraphStore, sample_repo: Path
) -> None:
    base = _base(graph_store, sample_repo)
    before_nodes = sorted(n.qualified_name for n in base.iter_nodes(R))
    before_edges = sorted(e.ident for e in base.iter_edges(R))

    a = build_overlay(base, R, {"shop/pricing.py": PRICING_A})
    b = build_overlay(base, R, {"shop/pricing.py": PRICING_B})

    # Callees of the changed function differ per overlay.
    assert _callees(base, "shop.pricing.with_tax") == {"shop.pricing.round_price"}
    assert _callees(a, "shop.pricing.with_tax") == {"shop.pricing.apply_rounding"}
    assert _callees(b, "shop.pricing.with_tax") == {
        "shop.pricing.round_price",
        "shop.models.apply_discount",
    }
    edge = next(r.edge for r in b.callees(R, "shop.pricing.with_tax") if "discount" in r.node.name)
    assert edge.resolution is Resolution.IMPORT

    # Version A deleted round_price; version B kept it.
    assert a.get_node(R, "shop.pricing.round_price") is None
    assert a.callers(R, "shop.pricing.round_price") == []
    assert _callers(b, "shop.pricing.round_price") == {"shop.pricing.with_tax"}
    assert b.get_node(R, "shop.pricing.apply_rounding") is None
    assert [n.qualified_name for n in a.lookup(R, "apply_rounding")] == [
        "shop.pricing.apply_rounding"
    ]
    assert b.lookup(R, "apply_rounding") == []

    # The new caller in B shows up from the callee's side too.
    assert "shop.pricing.with_tax" in _callers(b, "shop.models.apply_discount")
    assert "shop.pricing.with_tax" not in _callers(a, "shop.models.apply_discount")

    # Calls from unchanged files into the changed file are kept in both.
    for g in (base, a, b):
        assert {"shop.cart.Cart.checkout", "tests.pricing_test.test_with_tax"} <= _callers(
            g, "shop.pricing.with_tax"
        )
        assert [r.node.qualified_name for r in g.tests_for(R, "shop.pricing.with_tax")] == [
            "tests.pricing_test.test_with_tax"
        ]

    # Enclosing symbol follows each version's line layout.
    base_sym = base.enclosing_symbol(R, "shop/pricing.py", 10)
    a_sym = a.enclosing_symbol(R, "shop/pricing.py", 10)
    b_sym = b.enclosing_symbol(R, "shop/pricing.py", 10)
    assert base_sym is not None and base_sym.qualified_name == "shop.pricing.round_price"
    assert a_sym is not None and a_sym.qualified_name == "shop.pricing.apply_rounding"
    assert b_sym is not None and b_sym.qualified_name == "shop.pricing.with_tax"

    # Neighbourhoods differ, and the base is untouched.
    a_near = {n.node.qualified_name for n in a.neighbours(R, "shop.pricing.with_tax", hops=1)}
    b_near = {n.node.qualified_name for n in b.neighbours(R, "shop.pricing.with_tax", hops=1)}
    assert "shop.pricing.apply_rounding" in a_near - b_near
    assert "shop.models.apply_discount" in b_near - a_near
    assert sorted(n.qualified_name for n in base.iter_nodes(R)) == before_nodes
    assert sorted(e.ident for e in base.iter_edges(R)) == before_edges


def test_overlay_stats_and_file_paths(graph_store: GraphStore, sample_repo: Path) -> None:
    base = _base(graph_store, sample_repo)
    a = build_overlay(base, R, {"shop/pricing.py": PRICING_A})
    base_stats = base.stats(R)
    a_stats = a.stats(R)
    assert a_stats.functions == base_stats.functions  # round_price out, apply_rounding in
    assert a.file_paths(R) == base.file_paths(R)
    assert a_stats.edges["CALLS"] == base_stats.edges["CALLS"]


def test_overlay_with_added_and_deleted_files(graph_store: GraphStore, sample_repo: Path) -> None:
    base = _base(graph_store, sample_repo)
    new_file = b"from shop.cart import make_cart\n\n\ndef quick_cart():\n    return make_cart()\n"
    ov = build_overlay(
        base, R, {"shop/quick.py": new_file, "shop/jobs.py": None, "README.md": b"# hi\n"}
    )
    assert "shop/quick.py" in ov.file_paths(R)
    assert "shop/jobs.py" not in ov.file_paths(R)
    assert "README.md" not in ov.file_paths(R)
    assert ov.get_node(R, "shop.jobs.dispatch") is None
    assert ov.nodes_in_file(R, "shop/jobs.py") == []
    assert ov.callers(R, "shop.jobs.dispatch", min_confidence=0) == []
    assert "shop.quick.quick_cart" in _callers(ov, "shop.cart.make_cart")
    assert "shop.quick.quick_cart" not in _callers(base, "shop.cart.make_cart")
    assert base.get_node(R, "shop.jobs.dispatch") is not None
    assert all(e.path != "shop/jobs.py" for e in ov.iter_edges(R))


def test_overlay_only_changes_its_own_repo(graph_store: GraphStore, sample_repo: Path) -> None:
    base = _base(graph_store, sample_repo)
    a = build_overlay(base, R, {"shop/pricing.py": PRICING_A})
    assert a.get_node(OTHER, "shop.pricing.round_price") is not None
    assert {r.node.qualified_name for r in a.callees(OTHER, "shop.pricing.with_tax")} == {
        "shop.pricing.round_price"
    }
    assert {n.repo_id for n in a.iter_nodes(OTHER)} == {OTHER}
    assert {n.repo_id for n in a.iter_nodes(R)} == {R}
    assert all(r.node.repo_id == R for r in a.callers(R, "shop.pricing.with_tax"))


def test_vector_overlay_hides_changed_files(sample_repo: Path) -> None:
    embedder = FakeEmbedder(dimension=128)
    vectors = InMemoryVectorStore()
    index_repository(sample_repo, R, InMemoryGraphStore(), vectors=vectors, embedder=embedder)
    assert vectors.get(R, "shop.pricing.round_price") is not None

    items = embed_functions(parse_sources(R, {"shop/pricing.py": PRICING_A}), embedder)
    ov = OverlayVectors(vectors, R, items, shadowed_paths={"shop/pricing.py"})
    assert ov.get(R, "shop.pricing.round_price") is None
    assert ov.get(R, "shop.pricing.apply_rounding") is not None
    query = embedder.embed(["apply_rounding int value 100"])[0]
    hits = ov.search(R, query, k=3)
    assert hits[0].qualified_name == "shop.pricing.apply_rounding"
    assert all(h.qualified_name != "shop.pricing.round_price" for h in ov.search(R, query, k=50))
    # The base store still has the old function.
    assert any(
        h.qualified_name == "shop.pricing.round_price" for h in vectors.search(R, query, k=50)
    )
