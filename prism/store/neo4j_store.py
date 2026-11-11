"""Neo4j graph store.

Every node gets its kind label (File, Class, Function) plus a shared `Node`
label, so lookups by (repo_id, qualified_name) use one index whatever the kind.
Each label has a composite uniqueness constraint on (repo_id, qualified_name).
The whole node is kept as JSON in `n.json`; the fields queries filter on are
copied to their own properties.

Write semantics match the in-memory store (and the contract tests): replacing a
file deletes the edges that start in it and DETACH DELETEs nodes it no longer
has; an edge whose target does not exist is not created.
"""

from collections import Counter
from collections.abc import Collection, Iterable, Iterator, Sequence
from typing import Any, LiteralString

from neo4j import Driver, GraphDatabase
from pydantic import TypeAdapter

from prism.config import Settings, get_settings
from prism.indexer.model import (
    AnyNode,
    ClassNode,
    Edge,
    EdgeKind,
    FileGraph,
    FileNode,
    FunctionNode,
    Node,
    NodeKind,
    Resolution,
)
from prism.store.base import (
    GraphStats,
    GraphStore,
    Neighbour,
    Related,
    check_file_graph,
    default_threshold,
)

_NODE = TypeAdapter(AnyNode)
_ALL_KINDS = [k.value for k in EdgeKind]
# Relationship types are spliced into Cypher, so they come from this fixed map only.
_REL_TYPES: dict[EdgeKind, LiteralString] = {
    EdgeKind.DEFINES: "DEFINES",
    EdgeKind.IMPORTS: "IMPORTS",
    EdgeKind.CALLS: "CALLS",
    EdgeKind.TESTS: "TESTS",
    EdgeKind.INHERITS: "INHERITS",
    EdgeKind.OVERRIDES: "OVERRIDES",
}
_BATCH = 1000

_SCHEMA: tuple[LiteralString, ...] = (
    "CREATE CONSTRAINT prism_node_key IF NOT EXISTS "
    "FOR (n:Node) REQUIRE (n.repo_id, n.qualified_name) IS UNIQUE",
    "CREATE CONSTRAINT prism_file_key IF NOT EXISTS "
    "FOR (n:File) REQUIRE (n.repo_id, n.qualified_name) IS UNIQUE",
    "CREATE CONSTRAINT prism_class_key IF NOT EXISTS "
    "FOR (n:Class) REQUIRE (n.repo_id, n.qualified_name) IS UNIQUE",
    "CREATE CONSTRAINT prism_function_key IF NOT EXISTS "
    "FOR (n:Function) REQUIRE (n.repo_id, n.qualified_name) IS UNIQUE",
    "CREATE INDEX prism_node_path IF NOT EXISTS FOR (n:Node) ON (n.repo_id, n.path)",
    "CREATE INDEX prism_node_name IF NOT EXISTS FOR (n:Node) ON (n.repo_id, n.name)",
)

# Scored edges below the threshold are skipped; other kinds always pass.
_PASSES: LiteralString = "(NOT type(e) IN ['CALLS', 'TESTS'] OR e.confidence >= $threshold)"


def _node_row(node: Node) -> dict[str, Any]:
    return {
        "qualified_name": node.qualified_name,
        "name": node.name,
        "path": node.path,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "is_test": getattr(node, "is_test", False),
        "is_method": isinstance(node, FunctionNode) and node.is_method,
        "json": node.model_dump_json(),
    }


def _edge_row(edge: Edge) -> dict[str, Any]:
    return {
        "src": edge.src,
        "dst": edge.dst,
        "path": edge.path,
        "line": edge.line,
        "confidence": edge.confidence,
        "resolution": edge.resolution.value if edge.resolution else None,
    }


def _load(data: str) -> Node:
    return _NODE.validate_json(data)


def _chunks(rows: Sequence[dict[str, Any]], size: int = _BATCH) -> Iterator[list[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield list(rows[i : i + size])


class Neo4jGraphStore(GraphStore):
    def __init__(self, driver: Driver, *, database: str | None = None) -> None:
        self._driver = driver
        self._database = database
        self.ensure_schema()

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "Neo4jGraphStore":
        s = settings or get_settings()
        driver = GraphDatabase.driver(
            s.neo4j_uri,
            auth=(s.neo4j_user, s.neo4j_password.get_secret_value()),
            connection_timeout=5,
            max_transaction_retry_time=5,
        )
        try:
            # Fail fast: query retries would otherwise wait out the retry window.
            driver.verify_connectivity()
        except Exception:
            driver.close()
            raise
        return cls(driver)

    def close(self) -> None:
        self._driver.close()

    def _run(self, query: LiteralString, **params: Any) -> list[dict[str, Any]]:
        records = self._driver.execute_query(
            query, parameters_=params, database_=self._database
        ).records
        return [r.data() for r in records]

    def ensure_schema(self) -> None:
        """Create the constraints and indexes. Safe to run on every start."""
        for statement in _SCHEMA:
            self._run(statement)

    # Writes

    def replace_files(self, repo_id: str, graphs: Iterable[FileGraph]) -> None:
        batch = list(graphs)
        for graph in batch:
            check_file_graph(repo_id, graph)
        if not batch:
            return
        keep = {n.qualified_name for g in batch for n in g.nodes}
        # 1. Edges that start in these files go; nodes the files no longer have go,
        #    with every edge into them. Done per group of files to keep transactions small.
        all_paths = [g.path for g in batch]
        for i in range(0, len(all_paths), 200):
            paths = all_paths[i : i + 200]
            rows = self._run(
                "MATCH (n:Node {repo_id: $repo}) WHERE n.path IN $paths "
                "OPTIONAL MATCH (n)-[e]->() DELETE e "
                "RETURN DISTINCT n.qualified_name AS qn",
                repo=repo_id,
                paths=paths,
            )
            gone = [r["qn"] for r in rows if r["qn"] not in keep]
            for start in range(0, len(gone), _BATCH):
                self._run(
                    "UNWIND $qns AS qn MATCH (n:Node {repo_id: $repo, qualified_name: qn}) "
                    "DETACH DELETE n",
                    repo=repo_id,
                    qns=gone[start : start + _BATCH],
                )
        # 2. Nodes, one statement per kind so the labels can be static.
        by_kind: dict[NodeKind, list[dict[str, Any]]] = {k: [] for k in NodeKind}
        for graph in batch:
            for node in graph.nodes:
                by_kind[node.kind].append(_node_row(node))
        for kind, rows in by_kind.items():
            for chunk in _chunks(rows):
                self._merge_nodes(repo_id, kind, chunk)
        # 3. Edges, after all nodes exist. MATCH drops edges to missing targets.
        by_type: dict[EdgeKind, list[dict[str, Any]]] = {k: [] for k in EdgeKind}
        for graph in batch:
            for edge in graph.edges:
                by_type[edge.kind].append(_edge_row(edge))
        for edge_kind, rows in by_type.items():
            for chunk in _chunks(rows):
                self._merge_edges(repo_id, edge_kind, chunk)

    def _merge_nodes(self, repo_id: str, kind: NodeKind, rows: list[dict[str, Any]]) -> None:
        # A qualified name can change kind between versions; drop the other labels.
        set_labels: dict[NodeKind, LiteralString] = {
            NodeKind.FILE: "SET n:File REMOVE n:Class:Function",
            NodeKind.CLASS: "SET n:Class REMOVE n:File:Function",
            NodeKind.FUNCTION: "SET n:Function REMOVE n:File:Class",
        }
        query: LiteralString = (
            "UNWIND $rows AS row "
            "MERGE (n:Node {repo_id: $repo, qualified_name: row.qualified_name}) "
            + set_labels[kind]
            + " SET n.name = row.name, n.path = row.path, n.start_line = row.start_line, "
            "n.end_line = row.end_line, n.is_test = row.is_test, n.is_method = row.is_method, "
            "n.json = row.json"
        )
        self._run(query, repo=repo_id, rows=rows)

    def _merge_edges(self, repo_id: str, kind: EdgeKind, rows: list[dict[str, Any]]) -> None:
        rel: LiteralString = _REL_TYPES[kind]
        query: LiteralString = (
            "UNWIND $rows AS row "
            "MATCH (s:Node {repo_id: $repo, qualified_name: row.src}) "
            "MATCH (d:Node {repo_id: $repo, qualified_name: row.dst}) "
            "MERGE (s)-[e:" + rel + "]->(d) "
            "SET e.repo_id = $repo, e.path = row.path, e.line = row.line, "
            "e.confidence = row.confidence, e.resolution = row.resolution"
        )
        self._run(query, repo=repo_id, rows=rows)

    def remove_files(self, repo_id: str, paths: Iterable[str]) -> None:
        self._run(
            "MATCH (n:Node {repo_id: $repo}) WHERE n.path IN $paths DETACH DELETE n",
            repo=repo_id,
            paths=list(paths),
        )

    def delete_repo(self, repo_id: str) -> None:
        while True:
            rows = self._run(
                "MATCH (n:Node {repo_id: $repo}) WITH n LIMIT 5000 "
                "DETACH DELETE n RETURN count(*) AS deleted",
                repo=repo_id,
            )
            if not rows or rows[0]["deleted"] == 0:
                return

    def repo_ids(self) -> list[str]:
        rows = self._run("MATCH (n:File) RETURN DISTINCT n.repo_id AS repo ORDER BY repo")
        return [r["repo"] for r in rows]

    # Primitives

    def get_node(self, repo_id: str, qualified_name: str) -> Node | None:
        rows = self._run(
            "MATCH (n:Node {repo_id: $repo, qualified_name: $qn}) RETURN n.json AS json",
            repo=repo_id,
            qn=qualified_name,
        )
        return _load(rows[0]["json"]) if rows else None

    def nodes_in_file(self, repo_id: str, path: str) -> list[Node]:
        rows = self._run(
            "MATCH (n:Node {repo_id: $repo, path: $path}) RETURN n.json AS json",
            repo=repo_id,
            path=path,
        )
        nodes = [_load(r["json"]) for r in rows]
        return sorted(nodes, key=lambda n: (n.start_line, n.qualified_name))

    def find_by_name(self, repo_id: str, name: str) -> list[Node]:
        rows = self._run(
            "MATCH (n:Node {repo_id: $repo, name: $name}) WHERE NOT n:File "
            "RETURN n.json AS json ORDER BY n.qualified_name",
            repo=repo_id,
            name=name,
        )
        return [_load(r["json"]) for r in rows]

    def _edges(
        self,
        repo_id: str,
        qualified_name: str,
        kinds: Collection[EdgeKind] | None,
        outgoing: bool,
    ) -> list[Edge]:
        pattern: LiteralString = (
            "MATCH (s:Node {repo_id: $repo, qualified_name: $qn})-[e]->(d:Node {repo_id: $repo}) "
            if outgoing
            else "MATCH (s:Node {repo_id: $repo})-[e]->"
            "(d:Node {repo_id: $repo, qualified_name: $qn}) "
        )
        rows = self._run(
            pattern + "WHERE type(e) IN $kinds AND e.repo_id = $repo "
            "RETURN type(e) AS kind, s.qualified_name AS src, d.qualified_name AS dst, "
            "e.path AS path, e.line AS line, e.confidence AS confidence, "
            "e.resolution AS resolution",
            repo=repo_id,
            qn=qualified_name,
            kinds=[k.value for k in kinds] if kinds is not None else _ALL_KINDS,
        )
        return [_edge_from(repo_id, r) for r in rows]

    def out_edges(
        self, repo_id: str, qualified_name: str, kinds: Collection[EdgeKind] | None = None
    ) -> list[Edge]:
        return self._edges(repo_id, qualified_name, kinds, True)

    def in_edges(
        self, repo_id: str, qualified_name: str, kinds: Collection[EdgeKind] | None = None
    ) -> list[Edge]:
        return self._edges(repo_id, qualified_name, kinds, False)

    def iter_nodes(self, repo_id: str) -> Iterator[Node]:
        rows = self._run("MATCH (n:Node {repo_id: $repo}) RETURN n.json AS json", repo=repo_id)
        for r in rows:
            yield _load(r["json"])

    def iter_edges(self, repo_id: str) -> Iterator[Edge]:
        rows = self._run(
            "MATCH (s:Node {repo_id: $repo})-[e]->(d:Node {repo_id: $repo}) "
            "WHERE e.repo_id = $repo "
            "RETURN type(e) AS kind, s.qualified_name AS src, d.qualified_name AS dst, "
            "e.path AS path, e.line AS line, e.confidence AS confidence, "
            "e.resolution AS resolution",
            repo=repo_id,
        )
        for r in rows:
            yield _edge_from(repo_id, r)

    def file_paths(self, repo_id: str) -> list[str]:
        rows = self._run(
            "MATCH (n:File {repo_id: $repo}) RETURN n.path AS path ORDER BY path", repo=repo_id
        )
        return [r["path"] for r in rows]

    # Queries, in Cypher

    def _related_q(
        self,
        repo_id: str,
        qualified_name: str,
        rel: LiteralString,
        outgoing: bool,
        min_confidence: float | None,
    ) -> list[Related]:
        if outgoing:
            pattern: LiteralString = (
                "MATCH (t:Node {repo_id: $repo, qualified_name: $qn})-[e:" + rel + "]->"
                "(o:Node {repo_id: $repo}) "
            )
        else:
            pattern = (
                "MATCH (o:Node {repo_id: $repo})-[e:" + rel + "]->"
                "(t:Node {repo_id: $repo, qualified_name: $qn}) "
            )
        rows = self._run(
            pattern + "WHERE e.confidence >= $threshold "
            "RETURN o.json AS json, type(e) AS kind, startNode(e).qualified_name AS src, "
            "endNode(e).qualified_name AS dst, e.path AS path, e.line AS line, "
            "e.confidence AS confidence, e.resolution AS resolution",
            repo=repo_id,
            qn=qualified_name,
            threshold=default_threshold(min_confidence),
        )
        out = [Related(node=_load(r["json"]), edge=_edge_from(repo_id, r)) for r in rows]
        return sorted(out, key=lambda r: (-r.edge.confidence, r.node.qualified_name))

    def callers(
        self, repo_id: str, qualified_name: str, *, min_confidence: float | None = None
    ) -> list[Related]:
        return self._related_q(repo_id, qualified_name, "CALLS", False, min_confidence)

    def callees(
        self, repo_id: str, qualified_name: str, *, min_confidence: float | None = None
    ) -> list[Related]:
        return self._related_q(repo_id, qualified_name, "CALLS", True, min_confidence)

    def tests_for(
        self, repo_id: str, qualified_name: str, *, min_confidence: float | None = None
    ) -> list[Related]:
        return self._related_q(repo_id, qualified_name, "TESTS", False, min_confidence)

    def neighbours(
        self,
        repo_id: str,
        qualified_name: str,
        *,
        hops: int = 1,
        kinds: Collection[EdgeKind] | None = None,
        min_confidence: float | None = None,
    ) -> list[Neighbour]:
        """Breadth-first, one Cypher query per hop.

        A single variable-length MATCH enumerates every path and blows up on
        dense graphs; expanding a frontier per hop visits each node once.
        """
        if hops < 1 or self.get_node(repo_id, qualified_name) is None:
            return []
        params: dict[str, Any] = {
            "repo": repo_id,
            "kinds": [k.value for k in kinds] if kinds is not None else _ALL_KINDS,
            "threshold": default_threshold(min_confidence),
        }
        seen = {qualified_name}
        frontier = [qualified_name]
        found: list[Neighbour] = []
        for depth in range(1, hops + 1):
            if not frontier:
                break
            rows = self._run(
                "UNWIND $frontier AS q "
                "MATCH (a:Node {repo_id: $repo, qualified_name: q})-[e]-(b:Node {repo_id: $repo}) "
                "WHERE type(e) IN $kinds AND e.repo_id = $repo AND " + _PASSES + " "
                "AND NOT b.qualified_name IN $seen "
                "RETURN DISTINCT b.qualified_name AS qn, b.json AS json",
                frontier=frontier,
                seen=list(seen),
                **params,
            )
            frontier = []
            for r in rows:
                if r["qn"] in seen:
                    continue
                seen.add(r["qn"])
                frontier.append(r["qn"])
                found.append(Neighbour(node=_load(r["json"]), distance=depth))
        return sorted(found, key=lambda n: (n.distance, n.node.qualified_name))

    def enclosing_symbol(self, repo_id: str, path: str, line: int) -> Node | None:
        rows = self._run(
            "MATCH (n:Node {repo_id: $repo, path: $path}) "
            "WHERE n.start_line <= $line AND $line <= n.end_line "
            "RETURN n.json AS json",
            repo=repo_id,
            path=path,
            line=line,
        )
        nodes = [_load(r["json"]) for r in rows]
        if not any(isinstance(n, FileNode) for n in nodes):
            return None
        around = [n for n in nodes if isinstance(n, ClassNode | FunctionNode)]
        if not around:
            return next(n for n in nodes if isinstance(n, FileNode))
        return min(around, key=lambda n: (n.end_line - n.start_line, -n.start_line))

    def stats(self, repo_id: str, *, min_confidence: float | None = None) -> GraphStats:
        threshold = default_threshold(min_confidence)
        node_rows = self._run(
            "MATCH (n:Node {repo_id: $repo}) "
            "RETURN sum(CASE WHEN n:File THEN 1 ELSE 0 END) AS files, "
            "sum(CASE WHEN n:File AND n.is_test THEN 1 ELSE 0 END) AS test_files, "
            "sum(CASE WHEN n:Class THEN 1 ELSE 0 END) AS classes, "
            "sum(CASE WHEN n:Function THEN 1 ELSE 0 END) AS functions, "
            "sum(CASE WHEN n:Function AND n.is_method THEN 1 ELSE 0 END) AS methods, "
            "sum(CASE WHEN n:Function AND n.is_test THEN 1 ELSE 0 END) AS tests",
            repo=repo_id,
        )
        n = node_rows[0] if node_rows else {}
        edge_rows = self._run(
            "MATCH (:Node {repo_id: $repo})-[e]->(:Node {repo_id: $repo}) "
            "WHERE e.repo_id = $repo "
            "RETURN type(e) AS kind, e.resolution AS resolution, "
            "count(*) AS total, sum(CASE WHEN e.confidence >= $threshold THEN 1 ELSE 0 END) AS ok",
            repo=repo_id,
            threshold=threshold,
        )
        kinds: Counter[str] = Counter()
        by_resolution: Counter[str] = Counter()
        retrievable = 0
        for r in edge_rows:
            kinds[r["kind"]] += r["total"]
            if r["kind"] == EdgeKind.CALLS.value:
                by_resolution[r["resolution"] or "unknown"] += r["total"]
                retrievable += r["ok"]
        return GraphStats(
            repo_id=repo_id,
            files=n.get("files") or 0,
            test_files=n.get("test_files") or 0,
            classes=n.get("classes") or 0,
            functions=n.get("functions") or 0,
            methods=n.get("methods") or 0,
            test_functions=n.get("tests") or 0,
            edges={k.value: kinds.get(k.value, 0) for k in EdgeKind},
            calls_by_resolution=dict(sorted(by_resolution.items())),
            calls_retrievable=retrievable,
            threshold=threshold,
        )

    def counts(self) -> dict[str, int]:
        """Node and relationship totals across every repo, straight from Neo4j."""
        nodes = self._run("MATCH (n:Node) RETURN count(n) AS c")
        rels = self._run("MATCH ()-[e]->() RETURN count(e) AS c")
        return {"nodes": nodes[0]["c"], "relationships": rels[0]["c"]}


def _edge_from(repo_id: str, r: dict[str, Any]) -> Edge:
    resolution = r.get("resolution")
    return Edge(
        kind=EdgeKind(r["kind"]),
        repo_id=repo_id,
        src=r["src"],
        dst=r["dst"],
        path=r["path"],
        line=r.get("line"),
        confidence=r["confidence"],
        resolution=Resolution(resolution) if resolution else None,
    )
