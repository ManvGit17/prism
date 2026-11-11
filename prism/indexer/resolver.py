"""Best-effort call resolution: same file, then imports, then name match.

Each CALLS edge records how it was resolved and a confidence from a fixed band
(ADR 0004). Name matches on very common names get the lowest band, which the
default retrieval threshold (`PRISM_CALL_CONFIDENCE_THRESHOLD`) leaves out.

TESTS edges go from a test function to each non-test function or class it
calls directly, with the confidence of the CALLS edge.
"""

import builtins
from collections.abc import Iterable, Sequence

from prism.indexer.model import (
    ClassNode,
    Edge,
    EdgeKind,
    FileGraph,
    FileNode,
    FunctionNode,
    ImportRef,
    Node,
    Resolution,
)
from prism.indexer.parsers.base import CallSite, ParsedFile, ReceiverKind
from prism.indexer.symbols import SymbolTable

CONFIDENCE: dict[Resolution, float] = {
    Resolution.SAME_FILE: 0.95,
    Resolution.SELF_METHOD: 0.9,
    Resolution.IMPORT: 0.9,
    Resolution.INHERITED: 0.8,
    Resolution.STAR_IMPORT: 0.7,
    Resolution.NAME_UNIQUE: 0.6,
    Resolution.NAME_AMBIGUOUS: 0.3,
    Resolution.NAME_COMMON: 0.1,
}

# Names that a name-only match gets wrong most of the time: builtin container
# and string methods, and verbs every codebase reuses.
_COMMON_WORDS = """
    add all any append apply build call check clear close commit compile connect copy count
    create debug decode delete dispatch emit encode error exception execute exists extend fetch
    fetchall fetchone filter find first flush format get get_or_create handle head index info init
    insert items iter join keys list load log main map next open parse patch pop post process put
    query read register remove render replace reset run save send set setup sort split start stop
    strip teardown test update validate values wait warning write
"""
COMMON_NAMES: frozenset[str] = frozenset(_COMMON_WORDS.split())

# A name with more candidates than this is not linked at all.
MAX_NAME_CANDIDATES = 10

_BUILTINS = frozenset(dir(builtins))


def resolve(parsed: Sequence[ParsedFile], symbols: SymbolTable) -> list[FileGraph]:
    """Build each parsed file's graph: its nodes, DEFINES, IMPORTS, CALLS and TESTS edges.

    `symbols` must hold every node the calls may point at, including the nodes
    of `parsed` itself.
    """
    return [_FileResolver(pf, symbols).run() for pf in parsed]


def is_common_name(name: str) -> bool:
    return name in COMMON_NAMES or (name.startswith("__") and name.endswith("__"))


class _FileResolver:
    def __init__(self, pf: ParsedFile, symbols: SymbolTable) -> None:
        self.pf = pf
        self.symbols = symbols
        self.bindings: dict[str, ImportRef] = {
            ref.local_name: ref for ref in pf.file.imports if not ref.is_star
        }
        self.stars = [ref for ref in pf.file.imports if ref.is_star]
        self.calls: dict[tuple[str, str], Edge] = {}

    def run(self) -> FileGraph:
        for site in self.pf.calls:
            for target, resolution in self.resolve_site(site):
                self.add_call(site, target, resolution)
        edges: list[Edge] = [*self.pf.defines, *self.import_edges(), *self.calls.values()]
        edges.extend(self.test_edges())
        return FileGraph(
            repo_id=self.pf.repo_id, path=self.pf.path, nodes=self.pf.nodes, edges=tuple(edges)
        )

    def add_call(self, site: CallSite, target: Node, resolution: Resolution) -> None:
        if isinstance(target, FileNode):
            return
        confidence = CONFIDENCE[resolution]
        key = (site.caller, target.qualified_name)
        old = self.calls.get(key)
        if old is not None and old.confidence >= confidence:
            return
        self.calls[key] = Edge(
            kind=EdgeKind.CALLS,
            repo_id=self.pf.repo_id,
            src=site.caller,
            dst=target.qualified_name,
            path=self.pf.path,
            line=old.line if old is not None else site.line,
            confidence=confidence,
            resolution=resolution,
        )

    # Resolution

    def resolve_site(self, site: CallSite) -> list[tuple[Node, Resolution]]:
        kind = site.receiver_kind
        if kind is ReceiverKind.NONE:
            return self.bare(site)
        if kind is ReceiverKind.SELF:
            return self.on_self(site)
        if kind is ReceiverKind.SUPER:
            return self.on_super(site)
        if kind is ReceiverKind.NAME and site.receiver is not None:
            return self.on_name(site, site.receiver)
        return self.name_match(site.name, self.symbols.methods.get(site.name, []))

    def bare(self, site: CallSite) -> list[tuple[Node, Resolution]]:
        local = self.scope_lookup(site.caller, site.name)
        if local is not None:
            return [(local, Resolution.SAME_FILE)]
        ref = self.bindings.get(site.name)
        if ref is not None:
            target = self.symbols.resolve_dotted(ref.target)
            # Imported from outside the repo, or a module object: no edge.
            return [] if target is None else [(target, Resolution.IMPORT)]
        if site.name in _BUILTINS:
            return []
        for star in self.stars:
            target = self.symbols.resolve_dotted(f"{star.target}.{site.name}")
            if target is not None:
                return [(target, Resolution.STAR_IMPORT)]
        return self.name_match(site.name, self.symbols.top_level.get(site.name, []))

    def on_self(self, site: CallSite) -> list[tuple[Node, Resolution]]:
        if site.enclosing_class is not None:
            method, inherited = self.symbols.find_method(site.enclosing_class, site.name)
            if method is not None:
                return [(method, Resolution.INHERITED if inherited else Resolution.SELF_METHOD)]
        return self.name_match(site.name, self.symbols.methods.get(site.name, []))

    def on_super(self, site: CallSite) -> list[tuple[Node, Resolution]]:
        if site.enclosing_class is None:
            return []
        for base in self.symbols.base_classes(site.enclosing_class):
            method, _ = self.symbols.find_method(base.qualified_name, site.name)
            if method is not None:
                return [(method, Resolution.INHERITED)]
        return []

    def on_name(self, site: CallSite, receiver: str) -> list[tuple[Node, Resolution]]:
        head, _, rest = receiver.partition(".")
        tail = f"{rest}.{site.name}" if rest else site.name
        ref = self.bindings.get(head)
        if ref is not None:
            target = self.symbols.resolve_dotted(f"{ref.target}.{tail}")
            if target is not None and not isinstance(target, FileNode):
                return [(target, Resolution.IMPORT)]
            if self.symbols.resolve_dotted(ref.target) is None:
                return []  # receiver comes from outside the repo, e.g. `json.loads`
            return self.name_match(site.name, self.symbols.methods.get(site.name, []))
        local = self.scope_lookup(site.caller, head)
        if isinstance(local, ClassNode | FunctionNode):
            target = self.symbols.resolve_dotted(f"{local.qualified_name}.{tail}")
            if target is not None and not isinstance(target, FileNode):
                return [(target, Resolution.SAME_FILE)]
        elif head in _BUILTINS:
            return []  # str.join, dict.fromkeys
        return self.name_match(site.name, self.symbols.methods.get(site.name, []))

    def name_match(self, name: str, candidates: Iterable[Node]) -> list[tuple[Node, Resolution]]:
        found = list(candidates)
        if not found or len(found) > MAX_NAME_CANDIDATES:
            return []
        if is_common_name(name):
            band = Resolution.NAME_COMMON
        elif len(found) == 1:
            band = Resolution.NAME_UNIQUE
        else:
            band = Resolution.NAME_AMBIGUOUS
        return [(node, band) for node in found]

    def scope_lookup(self, caller: str, name: str) -> Node | None:
        """Find `name` in the caller's enclosing function scopes, then the module.

        Class bodies are not enclosing scopes for the functions inside them.
        """
        node = self.symbols.nodes.get(caller)
        while node is not None:
            if isinstance(node, FileNode):
                hit = self.symbols.nodes.get(f"{node.module}.{name}")
                return None if isinstance(hit, FileNode) else hit
            if isinstance(node, FunctionNode) or node.qualified_name == caller:
                hit = self.symbols.nodes.get(f"{node.qualified_name}.{name}")
                if hit is not None:
                    return hit
            node = self.symbols.nodes.get(node.owner)
        return None

    # Other edges

    def import_edges(self) -> list[Edge]:
        seen: dict[str, Edge] = {}
        for ref in self.pf.file.imports:
            target = self.symbols.resolve_module(ref.target) or self.symbols.resolve_module(
                ref.module
            )
            if target is None or target.path == self.pf.path or target.path in seen:
                continue
            seen[target.path] = Edge(
                kind=EdgeKind.IMPORTS,
                repo_id=self.pf.repo_id,
                src=self.pf.file.qualified_name,
                dst=target.qualified_name,
                path=self.pf.path,
                line=ref.line,
            )
        return list(seen.values())

    def test_edges(self) -> list[Edge]:
        out: list[Edge] = []
        for edge in self.calls.values():
            src = self.symbols.nodes.get(edge.src)
            dst = self.symbols.nodes.get(edge.dst)
            if not (isinstance(src, FunctionNode) and src.is_test):
                continue
            if dst is None or isinstance(dst, FileNode) or dst.is_test:
                continue
            out.append(edge.model_copy(update={"kind": EdgeKind.TESTS}))
        return out
