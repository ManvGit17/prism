"""One review, start to finish, for every front end: the CLI, the MCP server and the App worker.

`review_async` builds the gate, the reviewer, the verifier, SAST, retrieval, tracing and prompts
from the settings, runs the graph, stores the result and returns a `ReviewOutcome`. It never
prints: notes go to a callback, and failures raise `ReviewError` with an exit code the CLI uses.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from prism.config import Settings, get_settings
from prism.indexer.pipeline import index_repository
from prism.store import InMemoryGraphStore
from prism.store.factory import StoreUnavailable, open_stores

if TYPE_CHECKING:
    from prism.indexer.embedder import Embedder
    from prism.observability.prompts import PromptSource
    from prism.observability.tracing import Tracer
    from prism.review.graph import ReviewState
    from prism.review.handoff import ClaudeVerifier
    from prism.schemas import ContextMode, PRReview
    from prism.store.base import GraphReader, VectorReader
    from prism.store.reviews import ReviewStore, StoredReview
    from prism.store.reviews import Status as ReviewStatus

log = logging.getLogger(__name__)

Notes = Callable[[str], None]
Outcome = Literal["complete", "awaiting_verification", "incomplete"]


class ReviewError(RuntimeError):
    """A review could not run. `code` is the CLI exit code."""

    def __init__(self, message: str, code: int = 3) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ReviewRequest:
    repo: Path
    diff_text: str
    repo_id: str | None = None
    pr_number: int | None = None
    work: Path | None = None
    verifier: str | None = None  # handoff, api or rules; default PRISM_VERIFIER
    model: str | None = None
    max_hunks: int | None = None
    time_limit_s: float | None = None
    store: str | None = None  # persistent or memory, for the verifier's context
    mode: str | None = None  # context mode
    sast: bool = True
    save: bool = True


@dataclass
class ReviewOutcome:
    review: PRReview
    state: ReviewState
    status: Outcome
    work: Path
    verify_dir: Path | None = None  # where handoff requests and answers live
    pending: int = 0
    errors: list[str] = field(default_factory=list)
    stored: StoredReview | None = None
    trace_url: str | None = None
    prompts: str = ""


def work_dir_for(s: Settings, repo_id: str, sha: str, diff_text: str) -> Path:
    digest = hashlib.sha1(diff_text.encode("utf-8")).hexdigest()[:8]
    return Path(s.review_work_dir) / f"{repo_id}-{sha[:12]}-{digest}"


def observability(s: Settings) -> tuple[Tracer, PromptSource]:
    """The tracer and the prompt source the settings ask for."""
    from prism.observability.prompts import PromptSource
    from prism.observability.tracing import LangfuseTracer, NoopTracer

    tracer: Tracer = NoopTracer()
    client = None
    if s.tracing == "langfuse" or s.prompt_source == "langfuse":
        lf = LangfuseTracer.from_settings(s)
        client = lf.client
        if s.tracing == "langfuse":
            tracer = lf
    return tracer, PromptSource(client if s.prompt_source == "langfuse" else None, s.prompt_label)


@contextmanager
def context_sources(
    repo: Path, rid: str, mode: ContextMode, store: str | None, notes: Notes, s: Settings
) -> Iterator[tuple[GraphReader, VectorReader | None, Embedder | None]]:
    """Base graph, vectors and embedder for retrieval, from the persistent store or by indexing
    the checkout in memory."""
    from prism.indexer.embedder import OllamaEmbedder
    from prism.store import InMemoryVectorStore

    kind = "memory" if (store or s.store) == "memory" else "persistent"
    if kind == "persistent":
        try:
            stores = open_stores("persistent", with_vectors=mode.uses_vector)
        except StoreUnavailable as exc:
            raise ReviewError(
                f"{exc}\nStart the services (make up) or use --store memory."
            ) from exc
        try:
            if stores.ledger is not None and stores.ledger.latest_complete_run(rid) is None:
                notes(f"note: {rid!r} has no complete index run; run prism index first")
            yield stores.graph, stores.vectors, stores.embedder
        finally:
            stores.close()
        return
    memory = InMemoryGraphStore()
    embedder = OllamaEmbedder() if mode.uses_vector else None
    mem_vectors = InMemoryVectorStore() if embedder is not None else None
    if embedder is not None:
        notes("note: embedding the whole repo in memory for vector context")
    index_repository(repo, rid, memory, vectors=mem_vectors, embedder=embedder)
    yield memory, mem_vectors, embedder


def save_review(
    s: Settings,
    outcome: ReviewOutcome,
    *,
    key: str,
    trace_id: str | None,
    notes: Notes,
    store: ReviewStore | None = None,
) -> StoredReview | None:
    """Store the review in `store`, else in Postgres; a review never fails because Postgres is
    down."""
    from prism.store.reviews import PostgresReviewStore, stored_review

    status: ReviewStatus = "complete" if outcome.status == "complete" else "awaiting_verification"
    record = stored_review(
        outcome.review,
        key=key,
        status=status,
        decisions=outcome.state.get("decisions", []),
        tools=outcome.state.get("tools", {}),
        trace_id=trace_id,
        work_dir=str(outcome.work),
    )
    if store is not None:
        return store.save(record)
    if s.review_store == "off":
        return None
    import psycopg

    try:
        pg = PostgresReviewStore.from_settings()
    except psycopg.Error as exc:
        notes(f"note: review not saved, Postgres unreachable ({type(exc).__name__})")
        return None
    try:
        return pg.save(record)
    finally:
        pg.close()


async def review_async(
    req: ReviewRequest,
    *,
    settings: Settings | None = None,
    notes: Notes = log.info,
    review_store: ReviewStore | None = None,
) -> ReviewOutcome:
    from prism.models.gate import GateError, load_gate
    from prism.observability.prompts import REVIEW, REVIEWER, VERIFY
    from prism.retrieval.context import ContextOptions, build_context
    from prism.retrieval.render import render_hunk
    from prism.retrieval.rerank import make_reranker
    from prism.retrieval.tokens import token_counter
    from prism.review.checks import RuleVerifier
    from prism.review.graph import ReviewOptions, Verifier, head_sha, run_review
    from prism.review.handoff import ClaudeVerifier, HandoffVerifier, ReplyCache
    from prism.review.plan import HunkTask
    from prism.review.reviewer_client import OllamaReviewer
    from prism.review.sast import SastReport, default_tools, run_sast
    from prism.schemas import ContextMode
    from prism.store.reviews import review_key

    s = settings or get_settings()
    repo, text = req.repo, req.diff_text
    try:
        gate = load_gate(s)
    except GateError as exc:
        raise ReviewError(str(exc)) from exc
    kind = req.verifier or s.verifier
    if kind not in ("handoff", "api", "rules"):
        raise ReviewError(f"unknown verifier {kind!r}", code=2)
    try:
        context_mode = ContextMode(req.mode or s.context_mode)
    except ValueError as exc:
        raise ReviewError(f"unknown context mode {req.mode!r}", code=2) from exc
    rid = req.repo_id or repo.resolve().name
    work = req.work or work_dir_for(s, rid, head_sha(repo), text)
    options = ReviewOptions(
        max_hunks=req.max_hunks or s.review_max_hunks,
        time_limit_s=req.time_limit_s or s.review_time_limit_s,
        max_comments=s.review_max_comments,
        concurrency=s.model_concurrency,
        retries=s.reviewer_retries,
        sast_comment_min_severity=s.sast_comment_min_severity,
        # The rules verifier has no model to escalate to.
        escalate=s.escalate and kind != "rules",
        escalate_min_lines=s.escalate_min_lines,
        route_all=s.route_all and kind != "rules",
    )
    names = [n.strip() for n in s.sast_tools.split(",") if n.strip()] if req.sast else []
    tools = default_tools(names, s.semgrep_config, s.pmd_home)

    def sast_fn(paths: Sequence[str]) -> SastReport:
        return run_sast(repo, paths, tools, timeout_s=s.sast_timeout_s)

    try:
        tracer, prompts = observability(s)
    except ImportError as exc:
        raise ReviewError("PRISM_TRACING=langfuse needs `uv sync --group observability`") from exc
    reviewer_prompt = prompts.get(REVIEWER)
    rubrics = {"rubric": prompts.get(VERIFY).text, "review_rubric": prompts.get(REVIEW).text}
    if s.review_unit == "pr" and not options.route_all:
        raise ReviewError("PRISM_REVIEW_UNIT=pr needs PRISM_ROUTE_ALL=true", code=2)
    whole_pr = s.review_unit == "pr"

    chosen: Verifier
    claude: ClaudeVerifier | None = None
    verify_dir: Path | None = None
    if kind == "handoff":
        handoff = HandoffVerifier(
            work, min_confidence=s.verifier_min_confidence, whole_pr=whole_pr, **rubrics
        )
        claude, chosen, verify_dir = handoff, handoff, handoff.dir
    elif kind == "api":
        try:
            from prism.review.frontier_api import ApiBackend

            backend = ApiBackend(
                s.frontier_model,
                effort=s.frontier_effort,
                price_in_per_mtok=s.frontier_price_in_per_mtok,
                price_out_per_mtok=s.frontier_price_out_per_mtok,
            )
        except ImportError as exc:
            raise ReviewError("the api verifier needs `uv sync --group frontier`") from exc
        claude = ClaudeVerifier(
            backend, min_confidence=s.verifier_min_confidence, whole_pr=whole_pr, **rubrics
        )
        chosen = claude
    else:
        chosen = RuleVerifier()

    # Only a Claude verifier reads context; the rules verifier must not need the stores.
    needs_context = kind != "rules" and context_mode is not ContextMode.NONE
    sources: Any = (
        context_sources(repo, rid, context_mode, req.store, notes, s)
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
                reranker=make_reranker(s.reranker),
                counter=token_counter(s.token_counter),
            )
            wanted = {(t.path, t.hunk.header) for t in tasks}
            return {
                (h.path, h.hunk_header): render_hunk(h)
                for h in bundle.hunks
                if (h.path, h.hunk_header) in wanted
            }

        reviewer = OllamaReviewer(
            s.ollama_url,
            req.model or s.reviewer_model,
            timeout_s=s.reviewer_timeout_s,
            system=reviewer_prompt.text,
        )
        try:
            result, state = await run_review(
                repo,
                text,
                gate=gate,
                model=reviewer,
                verifier=chosen,
                options=options,
                repo_name=rid,
                pr_number=req.pr_number,
                cache=ReplyCache(work / "replies.json"),
                context_fn=contexts,
                sast_fn=sast_fn,
                tracer=tracer,
                reviewer_prompt=reviewer_prompt.client,
            )
        finally:
            await reviewer.aclose()

    pending = claude.pending if claude is not None else 0
    status: Outcome = "complete"
    if pending:
        status = "awaiting_verification" if kind == "handoff" else "incomplete"
    outcome = ReviewOutcome(
        review=result,
        state=state,
        status=status,
        work=work,
        verify_dir=verify_dir,
        pending=pending,
        errors=list(claude.errors) if claude is not None else [],
        trace_url=tracer.trace_url(),
        prompts=prompts.summary(),
    )
    if req.save:
        outcome.stored = save_review(
            s,
            outcome,
            key=review_key(rid, req.pr_number, result.head_sha, text),
            trace_id=tracer.trace_id(),
            notes=notes,
            store=review_store,
        )
    return outcome
