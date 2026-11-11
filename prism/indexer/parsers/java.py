"""Java parser built on tree-sitter.

Extracts the file and its package, classes, interfaces, enums and records
(nested ones too), methods and constructors, imports and call sites.

Qualified names follow Java source names: `com.acme.shop.Cart.Item` for a
nested type. Methods carry their erased parameter types so overloads stay
distinct and a name is stable when an overload is added:
`com.acme.shop.Cart.addItem(Item,int)`, `...Cart.log(String...)`.
Constructors are named `<init>`: `com.acme.shop.Cart.<init>(Repo)`.

Anonymous and local classes do not become nodes; calls inside them belong to
the enclosing method. Receiver types are recorded only when they are declared:
a parameter, local variable or field with a written type, `var x = new T()`,
a cast, or `new T().m()`.
"""

import re
from dataclasses import dataclass, field

import tree_sitter_java
from tree_sitter import Language, Node, Parser

from prism.indexer.model import (
    ClassNode,
    Edge,
    EdgeKind,
    FileNode,
    FunctionNode,
    ImportRef,
)
from prism.indexer.parsers.base import CallSite, ParsedFile, ReceiverKind

_LANGUAGE = Language(tree_sitter_java.language())
_WS = re.compile(r"\s+")
_GENERIC = re.compile(r"<[^<>]*>")

_TYPE_DECLS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}
_METHOD_DECLS = ("method_declaration", "constructor_declaration", "compact_constructor_declaration")
_TEST_ANNOTATIONS = frozenset({"Test", "ParameterizedTest", "RepeatedTest", "TestFactory"})


def is_java_test_file(path: str) -> bool:
    """Under `src/test/`, or a file named `*Test`, `*Tests`, `*IT` or `*TestCase`."""
    if path.startswith("src/test/") or "/src/test/" in path:
        return True
    stem = path.rsplit("/", 1)[-1].removesuffix(".java")
    return stem.endswith(("Test", "Tests", "IT", "TestCase"))


def erase(type_text: str) -> str:
    """`java.util.List<String>` -> `List`; `Map.Entry<K, V>[]` -> `Entry[]`; `int...` stays."""
    text = _WS.sub("", type_text)
    while "<" in text:
        stripped = _GENERIC.sub("", text)
        if stripped == text:
            break
        text = stripped
    text = re.sub(r"@\w+(\([^)]*\))?", "", text)  # type annotations
    suffix = ""
    while text.endswith("[]"):
        suffix += "[]"
        text = text[:-2]
    return text.rsplit(".", 1)[-1] + suffix


def type_name(type_text: str) -> str:
    """A type as written, without generics or annotations: `Map.Entry<K,V>` -> `Map.Entry`."""
    text = _WS.sub("", type_text)
    while "<" in text:
        stripped = _GENERIC.sub("", text)
        if stripped == text:
            break
        text = stripped
    return re.sub(r"@\w+(\([^)]*\))?", "", text)


def object_type(declared: str | None) -> str | None:
    """A declared type that can have methods: not a primitive, not an array."""
    if declared is None or declared.endswith("]"):
        return None
    return None if declared[:1].islower() and "." not in declared else declared


@dataclass(slots=True)
class _Scope:
    qn: str  # caller for calls in this scope
    prefix: str  # prefix for child type names
    kind: str  # "file", "class", "function" or "anon"
    enclosing_class: str | None
    parent: "_Scope | None" = None
    class_super: str | None = None  # raw superclass name, for super(...)
    record_types: tuple[str, ...] = ()  # record components, for the compact constructor
    vars: dict[str, str | None] = field(default_factory=dict)
    names: dict[str, int] = field(default_factory=dict)

    def child_qn(self, name: str) -> str:
        n = self.names.get(name, 0) + 1
        self.names[name] = n
        qn = f"{self.prefix}.{name}" if self.prefix else name
        return qn if n == 1 else f"{qn}#{n}"

    def lookup(self, name: str) -> tuple[bool, str | None]:
        scope: _Scope | None = self
        while scope is not None:
            if name in scope.vars:
                return True, scope.vars[name]
            scope = scope.parent
        return False, None


@dataclass(slots=True)
class _RawType:
    qn: str
    name: str
    owner: str
    kind: str
    start_line: int
    end_line: int
    span: tuple[int, int]
    docstring: str | None
    bases: tuple[str, ...]
    annotations: tuple[str, ...]


@dataclass(slots=True)
class _RawMethod:
    qn: str
    name: str
    owner: str
    start_line: int
    end_line: int
    span: tuple[int, int]
    body_span: tuple[int, int]
    signature: str
    docstring: str | None
    annotations: tuple[str, ...]


class JavaParser:
    language: str = "java"
    extensions: tuple[str, ...] = (".java",)

    def parse(self, repo_id: str, path: str, source: bytes) -> ParsedFile:
        return _JavaVisitor(repo_id, path, source, Parser(_LANGUAGE)).run()


class _JavaVisitor:
    def __init__(self, repo_id: str, path: str, source: bytes, parser: Parser) -> None:
        self.repo_id = repo_id
        self.path = path
        self.source = source
        self.parser = parser
        self.package = ""
        self.types: list[_RawType] = []
        self.methods: list[_RawMethod] = []
        self.imports: list[ImportRef] = []
        self.calls: list[CallSite] = []

    def text(self, node: Node | None) -> str:
        if node is None:
            return ""
        return self.source[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def run(self) -> ParsedFile:
        root = self.parser.parse(self.source).root_node
        for child in root.named_children:
            if child.type == "package_declaration":
                name = next((c for c in child.named_children if "identifier" in c.type), None)
                self.package = self.text(name)
            elif child.type == "import_declaration":
                self.import_declaration(child)
        file_scope = _Scope(qn=self.path, prefix=self.package, kind="file", enclosing_class=None)
        stack: list[tuple[Node, _Scope]] = [(root, file_scope)]
        while stack:
            node, scope = stack.pop()
            stack.extend(reversed(self.visit(node, scope)))
        return self.build(has_errors=root.has_error)

    # Imports

    def import_declaration(self, node: Node) -> None:
        line = node.start_point.row + 1
        is_static = any(c.type == "static" for c in node.children)
        name_node = next(
            (c for c in node.named_children if c.type in ("scoped_identifier", "identifier")),
            None,
        )
        name = self.text(name_node)
        if not name:
            return
        if any(c.type == "asterisk" for c in node.children):
            self.imports.append(
                ImportRef(local_name="*", target=name, module=name, line=line, is_static=is_static)
            )
            return
        module, _, local = name.rpartition(".")
        self.imports.append(
            ImportRef(local_name=local, target=name, module=module, line=line, is_static=is_static)
        )

    # Traversal

    def visit(self, node: Node, scope: _Scope) -> list[tuple[Node, _Scope]]:
        t = node.type
        if t in ("package_declaration", "import_declaration", "line_comment", "block_comment"):
            return []
        if t in _TYPE_DECLS:
            if scope.kind in ("file", "class"):
                return self.type_declaration(node, scope)
            return self.children(node, self.anon(scope))  # local class: part of the method
        if t in _METHOD_DECLS:
            if scope.kind == "class":
                return self.method_declaration(node, scope)
            return self.children(node, self.anon(scope))  # method of an anonymous class
        if t == "local_variable_declaration":
            self.declare_locals(node, scope)
        elif t == "method_invocation":
            self.method_invocation(node, scope)
        elif t == "object_creation_expression":
            self.object_creation(node, scope)
            out: list[tuple[Node, _Scope]] = []
            for c in node.children:
                out.append((c, self.anon(scope) if c.type == "class_body" else scope))
            return out
        elif t == "enum_constant":
            return self.children(node, self.anon(scope))
        elif t == "method_reference":
            self.method_reference(node, scope)
        elif t == "explicit_constructor_invocation":
            self.explicit_constructor(node, scope)
        return self.children(node, scope)

    def children(self, node: Node, scope: _Scope) -> list[tuple[Node, _Scope]]:
        return [(c, scope) for c in node.children]

    def anon(self, scope: _Scope) -> _Scope:
        """A scope for code that belongs to the enclosing member but defines no nodes."""
        if scope.kind == "anon":
            return scope
        return _Scope(
            qn=scope.qn,
            prefix=scope.prefix,
            kind="anon",
            enclosing_class=scope.enclosing_class,
            parent=scope,
            class_super=scope.class_super,
        )

    def javadoc(self, node: Node) -> str | None:
        prev = node.prev_named_sibling
        if prev is None or prev.type != "block_comment":
            return None
        raw = self.text(prev)
        if not raw.startswith("/**"):
            return None
        lines = raw.removeprefix("/**").removesuffix("*/").splitlines()
        cleaned = [re.sub(r"^\s*\*?\s?", "", ln).rstrip() for ln in lines]
        text = "\n".join(cleaned).strip()
        return text or None

    def annotations(self, node: Node) -> tuple[str, ...]:
        mods = next((c for c in node.children if c.type == "modifiers"), None)
        if mods is None:
            return ()
        return tuple(
            self.text(c).lstrip("@").strip()
            for c in mods.named_children
            if c.type in ("marker_annotation", "annotation")
        )

    def type_declaration(self, node: Node, scope: _Scope) -> list[tuple[Node, _Scope]]:
        name = self.text(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        if not name or body is None:
            return self.children(node, scope)
        qn = scope.child_qn(name)
        bases: list[str] = []
        superclass = node.child_by_field_name("superclass")
        class_super: str | None = None
        if superclass is not None:
            sup = next((c for c in superclass.named_children), None)
            if sup is not None:
                class_super = type_name(self.text(sup))
                bases.append(class_super)
        for field_name in ("interfaces",):
            holder = node.child_by_field_name(field_name)
            if holder is not None:
                bases.extend(self.type_list(holder))
        # Interfaces list their supertypes in `extends_interfaces`.
        for c in node.children:
            if c.type == "extends_interfaces":
                bases.extend(self.type_list(c))
        kind = _TYPE_DECLS[node.type]
        self.types.append(
            _RawType(
                qn=qn,
                name=name,
                owner=scope.qn,
                kind=kind,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                span=(node.start_byte, node.end_byte),
                docstring=self.javadoc(node),
                bases=tuple(bases),
                annotations=self.annotations(node),
            )
        )
        inner = _Scope(
            qn=qn,
            prefix=qn,
            kind="class",
            enclosing_class=qn,
            parent=scope,
            class_super=class_super,
        )
        if kind == "record":
            params = node.child_by_field_name("parameters")
            inner.record_types = tuple(self.param_types(params))
            for pname, ptype in self.param_vars(params):
                inner.vars[pname] = ptype
        self.declare_fields(body, inner)
        out: list[tuple[Node, _Scope]] = []
        for c in node.children:
            if c == body:
                out.append((c, inner))
            elif c.type in ("superclass", "super_interfaces", "extends_interfaces", "modifiers"):
                out.append((c, scope))  # annotation arguments run in the outer scope
        return out

    def type_list(self, holder: Node) -> list[str]:
        out: list[str] = []
        for c in holder.named_children:
            if c.type == "type_list":
                out.extend(type_name(self.text(t)) for t in c.named_children)
            elif "type" in c.type:
                out.append(type_name(self.text(c)))
        return out

    def declare_fields(self, body: Node, scope: _Scope) -> None:
        for member in body.named_children:
            if member.type == "enum_body_declarations":
                self.declare_fields(member, scope)
            if member.type not in ("field_declaration", "constant_declaration"):
                continue
            ftype = member.child_by_field_name("type")
            for decl in member.children_by_field_name("declarator"):
                name = self.text(decl.child_by_field_name("name"))
                if name:
                    scope.vars[name] = self.declared_type(ftype, decl)

    def declared_type(self, type_node: Node | None, declarator: Node | None) -> str | None:
        text = self.text(type_node)
        if not text:
            return None
        if text == "var":
            value = declarator.child_by_field_name("value") if declarator is not None else None
            if value is not None and value.type == "object_creation_expression":
                return type_name(self.text(value.child_by_field_name("type")))
            return None
        name = type_name(text)
        if declarator is not None and declarator.child_by_field_name("dimensions") is not None:
            name += "[]"
        return name

    def declare_locals(self, node: Node, scope: _Scope) -> None:
        ftype = node.child_by_field_name("type")
        target = scope if scope.kind in ("function", "anon") else None
        if target is None:
            return
        for decl in node.children_by_field_name("declarator"):
            name = self.text(decl.child_by_field_name("name"))
            if name:
                target.vars[name] = self.declared_type(ftype, decl)

    def param_vars(self, params: Node | None) -> list[tuple[str, str | None]]:
        out: list[tuple[str, str | None]] = []
        if params is None:
            return out
        for p in params.named_children:
            if p.type == "formal_parameter":
                name = self.text(p.child_by_field_name("name"))
                out.append((name, self.declared_type(p.child_by_field_name("type"), None)))
            elif p.type == "spread_parameter":
                decl = next((c for c in p.named_children if c.type == "variable_declarator"), None)
                name = self.text(decl.child_by_field_name("name")) if decl is not None else ""
                out.append((name, None))  # varargs are arrays
        return out

    def param_types(self, params: Node | None) -> list[str]:
        out: list[str] = []
        if params is None:
            return out
        for p in params.named_children:
            if p.type == "formal_parameter":
                out.append(erase(self.text(p.child_by_field_name("type"))))
            elif p.type == "spread_parameter":
                type_node = next(
                    (
                        c
                        for c in p.named_children
                        if c.type not in ("modifiers", "variable_declarator")
                    ),
                    None,
                )
                out.append(erase(self.text(type_node)) + "...")
            elif p.type == "receiver_parameter":
                continue
        return out

    def method_declaration(self, node: Node, scope: _Scope) -> list[tuple[Node, _Scope]]:
        params = node.child_by_field_name("parameters")
        body = node.child_by_field_name("body")
        if node.type == "method_declaration":
            name = self.text(node.child_by_field_name("name"))
            types = self.param_types(params)
        else:
            name = "<init>"
            types = (
                self.param_types(params)
                if node.type == "constructor_declaration"
                else list(scope.record_types)
            )
        if not name:
            return self.children(node, scope)
        qn = scope.child_qn(f"{name}({','.join(types)})")
        start, end = node.start_byte, node.end_byte
        body_span = (body.start_byte, body.end_byte) if body is not None else (end, end)
        self.methods.append(
            _RawMethod(
                qn=qn,
                name=name,
                owner=scope.qn,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                span=(start, end),
                body_span=body_span,
                signature=self.signature(node, body),
                docstring=self.javadoc(node),
                annotations=self.annotations(node),
            )
        )
        inner = _Scope(
            qn=qn,
            prefix=qn,
            kind="function",
            enclosing_class=scope.enclosing_class,
            parent=scope,
            class_super=scope.class_super,
            record_types=scope.record_types,
        )
        for pname, ptype in self.param_vars(params):
            if pname:
                inner.vars[pname] = ptype
        out: list[tuple[Node, _Scope]] = []
        for c in node.children:
            if c == body:
                out.append((c, inner))
            elif c.type == "modifiers":
                out.append((c, scope))
        return out

    def signature(self, node: Node, body: Node | None) -> str:
        """Modifiers (without annotations), type parameters, return type, name, parameters,
        throws; on one line."""
        mods = next((c for c in node.children if c.type == "modifiers"), None)
        words: list[str] = []
        start = node.start_byte
        if mods is not None:
            words = [
                self.text(c)
                for c in mods.children
                if c.type not in ("marker_annotation", "annotation")
            ]
            start = mods.end_byte
        end = body.start_byte if body is not None else node.end_byte
        rest = self.source[start:end].decode("utf-8", "replace").strip().removesuffix(";")
        text = " ".join([*words, rest]).strip()
        return _WS.sub(" ", text).replace("( ", "(").replace(" )", ")")

    # Calls

    def receiver(self, obj: Node, scope: _Scope) -> tuple[ReceiverKind, str | None, str | None]:
        """Receiver kind, dotted receiver text, and declared receiver type if known."""
        t = obj.type
        if t == "this":
            return ReceiverKind.SELF, None, None
        if t == "super":
            return ReceiverKind.SUPER, None, None
        if t == "identifier":
            name = self.text(obj)
            found, declared = scope.lookup(name)
            return ReceiverKind.NAME, name, object_type(declared) if found else None
        if t == "field_access":
            target = obj.child_by_field_name("object")
            field_name = self.text(obj.child_by_field_name("field"))
            if target is not None and target.type == "this":
                _, declared = self.field_type(scope, field_name)
                return ReceiverKind.NAME, f"this.{field_name}", object_type(declared)
            dotted = self.dotted(obj)
            if dotted is not None:
                return ReceiverKind.NAME, dotted, None
        if t == "object_creation_expression":
            return ReceiverKind.EXPR, None, type_name(self.text(obj.child_by_field_name("type")))
        if t == "parenthesized_expression":
            inner = next(iter(obj.named_children), None)
            if inner is not None and inner.type == "cast_expression":
                return (
                    ReceiverKind.EXPR,
                    None,
                    type_name(self.text(inner.child_by_field_name("type"))),
                )
        if t in ("type_identifier", "scoped_type_identifier", "generic_type"):
            return ReceiverKind.NAME, type_name(self.text(obj)), None
        return ReceiverKind.EXPR, None, None

    def field_type(self, scope: _Scope, name: str) -> tuple[bool, str | None]:
        s: _Scope | None = scope
        while s is not None and s.kind != "class":
            s = s.parent
        while s is not None:
            if name in s.vars:
                return True, s.vars[name]
            s = s.parent
        return False, None

    def dotted(self, node: Node) -> str | None:
        if node.type == "identifier":
            return self.text(node)
        if node.type == "field_access":
            head = node.child_by_field_name("object")
            name = self.text(node.child_by_field_name("field"))
            prefix = self.dotted(head) if head is not None else None
            return None if prefix is None or not name else f"{prefix}.{name}"
        return None

    def add_call(
        self,
        scope: _Scope,
        name: str,
        kind: ReceiverKind,
        receiver: str | None,
        line: int,
        receiver_type: str | None = None,
        arg_count: int | None = None,
        arg_types: tuple[str | None, ...] | None = None,
    ) -> None:
        self.calls.append(
            CallSite(
                scope.qn,
                name,
                kind,
                receiver,
                line,
                scope.enclosing_class,
                receiver_type=receiver_type,
                arg_count=arg_count,
                arg_types=arg_types,
            )
        )

    def arg_types(self, args: Node | None, scope: _Scope) -> tuple[str | None, ...] | None:
        """Argument types where the source states them: literals, casts, `new T()`,
        declared variables. None for any other argument."""
        if args is None:
            return None
        return tuple(self.expr_type(a, scope) for a in args.named_children)

    def expr_type(self, node: Node, scope: _Scope) -> str | None:
        t = node.type
        if t in ("string_literal", "text_block"):
            return "String"
        if t == "character_literal":
            return "char"
        if t in ("true", "false"):
            return "boolean"
        if t == "null_literal":
            return "null"
        if t.endswith("integer_literal"):
            return "long" if self.text(node).lower().endswith("l") else "int"
        if t.endswith("floating_point_literal"):
            return "float" if self.text(node).lower().endswith("f") else "double"
        if t == "cast_expression":
            return type_name(self.text(node.child_by_field_name("type")))
        if t == "object_creation_expression":
            return type_name(self.text(node.child_by_field_name("type")))
        if t == "identifier":
            found, declared = scope.lookup(self.text(node))
            return declared if found else None
        if t == "field_access":
            target = node.child_by_field_name("object")
            if target is not None and target.type == "this":
                return self.field_type(scope, self.text(node.child_by_field_name("field")))[1]
        return None

    def method_invocation(self, node: Node, scope: _Scope) -> None:
        name = self.text(node.child_by_field_name("name"))
        args = node.child_by_field_name("arguments")
        count = len(args.named_children) if args is not None else None
        types = self.arg_types(args, scope)
        obj = node.child_by_field_name("object")
        line = node.start_point.row + 1
        if obj is None:
            self.add_call(scope, name, ReceiverKind.NONE, None, line, None, count, types)
            return
        kind, receiver, declared = self.receiver(obj, scope)
        self.add_call(scope, name, kind, receiver, line, declared, count, types)

    def object_creation(self, node: Node, scope: _Scope) -> None:
        created = type_name(self.text(node.child_by_field_name("type")))
        if created:
            self.add_call(scope, created, ReceiverKind.NEW, None, node.start_point.row + 1)

    def method_reference(self, node: Node, scope: _Scope) -> None:
        parts = node.children
        if len(parts) < 3:
            return
        target, last = parts[0], parts[-1]
        line = node.start_point.row + 1
        if last.type == "new":
            self.add_call(scope, type_name(self.text(target)), ReceiverKind.NEW, None, line)
            return
        name = self.text(last)
        kind, receiver, declared = self.receiver(target, scope)
        self.add_call(scope, name, kind, receiver, line, declared, None)

    def explicit_constructor(self, node: Node, scope: _Scope) -> None:
        ctor = node.child_by_field_name("constructor")
        if ctor is not None and ctor.type == "super" and scope.class_super:
            self.add_call(
                scope, scope.class_super, ReceiverKind.NEW, None, node.start_point.row + 1
            )

    # Assembly

    def build(self, *, has_errors: bool) -> ParsedFile:
        file_is_test = is_java_test_file(self.path)
        classes: list[ClassNode] = []
        test_classes: set[str] = set()
        class_level_test: set[str] = set()
        for rt in self.types:
            plain = {a.split("(")[0].rsplit(".", 1)[-1] for a in rt.annotations}
            if "Test" in plain:
                class_level_test.add(rt.qn)  # TestNG: every public method is a test
            is_test = file_is_test or rt.name.endswith(("Test", "Tests", "IT"))
            if is_test:
                test_classes.add(rt.qn)
            classes.append(
                ClassNode(
                    repo_id=self.repo_id,
                    qualified_name=rt.qn,
                    name=rt.name,
                    path=self.path,
                    language="java",
                    start_line=rt.start_line,
                    end_line=rt.end_line,
                    owner=rt.owner,
                    docstring=rt.docstring,
                    bases=rt.bases,
                    decorators=rt.annotations,
                    span=rt.span,
                    is_test=is_test,
                    class_kind=rt.kind,
                )
            )
        functions: list[FunctionNode] = []
        for rm in self.methods:
            plain = {a.split("(")[0].rsplit(".", 1)[-1] for a in rm.annotations}
            is_test = bool(plain & _TEST_ANNOTATIONS) or (
                rm.owner in class_level_test
                and rm.name != "<init>"
                and rm.signature.startswith("public")
            )
            functions.append(
                FunctionNode(
                    repo_id=self.repo_id,
                    qualified_name=rm.qn,
                    name=rm.name,
                    path=self.path,
                    language="java",
                    start_line=rm.start_line,
                    end_line=rm.end_line,
                    owner=rm.owner,
                    signature=rm.signature,
                    docstring=rm.docstring,
                    decorators=rm.annotations,
                    span=rm.span,
                    body_span=rm.body_span,
                    is_method=True,
                    is_test=is_test,
                )
            )
        file_node = FileNode(
            repo_id=self.repo_id,
            qualified_name=self.path,
            name=self.path.rsplit("/", 1)[-1],
            path=self.path,
            language="java",
            start_line=1,
            end_line=max(1, len(self.source.splitlines())),
            module=self.package,
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
            language="java",
            source=self.source,
            file=file_node,
            classes=tuple(classes),
            functions=tuple(functions),
            defines=defines,
            calls=tuple(sorted(self.calls, key=lambda c: (c.line, c.caller, c.name))),
            has_errors=has_errors,
        )
