"""In-memory graph and vector stores.

Used by tests, by the per-PR overlay, and by the GitHub Action mode, which runs
without Neo4j or pgvector. Not thread-safe for concurrent writes; reads of a
store nobody is writing are safe.
"""

import math
from collections import defaultdict
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass, field

from prism.indexer.model import Edge, EdgeKind, FileGraph, FileNode, Node
from prism.store.base import (
    GraphStore,
    VectorHit,
    VectorItem,
    check_file_graph,
)

_EdgeId = tuple[EdgeKind, str, str]


@dataclass
class _RepoGraph:
    nodes: dict[str, Node] = field(default_factory=dict)
    files: dict[str, set[str]] = field(default_factory=dict)
    by_name: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    owned: dict[str, dict[_EdgeId, Edge]] = field(default_factory=lambda: defaultdict(dict))
    out: dict[str, dict[_EdgeId, Edge]] = field(default_factory=lambda: defaultdict(dict))
    inn: dict[str, dict[_EdgeId, Edge]] = field(default_factory=lambda: defaultdict(dict))

    def drop_edge(self, edge: Edge) -> None:
        self.owned[edge.path].pop(edge.ident, None)
        self.out[edge.src].pop(edge.ident, None)
        self.inn[edge.dst].pop(edge.ident, None)

    def drop_node(self, qn: str) -> None:
        for edge in [*self.out.pop(qn, {}).values(), *self.inn.pop(qn, {}).values()]:
            self.drop_edge(edge)
        node = self.nodes.pop(qn, None)
        if node is not None:
            self.by_name[node.name].discard(qn)
            self.files.get(node.path, set()).discard(qn)

    def clear_file(self, path: str, keep: set[str]) -> None:
        for edge in list(self.owned.pop(path, {}).values()):
            self.drop_edge(edge)
        for qn in list(self.files.get(path, set())):
            if qn not in keep:
                self.drop_node(qn)

    def put_node(self, node: Node) -> None:
        qn = node.qualified_name
        old = self.nodes.get(qn)
        if old is not None and old.path != node.path:
            self.files.get(old.path, set()).discard(qn)
        if old is not None:
            self.by_name[old.name].discard(qn)
        self.nodes[qn] = node
        self.files.setdefault(node.path, set()).add(qn)
        if not isinstance(node, FileNode):
            self.by_name[node.name].add(qn)

    def put_edge(self, edge: Edge) -> None:
        if edge.src not in self.nodes or edge.dst not in self.nodes:
            return
        self.owned[edge.path][edge.ident] = edge
        self.out[edge.src][edge.ident] = edge
        self.inn[edge.dst][edge.ident] = edge


class InMemoryGraphStore(GraphStore):
    def __init__(self) -> None:
        self._repos: dict[str, _RepoGraph] = {}

    def _repo(self, repo_id: str) -> _RepoGraph | None:
        return self._repos.get(repo_id)

    # Writes

    def replace_files(self, repo_id: str, graphs: Iterable[FileGraph]) -> None:
        batch = list(graphs)
        for graph in batch:
            check_file_graph(repo_id, graph)
        repo = self._repos.setdefault(repo_id, _RepoGraph())
        for graph in batch:
            repo.clear_file(graph.path, {n.qualified_name for n in graph.nodes})
        for graph in batch:
            repo.files.setdefault(graph.path, set())
            for node in graph.nodes:
                repo.put_node(node)
        for graph in batch:
            for edge in graph.edges:
                repo.put_edge(edge)

    def remove_files(self, repo_id: str, paths: Iterable[str]) -> None:
        repo = self._repo(repo_id)
        if repo is None:
            return
        for path in paths:
            repo.clear_file(path, set())
            repo.files.pop(path, None)

    def delete_repo(self, repo_id: str) -> None:
        self._repos.pop(repo_id, None)

    def repo_ids(self) -> list[str]:
        return sorted(r for r, g in self._repos.items() if g.nodes)

    # Primitives

    def get_node(self, repo_id: str, qualified_name: str) -> Node | None:
        repo = self._repo(repo_id)
        return None if repo is None else repo.nodes.get(qualified_name)

    def nodes_in_file(self, repo_id: str, path: str) -> list[Node]:
        repo = self._repo(repo_id)
        if repo is None:
            return []
        return sorted(
            (repo.nodes[qn] for qn in repo.files.get(path, set())),
            key=lambda n: (n.start_line, n.qualified_name),
        )

    def find_by_name(self, repo_id: str, name: str) -> list[Node]:
        repo = self._repo(repo_id)
        if repo is None:
            return []
        return sorted((repo.nodes[qn] for qn in repo.by_name.get(name, set())), key=_qn)

    def out_edges(
        self, repo_id: str, qualified_name: str, kinds: Collection[EdgeKind] | None = None
    ) -> list[Edge]:
        repo = self._repo(repo_id)
        if repo is None:
            return []
        return _select(repo, repo.out.get(qualified_name, {}).values(), kinds)

    def in_edges(
        self, repo_id: str, qualified_name: str, kinds: Collection[EdgeKind] | None = None
    ) -> list[Edge]:
        repo = self._repo(repo_id)
        if repo is None:
            return []
        return _select(repo, repo.inn.get(qualified_name, {}).values(), kinds)

    def iter_nodes(self, repo_id: str) -> Iterator[Node]:
        repo = self._repo(repo_id)
        if repo is not None:
            yield from list(repo.nodes.values())

    def iter_edges(self, repo_id: str) -> Iterator[Edge]:
        repo = self._repo(repo_id)
        if repo is not None:
            for edges in list(repo.owned.values()):
                yield from list(edges.values())

    def file_paths(self, repo_id: str) -> list[str]:
        repo = self._repo(repo_id)
        return [] if repo is None else sorted(repo.files)


def _qn(node: Node) -> str:
    return node.qualified_name


def _select(
    repo: _RepoGraph, edges: Iterable[Edge], kinds: Collection[EdgeKind] | None
) -> list[Edge]:
    return [
        e
        for e in edges
        if (kinds is None or e.kind in kinds) and e.src in repo.nodes and e.dst in repo.nodes
    ]


class InMemoryVectorStore:
    """Brute-force cosine search. Fine for one repo's functions in tests and Action mode."""

    def __init__(self) -> None:
        self._repos: dict[str, dict[str, VectorItem]] = {}
        self._dims: dict[str, int] = {}

    def upsert(self, repo_id: str, items: Iterable[VectorItem]) -> None:
        batch = list(items)
        repo = self._repos.setdefault(repo_id, {})
        for item in batch:
            dim = self._dims.setdefault(repo_id, len(item.vector))
            if len(item.vector) != dim:
                raise ValueError(f"vector has {len(item.vector)} dimensions, repo uses {dim}")
        for item in batch:
            repo[item.qualified_name] = item

    def remove_files(self, repo_id: str, paths: Iterable[str]) -> None:
        drop = set(paths)
        repo = self._repos.get(repo_id, {})
        for qn in [qn for qn, item in repo.items() if item.path in drop]:
            del repo[qn]

    def delete_repo(self, repo_id: str) -> None:
        self._repos.pop(repo_id, None)
        self._dims.pop(repo_id, None)

    def get(self, repo_id: str, qualified_name: str) -> VectorItem | None:
        return self._repos.get(repo_id, {}).get(qualified_name)

    def count(self, repo_id: str) -> int:
        return len(self._repos.get(repo_id, {}))

    def repo_ids(self) -> list[str]:
        return sorted(r for r, items in self._repos.items() if items)

    def close(self) -> None:
        pass

    def search(
        self,
        repo_id: str,
        vector: Sequence[float],
        *,
        k: int = 10,
        exclude_paths: Collection[str] = (),
    ) -> list[VectorHit]:
        if k < 1:
            return []
        excluded = set(exclude_paths)
        hits = [
            VectorHit(
                qualified_name=item.qualified_name,
                path=item.path,
                score=cosine(vector, item.vector),
            )
            for item in self._repos.get(repo_id, {}).values()
            if item.path not in excluded
        ]
        hits.sort(key=lambda h: (-h.score, h.qualified_name))
        return hits[:k]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"vectors differ in length: {len(a)} and {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
