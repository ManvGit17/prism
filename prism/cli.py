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
    from collections.abc import AsyncIterator

    from prism.github.worker import AppWorker, PullJob
    from prism.indexer.embedder import Embedder
    from prism.schemas import ContextMode
    from prism.store.base import VectorReader
    from prism.store.reviews import ReviewStore

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
        typer.Option("--verifier", help="handoff, api or rules. Default PRISM_VERIFIER."),
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
    save: Annotated[
        bool,
        typer.Option("--save/--no-save", help="Store the review in Postgres (PRISM_REVIEW_STORE)."),
    ] = True,
) -> None:
    """Review a diff and print a validated PRReview as JSON.

    With the handoff verifier (the default) the first run stops with exit code 2 after writing
    verifier requests into the work directory; a Claude model writes the answers (see the rubrics
    there), and running the same command again finishes the review.
    """
    import asyncio
    import logging

    from prism.review.service import ReviewError, ReviewRequest, review_async

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    def note(msg: str) -> None:
        typer.echo(msg, err=True)

    request = ReviewRequest(
        repo=repo,
        diff_text=Path(diff).read_text(encoding="utf-8"),
        repo_id=repo_id,
        pr_number=pr_number,
        work=work,
        verifier=verifier,
        model=model,
        max_hunks=max_hunks,
        time_limit_s=time_limit,
        store=store,
        mode=mode,
        sast=not no_sast,
        save=save,
    )
    try:
        outcome = asyncio.run(review_async(request, notes=note))
    except ReviewError as exc:
        note(str(exc))
        raise typer.Exit(exc.code) from exc

    if outcome.prompts:
        note(outcome.prompts)
    if outcome.trace_url:
        note(f"trace: {outcome.trace_url}")
    if outcome.stored is not None:
        stored = outcome.stored
        note(f"saved review {stored.id} ({stored.status}, {len(stored.findings)} findings)")
    for err in outcome.errors:
        note(f"verifier: {err}")
    if outcome.status == "incomplete":
        note(f"{outcome.pending} answer(s) missing from the API; see the errors above.")
        raise typer.Exit(4)
    if outcome.status == "awaiting_verification":
        note(
            f"{outcome.pending} answer(s) needed from Claude in {outcome.verify_dir}: verdicts_NN"
            ".jsonl for requests_NN.jsonl (RUBRIC.md) and reviews_NN.jsonl for "
            "escalations_NN.jsonl (REVIEW_RUBRIC.md). Then run the same command again."
        )
        raise typer.Exit(2)
    payload = outcome.review.model_dump_json(indent=2)
    if out is not None:
        out.write_text(payload + "\n", encoding="utf-8")
    typer.echo(payload)


prompts_app = typer.Typer(help="Prompts in Langfuse prompt management.", no_args_is_help=True)
app.add_typer(prompts_app, name="prompts")


@prompts_app.command("push")
def prompts_push(
    label: Annotated[
        str, typer.Option("--label", help="Label for the new versions, e.g. staging.")
    ] = "staging",
) -> None:
    """Copy the prompts in the code to Langfuse as new versions with LABEL."""
    from prism.observability.prompts import push
    from prism.observability.tracing import LangfuseTracer

    client = LangfuseTracer.from_settings(get_settings()).client
    if not client.auth_check():
        typer.echo("Langfuse rejected the keys; check PRISM_LANGFUSE_* and the server.", err=True)
        raise typer.Exit(3)
    for line in push(client, label):
        typer.echo(line)


@app.command("mcp")
def mcp_server() -> None:
    """Serve PRism's review tools over MCP on stdio (needs `uv sync --group mcp`)."""
    try:
        from prism.mcp_server import main
    except ImportError as exc:
        typer.echo("prism mcp needs the mcp group: uv sync --group mcp", err=True)
        raise typer.Exit(3) from exc
    main()


app_cmd = typer.Typer(help="The GitHub App (needs `uv sync --group app`).", no_args_is_help=True)
app.add_typer(app_cmd, name="app")


def _worker() -> "tuple[AppWorker, ReviewStore]":
    import logging

    from prism.github.api import AppAuth, GitHubError
    from prism.github.worker import AppWorker
    from prism.mcp_server import PrismTools

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    s = get_settings()
    try:
        auth = AppAuth.from_settings(s)
    except (GitHubError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(3) from exc
    store = PrismTools(s).store  # Postgres when it is up, else in memory
    return AppWorker(auth, s, store), store


@app_cmd.command("serve")
def app_serve(
    port: Annotated[int, typer.Option("--port")] = 8080,
    poll: Annotated[
        bool,
        typer.Option("--poll/--no-poll", help="Also poll open PRs (PRISM_APP_POLL_INTERVAL_S)."),
    ] = False,
) -> None:
    """Receive webhooks on 127.0.0.1:PORT/webhook and review PRs; held reviews are posted once
    their verification is answered (`prism app resume`, or every poll)."""
    import asyncio
    import contextlib

    import uvicorn

    from prism.github.webhook import create_app

    worker, _ = _worker()
    s = get_settings()
    if not s.github_webhook_secret.get_secret_value():
        typer.echo("set PRISM_GITHUB_WEBHOOK_SECRET in .env", err=True)
        raise typer.Exit(3)
    enqueue = None
    if s.app_queue == "celery":
        from prism.github.queue import RedisCoordinator, enqueue_job, review_pull

        coord = RedisCoordinator.from_url(s.redis_url)

        def enqueue(job: "PullJob") -> None:
            enqueue_job(coord, job, review_pull.delay)

        typer.echo("reviews go to the Celery queue; run `prism app worker`", err=True)
    web = create_app(worker, s.github_webhook_secret.get_secret_value(), enqueue)

    async def poller() -> None:
        while True:
            for key, result in (await worker.poll_once() | await worker.resume()).items():
                typer.echo(f"{key}: {result}", err=True)
            await asyncio.sleep(s.app_poll_interval_s)

    @contextlib.asynccontextmanager
    async def lifespan(_: object) -> "AsyncIterator[None]":
        task = asyncio.create_task(poller()) if poll else None
        yield
        if task is not None:
            task.cancel()

    web.router.lifespan_context = lifespan
    uvicorn.run(web, host="127.0.0.1", port=port, log_level="info")


@app_cmd.command("poll")
def app_poll(
    once: Annotated[bool, typer.Option("--once", help="One pass, then exit.")] = False,
) -> None:
    """Review open PRs on the installed repos that have no review at their head commit."""
    import asyncio

    worker, _ = _worker()
    s = get_settings()

    async def run() -> None:
        while True:
            for key, result in (await worker.poll_once() | await worker.resume()).items():
                typer.echo(f"{key}: {result}")
            if once:
                return
            await asyncio.sleep(s.app_poll_interval_s)

    asyncio.run(run())


@app_cmd.command("resume")
def app_resume() -> None:
    """Post held reviews whose verification has been answered since."""
    import asyncio

    worker, _ = _worker()
    for key, result in asyncio.run(worker.resume()).items():
        typer.echo(f"{key}: {result}")


@app_cmd.command("worker")
def app_worker(
    concurrency: Annotated[int, typer.Option("--concurrency", min=1)] = 1,
) -> None:
    """Run a Celery worker for queued reviews (PRISM_APP_QUEUE=celery). On Windows it uses the
    solo pool, since Celery's default prefork pool does not run there."""
    import sys

    from prism.github.queue import celery_app

    pool = "solo" if sys.platform == "win32" else "prefork"
    args = ["worker", "--loglevel=INFO", f"--pool={pool}", "-Q", "prism-reviews"]
    if pool != "solo":
        args.append(f"--concurrency={concurrency}")
    celery_app.worker_main(args)


@app_cmd.command("beat")
def app_beat() -> None:
    """Run Celery beat: posts answered held reviews every minute, and polls for open PRs when
    PRISM_APP_POLL_WITH_BEAT is on."""
    from prism.github.queue import celery_app

    celery_app.start(["beat", "--loglevel=INFO", "--schedule", ".prism-work/celerybeat-schedule"])


@app_cmd.command("feedback")
def app_feedback() -> None:
    """Read reactions on PRism's recent comments and send changes to Langfuse (one pass)."""
    from prism.github.queue import feedback_collector

    worker, _ = _worker()
    counts = feedback_collector(worker, get_settings()).collect()
    typer.echo(", ".join(f"{k} {v}" for k, v in counts.items()))


@app_cmd.command("feedback-report")
def app_feedback_report(
    days: Annotated[int, typer.Option("--days", help="Comments posted in the last N days.")] = 90,
    target: Annotated[float, typer.Option("--target", help="Useful share to aim for.")] = 0.8,
    out: Annotated[Path | None, typer.Option("--out", help="Also write the report here.")] = None,
) -> None:
    """How useful PRism's posted comments were, by author, category and confidence, and the
    threshold that would keep the useful share at TARGET (from reactions and resolved threads)."""
    from prism.github.feedback_loop import rated, render, report
    from prism.store.reviews import PostgresReviewStore

    store = PostgresReviewStore.from_settings()
    try:
        text = render(report(rated(store, days), target), days)
    finally:
        store.close()
    if out is not None:
        out.write_text(text, encoding="utf-8")
    typer.echo(text)


@app_cmd.command("feedback-export")
def app_feedback_export(
    out: Annotated[Path, typer.Option("--out", help="JSONL of rated comments.")],
    days: Annotated[int, typer.Option("--days", help="Comments posted in the last N days.")] = 90,
) -> None:
    """Rated comments as JSONL, plus preference pairs (prompt, chosen, rejected) for a DPO
    round on the reviewer, in OUT's folder as <name>.pairs.jsonl."""
    from prism.github.feedback_loop import export, rated
    from prism.store.reviews import PostgresReviewStore

    store = PostgresReviewStore.from_settings()
    try:
        items, pairs = export(rated(store, days), out)
    finally:
        store.close()
    typer.echo(f"{items} rated comment(s) and {pairs} preference pair(s) written next to {out}")


@app.command("dashboard")
def dashboard(
    port: Annotated[int, typer.Option("--port")] = 8090,
) -> None:
    """Serve the dashboard on 127.0.0.1:PORT (needs `uv sync --group app`, and the page built
    with `npm ci && npm run build` in dashboard/)."""
    import uvicorn

    from prism.dashboard import DashboardData, create_dashboard
    from prism.mcp_server import PrismTools

    root = Path(__file__).resolve().parent.parent
    store = PrismTools(get_settings()).store  # Postgres when it is up, else empty in memory
    web = create_dashboard(
        DashboardData(store, root / "eval" / "reports"), root / "dashboard" / "dist"
    )
    typer.echo(f"dashboard on http://127.0.0.1:{port}", err=True)
    uvicorn.run(web, host="127.0.0.1", port=port, log_level="warning")


bench_app = typer.Typer(help="BugBench: planted-defect PRs and recall.", no_args_is_help=True)
app.add_typer(bench_app, name="bench")


@bench_app.command("run")
def bench_run(
    cases: Annotated[list[Path], typer.Argument(help="Case files (TOML) under bugbench/cases.")],
    root: Annotated[
        Path, typer.Option("--root", envvar="PRISM_BENCH_ROOT", help="Clones, worktrees, runs.")
    ] = Path(".bench"),
    run: Annotated[str, typer.Option("--run", help="Folder under ROOT/runs.")] = "slice",
    mode: Annotated[str, typer.Option("--mode", help="Context mode for the verifier.")] = "graph",
    verifier: Annotated[str, typer.Option("--verifier")] = "handoff",
    model: Annotated[str | None, typer.Option("--model")] = None,
    sast: Annotated[bool, typer.Option("--sast/--no-sast")] = True,
    only: Annotated[str | None, typer.Option("--only", help="Comma-separated case ids.")] = None,
    report: Annotated[
        Path | None, typer.Option("--report", help="Also write a Markdown report here.")
    ] = None,
) -> None:
    """Review every case. Exit 2 while Claude's answers are missing (the open requests are in
    one handoff round folder); with everything answered, score and write the results."""
    import asyncio
    import json

    from prism.bench.cases import load
    from prism.bench.run import RunConfig, run_cases
    from prism.review.service import ReviewError

    s = get_settings().model_copy(update={"review_store": "off", "tracing": "off"})
    cfg = RunConfig(root.resolve(), run, mode=mode, verifier=verifier, model=model, sast=sast)
    picked = {x.strip() for x in only.split(",") if x.strip()} if only else None
    try:
        result = asyncio.run(
            run_cases(
                [load(c) for c in cases],
                cfg,
                s,
                notes=lambda m: typer.echo(m, err=True),
                only=picked,
            )
        )
    except ReviewError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(exc.code) from exc
    if result.waiting:
        typer.echo(
            f"{len(result.waiting)} case(s) waiting for Claude; answer the files in "
            f"{result.round_dir} and run again",
            err=True,
        )
        raise typer.Exit(2)
    if report is not None:
        from prism.bench.report import render

        report.write_text(render(result.summary, result.results), encoding="utf-8")
        typer.echo(f"report written to {report}", err=True)
    typer.echo(json.dumps(result.summary, indent=2))


@bench_app.command("sweep")
def bench_sweep(
    cases: Annotated[list[Path], typer.Argument(help="The run's case files.")],
    run: Annotated[str, typer.Option("--run", help="A finished run under ROOT/runs.")],
    thresholds: Annotated[
        str, typer.Option("--thresholds", help="Comma-separated confidence thresholds.")
    ] = "0.3,0.4,0.5,0.6,0.7,0.8,0.9",
    root: Annotated[
        Path, typer.Option("--root", envvar="PRISM_BENCH_ROOT", help="Clones, worktrees, runs.")
    ] = Path(".bench"),
    mode: Annotated[str, typer.Option("--mode", help="Context mode for the verifier.")] = "graph",
    model: Annotated[str | None, typer.Option("--model")] = None,
    sast: Annotated[bool, typer.Option("--sast/--no-sast")] = True,
    only: Annotated[str | None, typer.Option("--only", help="Comma-separated case ids.")] = None,
) -> None:
    """Re-score a finished run at other confidence thresholds from its saved answers (no new
    Claude work). Run it with the run's own settings; writes ROOT/runs/RUN/sweep.json."""
    import asyncio

    from prism.bench.cases import load
    from prism.bench.run import RunConfig
    from prism.bench.sweep import render_sweep, run_sweep

    s = get_settings().model_copy(update={"review_store": "off", "tracing": "off"})
    cfg = RunConfig(root.resolve(), run, mode=mode, verifier="handoff", model=model, sast=sast)
    picked = {x.strip() for x in only.split(",") if x.strip()} if only else None
    ts = [float(x) for x in thresholds.split(",") if x.strip()]
    try:
        sweep = asyncio.run(
            run_sweep(
                [load(c) for c in cases],
                cfg,
                s,
                ts,
                notes=lambda m: typer.echo(m, err=True),
                only=picked,
            )
        )
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(render_sweep([(run, sweep)], f"Threshold sweep: {run}"))


@bench_app.command("sweep-table")
def bench_sweep_table(
    runs: Annotated[list[str], typer.Argument(help="NAME=RUN pairs of swept runs.")],
    report: Annotated[Path, typer.Option("--report", help="Markdown file to write.")],
    title: Annotated[str, typer.Option("--title")] = "BugBench threshold sweep",
    root: Annotated[
        Path, typer.Option("--root", envvar="PRISM_BENCH_ROOT", help="Clones, worktrees, runs.")
    ] = Path(".bench"),
) -> None:
    """The sweeps of several runs side by side."""
    import json

    from prism.bench.sweep import render_sweep

    cols = []
    for pair in runs:
        name, _, run = pair.partition("=")
        path = root.resolve() / "runs" / (run or name) / "sweep.json"
        cols.append((name, json.loads(path.read_text("utf-8"))))
    out = render_sweep(cols, title)
    report.write_text(out, encoding="utf-8", newline="\n")
    typer.echo(out)


@bench_app.command("judge")
def bench_judge(
    cases: Annotated[list[Path], typer.Argument(help="Case files the runs used.")],
    runs: Annotated[str, typer.Option("--runs", help="Comma-separated finished runs.")],
    root: Annotated[
        Path, typer.Option("--root", envvar="PRISM_BENCH_ROOT", help="Clones, worktrees, runs.")
    ] = Path(".bench"),
) -> None:
    """Judge every comment the runs posted: precision, and whether it names the defect. Exit 2
    while verdicts are missing (the open requests are in ROOT/judge/round_NN)."""
    import json

    from prism.bench.cases import load
    from prism.bench.judge import judge_runs

    names = [r.strip() for r in runs.split(",") if r.strip()]
    out, round_dir = judge_runs([load(c) for c in cases], root.resolve(), names)
    if round_dir is not None:
        typer.echo(f"comments waiting for the judge; answer the files in {round_dir}", err=True)
        raise typer.Exit(2)
    typer.echo(json.dumps(out, indent=2))


@bench_app.command("all")
def bench_all(
    config: Annotated[Path, typer.Argument(help="The suite, e.g. bugbench/configs.toml.")] = Path(
        "bugbench/configs.toml"
    ),
    root: Annotated[
        Path, typer.Option("--root", envvar="PRISM_BENCH_ROOT", help="Clones, worktrees, runs.")
    ] = Path(".bench"),
    only: Annotated[
        str | None, typer.Option("--only", help="Comma-separated run folders to run, in order.")
    ] = None,
    pr_agent: Annotated[
        Path | None, typer.Option("--pr-agent", help="The pr-agent executable.")
    ] = None,
) -> None:
    """Run every configuration in CONFIG, judge the comments and write the report. Exit 2 while
    Claude answers are missing, listing the handoff rounds to answer."""
    import asyncio

    from prism.bench.suite import load_suite, run_suite

    suite = load_suite(config)
    if only:
        wanted = [x.strip() for x in only.split(",") if x.strip()]
        by_run = {r.run: r for r in suite.runs}
        suite.runs = [by_run[w] for w in wanted]
    base = root.resolve()
    exe = pr_agent or base / "envs" / "pr-agent" / (
        "Scripts/pr-agent.exe" if sys.platform == "win32" else "bin/pr-agent"
    )
    waiting, text = asyncio.run(
        run_suite(
            suite, base, get_settings(), pr_agent=exe, notes=lambda m: typer.echo(m, err=True)
        )
    )
    if waiting:
        typer.echo("Claude answers missing; answer these rounds and run again:", err=True)
        for rd in waiting:
            typer.echo(f"  {rd}", err=True)
        raise typer.Exit(2)
    typer.echo(text or "")
    typer.echo(f"report written to {suite.report}", err=True)


@bench_app.command("experiments")
def bench_experiments(
    cases: Annotated[list[Path], typer.Argument(help="Case files the runs used.")],
    runs: Annotated[str, typer.Option("--runs", help="Comma-separated NAME=RUN pairs.")],
    root: Annotated[
        Path, typer.Option("--root", envvar="PRISM_BENCH_ROOT", help="Clones, worktrees, runs.")
    ] = Path(".bench"),
) -> None:
    """Publish finished runs to Langfuse as experiments over a `bugbench` dataset (needs
    `uv sync --group observability` and the `langfuse` Compose profile)."""
    from langfuse import Langfuse

    from prism.bench.cases import load
    from prism.bench.experiments import publish

    s = get_settings()
    client = Langfuse(
        public_key=s.langfuse_public_key,
        secret_key=s.langfuse_secret_key.get_secret_value(),
        base_url=s.langfuse_host,
    )
    pairs = []
    for pair in runs.split(","):
        name, _, run = pair.strip().partition("=")
        pairs.append((name, run or name))
    done = publish(client, [load(c) for c in cases], root.resolve(), pairs)
    typer.echo(f"{len(done)} experiment(s) published to {s.langfuse_host}: {', '.join(done)}")


@bench_app.command("generate")
def bench_generate(
    repo: Annotated[str, typer.Argument(help="A repo name from bugbench/repos.toml.")],
    out: Annotated[Path, typer.Option("--out", help="Case file to write.")],
    injected: Annotated[int, typer.Option("--injected")] = 15,
    clean: Annotated[int, typer.Option("--clean")] = 4,
    window: Annotated[int, typer.Option("--window", help="Recent commits to draw from.")] = 150,
    seed: Annotated[int, typer.Option("--seed")] = 7,
    root: Annotated[
        Path, typer.Option("--root", envvar="PRISM_BENCH_ROOT", help="Clones, worktrees, runs.")
    ] = Path(".bench"),
    repos: Annotated[Path, typer.Option("--repos")] = Path("bugbench/repos.toml"),
    extend: Annotated[
        list[Path] | None,
        typer.Option("--extend", help="Existing case files of REPO: skip their commits."),
    ] = None,
) -> None:
    """Mutate recent commits of REPO and keep the mutations a test catches (ADR 0021)."""
    import re
    from collections import Counter

    from prism.bench.cases import load
    from prism.bench.mutate import generate, load_repos, write_cases

    spec = load_repos(repos)[repo]
    note = lambda m: typer.echo(m, err=True)  # noqa: E731
    old = [c for f in extend or [] for c in load(f).cases]

    def after(tag: str) -> int:
        nums = [int(m.group(1)) for c in old if (m := re.fullmatch(rf".+-{tag}(\d+)", c.id))]
        return max(nums, default=0) + 1

    got = generate(
        root.resolve(),
        spec,
        injected=injected,
        clean=clean,
        window=window,
        seed=seed,
        notes=note,
        used=[c.commit for c in old],
        kinds_used=Counter(c.kind for c in old if c.group == "injected"),
        first_injected=after("i"),
        first_clean=after("c"),
        progress=root.resolve() / "gen" / f"{out.stem}.progress.jsonl",
    )
    header = (
        f"BugBench cases for {repo}, from `prism bench generate` (seed {seed}, window {window}).\n"
        "Injected cases: one mutation on a line the commit added; the repo's tests pass at the\n"
        "commit and `failing` fails with the mutation. Clean cases: the commit as it is."
    )
    write_cases(out, spec, [*got.injected, *got.clean], header)
    typer.echo(
        f"{len(got.injected)} injected, {len(got.clean)} clean written to {out}; "
        f"skipped: {dict(got.skipped)}",
        err=True,
    )


@bench_app.command("real")
def bench_real(
    repo: Annotated[str, typer.Argument(help="A repo name from bugbench/repos.toml.")],
    out: Annotated[Path, typer.Option("--out", help="JSONL file of candidates to curate.")],
    window: Annotated[int, typer.Option("--window", help="Recent commits to search.")] = 300,
    root: Annotated[
        Path, typer.Option("--root", envvar="PRISM_BENCH_ROOT", help="Clones, worktrees, runs.")
    ] = Path(".bench"),
    repos: Annotated[Path, typer.Option("--repos")] = Path("bugbench/repos.toml"),
    extend: Annotated[
        list[Path] | None,
        typer.Option("--extend", help="Existing case files of REPO: skip their commits."),
    ] = None,
) -> None:
    """Real-bug candidates for REPO (SZZ: a fix blamed back to the commit that introduced it),
    to read and curate by hand into a case file."""
    import json
    from dataclasses import asdict

    from prism.bench.cases import load
    from prism.bench.mutate import load_repos
    from prism.bench.realbugs import candidates

    spec = load_repos(repos)[repo]
    taken = {c.commit[:7] for f in extend or [] for c in load(f).cases}
    found = [
        c for c in candidates(root.resolve() / spec.name, spec, window) if c.introduced not in taken
    ]
    out.write_text(
        "".join(json.dumps(asdict(c)) + "\n" for c in found), encoding="utf-8", newline="\n"
    )
    typer.echo(f"{len(found)} candidate(s) written to {out}", err=True)


@bench_app.command("baseline")
def bench_baseline(
    cases: Annotated[list[Path], typer.Argument(help="Case files (TOML) under bugbench/cases.")],
    root: Annotated[
        Path, typer.Option("--root", envvar="PRISM_BENCH_ROOT", help="Clones, worktrees, runs.")
    ] = Path(".bench"),
    run: Annotated[str, typer.Option("--run", help="Folder under ROOT/runs.")] = "pr-agent",
    pr_agent: Annotated[
        Path | None, typer.Option("--pr-agent", help="The pr-agent executable.")
    ] = None,
    only: Annotated[str | None, typer.Option("--only", help="Comma-separated case ids.")] = None,
) -> None:
    """Run PR-Agent on every case with Claude answering through a handoff. Exit 2 while
    prompts are unanswered; with everything answered, score and write the results."""
    import json

    from prism.bench.baseline import run_baseline
    from prism.bench.cases import load

    base = root.resolve()
    exe = pr_agent or base / "envs" / "pr-agent" / (
        "Scripts/pr-agent.exe" if sys.platform == "win32" else "bin/pr-agent"
    )
    picked = {x.strip() for x in only.split(",") if x.strip()} if only else None
    out = run_baseline(
        [load(c) for c in cases],
        base,
        run,
        exe,
        notes=lambda m: typer.echo(m, err=True),
        only=picked,
    )
    if out.waiting:
        typer.echo(
            f"{len(out.waiting)} case(s) waiting for Claude; answer the files in "
            f"{out.round_dir} and run again",
            err=True,
        )
        raise typer.Exit(2)
    typer.echo(json.dumps(out.summary, indent=2))


@bench_app.command("compare")
def bench_compare(
    prism_run: Annotated[str, typer.Argument(help="PRism run under ROOT/runs.")],
    other_run: Annotated[str, typer.Argument(help="Baseline run under ROOT/runs.")],
    report: Annotated[Path, typer.Option("--report", help="Markdown file to write.")],
    name: Annotated[str, typer.Option("--name")] = "PR-Agent",
    root: Annotated[
        Path, typer.Option("--root", envvar="PRISM_BENCH_ROOT", help="Clones, worktrees, runs.")
    ] = Path(".bench"),
) -> None:
    """Compare a PRism run with a baseline run on the same cases."""
    import glob
    import json
    import os

    from prism.bench.report import render_comparison

    runs = root.resolve() / "runs"

    def rows(run: str) -> list[dict[str, object]]:
        text = (runs / run / "results.jsonl").read_text(encoding="utf-8")
        return [json.loads(x) for x in text.splitlines() if x.strip()]

    def volume(patterns: list[str]) -> dict[str, float]:
        files = [f for p in patterns for f in glob.glob(str(runs / p))]
        calls = sum(
            sum(1 for line in Path(f).read_text(encoding="utf-8").splitlines() if line.strip())
            for f in files
        )
        return {"calls": calls, "mb": sum(os.path.getsize(f) for f in files) / 1e6}

    claude = {
        "prism": volume(
            [
                f"{prism_run}/handoff/round_*/requests_*.jsonl",
                f"{prism_run}/handoff/round_*/escalations_*.jsonl",
            ]
        ),
        "other": volume([f"{other_run}/llm/rounds/round_*/prompts_*.jsonl"]),
    }
    text = render_comparison(prism_run, rows(prism_run), other_run, rows(other_run), name, claude)
    report.write_text(text, encoding="utf-8", newline="\n")
    typer.echo(text)


@bench_app.command("table")
def bench_table(
    runs: Annotated[list[str], typer.Argument(help="NAME=RUN pairs, one column each.")],
    report: Annotated[Path, typer.Option("--report", help="Markdown file to write.")],
    title: Annotated[str, typer.Option("--title")] = "BugBench ablations",
    root: Annotated[
        Path, typer.Option("--root", envvar="PRISM_BENCH_ROOT", help="Clones, worktrees, runs.")
    ] = Path(".bench"),
) -> None:
    """A side-by-side table of finished runs (PRism or PR-Agent) on their shared cases."""
    import json

    from prism.bench.report import render_table

    cols = []
    for pair in runs:
        name, _, run = pair.partition("=")
        text = (root.resolve() / "runs" / (run or name) / "results.jsonl").read_text("utf-8")
        cols.append((name, [json.loads(x) for x in text.splitlines() if x.strip()]))
    out = render_table(cols, title)
    report.write_text(out, encoding="utf-8", newline="\n")
    typer.echo(out)
