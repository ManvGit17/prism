"""Incremental base updates must end where a full index of the new tree ends."""

import shutil
from pathlib import Path

import pytest
from repo_ids import repo_id

from prism.indexer.embedder import FakeEmbedder
from prism.indexer.pipeline import index_repository, update_repository
from prism.store import GraphStore, InMemoryGraphStore, InMemoryVectorStore

R = repo_id("inc")

PRICING_V2 = '''"""Prices and tax, version 2."""

TAX = 0.2


def with_tax(amount):
    return apply_rounding(amount * (1 + TAX))


def apply_rounding(value):
    return int(value * 100) / 100
'''

USES_EXTRA = "from shop.extra import helper\n\n\ndef go():\n    return helper()\n"
EXTRA = "def helper():\n    return 1\n"


def _snapshot(store: GraphStore, rid: str) -> tuple[list[object], list[object]]:
    nodes = sorted(n.model_dump_json() for n in store.iter_nodes(rid))
    edges = sorted(e.model_dump_json() for e in store.iter_edges(rid))
    return list(nodes), list(edges)


@pytest.fixture
def tree(tmp_path: Path, sample_repo: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(sample_repo, root)
    (root / "shop" / "uses_extra.py").write_text(USES_EXTRA, encoding="utf-8")
    return root


def test_update_matches_a_full_index(graph_store: GraphStore, tree: Path) -> None:
    index_repository(tree, R, graph_store)
    assert graph_store.callees(R, "shop.uses_extra.go") == []

    (tree / "shop" / "pricing.py").write_text(PRICING_V2, encoding="utf-8")
    (tree / "shop" / "extra.py").write_text(EXTRA, encoding="utf-8")
    (tree / "shop" / "jobs.py").unlink()
    report = update_repository(
        tree,
        R,
        changed_paths=["shop/pricing.py", "shop/extra.py"],
        removed_paths=["shop/jobs.py"],
        store=graph_store,
    )
    assert report.kind == "incremental"
    assert report.removed == ["shop/jobs.py"]
    # Callers of with_tax, the star importer, the test, and the file whose import now resolves.
    assert {"shop/cart.py", "shop/legacy.py", "tests/pricing_test.py", "shop/uses_extra.py"} <= set(
        report.dependents
    )

    fresh = InMemoryGraphStore()
    index_repository(tree, R, fresh)
    assert _snapshot(graph_store, R) == _snapshot(fresh, R)
    assert [r.node.qualified_name for r in graph_store.callees(R, "shop.uses_extra.go")] == [
        "shop.extra.helper"
    ]
    assert graph_store.get_node(R, "shop.pricing.round_price") is None
    assert graph_store.get_node(R, "shop.jobs.dispatch") is None


def test_update_rewrites_vectors_of_changed_files_only(tree: Path) -> None:
    graph = InMemoryGraphStore()
    vectors = InMemoryVectorStore()
    embedder = FakeEmbedder(dimension=64)
    index_repository(tree, R, graph, vectors=vectors, embedder=embedder)
    before = vectors.get(R, "shop.cart.make_cart")
    (tree / "shop" / "pricing.py").write_text(PRICING_V2, encoding="utf-8")
    report = update_repository(
        tree, R, ["shop/pricing.py"], [], graph, vectors=vectors, embedder=embedder
    )
    assert report.vectors == 2
    assert vectors.get(R, "shop.pricing.round_price") is None
    assert vectors.get(R, "shop.pricing.apply_rounding") is not None
    assert vectors.get(R, "shop.cart.make_cart") == before
    assert vectors.count(R) == graph.stats(R).functions


def test_update_on_an_empty_store_is_a_full_index(tree: Path) -> None:
    graph = InMemoryGraphStore()
    report = update_repository(tree, R, ["shop/pricing.py"], [], graph)
    assert report.kind == "full"
    assert graph.stats(R).files == 10


JAVA_REPO = Path(__file__).parent / "fixtures" / "java_repo"
SHOP = "src/main/java/com/acme/shop"

ITEM_V2 = """package com.acme.shop;

/** A thing for sale, version 2. */
public class Item implements Priced {
    private final double price;

    public Item(String name, double price) {
        this.price = price;
    }

    @Override
    public double price() {
        return Tax.apply(price);
    }

    public static Item of(String name) {
        return new Item(name, 1.0);
    }
}
"""

TAX = """package com.acme.shop;

final class Tax {
    static double apply(double amount) {
        return amount * 1.2;
    }
}
"""


def test_java_update_matches_a_full_index(graph_store: GraphStore, tmp_path: Path) -> None:
    root = tmp_path / "java"
    shutil.copytree(JAVA_REPO, root)
    index_repository(root, R, graph_store)
    (root / SHOP / "Item.java").write_text(ITEM_V2, encoding="utf-8")
    (root / SHOP / "Tax.java").write_text(TAX, encoding="utf-8")
    report = update_repository(root, R, [f"{SHOP}/Item.java", f"{SHOP}/Tax.java"], [], graph_store)
    assert report.kind == "incremental"
    # Same package needs no import, so the whole package is re-resolved.
    assert f"{SHOP}/Cart.java" in report.dependents
    fresh = InMemoryGraphStore()
    index_repository(root, R, fresh)
    assert _snapshot(graph_store, R) == _snapshot(fresh, R)
    assert graph_store.get_node(R, "com.acme.shop.Item.name()") is None
    callees = {r.node.qualified_name for r in graph_store.callees(R, "com.acme.shop.Item.price()")}
    assert callees == {"com.acme.shop.Tax.apply(double)"}
