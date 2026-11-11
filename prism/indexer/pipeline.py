"""Index a repository into a graph store, or build a PR overlay over one.

Stages: discover files, parse each with its language parser, build a symbol
table, resolve calls, then write one `FileGraph` per file.
"""

import hashlib
import os
import subprocess
import time
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from prism.indexer.embedder import Embedder, function_text
from prism.indexer.model import FileGraph, FileNode, Node
from prism.indexer.parsers import ParsedFile, parser_for
from prism.indexer.resolver import resolve
from prism.indexer.symbols import SymbolTable
from prism.store.base import GraphReader, GraphStore, VectorItem, VectorStore
from prism.store.ledger import IndexLedger, IndexRun
from prism.store.overlay import OverlayGraph

# Build output, virtualenvs and generated code. Hidden folders (.git, .gradle) are skipped too.
SKIP_DIRS = frozenset(
    {
        "__pycache__",
        "bin",
        "build",
        "dist",
        "env",
        "generated",
        "generated-sources",
        "generated-test-sources",
        "node_modules",
        "out",
        "site-packages",
        "target",
        "venv",
    }
)
MAX_FILE_BYTES = 2_000_000


@dataclass
class IndexReport:
    repo_id: str
    kind: str = "full"
    commit_sha: str | None = None
    run_id: int | None = None
    files: int = 0
    files_with_errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    vectors: int = 0
    seconds: float = 0.0


def discover_files(root: Path) -> tuple[list[str], list[str]]:
    """Repo-relative POSIX paths of files a parser handles, and of files skipped for size.

    Hidden directories and the usual virtualenv and build folders are skipped.
    """
    found: list[str] = []
    skipped: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            if parser_for(rel) is None:
                continue
            if full.stat().st_size > MAX_FILE_BYTES:
                skipped.append(rel)
                continue
            found.append(rel)
    return sorted(found), sorted(skipped)


def parse_sources(repo_id: str, sources: Mapping[str, bytes]) -> list[ParsedFile]:
    out: list[ParsedFile] = []
    for path in sorted(sources):
        parser = parser_for(path)
        if parser is not None:
            out.append(parser.parse(repo_id, path, sources[path]))
    return out


def build_file_graphs(
    parsed: Sequence[ParsedFile], context: Iterable[Node] = ()
) -> list[FileGraph]:
    """Resolve the parsed files' calls against their own nodes plus `context`."""
    symbols = SymbolTable([*context, *(n for pf in parsed for n in pf.nodes)])
    return resolve(parsed, symbols)


def embed_functions(parsed: Sequence[ParsedFile], embedder: Embedder) -> list[VectorItem]:
    texts: list[str] = []
    meta: list[tuple[str, str]] = []
    for pf in parsed:
        for fn in pf.functions:
            texts.append(function_text(fn, pf.source))
            meta.append((fn.qualified_name, fn.path))
    vectors = embedder.embed(texts) if texts else []
    return [
        VectorItem(
            qualified_name=qn,
            path=path,
            vector=tuple(vec),
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
        )
        for (qn, path), vec, text in zip(meta, vectors, texts, strict=True)
    ]


def commit_sha_for(root: Path) -> str:
    """HEAD of the git repo at `root`, or "working-tree" when it is not a git repo."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "working-tree"
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else "working-tree"


_EMBED_CHUNK = 64


def _write_vectors(
    parsed: Sequence[ParsedFile],
    vectors: VectorStore,
    embedder: Embedder,
    repo_id: str,
    beat: Callable[[], None],
) -> int:
    """Embed and upsert in chunks, beating the ledger heartbeat between chunks."""
    written = 0
    pending: list[ParsedFile] = []
    count = 0
    for pf in [*parsed, None]:
        if pf is not None:
            pending.append(pf)
            count += len(pf.functions)
            if count < _EMBED_CHUNK:
                continue
        if pending:
            items = embed_functions(pending, embedder)
            vectors.upsert(repo_id, items)
            written += len(items)
            beat()
        pending, count = [], 0
    return written


@contextmanager
def _ledger_run(
    ledger: IndexLedger | None, repo_id: str, sha: str, kind: str
) -> Iterator[tuple[IndexRun | None, Callable[[], None]]]:
    """Record a run as running, then complete or failed. Yields the run and a heartbeat."""
    if ledger is None:
        yield None, lambda: None
        return
    run = ledger.start(repo_id, sha, kind=kind)
    try:
        yield run, lambda: ledger.heartbeat(run)
    except BaseException as exc:
        ledger.fail(run, f"{type(exc).__name__}: {exc}")
        raise
    ledger.complete(run)


def index_repository(
    root: Path,
    repo_id: str,
    store: GraphStore,
    *,
    vectors: VectorStore | None = None,
    embedder: Embedder | None = None,
    ledger: IndexLedger | None = None,
    commit_sha: str | None = None,
) -> IndexReport:
    """Full index of a working tree. Files no longer present are removed from the store.

    With a ledger, the run is recorded as running, both stores are written, and
    the run is marked complete (or failed on any error).
    """
    started = time.perf_counter()
    root = root.resolve()
    sha = commit_sha or commit_sha_for(root)
    with _ledger_run(ledger, repo_id, sha, "full") as (run, beat):
        paths, skipped = discover_files(root)
        sources = {p: (root / p).read_bytes() for p in paths}
        parsed = parse_sources(repo_id, sources)
        graphs = build_file_graphs(parsed)
        beat()

        current = set(paths)
        store.remove_files(repo_id, [p for p in store.file_paths(repo_id) if p not in current])
        store.replace_files(repo_id, graphs)
        beat()

        report = IndexReport(repo_id=repo_id, kind="full", commit_sha=sha, skipped=skipped)
        report.files = len(parsed)
        report.files_with_errors = [pf.path for pf in parsed if pf.has_errors]
        report.run_id = run.id if run else None
        if vectors is not None and embedder is not None:
            vectors.delete_repo(repo_id)
            report.vectors = _write_vectors(parsed, vectors, embedder, repo_id, beat)
    report.seconds = time.perf_counter() - started
    return report


def find_dependents(
    store: GraphReader,
    repo_id: str,
    touched: Collection[str],
    symbols: SymbolTable,
) -> set[str]:
    """Files whose edges can move when `touched` files change.

    Two sources: (1) any file with an edge into a node of a touched file, read
    from the store before the update (callers, importers, tests); (2) any file
    whose imports resolve, against the updated symbols, to a touched module (a
    file that imports a module the update adds); (3) for Java, every file in a
    touched package or importing a touched type or package. Name-match edges that a new
    definition would capture are not found (ADR 0004).
    """
    touched_set = set(touched)
    out: set[str] = set()
    touched_files: list[FileNode] = []
    for path in touched_set:
        for node in store.nodes_in_file(repo_id, path):
            if isinstance(node, FileNode):
                touched_files.append(node)
            for edge in store.in_edges(repo_id, node.qualified_name):
                if edge.path not in touched_set:
                    out.add(edge.path)
        new = symbols.nodes.get(path)
        if isinstance(new, FileNode):
            touched_files.append(new)
    # Java needs no import within a package, so a change reaches the whole package.
    out |= symbols.java.dependents(touched_files)
    for file in symbols.files.values():
        if file.path in touched_set or file.path in out:
            continue
        for ref in file.imports:
            target = symbols.resolve_module(ref.target) or symbols.resolve_module(ref.module)
            if target is not None and target.path in touched_set:
                out.add(file.path)
                break
    return out


def update_repository(
    root: Path,
    repo_id: str,
    changed_paths: Iterable[str],
    removed_paths: Iterable[str],
    store: GraphStore,
    *,
    vectors: VectorStore | None = None,
    embedder: Embedder | None = None,
    ledger: IndexLedger | None = None,
    commit_sha: str | None = None,
) -> IndexReport:
    """Incremental update of the base graph after the default branch moved.

    Re-parses the changed files, removes the deleted ones, and re-resolves the
    files that depend on either (see `find_dependents`). Falls back to a full
    index when the repo has no complete run, or its last run did not finish.
    """
    root = root.resolve()
    sha = commit_sha or commit_sha_for(root)
    if ledger is not None:
        ledger.expire_stale(repo_id)
        if ledger.needs_full_index(repo_id) and not ledger.index_in_progress(repo_id):
            return index_repository(
                root,
                repo_id,
                store,
                vectors=vectors,
                embedder=embedder,
                ledger=ledger,
                commit_sha=sha,
            )
    if not store.file_paths(repo_id):
        return index_repository(
            root,
            repo_id,
            store,
            vectors=vectors,
            embedder=embedder,
            ledger=ledger,
            commit_sha=sha,
        )

    started = time.perf_counter()
    wanted = {p for p in changed_paths if parser_for(p) is not None}
    changed = sorted(p for p in wanted if (root / p).is_file())
    removed = sorted(
        {p for p in removed_paths if parser_for(p) is not None} | (wanted - set(changed))
    )
    touched = set(changed) | set(removed)
    with _ledger_run(ledger, repo_id, sha, "incremental") as (run, beat):
        parsed_changed = parse_sources(repo_id, {p: (root / p).read_bytes() for p in changed})
        context = [n for n in store.iter_nodes(repo_id) if n.path not in touched]
        symbols = SymbolTable([*context, *(n for pf in parsed_changed for n in pf.nodes)])
        dependents = sorted(
            p for p in find_dependents(store, repo_id, touched, symbols) if (root / p).is_file()
        )
        parsed_deps = parse_sources(repo_id, {p: (root / p).read_bytes() for p in dependents})
        dep_paths = set(dependents)
        # Rebuild the table with the re-read dependents so their nodes match the disk.
        symbols = SymbolTable(
            [
                *(n for n in context if n.path not in dep_paths),
                *(n for pf in [*parsed_changed, *parsed_deps] for n in pf.nodes),
            ]
        )
        graphs = resolve([*parsed_changed, *parsed_deps], symbols)
        beat()

        store.remove_files(repo_id, removed)
        store.replace_files(repo_id, graphs)
        beat()

        report = IndexReport(repo_id=repo_id, kind="incremental", commit_sha=sha)
        report.files = len(parsed_changed)
        report.removed = removed
        report.dependents = dependents
        report.files_with_errors = [pf.path for pf in parsed_changed if pf.has_errors]
        report.run_id = run.id if run else None
        if vectors is not None and embedder is not None:
            vectors.remove_files(repo_id, sorted(touched))
            report.vectors = _write_vectors(parsed_changed, vectors, embedder, repo_id, beat)
    report.seconds = time.perf_counter() - started
    return report


def build_overlay(
    base: GraphReader, repo_id: str, changed: Mapping[str, bytes | None]
) -> OverlayGraph:
    """Overlay for one PR. `changed` maps a path to its content at the head commit, or None if
    the PR deletes it. Files in languages with no parser are ignored.
    """
    sources = {p: s for p, s in changed.items() if s is not None and parser_for(p) is not None}
    removed = [p for p, s in changed.items() if s is None]
    shadowed = set(sources) | set(removed)
    parsed = parse_sources(repo_id, sources)
    context = (n for n in base.iter_nodes(repo_id) if n.path not in shadowed)
    graphs = build_file_graphs(parsed, context)
    return OverlayGraph(base, repo_id, graphs, removed_paths=removed)
