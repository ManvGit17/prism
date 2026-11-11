"""Per-PR overlay: the base graph with a PR's changed files swapped in.

The overlay holds the changed files' nodes and edges in memory and reads
everything else from the base store. It never writes to the base, so any number
of overlays can sit on one base at the same time without seeing each other.

A base file the PR changed or deleted is "shadowed": its nodes and the edges
that start in it are hidden. Base edges from unchanged files into a changed
file stay while their target still exists in the overlay. Calls in unchanged
files are not re-resolved; a name-matched edge that would now point somewhere
else keeps its old target (ADR 0004).
"""

from collections import defaultdict
from collections.abc import Collection, Iterable, Iterator, Sequence

from prism.indexer.model import Edge, EdgeKind, FileGraph, FileNode, Node
from prism.store.base import GraphReader, VectorHit, VectorItem, VectorReader, check_file_graph
from prism.store.memory import InMemoryVectorStore

_EdgeId = tuple[EdgeKind, str, str]


class OverlayGraph(GraphReader):
    def __init__(
        self,
        base: GraphReader,
        repo_id: str,
        graphs: Iterable[FileGraph],
        removed_paths: Iterable[str] = (),
    ) -> None:
        self._base = base
        self.repo_id = repo_id
        batch = list(graphs)
        for graph in batch:
            check_file_graph(repo_id, graph)
        self.changed_paths = frozenset(g.path for g in batch)
        self.removed_paths = frozenset(removed_paths) - self.changed_paths
        self._shadowed = self.changed_paths | self.removed_paths
        self._nodes: dict[str, Node] = {}
        self._files: dict[str, list[Node]] = {g.path: [] for g in batch}
        self._by_name: dict[str, list[Node]] = defaultdict(list)
        self._out: dict[str, dict[_EdgeId, Edge]] = defaultdict(dict)
        self._in: dict[str, dict[_EdgeId, Edge]] = defaultdict(dict)
        for graph in batch:
            for node in graph.nodes:
                self._nodes[node.qualified_name] = node
                self._files[graph.path].append(node)
                if not isinstance(node, FileNode):
                    self._by_name[node.name].append(node)
        for graph in batch:
            for edge in graph.edges:
                self._out[edge.src][edge.ident] = edge
                self._in[edge.dst][edge.ident] = edge

    def _mine(self, repo_id: str) -> bool:
        return repo_id == self.repo_id

    def _visible(self, node: Node | None) -> Node | None:
        if node is None or node.path in self._shadowed:
            return None
        return node

    def _live(
        self, repo_id: str, edges: Iterable[Edge], kinds: Collection[EdgeKind] | None
    ) -> list[Edge]:
        return [
            e
            for e in edges
            if (kinds is None or e.kind in kinds)
            and self.get_node(repo_id, e.src) is not None
            and self.get_node(repo_id, e.dst) is not None
        ]

    # Primitives

    def get_node(self, repo_id: str, qualified_name: str) -> Node | None:
        if not self._mine(repo_id):
            return self._base.get_node(repo_id, qualified_name)
        local = self._nodes.get(qualified_name)
        if local is not None:
            return local
        return self._visible(self._base.get_node(repo_id, qualified_name))

    def nodes_in_file(self, repo_id: str, path: str) -> list[Node]:
        if self._mine(repo_id) and path in self._shadowed:
            return sorted(self._files.get(path, []), key=lambda n: (n.start_line, n.qualified_name))
        return self._base.nodes_in_file(repo_id, path)

    def find_by_name(self, repo_id: str, name: str) -> list[Node]:
        if not self._mine(repo_id):
            return self._base.find_by_name(repo_id, name)
        base = [n for n in self._base.find_by_name(repo_id, name) if n.path not in self._shadowed]
        return sorted([*base, *self._by_name.get(name, [])], key=lambda n: n.qualified_name)

    def out_edges(
        self, repo_id: str, qualified_name: str, kinds: Collection[EdgeKind] | None = None
    ) -> list[Edge]:
        if not self._mine(repo_id):
            return self._base.out_edges(repo_id, qualified_name, kinds)
        if qualified_name in self._nodes:
            return self._live(repo_id, self._out.get(qualified_name, {}).values(), kinds)
        if self.get_node(repo_id, qualified_name) is None:
            return []
        return self._live(repo_id, self._base.out_edges(repo_id, qualified_name, kinds), kinds)

    def in_edges(
        self, repo_id: str, qualified_name: str, kinds: Collection[EdgeKind] | None = None
    ) -> list[Edge]:
        if not self._mine(repo_id):
            return self._base.in_edges(repo_id, qualified_name, kinds)
        if self.get_node(repo_id, qualified_name) is None:
            return []
        merged: dict[_EdgeId, Edge] = {
            e.ident: e
            for e in self._base.in_edges(repo_id, qualified_name, kinds)
            if e.path not in self._shadowed
        }
        merged.update(self._in.get(qualified_name, {}))
        return self._live(repo_id, merged.values(), kinds)

    def iter_nodes(self, repo_id: str) -> Iterator[Node]:
        if not self._mine(repo_id):
            yield from self._base.iter_nodes(repo_id)
            return
        for node in self._base.iter_nodes(repo_id):
            if node.path not in self._shadowed:
                yield node
        yield from list(self._nodes.values())

    def iter_edges(self, repo_id: str) -> Iterator[Edge]:
        if not self._mine(repo_id):
            yield from self._base.iter_edges(repo_id)
            return
        base = (e for e in self._base.iter_edges(repo_id) if e.path not in self._shadowed)
        yield from self._live(repo_id, base, None)
        yield from self._live(repo_id, (e for d in self._out.values() for e in d.values()), None)

    def file_paths(self, repo_id: str) -> list[str]:
        if not self._mine(repo_id):
            return self._base.file_paths(repo_id)
        paths = set(self._base.file_paths(repo_id)) - self.removed_paths
        return sorted(paths | self.changed_paths)


class OverlayVectors:
    """Vector search over a base store with a PR's changed files swapped in."""

    def __init__(
        self,
        base: VectorReader,
        repo_id: str,
        items: Iterable[VectorItem],
        shadowed_paths: Iterable[str],
    ) -> None:
        self._base = base
        self.repo_id = repo_id
        self._shadowed = frozenset(shadowed_paths)
        self._local = InMemoryVectorStore()
        self._local.upsert(repo_id, items)

    def get(self, repo_id: str, qualified_name: str) -> VectorItem | None:
        if repo_id == self.repo_id:
            local = self._local.get(repo_id, qualified_name)
            if local is not None:
                return local
            item = self._base.get(repo_id, qualified_name)
            return None if item is None or item.path in self._shadowed else item
        return self._base.get(repo_id, qualified_name)

    def search(
        self,
        repo_id: str,
        vector: Sequence[float],
        *,
        k: int = 10,
        exclude_paths: Collection[str] = (),
    ) -> list[VectorHit]:
        if repo_id != self.repo_id:
            return self._base.search(repo_id, vector, k=k, exclude_paths=exclude_paths)
        excluded = self._shadowed | frozenset(exclude_paths)
        hits = [
            *self._base.search(repo_id, vector, k=k, exclude_paths=excluded),
            *self._local.search(repo_id, vector, k=k, exclude_paths=exclude_paths),
        ]
        hits.sort(key=lambda h: (-h.score, h.qualified_name))
        return hits[:k]
