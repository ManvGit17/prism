"""Python parser built on tree-sitter.

Extracts the file, its classes and functions (methods and nested functions
included), imports, and call sites. Calls are attributed to the innermost
enclosing function, class body or module. Decorators, default values and base
class lists run when the definition runs, so their calls belong to the
enclosing scope, not to the definition.
"""

import ast
import inspect
import re
from dataclasses import dataclass, field

import tree_sitter_python
from tree_sitter import Language, Node, Parser

from prism.indexer.model import (
    ClassNode,
    Edge,
    EdgeKind,
    FileNode,
    FunctionNode,
    ImportRef,
    module_name_for_path,
)
from prism.indexer.parsers.base import CallSite, ParsedFile, ReceiverKind

_LANGUAGE = Language(tree_sitter_python.language())
_WS = re.compile(r"\s+")


def is_python_test_file(path: str) -> bool:
    """`test_*.py`, `*_test.py`, `tests.py`, `conftest.py`, or a file under `tests/` or `test/`."""
    parts = path.split("/")
    base = parts[-1]
    if base.startswith("test_") or base.endswith("_test.py") or base in ("tests.py", "conftest.py"):
        return True
    return any(p in ("tests", "test") for p in parts[:-1])


@dataclass(slots=True)
class _Scope:
    qn: str  # qualified name of the scope node (file path for the module)
    prefix: str  # prefix for children's qualified names (module name for the module)
    kind: str  # "file", "class" or "function"
    enclosing_class: str | None
    self_name: str | None
    names: dict[str, int] = field(default_factory=dict)

    def child_qn(self, name: str) -> str:
        n = self.names.get(name, 0) + 1
        self.names[name] = n
        qn = f"{self.prefix}.{name}" if self.prefix else name
        return qn if n == 1 else f"{qn}#{n}"


@dataclass(slots=True)
class _RawClass:
    qn: str
    name: str
    owner: str
    owner_kind: str
    start_line: int
    end_line: int
    span: tuple[int, int]
    docstring: str | None
    raw_bases: tuple[str, ...]
    decorators: tuple[str, ...]


@dataclass(slots=True)
class _RawFunction:
    qn: str
    name: str
    owner: str
    owner_kind: str
    start_line: int
    end_line: int
    span: tuple[int, int]
    body_span: tuple[int, int]
    signature: str
    docstring: str | None
    decorators: tuple[str, ...]
    is_async: bool


class PythonParser:
    language: str = "python"
    extensions: tuple[str, ...] = (".py", ".pyi")

    def parse(self, repo_id: str, path: str, source: bytes) -> ParsedFile:
        # A tree-sitter Parser is not thread-safe and is cheap to make, so make one per call.
        return _FileVisitor(repo_id, path, source, Parser(_LANGUAGE)).run()


class _FileVisitor:
    def __init__(self, repo_id: str, path: str, source: bytes, parser: Parser) -> None:
        self.repo_id = repo_id
        self.path = path
        self.source = source
        self.parser = parser
        self.module = module_name_for_path(path)
        self.is_init = path.rsplit("/", 1)[-1] in ("__init__.py", "__init__.pyi")
        self.classes: list[_RawClass] = []
        self.functions: list[_RawFunction] = []
        self.imports: list[ImportRef] = []
        self.calls: list[CallSite] = []

    def text(self, node: Node) -> str:
        return self.source[node.start_byte : node.end_byte].decode("utf-8", "replace")

    # Traversal

    def run(self) -> ParsedFile:
        tree = self.parser.parse(self.source)
        root = tree.root_node
        file_scope = _Scope(
            qn=self.path, prefix=self.module, kind="file", enclosing_class=None, self_name=None
        )
        stack: list[tuple[Node, _Scope]] = [(root, file_scope)]
        while stack:
            node, scope = stack.pop()
            pushed = self.visit(node, scope)
            stack.extend(reversed(pushed))
        return self.build(has_errors=root.has_error)

    def visit(self, node: Node, scope: _Scope) -> list[tuple[Node, _Scope]]:
        """Handle one node and return the children to visit, each with its scope."""
        t = node.type
        if t == "decorated_definition":
            definition = node.child_by_field_name("definition")
            decorators = [c for c in node.named_children if c.type == "decorator"]
            out: list[tuple[Node, _Scope]] = [(d, scope) for d in decorators]
            if definition is not None:
                texts = tuple(self.text(d).lstrip("@").strip() for d in decorators)
                out.extend(self.definition(definition, node, texts, scope))
            return out
        if t in ("function_definition", "class_definition"):
            return self.definition(node, node, (), scope)
        if t == "import_statement":
            self.import_statement(node)
            return []
        if t == "import_from_statement":
            self.import_from(node)
            return []
        if t == "future_import_statement":
            return []
        if t == "call":
            self.call(node, scope)
        return [(c, scope) for c in node.children]

    def definition(
        self, node: Node, outer: Node, decorators: tuple[str, ...], scope: _Scope
    ) -> list[tuple[Node, _Scope]]:
        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        if name_node is None or body is None:
            return [(c, scope) for c in node.children]
        name = self.text(name_node)
        qn = scope.child_qn(name)
        start_line = outer.start_point.row + 1
        end_line = outer.end_point.row + 1
        span = (outer.start_byte, outer.end_byte)
        docstring = self.docstring(body)
        out: list[tuple[Node, _Scope]] = []

        if node.type == "class_definition":
            supers = node.child_by_field_name("superclasses")
            raw_bases: list[str] = []
            if supers is not None:
                raw_bases = [
                    self.text(c) for c in supers.named_children if c.type != "keyword_argument"
                ]
                out.append((supers, scope))
            self.classes.append(
                _RawClass(
                    qn,
                    name,
                    scope.qn,
                    scope.kind,
                    start_line,
                    end_line,
                    span,
                    docstring,
                    tuple(raw_bases),
                    decorators,
                )
            )
            inner = _Scope(qn=qn, prefix=qn, kind="class", enclosing_class=qn, self_name=None)
            out.append((body, inner))
            return out

        params = node.child_by_field_name("parameters")
        return_type = node.child_by_field_name("return_type")
        signature = self.signature(node, params, body)
        is_async = any(c.type == "async" for c in node.children)
        self.functions.append(
            _RawFunction(
                qn,
                name,
                scope.qn,
                scope.kind,
                start_line,
                end_line,
                span,
                (body.start_byte, body.end_byte),
                signature,
                docstring,
                decorators,
                is_async,
            )
        )
        if scope.kind == "class":
            static = any(d.split("(")[0].endswith("staticmethod") for d in decorators)
            self_name = None if static else self.first_param(params)
            enclosing_class: str | None = scope.qn
        else:
            # Nested functions keep the enclosing method's `self` as a closure.
            self_name = scope.self_name
            enclosing_class = scope.enclosing_class
        if params is not None:
            out.append((params, scope))
        if return_type is not None:
            out.append((return_type, scope))
        inner = _Scope(
            qn=qn, prefix=qn, kind="function", enclosing_class=enclosing_class, self_name=self_name
        )
        out.append((body, inner))
        return out

    def signature(self, node: Node, params: Node | None, body: Node) -> str:
        """The def line on one line: whitespace collapsed, no trailing comma in the parameters."""

        def flat(start: int, end: int) -> str:
            return _WS.sub(" ", self.source[start:end].decode("utf-8", "replace"))

        if params is None:
            head = flat(node.start_byte, body.start_byte)
        else:
            inner = flat(params.start_byte + 1, params.end_byte - 1).strip().removesuffix(",")
            head = (
                flat(node.start_byte, params.start_byte)
                + f"({inner.strip()})"
                + flat(params.end_byte, body.start_byte)
            )
        return head.strip().removesuffix(":").strip()

    def first_param(self, params: Node | None) -> str | None:
        if params is None or not params.named_children:
            return None
        first = params.named_children[0]
        if first.type == "identifier":
            return self.text(first)
        named = first.child_by_field_name("name")
        if named is not None and named.type == "identifier":
            return self.text(named)
        for c in first.named_children:
            if c.type == "identifier":
                return self.text(c)
        return None

    def docstring(self, body: Node) -> str | None:
        if not body.named_children:
            return None
        first = body.named_children[0]
        if first.type != "expression_statement" or not first.named_children:
            return None
        expr = first.named_children[0]
        if expr.type not in ("string", "concatenated_string"):
            return None
        try:
            value = ast.literal_eval(self.text(expr))
        except (ValueError, SyntaxError):
            return None
        return inspect.cleandoc(value) if isinstance(value, str) else None

    # Imports

    def import_statement(self, node: Node) -> None:
        line = node.start_point.row + 1
        for child in node.children_by_field_name("name"):
            if child.type == "aliased_import":
                name = child.child_by_field_name("name")
                alias = child.child_by_field_name("alias")
                if name is None or alias is None:
                    continue
                module = self.text(name)
                self.imports.append(
                    ImportRef(local_name=self.text(alias), target=module, module=module, line=line)
                )
            elif child.type == "dotted_name":
                module = self.text(child)
                head = module.split(".")[0]
                self.imports.append(
                    ImportRef(local_name=head, target=head, module=module, line=line)
                )

    def import_from(self, node: Node) -> None:
        line = node.start_point.row + 1
        module_node = node.child_by_field_name("module_name")
        if module_node is None:
            return
        base = self.from_module(module_node)
        if not base:
            return
        if any(c.type == "wildcard_import" for c in node.children):
            self.imports.append(ImportRef(local_name="*", target=base, module=base, line=line))
            return
        for child in node.children_by_field_name("name"):
            if child.type == "aliased_import":
                name = child.child_by_field_name("name")
                alias = child.child_by_field_name("alias")
                if name is None or alias is None:
                    continue
                local, imported = self.text(alias), self.text(name)
            elif child.type == "dotted_name":
                local = imported = self.text(child)
            else:
                continue
            self.imports.append(
                ImportRef(local_name=local, target=f"{base}.{imported}", module=base, line=line)
            )

    def from_module(self, node: Node) -> str:
        if node.type == "dotted_name":
            return self.text(node)
        # relative_import: leading dots, then an optional dotted name.
        dots = 0
        rest = ""
        for c in node.children:
            if c.type == "import_prefix":
                dots = self.text(c).count(".")
            elif c.type == "dotted_name":
                rest = self.text(c)
        package = self.module.split(".") if self.module else []
        if not self.is_init and package:
            package = package[:-1]
        up = dots - 1
        if up > 0:
            package = package[:-up] if up <= len(package) else []
        parts = [*package, *([rest] if rest else [])]
        return ".".join(parts)

    # Calls

    def call(self, node: Node, scope: _Scope) -> None:
        fn = node.child_by_field_name("function")
        if fn is None:
            return
        line = node.start_point.row + 1
        if fn.type == "identifier":
            self.calls.append(
                CallSite(
                    scope.qn, self.text(fn), ReceiverKind.NONE, None, line, scope.enclosing_class
                )
            )
            return
        if fn.type != "attribute":
            return
        attr = fn.child_by_field_name("attribute")
        obj = fn.child_by_field_name("object")
        if attr is None or obj is None:
            return
        name = self.text(attr)
        kind, receiver = self.receiver(obj, scope)
        self.calls.append(CallSite(scope.qn, name, kind, receiver, line, scope.enclosing_class))

    def receiver(self, obj: Node, scope: _Scope) -> tuple[ReceiverKind, str | None]:
        if obj.type == "identifier" and scope.self_name and self.text(obj) == scope.self_name:
            return ReceiverKind.SELF, None
        if obj.type == "call":
            fn = obj.child_by_field_name("function")
            if fn is not None and fn.type == "identifier" and self.text(fn) == "super":
                return ReceiverKind.SUPER, None
        dotted = self.dotted(obj)
        if dotted is not None:
            return ReceiverKind.NAME, dotted
        return ReceiverKind.EXPR, None

    def dotted(self, node: Node) -> str | None:
        if node.type == "identifier":
            return self.text(node)
        if node.type == "attribute":
            obj = node.child_by_field_name("object")
            attr = node.child_by_field_name("attribute")
            if obj is None or attr is None:
                return None
            head = self.dotted(obj)
            return None if head is None else f"{head}.{self.text(attr)}"
        return None

    # Assembly

    def build(self, *, has_errors: bool) -> ParsedFile:
        file_is_test = is_python_test_file(self.path)
        bindings = {i.local_name: i.target for i in self.imports if not i.is_star}
        top_level = {c.name for c in self.classes if c.owner_kind == "file"}
        top_level.update(f.name for f in self.functions if f.owner_kind == "file")

        def expand(expr: str) -> str:
            expr = expr.split("[", 1)[0].split("(", 1)[0].strip()
            head, _, rest = expr.partition(".")
            if head in bindings:
                return bindings[head] + (f".{rest}" if rest else "")
            if head in top_level and self.module:
                return f"{self.module}.{expr}"
            return expr

        classes: list[ClassNode] = []
        test_classes: set[str] = set()
        for rc in self.classes:
            bases = tuple(expand(b) for b in rc.raw_bases)
            is_test = (
                rc.name.startswith("Test")
                or any(b.rsplit(".", 1)[-1].endswith("TestCase") for b in bases)
                or (file_is_test and rc.name.endswith(("Test", "Tests")))
            )
            if is_test:
                test_classes.add(rc.qn)
            classes.append(
                ClassNode(
                    repo_id=self.repo_id,
                    qualified_name=rc.qn,
                    name=rc.name,
                    path=self.path,
                    language="python",
                    start_line=rc.start_line,
                    end_line=rc.end_line,
                    owner=rc.owner,
                    docstring=rc.docstring,
                    bases=bases,
                    decorators=rc.decorators,
                    span=rc.span,
                    is_test=is_test,
                )
            )

        functions: list[FunctionNode] = []
        for rf in self.functions:
            is_method = rf.owner_kind == "class"
            is_test = rf.name.startswith("test") and (
                (rf.owner_kind == "file" and file_is_test) or rf.owner in test_classes
            )
            functions.append(
                FunctionNode(
                    repo_id=self.repo_id,
                    qualified_name=rf.qn,
                    name=rf.name,
                    path=self.path,
                    language="python",
                    start_line=rf.start_line,
                    end_line=rf.end_line,
                    owner=rf.owner,
                    signature=rf.signature,
                    docstring=rf.docstring,
                    decorators=rf.decorators,
                    span=rf.span,
                    body_span=rf.body_span,
                    is_method=is_method,
                    is_async=rf.is_async,
                    is_test=is_test,
                )
            )

        line_count = max(1, len(self.source.splitlines()))
        file_node = FileNode(
            repo_id=self.repo_id,
            qualified_name=self.path,
            name=self.path.rsplit("/", 1)[-1],
            path=self.path,
            language="python",
            start_line=1,
            end_line=line_count,
            module=self.module,
            is_test=file_is_test,
            imports=tuple(self.imports),
        )
        defined: list[ClassNode | FunctionNode] = [*classes, *functions]
        defines = tuple(
            Edge(
                kind=EdgeKind.DEFINES,
                repo_id=self.repo_id,
                src=n.owner,
                dst=n.qualified_name,
                path=self.path,
                line=n.start_line,
            )
            for n in defined
        )
        return ParsedFile(
            repo_id=self.repo_id,
            path=self.path,
            language="python",
            source=self.source,
            file=file_node,
            classes=tuple(classes),
            functions=tuple(functions),
            defines=defines,
            calls=tuple(sorted(self.calls, key=lambda c: (c.line, c.caller, c.name))),
            has_errors=has_errors,
        )
