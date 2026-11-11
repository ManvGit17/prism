"""Symbol table the call resolver looks names up in.

It is built from graph nodes only, so it can be built from a full parse or from
a store (base graph minus a PR's changed files, plus the overlay's nodes).
"""

import sys
from collections import defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING

from prism.indexer.model import ClassNode, FileNode, FunctionNode, ImportRef, Node

if TYPE_CHECKING:
    from prism.indexer.java_resolver import JavaIndex

_STDLIB = frozenset(sys.stdlib_module_names)
_MAX_REEXPORT_DEPTH = 5
_MAX_BASE_DEPTH = 10


class SymbolTable:
    def __init__(self, nodes: Iterable[Node]) -> None:
        self.nodes: dict[str, Node] = {}
        self.files: dict[str, FileNode] = {}  # by path
        self.modules: dict[str, FileNode] = {}  # by module name derived from the path
        self.top_level: dict[str, list[Node]] = defaultdict(list)  # functions and classes
        self.methods: dict[str, list[FunctionNode]] = defaultdict(list)
        self.class_methods: dict[str, dict[str, FunctionNode]] = defaultdict(dict)
        self._fallback_cache: dict[str, bool] = {}
        self._other: list[Node] = []
        self._java: JavaIndex | None = None
        for node in nodes:
            self._add(node)
        self.import_names = self._import_names()

    def _add(self, node: Node) -> None:
        self.nodes[node.qualified_name] = node
        if node.language != "python":
            self._other.append(node)
            return  # the lookups below follow Python rules; Java has its own index
        if isinstance(node, FileNode):
            self.files[node.path] = node
            self.modules[node.module] = node
            return
        if "#" in node.qualified_name:
            return  # a redefinition; lookups use the first definition
        if isinstance(node, FunctionNode) and node.is_method:
            self.methods[node.name].append(node)
            self.class_methods[node.owner][node.name] = node
        elif node.owner == node.path:
            self.top_level[node.name].append(node)

    def _import_names(self) -> dict[str, list[str]]:
        """Map the name a module is imported by to its path-derived module name.

        `src/shop/cart.py` is imported as `shop.cart` when `src/` has no
        `__init__.py`. Walk up while the parent directory is a package.
        """
        packages = {p.rsplit("/", 1)[0] for p in self.files if p.endswith("/__init__.py")}
        out: dict[str, list[str]] = defaultdict(list)
        for path, node in self.files.items():
            parts = node.module.split(".")
            dirs = path.split("/")[:-1]
            # Number of innermost directories that are packages.
            n = 0
            while n < len(dirs) and "/".join(dirs[: len(dirs) - n]) in packages:
                n += 1
            # A package's own directory is its last name part; a module adds its file name.
            keep = n if path.endswith("/__init__.py") else n + 1
            name = ".".join(parts[len(parts) - keep :]) if keep else ""
            # A stray `scripts/json.py` must not capture `import json`.
            if name and name != node.module and name.split(".")[0] not in _STDLIB:
                out[name].append(node.module)
        return out

    @property
    def java(self) -> "JavaIndex":
        """Index of the Java nodes, built on first use."""
        if self._java is None:
            from prism.indexer.java_resolver import JavaIndex

            self._java = JavaIndex(self, [n for n in self._other if n.language == "java"])
        return self._java

    # Lookups

    def resolve_module(self, dotted: str) -> FileNode | None:
        if dotted in self.modules:
            return self.modules[dotted]
        candidates = self.import_names.get(dotted, [])
        if len(candidates) == 1:
            return self.modules[candidates[0]]
        return None

    def resolve_dotted(self, dotted: str, _depth: int = 0) -> Node | None:
        """Find the repo node a fully expanded dotted name refers to, or None.

        Tries the longest module prefix, then the rest as a path inside the
        module. Follows re-exports (`from .models import Item` in `__init__.py`)
        and methods inherited from repo base classes.
        """
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            module = self.resolve_module(".".join(parts[:i]))
            if module is None:
                continue
            rest = parts[i:]
            if not rest:
                return module
            found = self._in_module(module, rest, _depth)
            if found is not None:
                return found
        return None

    def is_shadowed_fallback(self, node: Node) -> bool:
        """A module-level definition in an `except` clause whose name the file also imports
        from the repo, as in `try: from .x import f` / `except ImportError: def f(): ...`.
        The import is what normally runs, so lookups skip the fallback.
        """
        if not isinstance(node, ClassNode | FunctionNode) or not node.is_fallback:
            return False
        if node.owner != node.path:
            return False
        qn = node.qualified_name
        if qn in self._fallback_cache:
            return self._fallback_cache[qn]
        self._fallback_cache[qn] = False  # guards against import cycles
        file = self.files.get(node.path)
        ref = self.binding(file, node.name) if file is not None else None
        target = self.resolve_dotted(ref.target) if ref is not None else None
        result = target is not None and target.qualified_name != qn
        self._fallback_cache[qn] = result
        return result

    def _in_module(self, module: FileNode, rest: list[str], depth: int) -> Node | None:
        qn = f"{module.module}.{'.'.join(rest)}"
        node = self.nodes.get(qn)
        if node is not None and not self.is_shadowed_fallback(node):
            return node
        if len(rest) >= 2:
            cls = self.nodes.get(f"{module.module}.{rest[0]}")
            if isinstance(cls, ClassNode) and len(rest) == 2:
                method, _ = self.find_method(cls.qualified_name, rest[1])
                if method is not None:
                    return method
        if depth >= _MAX_REEXPORT_DEPTH:
            return None
        binding = self.binding(module, rest[0])
        if binding is not None:
            return self.resolve_dotted(".".join([binding.target, *rest[1:]]), depth + 1)
        for star in self.star_imports(module):
            found = self.resolve_dotted(".".join([star.target, *rest]), depth + 1)
            if found is not None:
                return found
        return None

    def binding(self, module: FileNode, local_name: str) -> ImportRef | None:
        for ref in module.imports:
            if ref.local_name == local_name:
                return ref
        return None

    def star_imports(self, module: FileNode) -> list[ImportRef]:
        return [r for r in module.imports if r.is_star]

    def find_method(self, class_qn: str, name: str) -> tuple[FunctionNode | None, bool]:
        """Look a method up on a class, then its repo base classes.

        Returns the method and whether it was inherited.
        """
        seen: set[str] = set()
        queue: list[tuple[str, int]] = [(class_qn, 0)]
        while queue:
            qn, depth = queue.pop(0)
            if qn in seen or depth > _MAX_BASE_DEPTH:
                continue
            seen.add(qn)
            method = self.class_methods.get(qn, {}).get(name)
            if method is not None:
                return method, depth > 0
            cls = self.nodes.get(qn)
            if isinstance(cls, ClassNode):
                for base in cls.bases:
                    target = self.resolve_dotted(base)
                    if isinstance(target, ClassNode):
                        queue.append((target.qualified_name, depth + 1))
        return None, False

    def base_classes(self, class_qn: str) -> list[ClassNode]:
        cls = self.nodes.get(class_qn)
        if not isinstance(cls, ClassNode):
            return []
        out: list[ClassNode] = []
        for base in cls.bases:
            target = self.resolve_dotted(base)
            if isinstance(target, ClassNode):
                out.append(target)
        return out
