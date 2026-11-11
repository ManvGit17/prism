"""Collect context items for a hunk from the graph and from vector search.

Graph items for each changed symbol S:

* signature: S's own code at the head (cut to the snippet line limit);
* class: for a method, its class header and member signatures;
* contract: supertype methods S overrides or implements;
* test: tests that call S directly;
* caller / callee: at 1 hop, or 2 with `hops=2`, at or above the confidence threshold;
  callers of the contract method count as callers of S (calls by dynamic dispatch);
* implementation: methods that override or implement S;
* class_test: tests of S's class, only when S has no direct test.

A long changed symbol is shown as its first lines plus the lines around the
hunk; a class as its header plus the member signatures nearest the change.

For a symbol the hunk deletes or renames, its callers and tests come from the
base graph. Vector items are the functions most similar to the hunk, outside
the changed files and not already found through the graph.
"""

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from prism.indexer.embedder import Embedder
from prism.indexer.model import ClassNode, FileNode, FunctionNode, Node
from prism.schemas import ContextItem, ContextKind
from prism.store.base import GraphReader, Related, VectorReader

_LANGUAGE_OF = {".py": "python", ".pyi": "python", ".java": "java"}
_CLASS_MEMBERS = 12  # member signatures shown in a class summary
_HEAD_LINES = 6  # first lines of a long changed symbol kept before the hunk window


class SnippetReader:
    """Reads code from the working tree at the PR head."""

    def __init__(self, root: Path, max_lines: int) -> None:
        self.root = root
        self.max_lines = max_lines
        self._files: dict[str, list[str]] = {}

    def lines(self, path: str) -> list[str]:
        if path not in self._files:
            full = self.root / path
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            self._files[path] = text.splitlines()
        return self._files[path]

    def code(self, node: Node) -> str:
        lines = self.lines(node.path)[node.start_line - 1 : node.end_line]
        if len(lines) > self.max_lines:
            hidden = len(lines) - (self.max_lines - 1)
            lines = [*lines[: self.max_lines - 1], f"    ... ({hidden} more lines)"]
        return "\n".join(lines)

    def focused(self, node: Node, focus: tuple[int, int]) -> str:
        """A long symbol cut to its first lines and a window around `focus` (new-file lines)."""
        if node.end_line - node.start_line + 1 <= self.max_lines:
            return self.code(node)
        source = self.lines(node.path)
        head_end = node.start_line + _HEAD_LINES - 1
        room = self.max_lines - _HEAD_LINES - 2
        lo = max(head_end + 1, focus[0] - room // 3)
        hi = min(node.end_line, lo + room - 1)
        out = source[node.start_line - 1 : head_end]
        if lo > head_end + 1:
            out.append(f"    ... ({lo - head_end - 1} lines)")
        out.extend(source[lo - 1 : hi])
        if hi < node.end_line:
            out.append(f"    ... ({node.end_line - hi} more lines)")
        return "\n".join(out)

    def class_summary(self, cls: ClassNode, members: Iterable[Node], near: int = 0) -> str:
        """The class's first line and up to `_CLASS_MEMBERS` method signatures, those
        nearest line `near` first, shown in file order."""
        source = self.lines(cls.path)
        header = ""
        for line in source[cls.start_line - 1 : cls.end_line]:
            if cls.name in line and not line.lstrip().startswith(("@", "#", "//", "*", "/*")):
                header = line.rstrip()
                break
        indent = " " * (len(header) - len(header.lstrip()) + 4)
        methods = [
            m for m in members if isinstance(m, FunctionNode) and m.owner == cls.qualified_name
        ]
        nearest = sorted(methods, key=lambda m: (abs(m.start_line - near), m.start_line))
        shown = sorted(nearest[:_CLASS_MEMBERS], key=lambda m: m.start_line)
        sigs = [f"{indent}{m.signature}" for m in shown]
        if len(methods) > len(shown):
            sigs.append(f"{indent}... ({len(methods) - len(shown)} more methods)")
        return "\n".join([header or f"class {cls.name}", *sigs])


def short(qn: str) -> str:
    """`shop.cart.Cart.add` -> `Cart.add`; parameter lists stay."""
    head, paren, params = qn.partition("(")
    parts = head.split(".")
    return ".".join(parts[-2:]) + paren + params


@dataclass
class Collector:
    repo_id: str
    head: GraphReader  # the PR overlay
    base: GraphReader
    snippets: SnippetReader
    hops: int = 1

    def item(
        self,
        node: Node,
        kind: ContextKind,
        source: str,
        reason: str,
        confidence: float = 1.0,
        snippet: str | None = None,
    ) -> ContextItem:
        return ContextItem(
            kind=kind,
            source=source,
            qualified_name=node.qualified_name,
            path=node.path,
            start_line=node.start_line,
            end_line=max(node.end_line, node.start_line),
            reason=reason,
            snippet=self.snippets.code(node) if snippet is None else snippet,
            confidence=max(0.0, min(1.0, confidence)),
        )

    def at_head(self, node: Node) -> Node | None:
        """The head version of a node, so snippets match the working tree."""
        return self.head.get_node(self.repo_id, node.qualified_name)

    def graph_items(
        self, symbols: Sequence[Node], deleted: Sequence[Node], focus: tuple[int, int] = (0, 0)
    ) -> list[ContextItem]:
        items: list[ContextItem] = []
        rid = self.repo_id
        for sym in symbols:
            name = short(sym.qualified_name)
            owner = self.head.get_node(rid, getattr(sym, "owner", ""))
            if isinstance(sym, ClassNode):
                members = self.head.nodes_in_file(rid, sym.path)
                items.append(
                    self.item(
                        sym,
                        ContextKind.SIGNATURE,
                        "graph",
                        f"changed class {name}",
                        snippet=self.snippets.class_summary(sym, members, focus[0]),
                    )
                )
            else:
                items.append(
                    self.item(
                        sym,
                        ContextKind.SIGNATURE,
                        "graph",
                        f"changed {name}",
                        snippet=self.snippets.focused(sym, focus),
                    )
                )
                if isinstance(owner, ClassNode):
                    members = self.head.nodes_in_file(rid, owner.path)
                    items.append(
                        self.item(
                            owner,
                            ContextKind.CLASS,
                            "graph",
                            f"class of {name}",
                            snippet=self.snippets.class_summary(owner, members, sym.start_line),
                        )
                    )
            contracts = [
                node
                for n in self.head.overrides(rid, sym.qualified_name)
                if (node := self.at_head(n.node)) is not None
            ]
            for node in contracts:
                items.append(
                    self.item(
                        node, ContextKind.CONTRACT, "graph", f"{name} overrides or implements this"
                    )
                )
            direct_tests = self.related(
                self.head.tests_for(rid, sym.qualified_name), ContextKind.TEST, f"tests {name}"
            )
            items.extend(direct_tests)
            if not direct_tests and isinstance(owner, ClassNode):
                items.extend(
                    self.related(
                        self.head.tests_for(rid, owner.qualified_name),
                        ContextKind.CLASS_TEST,
                        f"tests class {short(owner.qualified_name)}",
                    )
                )
            items.extend(self.neighbours(sym.qualified_name, name, callers=True))
            for node in contracts:
                items.extend(
                    self.related(
                        self.head.callers(rid, node.qualified_name),
                        ContextKind.CALLER,
                        f"calls {short(node.qualified_name)}, which {name} implements "
                        "(dynamic dispatch)",
                    )
                )
            for n in self.head.overridden_by(rid, sym.qualified_name):
                node = self.at_head(n.node)
                if node is not None:
                    items.append(
                        self.item(
                            node,
                            ContextKind.IMPLEMENTATION,
                            "graph",
                            f"overrides or implements {name}",
                        )
                    )
            items.extend(self.neighbours(sym.qualified_name, name, callers=False))
        for gone in deleted:
            name = short(gone.qualified_name)
            items.extend(
                self.related(
                    self.base.callers(rid, gone.qualified_name),
                    ContextKind.CALLER,
                    f"calls {name}, which this change removes or renames",
                    head=True,
                )
            )
            items.extend(
                self.related(
                    self.base.tests_for(rid, gone.qualified_name),
                    ContextKind.TEST,
                    f"tests {name}, which this change removes or renames",
                    head=True,
                )
            )
        return items

    def related(
        self, rows: Iterable[Related], kind: ContextKind, reason: str, *, head: bool = False
    ) -> list[ContextItem]:
        out: list[ContextItem] = []
        for r in rows:
            node: Node | None = self.at_head(r.node) if head else r.node
            if node is None:
                continue
            band = r.edge.resolution.value if r.edge.resolution else "edge"
            why = f"{reason} ({band}, {r.edge.confidence:.2f})"
            if isinstance(node, FileNode):
                out.append(self.module_item(node, r.edge.line or 1, kind, why, r.edge.confidence))
            else:
                out.append(self.item(node, kind, "graph", why, r.edge.confidence))
        return out

    def module_item(
        self, file: FileNode, line: int, kind: ContextKind, reason: str, confidence: float
    ) -> ContextItem:
        """Module-level code: a few lines around the call instead of the whole file."""
        lines = self.snippets.lines(file.path)
        start = max(1, line - 3)
        end = max(start, min(len(lines), line + 3))
        return ContextItem(
            kind=kind,
            source="graph",
            qualified_name=f"{file.qualified_name}:{line}",
            path=file.path,
            start_line=start,
            end_line=end,
            reason=reason,
            snippet="\n".join(lines[start - 1 : end]),
            confidence=confidence,
        )

    def neighbours(self, qn: str, name: str, *, callers: bool) -> list[ContextItem]:
        rid = self.repo_id
        kind = ContextKind.CALLER if callers else ContextKind.CALLEE
        verb = "calls" if callers else "called by"
        query = self.head.callers if callers else self.head.callees
        first = list(query(rid, qn))
        out = self.related(first, kind, f"{verb} {name}")
        if self.hops >= 2:
            for r in first:
                via = short(r.node.qualified_name)
                for r2 in query(rid, r.node.qualified_name):
                    if r2.node.qualified_name == qn:
                        continue
                    conf = r.edge.confidence * r2.edge.confidence
                    out.append(
                        self.item(
                            r2.node,
                            kind,
                            "graph",
                            f"{verb} {via}, which {verb} {name} (2 hops, {conf:.2f})",
                            conf,
                        )
                    )
        return out

    def vector_items(
        self,
        query_vector: Sequence[float],
        vectors: VectorReader,
        k: int,
        exclude_paths: Collection[str],
        exclude_names: Collection[str],
    ) -> list[ContextItem]:
        if k <= 0:
            return []
        hits = vectors.search(
            self.repo_id, query_vector, k=k + len(exclude_names) + 5, exclude_paths=exclude_paths
        )
        out: list[ContextItem] = []
        for hit in hits:
            if hit.qualified_name in exclude_names:
                continue
            node = self.head.get_node(self.repo_id, hit.qualified_name)
            if node is None:
                continue
            out.append(
                self.item(
                    node,
                    ContextKind.SIMILAR,
                    "vector",
                    f"similar code (cosine {hit.score:.2f})",
                    hit.score,
                )
            )
            if len(out) == k:
                break
        return out


def embed_query(embedder: Embedder, text: str) -> tuple[float, ...]:
    return embedder.embed([text])[0]


def dedupe(items: Iterable[ContextItem], exclude: Collection[str]) -> list[ContextItem]:
    """One item per symbol, keeping the highest-priority kind; drop excluded names."""
    best: dict[str, ContextItem] = {}
    for item in items:
        key = item.qualified_name
        if key in exclude and item.kind is not ContextKind.SIGNATURE:
            continue
        old = best.get(key)
        if old is None or item.kind.priority < old.kind.priority:
            best[key] = item
    return list(best.values())


def language_of(path: str) -> str:
    return _LANGUAGE_OF.get(Path(path).suffix.lower(), "")
