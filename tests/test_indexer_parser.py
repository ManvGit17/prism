from pathlib import Path

import pytest

from prism.indexer.model import EdgeKind, module_name_for_path
from prism.indexer.parsers import (
    CallSite,
    ParsedFile,
    PythonParser,
    ReceiverKind,
    parser_for,
)
from prism.indexer.parsers.python import is_python_test_file


def _parse(sample_repo: Path, path: str) -> ParsedFile:
    return PythonParser().parse("r", path, (sample_repo / path).read_bytes())


def _parse_text(path: str, text: str) -> ParsedFile:
    return PythonParser().parse("r", path, text.encode())


def _calls_from(pf: ParsedFile, caller: str) -> list[CallSite]:
    return [c for c in pf.calls if c.caller == caller]


def test_parser_registry_by_extension() -> None:
    parser = parser_for("shop/cart.py")
    assert parser is not None
    assert parser.language == "python"
    assert parser_for("Main.java") is None
    assert parser_for("Makefile") is None


@pytest.mark.parametrize(
    ("path", "module"),
    [
        ("shop/cart.py", "shop.cart"),
        ("shop/__init__.py", "shop"),
        ("app.py", "app"),
        ("src/pkg/mod.pyi", "src.pkg.mod"),
    ],
)
def test_module_name_for_path(path: str, module: str) -> None:
    assert module_name_for_path(path) == module


def test_extracts_classes_methods_and_spans(sample_repo: Path) -> None:
    pf = _parse(sample_repo, "shop/models.py")
    classes = {c.qualified_name: c for c in pf.classes}
    functions = {f.qualified_name: f for f in pf.functions}

    item = classes["shop.models.Item"]
    assert (item.start_line, item.end_line) == (4, 22)
    assert item.docstring == "A thing for sale."
    assert item.owner == "shop/models.py"

    # Base classes are expanded to qualified names through the file's own scope.
    assert classes["shop.models.DiscountItem"].bases == ("shop.models.Item",)

    total = functions["shop.models.Item.total"]
    assert total.signature == "def total(self, qty)"
    assert total.is_method
    assert total.owner == "shop.models.Item"
    assert (total.start_line, total.end_line) == (13, 14)
    start, end = total.body_span
    assert pf.source[start:end].decode() == "return self.price * qty"

    apply = functions["shop.models.apply_discount"]
    assert apply.docstring == "Take ten percent off."
    assert not apply.is_method


def test_redefinition_gets_a_suffix_and_decorators_count_in_the_span(sample_repo: Path) -> None:
    pf = _parse(sample_repo, "shop/models.py")
    functions = {f.qualified_name: f for f in pf.functions}
    getter = functions["shop.models.Item.label"]
    setter = functions["shop.models.Item.label#2"]
    assert getter.decorators == ("property",)
    assert setter.decorators == ("label.setter",)
    assert getter.start_line == 16  # the decorator line
    assert setter.start_line == 20


def test_defines_edges(sample_repo: Path) -> None:
    pf = _parse(sample_repo, "shop/util.py")
    defines = {(e.src, e.dst) for e in pf.defines}
    assert all(e.kind is EdgeKind.DEFINES for e in pf.defines)
    assert ("shop/util.py", "shop.util.cached") in defines
    assert ("shop.util.cached", "shop.util.cached.wrapper") in defines


def test_imports_absolute_aliased_and_relative(sample_repo: Path) -> None:
    pf = _parse(sample_repo, "shop/cart.py")
    bound = {i.local_name: (i.target, i.module) for i in pf.file.imports}
    assert bound["json"] == ("json", "json")
    assert bound["util"] == ("shop.util", "shop.util")
    assert bound["Item"] == ("shop.models.Item", "shop.models")
    assert bound["pricing"] == ("shop.pricing", "shop")

    init = _parse(sample_repo, "shop/__init__.py")
    assert [(i.local_name, i.target) for i in init.file.imports] == [("Item", "shop.models.Item")]

    legacy = _parse(sample_repo, "shop/legacy.py")
    stars = [i for i in legacy.file.imports if i.is_star]
    assert [s.target for s in stars] == ["shop.pricing"]


def test_relative_import_two_levels_up() -> None:
    pf = _parse_text("a/b/c.py", "from ..x import y\nfrom . import z\n")
    assert [(i.local_name, i.target) for i in pf.file.imports] == [("y", "a.x.y"), ("z", "a.b.z")]


def test_call_sites_and_receivers(sample_repo: Path) -> None:
    pf = _parse(sample_repo, "shop/cart.py")
    checkout = {
        (c.name, c.receiver_kind, c.receiver) for c in _calls_from(pf, "shop.cart.Cart.checkout")
    }
    assert checkout == {
        ("with_tax", ReceiverKind.NAME, "pricing"),
        ("subtotal", ReceiverKind.SELF, None),
        ("log_event", ReceiverKind.NAME, "util"),
        ("apply_discount", ReceiverKind.NONE, None),
    }
    add = _calls_from(pf, "shop.cart.Cart.add")
    assert [(c.name, c.receiver) for c in add] == [("append", "self.items")]
    make = _calls_from(pf, "shop.cart.make_cart")
    assert {c.name for c in make} == {"Cart", "add", "Item"}

    models = _parse(sample_repo, "shop/models.py")
    sup = _calls_from(models, "shop.models.DiscountItem.total")
    assert ("total", ReceiverKind.SUPER) in {(c.name, c.receiver_kind) for c in sup}
    assert all(c.enclosing_class == "shop.models.DiscountItem" for c in sup)


def test_decorator_calls_belong_to_the_enclosing_scope(sample_repo: Path) -> None:
    pf = _parse(sample_repo, "shop/util.py")
    wraps = [c for c in pf.calls if c.name == "wraps"]
    assert [c.caller for c in wraps] == ["shop.util.cached"]
    assert [c.name for c in _calls_from(pf, "shop.util.cached.wrapper")] == ["fn"]


def test_module_level_calls_belong_to_the_file() -> None:
    pf = _parse_text("run.py", "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n")
    assert [(c.caller, c.name) for c in pf.calls] == [("run.py", "main")]


def test_test_detection(sample_repo: Path) -> None:
    pf = _parse(sample_repo, "tests/test_cart.py")
    assert pf.file.is_test
    flags = {f.qualified_name: f.is_test for f in pf.functions}
    assert flags == {
        "tests.test_cart.test_make_cart": True,
        "tests.test_cart.TestCart.test_checkout": True,
        "tests.test_cart.TestCart.helper": False,
    }
    assert [c.is_test for c in pf.classes] == [True]

    assert _parse(sample_repo, "tests/pricing_test.py").file.is_test
    models = _parse(sample_repo, "shop/models.py")
    assert not models.file.is_test
    assert not any(f.is_test for f in models.functions)


def test_test_name_outside_a_test_file_is_not_a_test() -> None:
    pf = _parse_text("app/db.py", "def test_connection():\n    pass\n")
    assert not pf.functions[0].is_test


def test_unittest_style_class_in_a_test_file() -> None:
    src = (
        "import unittest\n\n"
        "class CartTests(unittest.TestCase):\n"
        "    def test_a(self):\n"
        "        pass\n"
    )
    pf = _parse_text("pkg/tests.py", src)
    assert pf.classes[0].bases == ("unittest.TestCase",)
    assert pf.classes[0].is_test
    assert pf.functions[0].is_test


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("test_x.py", True),
        ("pkg/x_test.py", True),
        ("tests/helpers.py", True),
        ("conftest.py", True),
        ("pkg/testing.py", False),
        ("pkg/contest.py", False),
    ],
)
def test_test_file_names(path: str, expected: bool) -> None:
    assert is_python_test_file(path) is expected


def test_async_signature_and_staticmethod_self() -> None:
    src = (
        "class A:\n"
        "    @staticmethod\n"
        "    def s(x):\n"
        "        x.go()\n"
        "    async def a(\n"
        "        self,\n"
        "        n: int = 1,\n"
        "    ) -> int:\n"
        "        return self.go()\n"
    )
    pf = _parse_text("m.py", src)
    fns = {f.name: f for f in pf.functions}
    assert fns["a"].is_async
    assert fns["a"].signature == "async def a(self, n: int = 1) -> int"
    kinds = {c.caller: c.receiver_kind for c in pf.calls}
    assert kinds["m.A.s"] is ReceiverKind.NAME  # x is not self in a staticmethod
    assert kinds["m.A.a"] is ReceiverKind.SELF


def test_broken_source_still_parses() -> None:
    pf = _parse_text("bad.py", "def ok():\n    return 1\n\ndef broken(:\n")
    assert pf.has_errors
    assert "bad.ok" in {f.qualified_name for f in pf.functions}
