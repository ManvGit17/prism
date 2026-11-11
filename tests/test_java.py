"""Java parsing and resolution on the Maven-style fixture under tests/fixtures/java_repo."""

from pathlib import Path

import pytest

from prism.indexer.model import ClassNode, EdgeKind, FunctionNode, Resolution
from prism.indexer.parsers import JavaParser, ParsedFile, ReceiverKind
from prism.indexer.parsers.java import erase, is_java_test_file
from prism.indexer.pipeline import build_file_graphs, index_repository, parse_sources
from prism.indexer.resolver import CONFIDENCE
from prism.store import InMemoryGraphStore, Neighbour

JAVA_REPO = Path(__file__).parent / "fixtures" / "java_repo"
MAIN = "src/main/java/com/acme"
R = "j"


def _parse(path: str) -> ParsedFile:
    return JavaParser().parse(R, path, (JAVA_REPO / path).read_bytes())


def _parse_text(path: str, text: str) -> ParsedFile:
    return JavaParser().parse(R, path, text.encode())


@pytest.fixture(scope="module")
def store() -> InMemoryGraphStore:
    s = InMemoryGraphStore()
    index_repository(JAVA_REPO, R, s)
    return s


# Parser


def test_package_imports_and_types() -> None:
    pf = _parse(f"{MAIN}/shop/Cart.java")
    assert pf.file.module == "com.acme.shop"
    imports = {(i.local_name, i.target, i.module, i.is_static) for i in pf.file.imports}
    assert imports == {
        ("round", "com.acme.util.Money.round", "com.acme.util.Money", True),
        ("Ledger", "com.acme.util.Ledger", "com.acme.util", False),
        ("ArrayList", "java.util.ArrayList", "java.util", False),
        ("List", "java.util.List", "java.util", False),
    }
    star = _parse(f"{MAIN}/shop/DiscountItem.java").file.imports
    assert [(i.local_name, i.target, i.is_star) for i in star] == [("*", "com.acme.util", True)]
    classes = {c.qualified_name: c for c in pf.classes}
    assert set(classes) == {"com.acme.shop.Cart", "com.acme.shop.Cart.Line"}
    assert classes["com.acme.shop.Cart.Line"].owner == "com.acme.shop.Cart"
    assert classes["com.acme.shop.Cart"].docstring == "A shopping cart."
    priced = _parse(f"{MAIN}/shop/Priced.java").classes[0]
    assert priced.class_kind == "interface"
    item = _parse(f"{MAIN}/shop/Item.java").classes[0]
    assert item.bases == ("Priced",)


def test_methods_overloads_constructors_and_spans() -> None:
    pf = _parse(f"{MAIN}/shop/Cart.java")
    fns = {f.qualified_name: f for f in pf.functions}
    assert {
        "com.acme.shop.Cart.<init>(Ledger)",
        "com.acme.shop.Cart.add(Item)",
        "com.acme.shop.Cart.add(Item,int)",
        "com.acme.shop.Cart.checkout(List)",
        "com.acme.shop.Cart.Line.<init>(Item,int)",
        "com.acme.shop.Cart.Line.amount()",
    } <= set(fns)
    add = fns["com.acme.shop.Cart.add(Item,int)"]
    assert add.name == "add"
    assert add.signature == "public void add(Item item, int quantity)"
    assert (add.start_line, add.end_line) == (22, 26)
    start, end = add.body_span
    assert pf.source[start:end].decode().startswith("{\n        for (int i = 0;")
    checkout = fns["com.acme.shop.Cart.checkout(List)"]
    assert checkout.signature == "public double checkout(List<Line> lines)"
    item = {f.name: f for f in _parse(f"{MAIN}/shop/Item.java").functions if f.name == "price"}
    assert item["price"].docstring == "The unit price."
    assert item["price"].decorators == ("Override",)


def test_varargs_records_enums_and_anonymous_classes() -> None:
    src = (
        "package p;\n"
        "public record Point(int x, java.util.List<String> tags) {\n"
        "    public Point { check(x); }\n"
        "    static void log(String fmt, Object... args) {}\n"
        "    enum Mode { A, B; void m() { helper(); } }\n"
        "    void run() {\n"
        "        Runnable r = new Runnable() { public void run() { inner(); } };\n"
        "        class Local { void f() { local(); } }\n"
        "    }\n"
        "}\n"
    )
    pf = _parse_text("p/Point.java", src)
    names = {f.qualified_name for f in pf.functions}
    assert names == {
        "p.Point.<init>(int,List)",
        "p.Point.log(String,Object...)",
        "p.Point.Mode.m()",
        "p.Point.run()",
    }
    kinds = {c.qualified_name: c.class_kind for c in pf.classes}
    assert kinds == {"p.Point": "record", "p.Point.Mode": "enum"}
    # Calls inside anonymous and local classes belong to the enclosing method.
    callers = {c.name: c.caller for c in pf.calls}
    assert callers["inner"] == "p.Point.run()"
    assert callers["local"] == "p.Point.run()"
    assert callers["Runnable"] == "p.Point.run()"


def test_call_sites_record_receivers_and_types() -> None:
    pf = _parse(f"{MAIN}/shop/Cart.java")
    sites = {(c.caller, c.name): c for c in pf.calls}
    record = sites[("com.acme.shop.Cart.checkout(List)", "record")]
    assert (record.receiver_kind, record.receiver, record.receiver_type) == (
        ReceiverKind.NAME,
        "ledger",
        "Ledger",
    )
    assert record.arg_count == 1
    gift = sites[("com.acme.shop.Cart.checkout(List)", "Gift")]
    assert gift.receiver_kind is ReceiverKind.NEW
    ref = sites[("com.acme.shop.Cart.total()", "price")]
    assert (ref.receiver, ref.arg_count) == ("Item", None)  # Item::price
    amount = sites[("com.acme.shop.Cart.checkout(List)", "amount")]
    assert amount.receiver_type is None  # an untyped lambda parameter


@pytest.mark.parametrize(
    ("text", "erased"),
    [("java.util.List<String>", "List"), ("Map.Entry<K, V>[]", "Entry[]"), ("int", "int")],
)
def test_erase(text: str, erased: str) -> None:
    assert erase(text) == erased


def test_test_detection() -> None:
    pf = _parse("src/test/java/com/acme/shop/CartTest.java")
    assert pf.file.is_test
    assert pf.classes[0].is_test
    flags = {f.name: f.is_test for f in pf.functions}
    assert flags == {"newCart": False, "totalSumsItems": True, "addWithQuantity": True}
    assert not _parse(f"{MAIN}/shop/Cart.java").file.is_test
    testng = _parse_text(
        "src/main/java/p/Checks.java",
        "package p;\n@Test\npublic class Checks {\n  public void a() {}\n  void b() {}\n}\n",
    )
    assert {f.name: f.is_test for f in testng.functions} == {"a": True, "b": False}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/test/java/p/Helper.java", True),
        ("module/src/test/java/p/Helper.java", True),
        ("src/main/java/p/OrderIT.java", True),
        ("src/main/java/p/Contest.java", False),
        ("src/main/java/p/Testing.java", False),
    ],
)
def test_java_test_file_names(path: str, expected: bool) -> None:
    assert is_java_test_file(path) is expected


# Resolution

S = "com.acme.shop"
U = "com.acme.util"


@pytest.mark.parametrize(
    ("src", "dst", "band"),
    [
        (f"{S}.Cart.add(Item,int)", f"{S}.Cart.add(Item)", Resolution.SELF_METHOD),
        (f"{S}.Cart.checkout(List)", f"{S}.Cart.total()", Resolution.SELF_METHOD),
        (f"{S}.DiscountItem.describe()", f"{S}.Item.name()", Resolution.INHERITED),
        (f"{S}.DiscountItem.price()", f"{S}.Item.price()", Resolution.INHERITED),  # super.
        (f"{S}.Cart.Line.amount()", f"{S}.Cart.scale(double,int)", Resolution.SAME_FILE),
        (f"{S}.Item.of(String)", f"{S}.Item", Resolution.SAME_FILE),  # new Item(...)
        (f"{S}.Cart.checkout(List)", f"{S}.Item.of(String)", Resolution.SAME_PACKAGE),
        (f"{S}.Cart.checkout(List)", f"{S}.Gift", Resolution.SAME_PACKAGE),
        (f"{S}.Cart.checkout(List)", f"{S}.Priced.price()", Resolution.SAME_PACKAGE),
        (f"{S}.Cart.total()", f"{S}.Item.price()", Resolution.SAME_PACKAGE),  # Item::price
        (f"{S}.DiscountItem.<init>(String,double)", f"{S}.Item", Resolution.SAME_PACKAGE),
        (f"{S}.Cart.checkout(List)", f"{U}.Ledger.record(double)", Resolution.IMPORT),
        (f"{S}.Cart.total()", f"{U}.Money.round(double)", Resolution.IMPORT),  # static import
        (f"{S}.CartTest.newCart()", f"{U}.Ledger", Resolution.IMPORT),  # fully qualified
        (f"{S}.DiscountItem.describe()", f"{U}.Money.format(double)", Resolution.STAR_IMPORT),
        (f"{S}.Cart.checkout(List)", f"{S}.Cart.Line.amount()", Resolution.NAME_UNIQUE),
    ],
)
def test_resolution_bands(store: InMemoryGraphStore, src: str, dst: str, band: Resolution) -> None:
    edges = {e.dst: e for e in store.out_edges(R, src, (EdgeKind.CALLS,))}
    assert dst in edges, f"no CALLS edge {src} -> {dst}; have {sorted(edges)}"
    assert edges[dst].resolution is band
    assert edges[dst].confidence == CONFIDENCE[band]


def test_external_calls_get_no_edge(store: InMemoryGraphStore) -> None:
    targets = {e.dst for e in store.iter_edges(R) if e.kind is EdgeKind.CALLS}
    # items.add (java.util.List), System.out.println, assertEquals, Math.round, String.format
    assert all(t.startswith("com.acme.") for t in targets)
    assert store.callees(R, f"{S}.Cart.add(Item)", min_confidence=0) == []


def test_overloads_are_chosen_by_argument_count(store: InMemoryGraphStore) -> None:
    one = {r.node.qualified_name for r in store.callees(R, f"{S}.CartTest.totalSumsItems()")}
    two = {r.node.qualified_name for r in store.callees(R, f"{S}.CartTest.addWithQuantity(int)")}
    assert f"{S}.Cart.add(Item)" in one and f"{S}.Cart.add(Item,int)" not in one
    assert f"{S}.Cart.add(Item,int)" in two and f"{S}.Cart.add(Item)" not in two


def test_overload_lookup(store: InMemoryGraphStore) -> None:
    both = [f"{S}.Cart.add(Item)", f"{S}.Cart.add(Item,int)"]
    assert [n.qualified_name for n in store.lookup(R, "add")] == both
    assert [n.qualified_name for n in store.lookup(R, "Cart.add")] == both
    assert [n.qualified_name for n in store.lookup(R, "Cart.add(Item, int)")] == [both[1]]
    assert [n.qualified_name for n in store.lookup(R, f"{S}.Cart.add(Item)")] == [both[0]]


def test_common_names_stay_low() -> None:
    src = {
        "p/Box.java": "package p;\npublic class Box {\n  public int size() { return 0; }\n}\n",
        "p/Use.java": (
            "package p;\nimport java.util.List;\npublic class Use {\n"
            "  int f(List<Box> boxes) { boxes.forEach(b -> b.size()); return 0; }\n}\n"
        ),
    }
    graphs = build_file_graphs(parse_sources(R, {k: v.encode() for k, v in src.items()}))
    calls = [e for g in graphs for e in g.edges if e.kind is EdgeKind.CALLS]
    assert [(e.dst, e.resolution) for e in calls] == [("p.Box.size()", Resolution.NAME_COMMON)]


def test_tests_edges(store: InMemoryGraphStore) -> None:
    tests = {r.node.qualified_name for r in store.tests_for(R, f"{S}.Cart.add(Item,int)")}
    assert tests == {f"{S}.CartTest.addWithQuantity(int)"}
    assert {r.node.qualified_name for r in store.tests_for(R, f"{S}.Cart.total()")} == {
        f"{S}.CartTest.totalSumsItems()"
    }


# Contracts


def test_overrides_and_implementations(store: InMemoryGraphStore) -> None:
    def names(rows: list[Neighbour]) -> list[tuple[str, int]]:
        return [(n.node.qualified_name, n.distance) for n in rows]

    assert names(store.overrides(R, f"{S}.Item.price()")) == [(f"{S}.Priced.price()", 1)]
    assert names(store.overrides(R, f"{S}.DiscountItem.price()")) == [
        (f"{S}.Item.price()", 1),
        (f"{S}.Priced.price()", 2),
    ]
    assert names(store.overridden_by(R, f"{S}.Priced.price()")) == [
        (f"{S}.Gift.price()", 1),
        (f"{S}.Item.price()", 1),
        (f"{S}.DiscountItem.price()", 2),
    ]
    assert names(store.supertypes(R, f"{S}.DiscountItem")) == [
        (f"{S}.Item", 1),
        (f"{S}.Priced", 2),
    ]
    priced = store.get_node(R, f"{S}.Priced")
    assert isinstance(priced, ClassNode) and priced.class_kind == "interface"


def test_python_overrides_and_supertypes(sample_repo: Path) -> None:
    s = InMemoryGraphStore()
    index_repository(sample_repo, "py", s)
    over = s.overrides("py", "shop.models.DiscountItem.total")
    assert [n.node.qualified_name for n in over] == ["shop.models.Item.total"]
    assert [n.node.qualified_name for n in s.supertypes("py", "shop.models.DiscountItem")] == [
        "shop.models.Item"
    ]


def test_mixed_repo_indexes_both_languages(tmp_path: Path, sample_repo: Path) -> None:
    import shutil

    shutil.copytree(sample_repo, tmp_path / "py")
    shutil.copytree(JAVA_REPO / "src", tmp_path / "src")
    (tmp_path / "target" / "classes").mkdir(parents=True)
    (tmp_path / "target" / "classes" / "Gen.java").write_text("class Gen {}", encoding="utf-8")
    s = InMemoryGraphStore()
    report = index_repository(tmp_path, "mix", s)
    langs = {n.language for n in s.iter_nodes("mix")}
    assert langs == {"python", "java"}
    assert report.files == 9 + 8
    assert all("target/" not in p for p in s.file_paths("mix"))
    # No edge crosses languages.
    for edge in s.iter_edges("mix"):
        src, dst = s.get_node("mix", edge.src), s.get_node("mix", edge.dst)
        assert src is not None and dst is not None
        assert src.language == dst.language
    py = s.get_node("mix", "py.shop.cart.Cart.add")
    java = s.get_node("mix", f"{S}.Cart.add(Item)")
    assert isinstance(py, FunctionNode) and isinstance(java, FunctionNode)


def test_overloads_are_narrowed_by_stated_argument_types() -> None:
    src = {
        "p/Conv.java": (
            "package p;\npublic class Conv {\n"
            "  public static char toChar(Character c) { return c; }\n"
            "  public static char toChar(String s) { return s.charAt(0); }\n"
            '  public static String yes(boolean b) { return ""; }\n'
            '  public static String yes(Boolean b) { return ""; }\n'
            "}\n"
        ),
        "p/Use.java": (
            "package p;\npublic class Use {\n"
            "  private static final Character A = 'a';\n"
            '  void str() { Conv.toChar("x"); }\n'
            "  void chr() { Conv.toChar(A); Conv.toChar((Character) null); }\n"
            "  void prim() { Conv.yes(true); }\n"
            "  void nul() { Conv.yes(null); }\n"
            "  void unknown(Object o) { Conv.yes(o.hashCode() > 0); }\n"
            "}\n"
        ),
    }
    graphs = build_file_graphs(parse_sources(R, {k: v.encode() for k, v in src.items()}))
    calls: dict[str, set[str]] = {}
    for g in graphs:
        for e in g.edges:
            if e.kind is EdgeKind.CALLS:
                calls.setdefault(e.src.rsplit(".", 1)[-1], set()).add(e.dst.rsplit(".", 1)[-1])
    assert calls["str()"] == {"toChar(String)"}
    assert calls["chr()"] == {"toChar(Character)"}
    assert calls["prim()"] == {"yes(boolean)"}  # an exact primitive match beats boxing
    assert calls["nul()"] == {"yes(Boolean)"}  # null never fits a primitive
    assert calls["unknown(Object)"] == {"yes(boolean)", "yes(Boolean)"}
