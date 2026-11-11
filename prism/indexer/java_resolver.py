"""Java call resolution (ADR 0004).

Order for a call: the enclosing class and its repo supertypes, enclosing outer
classes, then static imports, then a name match. A receiver with a declared
type, a class name (`Util.run()`) or `new T()` is looked up through its type,
and the type is found by: enclosing and nested classes, the same file, explicit
imports, the same package, on-demand imports. The band of the edge is the band
of whichever step found it. Overloads are narrowed by argument count when the
count is known; several left means an edge to each.
"""

from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from prism.indexer.model import (
    ClassNode,
    Edge,
    EdgeKind,
    FileNode,
    FunctionNode,
    Node,
    Resolution,
)
from prism.indexer.parsers.base import CallSite, ParsedFile, ReceiverKind
from prism.indexer.resolver import FileResolver

if TYPE_CHECKING:
    from prism.indexer.symbols import SymbolTable

# java.lang is imported implicitly; these never resolve into a repo.
_JAVA_LANG_NAMES = """
    Boolean Byte Character Class ClassLoader CharSequence Comparable Double Enum Error Exception
    Float IllegalArgumentException IllegalStateException Integer Iterable Long Math Number Object
    Override Process Record Runnable Runtime RuntimeException Short StrictMath String StringBuffer
    StringBuilder System Thread ThreadLocal Throwable UnsupportedOperationException Void
"""
JAVA_LANG = frozenset(_JAVA_LANG_NAMES.split())


@dataclass(frozen=True, slots=True)
class TypeHit:
    node: ClassNode | None
    band: Resolution | None
    external: bool = False


_MISS = TypeHit(None, None)
_EXTERNAL = TypeHit(None, None, external=True)


def params_of(qualified_name: str) -> tuple[list[str], bool]:
    """Parameter types from `pkg.C.m(A,int...)`, and whether the last is varargs."""
    if "(" not in qualified_name:
        return [], False
    inside = qualified_name[qualified_name.rindex("(") + 1 :].split(")", 1)[0]
    params = inside.split(",") if inside else []
    return params, bool(params) and params[-1].endswith("...")


def accepts(method: FunctionNode, arity: int | None) -> bool:
    if arity is None:
        return True
    params, varargs = params_of(method.qualified_name)
    return arity == len(params) or (varargs and arity >= len(params) - 1)


_BOXED = {
    "boolean": "Boolean",
    "byte": "Byte",
    "char": "Character",
    "double": "Double",
    "float": "Float",
    "int": "Integer",
    "long": "Long",
    "short": "Short",
}
# Types whose relations we know without the JDK: primitives, their boxes and String.
_KNOWN = frozenset({*_BOXED, *_BOXED.values(), "String"})
_WIDENS = {
    "byte": {"short", "int", "long", "float", "double"},
    "short": {"int", "long", "float", "double"},
    "char": {"int", "long", "float", "double"},
    "int": {"long", "float", "double"},
    "long": {"float", "double"},
    "float": {"double"},
}


def compatible(arg: str | None, param: str, *, boxing: bool = True) -> bool:
    """False only when both types are known and the argument cannot be passed."""
    if arg is None:
        return True
    param = param.removesuffix("...")
    if arg == "null":
        return param not in _BOXED  # null fits any reference type, never a primitive
    arg = arg.rsplit(".", 1)[-1]
    if arg == param or arg not in _KNOWN or param not in _KNOWN:
        return True
    unboxed = {v: k for k, v in _BOXED.items()}
    if not boxing and (arg in _BOXED) != (param in _BOXED):
        return False
    a = unboxed.get(arg, arg)
    p = unboxed.get(param, param)
    return a == p or p in _WIDENS.get(a, set())


def fits_types(
    method: FunctionNode, arg_types: tuple[str | None, ...], *, boxing: bool = True
) -> bool:
    params, varargs = params_of(method.qualified_name)
    for i, arg in enumerate(arg_types):
        if i < len(params):
            param = params[i]
        elif varargs:
            param = params[-1]
        else:
            return False
        if not compatible(arg, param, boxing=boxing):
            return False
    return True


def by_arity(
    methods: Iterable[FunctionNode],
    arity: int | None,
    arg_types: tuple[str | None, ...] | None = None,
) -> list[FunctionNode]:
    """Overloads that take `arity` arguments of compatible stated types. Falls back to the
    wider set when a filter would leave nothing."""
    found = list(methods)
    fit = [m for m in found if accepts(m, arity)] or found
    if arg_types and len(fit) > 1:
        # Like javac: overloads that fit without boxing win over those that need it.
        strict = [m for m in fit if fits_types(m, arg_types, boxing=False)]
        if strict:
            return strict
        typed = [m for m in fit if fits_types(m, arg_types)]
        return typed or fit
    return fit


class JavaIndex:
    def __init__(self, symbols: "SymbolTable", nodes: Iterable[Node]) -> None:
        self.symbols = symbols
        self.types: dict[str, ClassNode] = {}
        self.files: dict[str, FileNode] = {}
        self.packages: dict[str, list[str]] = defaultdict(list)
        self.methods: dict[str, dict[str, list[FunctionNode]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.by_name: dict[str, list[FunctionNode]] = defaultdict(list)
        for node in nodes:
            if isinstance(node, FileNode):
                self.files[node.path] = node
                self.packages[node.module].append(node.path)
            elif isinstance(node, ClassNode):
                self.types[node.qualified_name] = node
            elif "#" not in node.qualified_name:
                self.methods[node.owner][node.name].append(node)
                if node.name != "<init>":
                    self.by_name[node.name].append(node)
        self._supers: dict[str, list[tuple[ClassNode, Resolution]]] = {}

    def file_of(self, node: Node) -> FileNode | None:
        return self.files.get(node.path)

    def outer_classes(self, class_qn: str | None) -> list[ClassNode]:
        """The class itself, then each class it is nested in."""
        out: list[ClassNode] = []
        cls = self.types.get(class_qn) if class_qn else None
        while cls is not None:
            out.append(cls)
            cls = self.types.get(cls.owner)
        return out

    def resolve_type(self, text: str, file: FileNode, context: str | None) -> TypeHit:
        """Find a type named in `file`, from inside class `context` if given."""
        text = text.removesuffix("...").replace("[]", "")
        if not text:
            return _MISS
        if "." in text:
            if text in self.types:
                return TypeHit(self.types[text], Resolution.IMPORT)
            head, _, rest = text.partition(".")
            hit = self.resolve_type(head, file, context)
            if hit.node is not None:
                nested = self.types.get(f"{hit.node.qualified_name}.{rest}")
                return TypeHit(nested, hit.band) if nested is not None else _MISS
            if hit.external or head[:1].islower():
                return _EXTERNAL  # an external class, or a package-qualified external name
            return _MISS
        for cls in self.outer_classes(context):
            if cls.name == text:
                return TypeHit(cls, Resolution.SAME_FILE)
            nested = self.types.get(f"{cls.qualified_name}.{text}")
            if nested is not None:
                return TypeHit(nested, Resolution.SAME_FILE)
        own = self.types.get(_join(file.module, text))
        if own is not None and own.path == file.path:
            return TypeHit(own, Resolution.SAME_FILE)
        for ref in file.imports:
            if not ref.is_star and not ref.is_static and ref.local_name == text:
                target = self.types.get(ref.target)
                return TypeHit(target, Resolution.IMPORT) if target is not None else _EXTERNAL
        if own is not None:
            return TypeHit(own, Resolution.SAME_PACKAGE)
        for ref in file.imports:
            if ref.is_star:
                target = self.types.get(f"{ref.target}.{text}")
                if target is not None:
                    return TypeHit(target, Resolution.STAR_IMPORT)
        if text in JAVA_LANG:
            return _EXTERNAL
        return _MISS

    def supertypes(self, class_qn: str) -> list[tuple[ClassNode, Resolution]]:
        """Repo classes a class directly extends or implements, with the band that found each."""
        cached = self._supers.get(class_qn)
        if cached is not None:
            return cached
        self._supers[class_qn] = []  # guards against cycles in broken code
        cls = self.types.get(class_qn)
        file = self.file_of(cls) if cls is not None else None
        out: list[tuple[ClassNode, Resolution]] = []
        if cls is not None and file is not None:
            context = cls.owner if cls.owner in self.types else None
            for base in cls.bases:
                hit = self.resolve_type(base, file, context)
                if hit.node is not None and hit.band is not None and hit.node is not cls:
                    out.append((hit.node, hit.band))
        self._supers[class_qn] = out
        return out

    def find_methods(
        self,
        class_qn: str,
        name: str,
        arity: int | None,
        arg_types: tuple[str | None, ...] | None = None,
    ) -> tuple[list[FunctionNode], bool]:
        """Methods named `name` on a class, else on the nearest repo supertype that has them.

        Returns the matches and whether they were inherited.
        """
        seen: set[str] = set()
        queue: list[tuple[str, int]] = [(class_qn, 0)]
        while queue:
            qn, depth = queue.pop(0)
            if qn in seen or depth > 10:
                continue
            seen.add(qn)
            own = self.methods.get(qn, {}).get(name)
            if own:
                return by_arity(own, arity, arg_types), depth > 0
            queue.extend((sup.qualified_name, depth + 1) for sup, _ in self.supertypes(qn))
        return [], False

    def dependents(self, touched: Collection[FileNode]) -> set[str]:
        """Java files whose edges can move when these files change: the same package
        (no import needed), and files importing a type or package from them."""
        packages = {f.module for f in touched if f.language == "java"}
        touched_paths = {f.path for f in touched}
        touched_types = {t.qualified_name for t in self.types.values() if t.path in touched_paths}
        out: set[str] = set()
        for path, file in self.files.items():
            if path in touched_paths:
                continue
            if file.module in packages:
                out.add(path)
                continue
            for ref in file.imports:
                if (
                    ref.target in touched_types
                    or ref.module in touched_types
                    or (ref.is_star and ref.target in packages)
                    or (ref.is_star and ref.target in touched_types)
                ):
                    out.add(path)
                    break
        return out


def _join(package: str, name: str) -> str:
    return f"{package}.{name}" if package else name


class JavaFileResolver(FileResolver):
    def __init__(self, pf: ParsedFile, symbols: "SymbolTable") -> None:
        super().__init__(pf, symbols)
        self.index = symbols.java
        self.file = pf.file

    def methods_on(
        self,
        hit: TypeHit,
        name: str,
        arity: int | None,
        types: tuple[str | None, ...] | None,
    ) -> list[tuple[Node, Resolution]]:
        if hit.node is None or hit.band is None:
            return []
        found, _ = self.index.find_methods(hit.node.qualified_name, name, arity, types)
        return [(m, hit.band) for m in found]

    def resolve_site(self, site: CallSite) -> list[tuple[Node, Resolution]]:
        kind, name, arity = site.receiver_kind, site.name, site.arg_count
        types = site.arg_types
        context = site.enclosing_class
        if kind is ReceiverKind.NEW:
            hit = self.index.resolve_type(name, self.file, context)
            return [(hit.node, hit.band)] if hit.node is not None and hit.band else []
        if kind in (ReceiverKind.NONE, ReceiverKind.SELF):
            return self.unqualified(site, kind is ReceiverKind.NONE)
        if kind is ReceiverKind.SUPER:
            if context is None:
                return []
            for sup, _ in self.index.supertypes(context):
                found, _ = self.index.find_methods(sup.qualified_name, name, arity, types)
                if found:
                    return [(m, Resolution.INHERITED) for m in found]
            return []
        if site.receiver_type:
            hit = self.index.resolve_type(site.receiver_type, self.file, context)
            if hit.node is not None:
                # Declared type is in the repo: a miss means an external supertype's method.
                return self.methods_on(hit, name, arity, types)
            if hit.external:
                return []
            return self.name_match_java(name, arity, types)
        receiver = site.receiver
        if kind is ReceiverKind.NAME and receiver and not receiver.startswith("this."):
            head = receiver.split(".", 1)[0]
            if head[:1].isupper() or "." in receiver:
                hit = self.index.resolve_type(receiver, self.file, context)
                if hit.node is not None:
                    return self.methods_on(hit, name, arity, types)
                if hit.external:
                    return []
        return self.name_match_java(name, arity, types)

    def unqualified(self, site: CallSite, bare: bool) -> list[tuple[Node, Resolution]]:
        name, arity, types = site.name, site.arg_count, site.arg_types
        classes = self.index.outer_classes(site.enclosing_class)
        if classes:
            found, inherited = self.index.find_methods(
                classes[0].qualified_name, name, arity, types
            )
            if found:
                band = Resolution.INHERITED if inherited else Resolution.SELF_METHOD
                return [(m, band) for m in found]
        if not bare:
            return self.name_match_java(name, arity, types)
        for outer in classes[1:]:
            found, _ = self.index.find_methods(outer.qualified_name, name, arity, types)
            if found:
                return [(m, Resolution.SAME_FILE) for m in found]
        for ref in self.file.imports:
            if ref.is_static and not ref.is_star and ref.local_name == name:
                hit = self.index.resolve_type(ref.module, self.file, None)
                if hit.node is None:
                    return []  # statically imported from outside the repo
                found, _ = self.index.find_methods(hit.node.qualified_name, name, arity, types)
                return [(m, Resolution.IMPORT) for m in found]
        for ref in self.file.imports:
            if ref.is_static and ref.is_star:
                hit = self.index.resolve_type(ref.target, self.file, None)
                if hit.node is not None:
                    found, _ = self.index.find_methods(hit.node.qualified_name, name, arity, types)
                    if found:
                        return [(m, Resolution.STAR_IMPORT) for m in found]
        return self.name_match_java(name, arity, types)

    def name_match_java(
        self, name: str, arity: int | None, types: tuple[str | None, ...] | None
    ) -> list[tuple[Node, Resolution]]:
        return self.name_match(name, by_arity(self.index.by_name.get(name, []), arity, types))

    # Other edges

    def import_edges(self) -> list[Edge]:
        targets: dict[str, int] = {}
        for ref in self.file.imports:
            paths: list[str] = []
            if ref.is_star and not ref.is_static:
                paths = list(self.index.packages.get(ref.target, []))
                nested = self.index.types.get(ref.target)  # import a.b.Outer.*;
                if nested is not None:
                    paths.append(nested.path)
            else:
                type_name = ref.module if ref.is_static and not ref.is_star else ref.target
                cls = self.index.types.get(type_name)
                if cls is not None:
                    paths.append(cls.path)
            for path in paths:
                if path != self.pf.path:
                    targets.setdefault(path, ref.line)
        return [
            self.edge(EdgeKind.IMPORTS, self.file.qualified_name, path, line, None)
            for path, line in targets.items()
        ]

    def inherit_edges(self) -> list[Edge]:
        out: list[Edge] = []
        for cls in self.pf.classes:
            for sup, band in self.index.supertypes(cls.qualified_name):
                out.append(
                    self.edge(
                        EdgeKind.INHERITS,
                        cls.qualified_name,
                        sup.qualified_name,
                        cls.start_line,
                        band,
                    )
                )
        for fn in self.pf.functions:
            if fn.name == "<init>" or " static " in f" {fn.signature} ":
                continue
            for sup, _ in self.index.supertypes(fn.owner):
                parent = self.overridden_in(sup.qualified_name, fn)
                if parent is not None:
                    out.append(
                        self.edge(
                            EdgeKind.OVERRIDES,
                            fn.qualified_name,
                            parent.qualified_name,
                            fn.start_line,
                            None,
                        )
                    )
        return out

    def overridden_in(self, class_qn: str, fn: FunctionNode) -> FunctionNode | None:
        """The nearest method in `class_qn` or above with the same parameters; failing that,
        the same name and number of parameters (generic parameters erase differently)."""
        params, _ = params_of(fn.qualified_name)
        exact = fn.qualified_name[fn.qualified_name.rindex("(") :]
        seen: set[str] = set()
        queue = [class_qn]
        while queue:
            qn = queue.pop(0)
            if qn in seen:
                continue
            seen.add(qn)
            same_name = self.index.methods.get(qn, {}).get(fn.name, [])
            for m in same_name:
                if m.qualified_name.endswith(exact):
                    return m
            for m in same_name:
                if len(params_of(m.qualified_name)[0]) == len(params):
                    return m
            queue.extend(sup.qualified_name for sup, _ in self.index.supertypes(qn))
        return None
