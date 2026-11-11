"""Contract tests every GraphStore implementation must pass.

The `graph_store` fixture (conftest.py) is parametrised over the store
implementations, so a new store runs through exactly these tests.
"""

from pathlib import Path

import pytest
from repo_ids import repo_id

from prism.indexer.model import (
    ClassNode,
    Edge,
    EdgeKind,
    FileGraph,
    FileNode,
    FunctionNode,
    Node,
)
from prism.indexer.pipeline import build_file_graphs, discover_files, parse_sources
from prism.store import GraphStore

A = repo_id("a")
B = repo_id("b")
NOBODY = repo_id("nobody")


def _sample_graphs(sample_repo: Path, repo_id: str) -> list[FileGraph]:
    paths, _ = discover_files(sample_repo)
    sources = {p: (sample_repo / p).read_bytes() for p in paths}
    return build_file_graphs(parse_sources(repo_id, sources))


def _load(store: GraphStore, sample_repo: Path, repo_id: str = A) -> None:
    store.replace_files(repo_id, _sample_graphs(sample_repo, repo_id))


def _qns(nodes: list[Node]) -> list[str]:
    return [n.qualified_name for n in nodes]


PRICING_V2 = b'''"""Prices and tax."""


def with_tax(amount):
    return amount * 1.2
'''


def _file_graph(repo_id: str, path: str, source: bytes) -> FileGraph:
    return build_file_graphs(parse_sources(repo_id, {path: source}))[0]


def test_lookup(graph_store: GraphStore, sample_repo: Path) -> None:
    _load(graph_store, sample_repo)
    assert _qns(graph_store.lookup(A, "shop.cart.Cart.add")) == ["shop.cart.Cart.add"]
    assert _qns(graph_store.lookup(A, "make_cart")) == ["shop.cart.make_cart"]
    assert _qns(graph_store.lookup(A, "Cart.add")) == ["shop.cart.Cart.add"]
    assert _qns(graph_store.lookup(A, "total")) == [
        "shop.models.DiscountItem.total",
        "shop.models.Item.total",
    ]
    assert graph_store.lookup(A, "no_such_symbol") == []
    node = graph_store.get_node(A, "shop/cart.py")
    assert isinstance(node, FileNode)
    assert node.module == "shop.cart"


def test_node_round_trip_keeps_fields(graph_store: GraphStore, sample_repo: Path) -> None:
    graphs = _sample_graphs(sample_repo, A)
    graph_store.replace_files(A, graphs)
    for graph in graphs:
        for node in graph.nodes:
            assert graph_store.get_node(A, node.qualified_name) == node


def test_callers_and_callees(graph_store: GraphStore, sample_repo: Path) -> None:
    _load(graph_store, sample_repo)
    callers = graph_store.callers(A, "shop.pricing.with_tax")
    assert _qns([r.node for r in callers]) == [
        "shop.cart.Cart.checkout",
        "tests.pricing_test.test_with_tax",
        "shop.legacy.old_total",
    ]
    assert all(r.edge.kind is EdgeKind.CALLS for r in callers)
    callees = graph_store.callees(A, "shop.cart.Cart.checkout")
    assert {r.node.qualified_name for r in callees} == {
        "shop.pricing.with_tax",
        "shop.cart.Cart.subtotal",
        "shop.util.log_event",
        "shop.models.apply_discount",
    }
    assert graph_store.callers(A, "no.such.symbol") == []


def test_neighbours_by_hops(graph_store: GraphStore, sample_repo: Path) -> None:
    _load(graph_store, sample_repo)
    one = graph_store.neighbours(A, "shop.pricing.round_price", hops=1)
    assert [(n.node.qualified_name, n.distance) for n in one] == [
        ("shop.pricing.with_tax", 1),
        ("shop/pricing.py", 1),
    ]
    two = {
        n.node.qualified_name: n.distance
        for n in graph_store.neighbours(A, "shop.pricing.round_price", hops=2)
    }
    assert two["shop.cart.Cart.checkout"] == 2
    assert two["tests.pricing_test.test_with_tax"] == 2
    assert two["shop/cart.py"] == 2  # cart.py imports pricing.py
    calls_only = graph_store.neighbours(
        A, "shop.pricing.round_price", hops=2, kinds=(EdgeKind.CALLS,)
    )
    assert {n.node.qualified_name for n in calls_only} == {
        "shop.pricing.with_tax",
        "shop.cart.Cart.checkout",
        "shop.legacy.old_total",
        "tests.pricing_test.test_with_tax",
    }
    assert graph_store.neighbours(A, "no.such.symbol", hops=2) == []


def test_neighbours_respect_the_threshold(graph_store: GraphStore, sample_repo: Path) -> None:
    _load(graph_store, sample_repo)
    default = graph_store.neighbours(A, "shop.jobs.Worker.run", kinds=(EdgeKind.CALLS,))
    assert default == []
    lowered = graph_store.neighbours(
        A, "shop.jobs.Worker.run", kinds=(EdgeKind.CALLS,), min_confidence=0.1
    )
    assert [n.node.qualified_name for n in lowered] == ["shop.jobs.dispatch"]


def test_tests_for(graph_store: GraphStore, sample_repo: Path) -> None:
    _load(graph_store, sample_repo)
    tests = graph_store.tests_for(A, "shop.cart.make_cart")
    assert [r.node.qualified_name for r in tests] == ["tests.test_cart.test_make_cart"]
    assert tests[0].edge.kind is EdgeKind.TESTS
    node = tests[0].node
    assert isinstance(node, FunctionNode)
    assert node.is_test


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (1, "shop/models.py"),  # module docstring
        (3, "shop/models.py"),  # blank line between definitions
        (7, "shop.models.Item"),  # class attribute
        (14, "shop.models.Item.total"),
        (16, "shop.models.Item.label"),  # decorator line
        (21, "shop.models.Item.label#2"),
        (27, "shop.models.DiscountItem.total"),
        (36, "shop.models.apply_discount"),
    ],
)
def test_enclosing_symbol(
    graph_store: GraphStore, sample_repo: Path, line: int, expected: str
) -> None:
    _load(graph_store, sample_repo)
    node = graph_store.enclosing_symbol(A, "shop/models.py", line)
    assert node is not None
    assert node.qualified_name == expected


def test_enclosing_symbol_nested_function(graph_store: GraphStore, sample_repo: Path) -> None:
    _load(graph_store, sample_repo)
    node = graph_store.enclosing_symbol(A, "shop/util.py", 9)
    assert node is not None
    assert node.qualified_name == "shop.util.cached.wrapper"


def test_enclosing_symbol_outside(graph_store: GraphStore, sample_repo: Path) -> None:
    _load(graph_store, sample_repo)
    assert graph_store.enclosing_symbol(A, "shop/models.py", 500) is None
    assert graph_store.enclosing_symbol(A, "missing.py", 1) is None


def test_stats(graph_store: GraphStore, sample_repo: Path) -> None:
    _load(graph_store, sample_repo)
    stats = graph_store.stats(A)
    assert (stats.files, stats.test_files) == (9, 2)
    assert (stats.classes, stats.functions, stats.methods, stats.test_functions) == (5, 29, 14, 3)
    assert stats.edges["CALLS"] == sum(stats.calls_by_resolution.values())
    assert stats.calls_retrievable < stats.edges["CALLS"]
    assert stats.threshold == 0.5
    empty = graph_store.stats(NOBODY)
    assert (empty.files, empty.functions, empty.edges["CALLS"]) == (0, 0, 0)


def test_repo_isolation(graph_store: GraphStore, sample_repo: Path) -> None:
    # Two repos with identical qualified names in one store.
    _load(graph_store, sample_repo, A)
    _load(graph_store, sample_repo, B)
    graph_store.replace_files(B, [_file_graph(B, "shop/pricing.py", PRICING_V2)])

    assert graph_store.get_node(B, "shop.pricing.round_price") is None
    a_node = graph_store.get_node(A, "shop.pricing.round_price")
    assert a_node is not None
    assert a_node.repo_id == A

    results: list[Node] = [
        *graph_store.lookup(A, "with_tax"),
        *[r.node for r in graph_store.callers(A, "shop.pricing.with_tax", min_confidence=0)],
        *[r.node for r in graph_store.callees(A, "shop.cart.Cart.checkout", min_confidence=0)],
        *[r.node for r in graph_store.tests_for(A, "shop.pricing.with_tax", min_confidence=0)],
        *[n.node for n in graph_store.neighbours(A, "shop.pricing.with_tax", hops=3)],
        *graph_store.iter_nodes(A),
        *graph_store.nodes_in_file(A, "shop/pricing.py"),
        *graph_store.find_by_name(A, "with_tax"),
    ]
    enclosing = graph_store.enclosing_symbol(A, "shop/pricing.py", 11)
    assert enclosing is not None
    results.append(enclosing)
    assert results
    assert {n.repo_id for n in results} == {A}
    assert {e.repo_id for e in graph_store.iter_edges(A)} == {A}

    # Repo A still has the old callee; repo B has the new version.
    assert {r.node.qualified_name for r in graph_store.callees(A, "shop.pricing.with_tax")} == {
        "shop.pricing.round_price"
    }
    assert graph_store.callees(B, "shop.pricing.with_tax") == []
    assert graph_store.stats(A).functions == graph_store.stats(B).functions + 1

    for query in (NOBODY,):
        assert graph_store.lookup(query, "with_tax") == []
        assert graph_store.get_node(query, "shop.pricing.with_tax") is None
        assert list(graph_store.iter_nodes(query)) == []


def test_write_checks_ownership(graph_store: GraphStore, sample_repo: Path) -> None:
    graph = _file_graph(A, "shop/pricing.py", PRICING_V2)
    with pytest.raises(ValueError):
        graph_store.replace_files(B, [graph])
    stray = Edge(kind=EdgeKind.CALLS, repo_id=A, src="x.y", dst="x.z", path="shop/pricing.py")
    with pytest.raises(ValueError):
        graph_store.replace_files(A, [graph.model_copy(update={"edges": (stray,)})])


def test_replace_file_removes_dropped_symbols_and_their_edges(
    graph_store: GraphStore, sample_repo: Path
) -> None:
    _load(graph_store, sample_repo)
    old_round = graph_store.get_node(A, "shop.pricing.round_price")
    assert old_round is not None
    graph_store.replace_files(A, [_file_graph(A, "shop/pricing.py", PRICING_V2)])

    assert graph_store.get_node(A, "shop.pricing.round_price") is None
    assert graph_store.callees(A, "shop.pricing.with_tax") == []
    # Edges from other files into a symbol that still exists stay.
    callers = {r.node.qualified_name for r in graph_store.callers(A, "shop.pricing.with_tax")}
    assert "shop.cart.Cart.checkout" in callers
    enclosing = graph_store.enclosing_symbol(A, "shop/pricing.py", 5)
    assert enclosing is not None
    assert enclosing.qualified_name == "shop.pricing.with_tax"

    # Restoring the file restores the edges that start in it.
    original = (sample_repo / "shop/pricing.py").read_bytes()
    graph_store.replace_files(A, [_file_graph(A, "shop/pricing.py", original)])
    assert graph_store.get_node(A, "shop.pricing.round_price") == old_round
    assert {r.node.qualified_name for r in graph_store.callers(A, "shop.pricing.round_price")} == {
        "shop.pricing.with_tax"
    }


def test_removed_symbol_takes_inbound_edges_with_it(
    graph_store: GraphStore, sample_repo: Path
) -> None:
    _load(graph_store, sample_repo)
    no_tax = b"def round_price(value):\n    return round(value, 2)\n"
    graph_store.replace_files(A, [_file_graph(A, "shop/pricing.py", no_tax)])
    assert graph_store.get_node(A, "shop.pricing.with_tax") is None

    # Edges from other files into a removed symbol are gone for good, as with a
    # DETACH DELETE; they come back only when those files are indexed again.
    original = (sample_repo / "shop/pricing.py").read_bytes()
    graph_store.replace_files(A, [_file_graph(A, "shop/pricing.py", original)])
    assert graph_store.callers(A, "shop.pricing.with_tax", min_confidence=0) == []
    assert [r.node.qualified_name for r in graph_store.callees(A, "shop.pricing.with_tax")] == [
        "shop.pricing.round_price"
    ]


def test_remove_files_and_delete_repo(graph_store: GraphStore, sample_repo: Path) -> None:
    _load(graph_store, sample_repo)
    graph_store.remove_files(A, ["shop/cart.py"])
    assert graph_store.get_node(A, "shop.cart.make_cart") is None
    assert graph_store.nodes_in_file(A, "shop/cart.py") == []
    assert "shop/cart.py" not in graph_store.file_paths(A)
    assert graph_store.callers(A, "shop.pricing.with_tax", min_confidence=0) != []
    assert all(
        r.node.path != "shop/cart.py"
        for r in graph_store.callers(A, "shop.pricing.with_tax", min_confidence=0)
    )
    assert graph_store.callees(A, "tests.test_cart.test_make_cart", min_confidence=0) == []
    assert all(
        e.src != "shop/cart.py" and e.dst != "shop/cart.py" for e in graph_store.iter_edges(A)
    )

    graph_store.delete_repo(A)
    assert list(graph_store.iter_nodes(A)) == []
    assert graph_store.file_paths(A) == []


def test_edge_to_a_missing_node_is_not_stored(graph_store: GraphStore) -> None:
    common = {"repo_id": A, "path": "m.py", "language": "python", "start_line": 1, "end_line": 2}
    file_node = FileNode(qualified_name="m.py", name="m.py", module="m", **common)
    cls = ClassNode(qualified_name="m.K", name="K", owner="m.py", span=(0, 10), **common)
    dangling = Edge(kind=EdgeKind.CALLS, repo_id=A, src="m.K", dst="elsewhere.f", path="m.py")
    defines = Edge(kind=EdgeKind.DEFINES, repo_id=A, src="m.py", dst="m.K", path="m.py")
    graph_store.replace_files(
        A, [FileGraph(repo_id=A, path="m.py", nodes=(file_node, cls), edges=(dangling, defines))]
    )
    assert [e.ident for e in graph_store.iter_edges(A)] == [defines.ident]
    assert graph_store.out_edges(A, "m.K") == []
