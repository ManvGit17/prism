from pathlib import Path

import pytest

from prism.config import get_settings
from prism.indexer.model import Edge, EdgeKind, Resolution
from prism.indexer.pipeline import build_file_graphs, index_repository, parse_sources
from prism.indexer.resolver import CONFIDENCE, is_common_name
from prism.store import InMemoryGraphStore

R = "r"


@pytest.fixture
def store(sample_repo: Path) -> InMemoryGraphStore:
    s = InMemoryGraphStore()
    index_repository(sample_repo, R, s)
    return s


def _calls(sources: dict[str, str]) -> dict[tuple[str, str], Edge]:
    parsed = parse_sources(R, {p: t.encode() for p, t in sources.items()})
    graphs = build_file_graphs(parsed)
    return {(e.src, e.dst): e for g in graphs for e in g.edges if e.kind is EdgeKind.CALLS}


def _call(store: InMemoryGraphStore, src: str, dst: str) -> Edge | None:
    for edge in store.out_edges(R, src, (EdgeKind.CALLS,)):
        if edge.dst == dst:
            return edge
    return None


@pytest.mark.parametrize(
    ("src", "dst", "resolution"),
    [
        ("shop.util.slugify", "shop.util._clean", Resolution.SAME_FILE),
        ("shop.jobs.run_all", "shop.jobs.dispatch", Resolution.SAME_FILE),
        ("shop.cart.make_cart", "shop.cart.Cart", Resolution.SAME_FILE),
        ("shop.cart.Cart.checkout", "shop.cart.Cart.subtotal", Resolution.SELF_METHOD),
        (
            "shop.models.DiscountItem.describe",
            "shop.models.DiscountItem.total",
            Resolution.SELF_METHOD,
        ),
        ("shop.models.DiscountItem.total", "shop.models.Item.total", Resolution.INHERITED),
        ("shop.cart.Cart.checkout", "shop.pricing.with_tax", Resolution.IMPORT),
        ("shop.cart.Cart.checkout", "shop.util.log_event", Resolution.IMPORT),
        ("shop.cart.Cart.checkout", "shop.models.apply_discount", Resolution.IMPORT),
        ("shop.cart.make_cart", "shop.models.Item", Resolution.IMPORT),
        ("shop.legacy.make_item", "shop.models.Item", Resolution.IMPORT),  # via a re-export
        ("shop.legacy.old_total", "shop.pricing.with_tax", Resolution.STAR_IMPORT),
        ("shop.cart.Cart.dump", "shop.models.DiscountItem.describe", Resolution.NAME_UNIQUE),
        ("shop.cart.Cart.subtotal", "shop.models.Item.total", Resolution.NAME_AMBIGUOUS),
        ("shop.cart.Cart.subtotal", "shop.models.DiscountItem.total", Resolution.NAME_AMBIGUOUS),
        ("shop.jobs.dispatch", "shop.jobs.Worker.run", Resolution.NAME_COMMON),
        ("shop.cart.make_cart", "shop.cart.Cart.add", Resolution.NAME_COMMON),
    ],
)
def test_resolution_bands(
    store: InMemoryGraphStore, src: str, dst: str, resolution: Resolution
) -> None:
    edge = _call(store, src, dst)
    assert edge is not None, f"no CALLS edge {src} -> {dst}"
    assert edge.resolution is resolution
    assert edge.confidence == CONFIDENCE[resolution]


def test_bands_are_ordered() -> None:
    order = [
        Resolution.SAME_FILE,
        Resolution.IMPORT,
        Resolution.INHERITED,
        Resolution.STAR_IMPORT,
        Resolution.NAME_UNIQUE,
        Resolution.NAME_AMBIGUOUS,
        Resolution.NAME_COMMON,
    ]
    values = [CONFIDENCE[r] for r in order]
    assert values == sorted(values, reverse=True)
    threshold = get_settings().call_confidence_threshold
    assert CONFIDENCE[Resolution.NAME_UNIQUE] >= threshold > CONFIDENCE[Resolution.NAME_AMBIGUOUS]


def test_external_and_builtin_calls_get_no_edge(store: InMemoryGraphStore) -> None:
    # json.dumps, self.items.append, print, functools.wraps, str methods: none are in the repo.
    targets = {e.dst for e in store.iter_edges(R) if e.kind is EdgeKind.CALLS}
    assert all(t.startswith(("shop.", "tests.")) for t in targets)
    assert store.callees(R, "shop.util.log_event", min_confidence=0.0) == []
    assert store.callees(R, "shop.util.cached", min_confidence=0.0) == []


def test_common_name_edge_excluded_by_default_and_included_when_lowered(
    store: InMemoryGraphStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert is_common_name("run")
    assert store.callers(R, "shop.jobs.Worker.run") == []
    lowered = store.callers(R, "shop.jobs.Worker.run", min_confidence=0.1)
    assert [r.node.qualified_name for r in lowered] == ["shop.jobs.dispatch"]
    assert lowered[0].edge.resolution is Resolution.NAME_COMMON

    # The default comes from the setting.
    monkeypatch.setenv("PRISM_CALL_CONFIDENCE_THRESHOLD", "0.05")
    get_settings.cache_clear()
    assert [r.node.qualified_name for r in store.callers(R, "shop.jobs.Worker.run")] == [
        "shop.jobs.dispatch"
    ]


def test_ambiguous_edges_excluded_by_default(store: InMemoryGraphStore) -> None:
    callers = store.callers(R, "shop.models.Item.total")
    assert {r.node.qualified_name for r in callers} == {"shop.models.DiscountItem.total"}
    everything = store.callers(R, "shop.models.Item.total", min_confidence=0.0)
    assert {r.node.qualified_name for r in everything} == {
        "shop.models.DiscountItem.total",
        "shop.cart.Cart.subtotal",
    }


def test_tests_edges(store: InMemoryGraphStore) -> None:
    def names(qn: str, min_confidence: float | None = None) -> set[str]:
        return {
            r.node.qualified_name for r in store.tests_for(R, qn, min_confidence=min_confidence)
        }

    assert names("shop.cart.make_cart") == {"tests.test_cart.test_make_cart"}
    assert names("shop.cart.Cart") == {"tests.test_cart.TestCart.test_checkout"}
    assert names("shop.cart.Cart.checkout") == {"tests.test_cart.TestCart.test_checkout"}
    assert names("shop.pricing.with_tax") == {"tests.pricing_test.test_with_tax"}
    # cart.add(...) is a common-name match, so its TESTS edge is low confidence too.
    assert names("shop.cart.Cart.add") == set()
    assert names("shop.cart.Cart.add", 0.0) == {"tests.test_cart.TestCart.test_checkout"}
    # The non-test helper method in the test class makes no TESTS edge.
    tests_edges = [e for e in store.iter_edges(R) if e.kind is EdgeKind.TESTS]
    assert all(e.src != "tests.test_cart.TestCart.helper" for e in tests_edges)


def test_imports_edges(store: InMemoryGraphStore) -> None:
    targets = {e.dst for e in store.out_edges(R, "shop/cart.py", (EdgeKind.IMPORTS,))}
    assert targets == {"shop/models.py", "shop/util.py", "shop/pricing.py"}


def test_src_layout_import_names() -> None:
    calls = _calls(
        {
            "src/pkg/__init__.py": "",
            "src/pkg/a.py": "def helper_fn():\n    pass\n",
            "src/pkg/b.py": "from pkg.a import helper_fn\n\ndef g():\n    helper_fn()\n",
        }
    )
    assert calls[("src.pkg.b.g", "src.pkg.a.helper_fn")].resolution is Resolution.IMPORT


def test_stdlib_module_is_not_captured_by_a_repo_file() -> None:
    calls = _calls(
        {
            "scripts/json.py": "def dumps(x):\n    pass\n",
            "app.py": "import json\n\ndef h():\n    json.dumps(1)\n",
        }
    )
    assert calls == {}


def test_nested_function_and_class_scope_rules() -> None:
    calls = _calls(
        {
            "m.py": (
                "def outer():\n"
                "    def inner():\n"
                "        pass\n"
                "    inner()\n"
                "\n"
                "class C:\n"
                "    def a(self):\n"
                "        pass\n"
                "    def b(self):\n"
                "        a()\n"
            )
        }
    )
    assert calls[("m.outer", "m.outer.inner")].resolution is Resolution.SAME_FILE
    # A class body is not an enclosing scope: bare a() inside C.b is not C.a.
    assert not any(src == "m.C.b" for src, _ in calls)


def test_too_many_candidates_get_no_edge() -> None:
    sources = {f"m{i}.py": "class K:\n    def frobnicate(self):\n        pass\n" for i in range(12)}
    sources["use.py"] = "def f(x):\n    x.frobnicate()\n"
    calls = _calls(sources)
    assert not any(src == "use.f" for src, _ in calls)
