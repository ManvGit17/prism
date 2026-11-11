"""`prism` command line.

There is no persistent store yet, so every command indexes the repo in memory
first. The graph commands take the repo with `--path` until the Neo4j store
exists.
"""

import io
import sys
from pathlib import Path
from typing import Annotated

import typer

from prism.indexer.model import Node
from prism.indexer.pipeline import IndexReport, index_repository
from prism.store import GraphStats, InMemoryGraphStore, Related

app = typer.Typer(help="PRism: repo-aware code review.", no_args_is_help=True)
graph_app = typer.Typer(
    help=(
        "Query the code graph. Until the persistent store exists, each command indexes the repo "
        "given by --path in memory first, then answers from that."
    ),
    no_args_is_help=True,
)
app.add_typer(graph_app, name="graph")

RepoPath = Annotated[
    Path,
    typer.Option(
        "--path",
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Repo to index in memory before the query (needed until the persistent store exists).",
    ),
]
RepoId = Annotated[
    str | None, typer.Option("--repo-id", help="Repo id. Defaults to the folder name.")
]
MinConfidence = Annotated[
    float | None,
    typer.Option(
        "--min-confidence",
        min=0.0,
        max=1.0,
        help=(
            "Lowest CALLS/TESTS confidence to follow. Defaults to PRISM_CALL_CONFIDENCE_THRESHOLD."
        ),
    ),
]


@app.callback()
def _main() -> None:
    # File names can hold characters a redirected Windows console cannot encode.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="backslashreplace")


def _index(path: Path, repo_id: str | None) -> tuple[InMemoryGraphStore, str, IndexReport]:
    rid = repo_id or path.resolve().name
    store = InMemoryGraphStore()
    report = index_repository(path, rid, store)
    return store, rid, report


def _format_stats(stats: GraphStats, report: IndexReport) -> list[str]:
    lines = [
        f"repo: {stats.repo_id}",
        f"files: {stats.files} ({stats.test_files} test files)",
        f"classes: {stats.classes}",
        f"functions: {stats.functions} ({stats.methods} methods, {stats.test_functions} tests)",
        "edges: " + ", ".join(f"{k} {v}" for k, v in stats.edges.items()),
        "calls by resolution: "
        + ", ".join(f"{k} {v}" for k, v in stats.calls_by_resolution.items()),
        f"calls at or above threshold {stats.threshold:g}: {stats.calls_retrievable}"
        f" of {stats.edges.get('CALLS', 0)}",
        f"parse errors in {len(report.files_with_errors)} files; "
        f"{len(report.skipped)} files skipped for size; {report.seconds:.2f}s",
    ]
    lines.extend(f"  parse error: {p}" for p in report.files_with_errors)
    return lines


def _where(node: Node) -> str:
    return f"{node.path}:{node.start_line}"


def _pick(store: InMemoryGraphStore, repo_id: str, symbol: str) -> Node:
    matches = store.lookup(repo_id, symbol)
    if not matches:
        typer.echo(f"no symbol matches {symbol!r}", err=True)
        raise typer.Exit(1)
    if len(matches) > 1:
        typer.echo(f"{symbol!r} is ambiguous; use a qualified name:", err=True)
        for node in matches:
            typer.echo(f"  {node.qualified_name}  {_where(node)}", err=True)
        raise typer.Exit(2)
    return matches[0]


@app.command()
def index(
    path: Annotated[
        Path, typer.Argument(exists=True, file_okay=False, dir_okay=True, help="Repo to index.")
    ],
    repo_id: RepoId = None,
) -> None:
    """Index a repo in memory and print graph stats."""
    store, rid, report = _index(path, repo_id)
    for line in _format_stats(store.stats(rid), report):
        typer.echo(line)


@graph_app.command()
def neighbours(
    symbol: Annotated[str, typer.Argument(help="Qualified name, short name or dotted suffix.")],
    path: RepoPath,
    hops: Annotated[int, typer.Option("--hops", min=1, max=5)] = 1,
    repo_id: RepoId = None,
    min_confidence: MinConfidence = None,
) -> None:
    """Nodes within --hops edges of a symbol, in either direction."""
    store, rid, _ = _index(path, repo_id)
    node = _pick(store, rid, symbol)
    typer.echo(f"{node.qualified_name}  {_where(node)}")
    for n in store.neighbours(rid, node.qualified_name, hops=hops, min_confidence=min_confidence):
        typer.echo(
            f"  {n.distance}  {n.node.kind.value:<8}  {n.node.qualified_name}  {_where(n.node)}"
        )


def _print_related(rows: list[Related]) -> None:
    for r in rows:
        band = r.edge.resolution.value if r.edge.resolution else "-"
        line = f"{r.edge.path}:{r.edge.line}" if r.edge.line else _where(r.node)
        typer.echo(f"  {r.edge.confidence:.2f}  {band:<14}  {r.node.qualified_name}  {line}")


@graph_app.command()
def callers(
    symbol: Annotated[str, typer.Argument(help="Qualified name, short name or dotted suffix.")],
    path: RepoPath,
    repo_id: RepoId = None,
    min_confidence: MinConfidence = None,
) -> None:
    """Functions that call a symbol, with confidence and how each call was resolved."""
    store, rid, _ = _index(path, repo_id)
    node = _pick(store, rid, symbol)
    typer.echo(f"{node.qualified_name}  {_where(node)}")
    _print_related(store.callers(rid, node.qualified_name, min_confidence=min_confidence))


@graph_app.command()
def callees(
    symbol: Annotated[str, typer.Argument(help="Qualified name, short name or dotted suffix.")],
    path: RepoPath,
    repo_id: RepoId = None,
    min_confidence: MinConfidence = None,
) -> None:
    """Functions and classes a symbol calls."""
    store, rid, _ = _index(path, repo_id)
    node = _pick(store, rid, symbol)
    typer.echo(f"{node.qualified_name}  {_where(node)}")
    _print_related(store.callees(rid, node.qualified_name, min_confidence=min_confidence))


@graph_app.command()
def tests(
    symbol: Annotated[str, typer.Argument(help="Qualified name, short name or dotted suffix.")],
    path: RepoPath,
    repo_id: RepoId = None,
    min_confidence: MinConfidence = None,
) -> None:
    """Test functions that call a symbol directly."""
    store, rid, _ = _index(path, repo_id)
    node = _pick(store, rid, symbol)
    typer.echo(f"{node.qualified_name}  {_where(node)}")
    _print_related(store.tests_for(rid, node.qualified_name, min_confidence=min_confidence))
