"""Index a repository into a graph store, or build a PR overlay over one.

Stages: discover files, parse each with its language parser, build a symbol
table, resolve calls, then write one `FileGraph` per file.
"""

import hashlib
import os
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from prism.indexer.embedder import Embedder, function_text
from prism.indexer.model import FileGraph, Node
from prism.indexer.parsers import ParsedFile, parser_for
from prism.indexer.resolver import resolve
from prism.indexer.symbols import SymbolTable
from prism.store.base import GraphReader, GraphStore, VectorItem, VectorStore
from prism.store.overlay import OverlayGraph

SKIP_DIRS = frozenset(
    {
        "__pycache__",
        "build",
        "dist",
        "env",
        "node_modules",
        "site-packages",
        "venv",
    }
)
MAX_FILE_BYTES = 2_000_000


@dataclass
class IndexReport:
    repo_id: str
    files: int = 0
    files_with_errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
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


def index_repository(
    root: Path,
    repo_id: str,
    store: GraphStore,
    *,
    vectors: VectorStore | None = None,
    embedder: Embedder | None = None,
) -> IndexReport:
    """Full index of a working tree. Files no longer present are removed from the store."""
    started = time.perf_counter()
    root = root.resolve()
    paths, skipped = discover_files(root)
    sources = {p: (root / p).read_bytes() for p in paths}
    parsed = parse_sources(repo_id, sources)
    graphs = build_file_graphs(parsed)

    current = set(paths)
    store.remove_files(repo_id, [p for p in store.file_paths(repo_id) if p not in current])
    store.replace_files(repo_id, graphs)

    report = IndexReport(repo_id=repo_id, files=len(parsed), skipped=skipped)
    report.files_with_errors = [pf.path for pf in parsed if pf.has_errors]
    if vectors is not None and embedder is not None:
        vectors.delete_repo(repo_id)
        items = embed_functions(parsed, embedder)
        vectors.upsert(repo_id, items)
        report.vectors = len(items)
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
