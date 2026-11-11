"""Store interfaces for the code graph and for function embeddings.

`GraphReader` has a small set of abstract primitives and builds the query
methods on top of them. The in-memory store and the per-PR overlay use those
default queries; a Neo4j store implements the primitives with Cypher and may
override any query with a single Cypher statement. Either way the contract
tests in `tests/test_graph_store_contract.py` must pass unchanged.

Rules every implementation keeps:

* every call takes `repo_id` and never returns another repo's nodes or edges;
* an edge is returned only while both its ends exist;
* CALLS and TESTS edges below the confidence threshold are left out of query
  results unless the caller lowers `min_confidence`; the default threshold is
  `Settings.call_confidence_threshold`.
"""

from abc import ABC, abstractmethod
from collections import Counter, deque
from collections.abc import Collection, Iterable, Iterator, Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from prism.config import get_settings
from prism.indexer.model import (
    AnyNode,
    ClassNode,
    Edge,
    EdgeKind,
    FileGraph,
    FileNode,
    FunctionNode,
    Node,
)

_SCORED = (EdgeKind.CALLS, EdgeKind.TESTS)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Related(_Frozen):
    """A node reached over one edge: a caller, a callee or a test."""

    node: AnyNode
    edge: Edge


class Neighbour(_Frozen):
    node: AnyNode
    distance: int = Field(ge=1)


class GraphStats(_Frozen):
    repo_id: str
    files: int
    test_files: int
    classes: int
    functions: int
    methods: int
    test_functions: int
    edges: dict[str, int]
    calls_by_resolution: dict[str, int]
    calls_retrievable: int = Field(description="CALLS edges at or above the threshold.")
    threshold: float


def default_threshold(min_confidence: float | None) -> float:
    if min_confidence is not None:
        return min_confidence
    return get_settings().call_confidence_threshold


def passes(edge: Edge, threshold: float) -> bool:
    return edge.kind not in _SCORED or edge.confidence >= threshold


class GraphReader(ABC):
    # Primitives. Implementations return only live edges: both ends exist.

    @abstractmethod
    def get_node(self, repo_id: str, qualified_name: str) -> Node | None: ...

    @abstractmethod
    def nodes_in_file(self, repo_id: str, path: str) -> list[Node]:
        """All nodes of one file, the file node included. Empty if the file is unknown."""

    @abstractmethod
    def find_by_name(self, repo_id: str, name: str) -> list[Node]:
        """Classes and functions whose short name is `name`."""

    @abstractmethod
    def out_edges(
        self, repo_id: str, qualified_name: str, kinds: Collection[EdgeKind] | None = None
    ) -> list[Edge]: ...

    @abstractmethod
    def in_edges(
        self, repo_id: str, qualified_name: str, kinds: Collection[EdgeKind] | None = None
    ) -> list[Edge]: ...

    @abstractmethod
    def iter_nodes(self, repo_id: str) -> Iterator[Node]: ...

    @abstractmethod
    def iter_edges(self, repo_id: str) -> Iterator[Edge]: ...

    @abstractmethod
    def file_paths(self, repo_id: str) -> list[str]: ...

    # Queries

    def lookup(self, repo_id: str, symbol: str) -> list[Node]:
        """Find a symbol by qualified name, then by short name, then by dotted suffix.

        `Cart.add` matches `shop.cart.Cart.add`. Results are sorted by qualified name.
        """
        exact = self.get_node(repo_id, symbol)
        if exact is not None:
            return [exact]
        last = symbol.rsplit(".", 1)[-1]
        named = [n for n in self.find_by_name(repo_id, last) if n.repo_id == repo_id]
        if "." in symbol:
            suffix = "." + symbol
            named = [n for n in named if n.qualified_name.endswith(suffix)]
        return sorted(named, key=lambda n: n.qualified_name)

    def callers(
        self, repo_id: str, qualified_name: str, *, min_confidence: float | None = None
    ) -> list[Related]:
        return self._related(repo_id, qualified_name, EdgeKind.CALLS, False, min_confidence)

    def callees(
        self, repo_id: str, qualified_name: str, *, min_confidence: float | None = None
    ) -> list[Related]:
        return self._related(repo_id, qualified_name, EdgeKind.CALLS, True, min_confidence)

    def tests_for(
        self, repo_id: str, qualified_name: str, *, min_confidence: float | None = None
    ) -> list[Related]:
        """Test functions that call the symbol directly."""
        return self._related(repo_id, qualified_name, EdgeKind.TESTS, False, min_confidence)

    def _related(
        self,
        repo_id: str,
        qualified_name: str,
        kind: EdgeKind,
        outgoing: bool,
        min_confidence: float | None,
    ) -> list[Related]:
        threshold = default_threshold(min_confidence)
        edges = (
            self.out_edges(repo_id, qualified_name, (kind,))
            if outgoing
            else self.in_edges(repo_id, qualified_name, (kind,))
        )
        out: list[Related] = []
        for edge in edges:
            if edge.repo_id != repo_id or not passes(edge, threshold):
                continue
            node = self.get_node(repo_id, edge.dst if outgoing else edge.src)
            if node is not None and node.repo_id == repo_id:
                out.append(Related(node=node, edge=edge))
        return sorted(out, key=lambda r: (-r.edge.confidence, r.node.qualified_name))

    def neighbours(
        self,
        repo_id: str,
        qualified_name: str,
        *,
        hops: int = 1,
        kinds: Collection[EdgeKind] | None = None,
        min_confidence: float | None = None,
    ) -> list[Neighbour]:
        """Nodes within `hops` edges of the symbol, following edges both ways."""
        if hops < 1 or self.get_node(repo_id, qualified_name) is None:
            return []
        threshold = default_threshold(min_confidence)
        seen = {qualified_name}
        found: list[Neighbour] = []
        frontier: deque[tuple[str, int]] = deque([(qualified_name, 0)])
        while frontier:
            current, depth = frontier.popleft()
            if depth == hops:
                continue
            edges = [
                *self.out_edges(repo_id, current, kinds),
                *self.in_edges(repo_id, current, kinds),
            ]
            for edge in edges:
                if edge.repo_id != repo_id or not passes(edge, threshold):
                    continue
                other = edge.dst if edge.src == current else edge.src
                if other in seen:
                    continue
                node = self.get_node(repo_id, other)
                if node is None or node.repo_id != repo_id:
                    continue
                seen.add(other)
                found.append(Neighbour(node=node, distance=depth + 1))
                frontier.append((other, depth + 1))
        return sorted(found, key=lambda n: (n.distance, n.node.qualified_name))

    def enclosing_symbol(self, repo_id: str, path: str, line: int) -> Node | None:
        """The innermost function or class around a line, else the file node.

        None if the file is unknown or the line is outside it.
        """
        nodes = [n for n in self.nodes_in_file(repo_id, path) if n.repo_id == repo_id]
        file_node = next((n for n in nodes if isinstance(n, FileNode)), None)
        if file_node is None or not file_node.contains_line(line):
            return None
        around = [
            n for n in nodes if isinstance(n, ClassNode | FunctionNode) and n.contains_line(line)
        ]
        if not around:
            return file_node
        return min(around, key=lambda n: (n.end_line - n.start_line, -n.start_line))

    def stats(self, repo_id: str, *, min_confidence: float | None = None) -> GraphStats:
        threshold = default_threshold(min_confidence)
        kinds: Counter[str] = Counter()
        files = test_files = classes = functions = methods = tests = 0
        for node in self.iter_nodes(repo_id):
            if isinstance(node, FileNode):
                files += 1
                test_files += node.is_test
            elif isinstance(node, ClassNode):
                classes += 1
            else:
                functions += 1
                methods += node.is_method
                tests += node.is_test
        by_resolution: Counter[str] = Counter()
        retrievable = 0
        for edge in self.iter_edges(repo_id):
            kinds[edge.kind.value] += 1
            if edge.kind is EdgeKind.CALLS:
                by_resolution[edge.resolution.value if edge.resolution else "unknown"] += 1
                retrievable += edge.confidence >= threshold
        return GraphStats(
            repo_id=repo_id,
            files=files,
            test_files=test_files,
            classes=classes,
            functions=functions,
            methods=methods,
            test_functions=tests,
            edges={k.value: kinds.get(k.value, 0) for k in EdgeKind},
            calls_by_resolution=dict(sorted(by_resolution.items())),
            calls_retrievable=retrievable,
            threshold=threshold,
        )


class GraphStore(GraphReader):
    """A graph store that can be written. Only the base-branch indexer writes one."""

    @abstractmethod
    def replace_files(self, repo_id: str, graphs: Iterable[FileGraph]) -> None:
        """Replace each file's nodes and the edges that start in it.

        Nodes the new version no longer has are removed with all their edges,
        inbound ones included. An edge whose target does not exist once the
        whole batch is written is dropped, so write a full index in one call.
        Raises ValueError if a graph's nodes or edges belong to another repo or
        another file.
        """

    @abstractmethod
    def remove_files(self, repo_id: str, paths: Iterable[str]) -> None: ...

    @abstractmethod
    def delete_repo(self, repo_id: str) -> None: ...


def check_file_graph(repo_id: str, graph: FileGraph) -> None:
    """Raise ValueError unless every node and edge in `graph` belongs to `repo_id` and its file."""
    if graph.repo_id != repo_id:
        raise ValueError(f"graph for repo {graph.repo_id!r} written to repo {repo_id!r}")
    names = set()
    for node in graph.nodes:
        if node.repo_id != repo_id or node.path != graph.path:
            raise ValueError(f"node {node.qualified_name!r} does not belong to {graph.path!r}")
        names.add(node.qualified_name)
    for edge in graph.edges:
        if edge.repo_id != repo_id or edge.path != graph.path or edge.src not in names:
            raise ValueError(f"edge {edge.ident} does not start in {graph.path!r}")


# Vectors


class VectorItem(_Frozen):
    qualified_name: str
    path: str
    vector: tuple[float, ...]
    content_hash: str = Field(
        default="", description="Hash of the embedded text, to skip re-embedding."
    )


class VectorHit(_Frozen):
    qualified_name: str
    path: str
    score: float = Field(description="Cosine similarity.")


class VectorReader(Protocol):
    def search(
        self,
        repo_id: str,
        vector: Sequence[float],
        *,
        k: int = 10,
        exclude_paths: Collection[str] = (),
    ) -> list[VectorHit]:
        """Top `k` items by cosine similarity, best first, from one repo only."""
        ...

    def get(self, repo_id: str, qualified_name: str) -> VectorItem | None: ...


class VectorStore(VectorReader, Protocol):
    def count(self, repo_id: str) -> int: ...

    def upsert(self, repo_id: str, items: Iterable[VectorItem]) -> None:
        """Insert or replace items by qualified name. Raises ValueError on a dimension mismatch."""
        ...

    def remove_files(self, repo_id: str, paths: Iterable[str]) -> None: ...

    def delete_repo(self, repo_id: str) -> None: ...
