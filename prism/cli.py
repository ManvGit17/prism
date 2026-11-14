"""`prism` command line.

`prism index` writes to the persistent stores (Neo4j, pgvector, the ledger)
when `PRISM_STORE=persistent` and they are reachable, else to memory. The graph
commands read from the persistent store, or with `--path` index that folder in
memory first and answer from it.
"""

import io
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from prism.config import get_settings
from prism.indexer.model import Node
from prism.indexer.pipeline import IndexReport, index_repository
from prism.store import GraphReader, GraphStats, InMemoryGraphStore, Related
from prism.store.factory import StoreKind, Stores, StoreUnavailable, open_stores

if TYPE_CHECKING:
    from prism.indexer.embedder import Embedder
    from prism.schemas import ContextMode
    from prism.store.base import VectorReader

app = typer.Typer(help="PRism: repo-aware code review.", no_args_is_help=True)
graph_app = typer.Typer(
    help=(
        "Query the code graph. Reads the persistent store by default; with --path, indexes "
        "that folder in memory first and answers from that."
    ),
    no_args_is_help=True,
)
app.add_typer(graph_app, name="graph")

RepoPath = Annotated[
    Path | None,
    typer.Option(
        "--path",
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Index this folder in memory and query that instead of the persistent store.",
    ),
]
RepoId = Annotated[
    str | None,
    typer.Option(
        "--repo-id",
        help="Repo id. Defaults to the folder name, or the only repo in the persistent store.",
    ),
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
Symbol = Annotated[str, typer.Argument(help="Qualified name, short name or dotted suffix.")]


@app.callback()
def _main() -> None:
    # File names can hold characters a redirected Windows console cannot encode.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="backslashreplace")


def _format_stats(stats: GraphStats, report: IndexReport | None) -> list[str]:
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
    ]
    if report is not None:
        lines.append(
            f"parse errors in {len(report.files_with_errors)} files; "
            f"{len(report.skipped)} files skipped for size; {report.seconds:.2f}s"
        )
        lines.extend(f"  parse error: {p}" for p in report.files_with_errors)
    return lines


def _where(node: Node) -> str:
    return f"{node.path}:{node.start_line}"


def _pick(graph: GraphReader, repo_id: str, symbol: str) -> Node:
    matches = graph.lookup(repo_id, symbol)
    if not matches:
        typer.echo(f"no symbol matches {symbol!r} in repo {repo_id!r}", err=True)
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
    store: Annotated[
        str | None,
        typer.Option(
            "--store",
            help="memory or persistent. Defaults to PRISM_STORE; persistent falls back to "
            "memory when Neo4j or Postgres is unreachable.",
        ),
    ] = None,
    embed: Annotated[
        bool, typer.Option("--embed/--no-embed", help="Embed functions into pgvector.")
    ] = True,
) -> None:
    """Index a repo and print graph stats. The commit comes from git, else "working-tree"."""
    rid = repo_id or path.resolve().name
    kind: StoreKind = "memory" if (store or get_settings().store) == "memory" else "persistent"
    try:
        stores = open_stores(kind, with_vectors=embed)
    except StoreUnavailable as exc:
        typer.echo(f"persistent store unavailable, indexing in memory: {exc}", err=True)
        stores = open_stores("memory")
    try:
        report = index_repository(
            path,
            rid,
            stores.graph,
            vectors=stores.vectors,
            embedder=stores.embedder,
            ledger=stores.ledger,
        )
        for line in _format_stats(stores.graph.stats(rid), report):
            typer.echo(line)
        typer.echo(f"store: {stores.kind}; commit {report.commit_sha}; run {report.run_id}")
        if stores.vectors is not None:
            typer.echo(f"vectors: {report.vectors} written, {stores.vectors.count(rid)} stored")
    finally:
        stores.close()


@contextmanager
def _context_sources(
    repo: Path, rid: str, mode: "ContextMode", store: str | None
) -> "Iterator[tuple[GraphReader, VectorReader | None, Embedder | None]]":
    """Base graph, vectors and embedder for retrieval, from the persistent store or by indexing
    the checkout in memory. Exits with code 3 when the persistent store is down."""
    from prism.indexer.embedder import OllamaEmbedder
    from prism.store import InMemoryVectorStore

    kind: StoreKind = "memory" if (store or get_settings().store) == "memory" else "persistent"
    if kind == "persistent":
        try:
            stores = open_stores("persistent", with_vectors=mode.uses_vector)
        except StoreUnavailable as exc:
            typer.echo(f"{exc}\nStart the services (make up) or pass --store memory.", err=True)
            raise typer.Exit(3) from exc
        try:
            if stores.ledger is not None and stores.ledger.latest_complete_run(rid) is None:
                typer.echo(
                    f"note: {rid!r} has no complete index run; run prism index first", err=True
                )
            yield stores.graph, stores.vectors, stores.embedder
        finally:
            stores.close()
        return
    memory = InMemoryGraphStore()
    embedder = OllamaEmbedder() if mode.uses_vector else None
    mem_vectors = InMemoryVectorStore() if embedder is not None else None
    if embedder is not None:
        typer.echo("note: embedding the whole repo in memory for vector context", err=True)
    index_repository(repo, rid, memory, vectors=mem_vectors, embedder=embedder)
    yield memory, mem_vectors, embedder


@contextmanager
def _graph(path: Path | None, repo_id: str | None) -> Iterator[tuple[GraphReader, str]]:
    """The graph to query and the repo id, from --path (memory) or the persistent store."""
    if path is not None:
        rid = repo_id or path.resolve().name
        memory = InMemoryGraphStore()
        index_repository(path, rid, memory)
        yield memory, rid
        return
    try:
        stores: Stores = open_stores("persistent", with_vectors=False)
    except StoreUnavailable as exc:
        typer.echo(f"{exc}\nStart the services (make up) or pass --path.", err=True)
        raise typer.Exit(3) from exc
    try:
        rid = repo_id or _only_repo(stores.graph.repo_ids())
        if stores.ledger is not None:
            if stores.ledger.index_in_progress(rid):
                typer.echo(f"note: an index run for {rid!r} is in progress", err=True)
            elif stores.ledger.latest_complete_run(rid) is None:
                typer.echo(f"note: {rid!r} has no complete index run", err=True)
        yield stores.graph, rid
    finally:
        stores.close()


def _only_repo(repos: list[str]) -> str:
    if len(repos) == 1:
        return repos[0]
    listing = ", ".join(repos) if repos else "none"
    typer.echo(f"pass --repo-id; repos in the store: {listing}", err=True)
    raise typer.Exit(2)


@graph_app.command()
def neighbours(
    symbol: Symbol,
    path: RepoPath = None,
    hops: Annotated[int, typer.Option("--hops", min=1, max=5)] = 1,
    repo_id: RepoId = None,
    min_confidence: MinConfidence = None,
) -> None:
    """Nodes within --hops edges of a symbol, in either direction."""
    with _graph(path, repo_id) as (graph, rid):
        node = _pick(graph, rid, symbol)
        typer.echo(f"{node.qualified_name}  {_where(node)}")
        found = graph.neighbours(rid, node.qualified_name, hops=hops, min_confidence=min_confidence)
        for n in found:
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
    symbol: Symbol,
    path: RepoPath = None,
    repo_id: RepoId = None,
    min_confidence: MinConfidence = None,
) -> None:
    """Functions that call a symbol, with confidence and how each call was resolved."""
    with _graph(path, repo_id) as (graph, rid):
        node = _pick(graph, rid, symbol)
        typer.echo(f"{node.qualified_name}  {_where(node)}")
        _print_related(graph.callers(rid, node.qualified_name, min_confidence=min_confidence))


@graph_app.command()
def callees(
    symbol: Symbol,
    path: RepoPath = None,
    repo_id: RepoId = None,
    min_confidence: MinConfidence = None,
) -> None:
    """Functions and classes a symbol calls."""
    with _graph(path, repo_id) as (graph, rid):
        node = _pick(graph, rid, symbol)
        typer.echo(f"{node.qualified_name}  {_where(node)}")
        _print_related(graph.callees(rid, node.qualified_name, min_confidence=min_confidence))


@graph_app.command()
def tests(
    symbol: Symbol,
    path: RepoPath = None,
    repo_id: RepoId = None,
    min_confidence: MinConfidence = None,
) -> None:
    """Test functions that call a symbol directly."""
    with _graph(path, repo_id) as (graph, rid):
        node = _pick(graph, rid, symbol)
        typer.echo(f"{node.qualified_name}  {_where(node)}")
        _print_related(graph.tests_for(rid, node.qualified_name, min_confidence=min_confidence))


@app.command()
def context(
    repo: Annotated[
        Path,
        typer.Argument(
            exists=True, file_okay=False, dir_okay=True, help="Checkout of the PR head commit."
        ),
    ],
    diff: Annotated[str, typer.Argument(help="Unified diff file, or - for stdin.")],
    repo_id: RepoId = None,
    mode: Annotated[
        str | None,
        typer.Option(
            "--mode", help="none, vector, graph or graph+vector. Default PRISM_CONTEXT_MODE."
        ),
    ] = None,
    budget: Annotated[int | None, typer.Option("--budget", min=50, help="Tokens per hunk.")] = None,
    hops: Annotated[int | None, typer.Option("--hops", min=1, max=2)] = None,
    store: Annotated[
        str | None,
        typer.Option(
            "--store",
            help="persistent: base graph from Neo4j/pgvector for the repo id. memory: index "
            "this tree in memory, so the base equals the head (deleted symbols lose their "
            "callers, and vector modes embed the whole repo first).",
        ),
    ] = None,
    reranker: Annotated[
        str | None, typer.Option("--reranker", help="cross-encoder, overlap or none.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the bundle as JSON.")] = False,
    render: Annotated[
        bool, typer.Option("--render", help="Print the text block a prompt would include.")
    ] = False,
) -> None:
    """Ranked context for each hunk of a diff, with token counts."""
    from prism.retrieval.context import ContextOptions, build_context
    from prism.retrieval.render import render_bundle
    from prism.retrieval.rerank import make_reranker
    from prism.retrieval.tokens import token_counter
    from prism.schemas import ContextMode

    text = sys.stdin.read() if diff == "-" else Path(diff).read_text(encoding="utf-8")
    try:
        context_mode = ContextMode(mode or get_settings().context_mode)
    except ValueError as exc:
        raise typer.BadParameter(f"unknown mode {mode!r}") from exc
    options = ContextOptions.from_settings(mode=context_mode, budget=budget, hops=hops)
    rid = repo_id or repo.resolve().name
    with _context_sources(repo, rid, context_mode, store) as (graph, vectors, embedder):
        bundle = build_context(
            repo,
            rid,
            text,
            graph,
            options,
            vectors=vectors,
            embedder=embedder,
            reranker=make_reranker(reranker),
            counter=token_counter(),
        )
    if as_json:
        typer.echo(bundle.model_dump_json(indent=2))
        return
    if render:
        typer.echo(render_bundle(bundle))
        return
    typer.echo(
        f"repo {bundle.repo_id}; mode {bundle.mode.value}; counter {bundle.token_counter}; "
        f"reranker {bundle.reranker}"
    )
    for skipped in bundle.skipped_files:
        typer.echo(f"skipped binary file {skipped}")
    for h in bundle.hunks:
        typer.echo(
            f"\n{h.path} {h.hunk_header}\n  target {h.target}; "
            f"symbols {', '.join(h.symbols) or '-'}"
            + (f"; deleted {', '.join(h.deleted_symbols)}" if h.deleted_symbols else "")
            + f"\n  tokens {h.tokens_used}/{h.budget}; {len(h.items)} items, "
            f"{len(h.dropped)} dropped"
        )
        for rank, item in enumerate(h.items, 1):
            cut = " cut" if item.truncated else ""
            typer.echo(
                f"  {rank:>2} {item.kind.value:<14} {item.score:7.3f} {item.tokens:>5}t{cut}  "
                f"{item.qualified_name}  {item.path}:{item.start_line}-{item.end_line}"
                f"\n       {item.reason}"
            )
        for d in h.dropped:
            typer.echo(f"   - dropped {d.kind.value} {d.qualified_name}: {d.reason}")


@app.command()
def review(
    repo: Annotated[
        Path,
        typer.Option(
            "--repo",
            exists=True,
            file_okay=False,
            dir_okay=True,
            help="Checkout of the PR head commit.",
        ),
    ],
    diff: Annotated[str, typer.Option("--diff", help="Unified diff file.")],
    out: Annotated[
        Path | None, typer.Option("--out", help="Write the PRReview JSON here too.")
    ] = None,
    work: Annotated[
        Path | None,
        typer.Option(
            "--work",
            help="Work directory for cached replies and verifier batches. Default "
            "PRISM_REVIEW_WORK_DIR/<repo>-<sha>-<diff hash>.",
        ),
    ] = None,
    verifier: Annotated[
        str | None,
        typer.Option("--verifier", help="handoff or rules. Default PRISM_VERIFIER."),
    ] = None,
    model: Annotated[
        str | None, typer.Option("--model", help="Reviewer model. Default PRISM_REVIEWER_MODEL.")
    ] = None,
    max_hunks: Annotated[
        int | None, typer.Option("--max-hunks", min=1, help="Default PRISM_REVIEW_MAX_HUNKS.")
    ] = None,
    time_limit: Annotated[
        float | None,
        typer.Option("--time-limit", min=1, help="Seconds. Default PRISM_REVIEW_TIME_LIMIT_S."),
    ] = None,
    store: Annotated[
        str | None,
        typer.Option("--store", help="persistent or memory, for the verifier's context."),
    ] = None,
    mode: Annotated[
        str | None,
        typer.Option("--mode", help="Context mode for the verifier. Default PRISM_CONTEXT_MODE."),
    ] = None,
    repo_id: RepoId = None,
    pr_number: Annotated[int | None, typer.Option("--pr", min=1)] = None,
    no_sast: Annotated[
        bool, typer.Option("--no-sast", help="Skip static analysis (PRISM_SAST_TOOLS).")
    ] = False,
) -> None:
    """Review a diff and print a validated PRReview as JSON.

    With the handoff verifier (the default) the first run stops with exit code 2 after writing
    verifier requests into the work directory; a Claude model writes the verdicts (see
    verify/RUBRIC.md there), and running the same command again finishes the review.
    """
    import asyncio
    import hashlib
    import logging
    from collections.abc import Sequence
    from contextlib import nullcontext

    from prism.models.gate import GateError, load_gate
    from prism.retrieval.context import ContextOptions, build_context
    from prism.retrieval.render import render_hunk
    from prism.retrieval.rerank import make_reranker
    from prism.retrieval.tokens import token_counter
    from prism.review.checks import RuleVerifier
    from prism.review.graph import ReviewOptions, Verifier, head_sha, run_review
    from prism.review.handoff import HandoffVerifier, ReplyCache
    from prism.review.plan import HunkTask
    from prism.review.reviewer_client import OllamaReviewer
    from prism.review.sast import SastReport, default_tools, run_sast
    from prism.schemas import ContextMode

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    s = get_settings()
    text = Path(diff).read_text(encoding="utf-8")
    try:
        gate = load_gate(s)
    except GateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(3) from exc
    kind = verifier or s.verifier
    if kind not in ("handoff", "rules"):
        raise typer.BadParameter(f"unknown verifier {kind!r}")
    try:
        context_mode = ContextMode(mode or s.context_mode)
    except ValueError as exc:
        raise typer.BadParameter(f"unknown mode {mode!r}") from exc
    rid = repo_id or repo.resolve().name
    if work is None:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        work = Path(s.review_work_dir) / f"{rid}-{head_sha(repo)[:12]}-{digest}"
    options = ReviewOptions(
        max_hunks=max_hunks or s.review_max_hunks,
        time_limit_s=time_limit or s.review_time_limit_s,
        max_comments=s.review_max_comments,
        concurrency=s.model_concurrency,
        retries=s.reviewer_retries,
        sast_comment_min_severity=s.sast_comment_min_severity,
        # The rules verifier has no model to escalate to.
        escalate=s.escalate and kind == "handoff",
        escalate_min_lines=s.escalate_min_lines,
    )
    names = [] if no_sast else [n.strip() for n in s.sast_tools.split(",") if n.strip()]
    tools = default_tools(names, s.semgrep_config, s.pmd_home)

    def sast_fn(paths: Sequence[str]) -> SastReport:
        return run_sast(repo, paths, tools, timeout_s=s.sast_timeout_s)

    chosen: Verifier
    handoff: HandoffVerifier | None = None
    if kind == "handoff":
        handoff = HandoffVerifier(work, min_confidence=s.verifier_min_confidence)
        chosen = handoff
    else:
        chosen = RuleVerifier()

    # Only the handoff verifier reads context; the rules verifier must not need the stores.
    needs_context = kind == "handoff" and context_mode is not ContextMode.NONE
    sources = (
        _context_sources(repo, rid, context_mode, store)
        if needs_context
        else nullcontext((InMemoryGraphStore(), None, None))
    )
    with sources as (graph, vectors, embedder):

        def contexts(tasks: Sequence[HunkTask]) -> dict[tuple[str, str], str]:
            if not needs_context:
                return {}
            bundle = build_context(
                repo,
                rid,
                text,
                graph,
                ContextOptions.from_settings(mode=context_mode),
                vectors=vectors,
                embedder=embedder,
                reranker=make_reranker(),
                counter=token_counter(),
            )
            wanted = {(t.path, t.hunk.header) for t in tasks}
            return {
                (h.path, h.hunk_header): render_hunk(h)
                for h in bundle.hunks
                if (h.path, h.hunk_header) in wanted
            }

        async def go() -> str:
            reviewer = OllamaReviewer(
                s.ollama_url, model or s.reviewer_model, timeout_s=s.reviewer_timeout_s
            )
            try:
                result, _ = await run_review(
                    repo,
                    text,
                    gate=gate,
                    model=reviewer,
                    verifier=chosen,
                    options=options,
                    repo_name=rid,
                    pr_number=pr_number,
                    cache=ReplyCache(work / "replies.json"),
                    context_fn=contexts,
                    sast_fn=sast_fn,
                )
            finally:
                await reviewer.aclose()
            return result.model_dump_json(indent=2)

        payload = asyncio.run(go())

    if handoff is not None:
        for err in handoff.errors:
            typer.echo(f"bad verdict line {err}", err=True)
        if handoff.pending:
            typer.echo(
                f"{handoff.pending} answer(s) needed from Claude in {handoff.dir}: verdicts_NN"
                ".jsonl for requests_NN.jsonl (RUBRIC.md) and reviews_NN.jsonl for "
                "escalations_NN.jsonl (REVIEW_RUBRIC.md). Then run the same command again.",
                err=True,
            )
            raise typer.Exit(2)
    if out is not None:
        out.write_text(payload + "\n", encoding="utf-8")
    typer.echo(payload)
