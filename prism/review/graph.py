"""The review graph in LangGraph (PLAN.md Phase 5, ADR 0010).

    planner ─► Send() per file ─► file_reviewer ─► context ─► verifier ─► aggregator

The planner runs the gate over every hunk first, because the hunk budget is per PR, not per
file. Each `Send` then carries one file's selected hunks; the file reviewers run at the same
time and share one semaphore, so the number of model calls in flight never exceeds the limit
however many files fan out. Their candidate comments are merged by an `operator.add` reducer.
The context node retrieves code for the hunks that got a comment; only the verifier reads it.
"""

from __future__ import annotations

import asyncio
import operator
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from prism.models.gate import Gate
from prism.retrieval.diff import FileDiff, parse_diff
from prism.review.aggregate import cap, file_reviews, summary, verdict
from prism.review.checks import Candidate, Decision
from prism.review.handoff import ReplyCache
from prism.review.plan import FilePlan, HunkTask, NotReviewed, ReviewPlan, git_churn, plan_review
from prism.review.reviewer_client import HunkResult, ReviewerModel, review_hunk
from prism.schemas import PRReview, RawComment


class Verifier(Protocol):
    name: str

    async def verify(
        self,
        candidates: Sequence[Candidate],
        contexts: Mapping[tuple[str, str], str] | None = None,
        risk: Mapping[str, Sequence[str]] | None = None,
    ) -> list[Decision]: ...


# Rendered context per (path, hunk header), for the hunks given. Built from the head checkout.
ContextFn = Callable[[Sequence[HunkTask]], dict[tuple[str, str], str]]


@dataclass
class ReviewOptions:
    max_hunks: int = 40
    time_limit_s: float = 600.0
    max_comments: int = 20
    concurrency: int = 4
    retries: int = 2


class ReviewState(TypedDict, total=False):
    files: list[FileDiff]
    churn: dict[str, int]
    deadline: float
    plan: ReviewPlan
    candidates: Annotated[list[Candidate], operator.add]
    unreviewed: Annotated[list[NotReviewed], operator.add]
    contexts: dict[tuple[str, str], str]
    decisions: list[Decision]
    result: dict[str, Any]


class FileState(TypedDict):
    file: FilePlan
    deadline: float


def build_graph(
    gate: Gate,
    model: ReviewerModel,
    verifier: Verifier,
    options: ReviewOptions,
    *,
    cache: ReplyCache | None = None,
    context_fn: ContextFn | None = None,
) -> Any:
    """A compiled graph. One semaphore per graph: build a new graph for each review."""
    limit = asyncio.Semaphore(options.concurrency)

    def planner(state: ReviewState) -> dict[str, Any]:
        plan = plan_review(
            state["files"], gate, max_hunks=options.max_hunks, churn=state.get("churn")
        )
        return {"plan": plan}

    def fan_out(state: ReviewState) -> list[Send] | str:
        sends = [
            Send("file_reviewer", FileState(file=f, deadline=state["deadline"]))
            for f in state["plan"].files
            if f.tasks
        ]
        return sends or "context"

    async def file_reviewer(state: FileState) -> dict[str, Any]:
        file, deadline = state["file"], state["deadline"]

        async def one(task: HunkTask) -> Candidate | NotReviewed:
            cached = cache.get(model.name, task.path, task.text) if cache else None
            if cached is not None:
                if "not_reviewed" in cached:
                    return NotReviewed(task.path, task.hunk.header, cached["not_reviewed"])
                raw = RawComment(line=cached["line"], comment=cached["comment"])
                return Candidate(task=task, raw=raw, model=model.name)
            result = await review_hunk(
                model, task.text, limit, retries=options.retries, deadline=deadline
            )
            # A raised error (server down, timeout) is not cached, so a rerun tries again.
            if cache is not None and result.status != "error":
                cache.put(model.name, task.path, task.text, _cache_entry(result))
            if result.status == "time":
                return NotReviewed(task.path, task.hunk.header, "time")
            if result.comment is None:
                return NotReviewed(task.path, task.hunk.header, "model_failed")
            return Candidate(task=task, raw=result.comment, model=model.name)

        outcomes = await asyncio.gather(*(one(t) for t in file.tasks))
        return {
            "candidates": [o for o in outcomes if isinstance(o, Candidate)],
            "unreviewed": [o for o in outcomes if isinstance(o, NotReviewed)],
        }

    def ordered(state: ReviewState) -> list[Candidate]:
        order = {(t.path, t.index): i for i, t in enumerate(state["plan"].tasks)}
        # Fan-in order depends on which file finished first; sort so results are repeatable.
        return sorted(state.get("candidates", []), key=lambda c: order[(c.task.path, c.task.index)])

    def context(state: ReviewState) -> dict[str, Any]:
        if cache is not None:
            cache.save()
        tasks = [c.task for c in ordered(state)]
        if context_fn is None or not tasks:
            return {"contexts": {}}
        return {"contexts": context_fn(tasks)}

    async def verify(state: ReviewState) -> dict[str, Any]:
        risk = {f.path: f.reasons for f in state["plan"].files if f.reasons}
        decisions = await verifier.verify(ordered(state), state.get("contexts", {}), risk)
        return {"decisions": decisions}

    def aggregator(state: ReviewState) -> dict[str, Any]:
        plan = state["plan"]
        kept = [d.kept for d in state["decisions"] if d.kept is not None]
        comments, cut = cap(kept, options.max_comments)
        not_reviewed = [*plan.not_reviewed, *state.get("unreviewed", [])]
        v = verdict(comments, plan, len(not_reviewed))
        dropped = sum(1 for d in state["decisions"] if d.kept is None)
        text = summary(comments, plan, not_reviewed, cut, v)
        text += f"\nVerifier: {verifier.name}; {dropped} comment(s) dropped."
        return {
            "result": {
                "summary": text,
                "verdict": v,
                "files": file_reviews(plan, comments, not_reviewed),
            }
        }

    g = StateGraph(ReviewState)
    g.add_node("planner", planner)
    g.add_node("file_reviewer", file_reviewer)
    g.add_node("context", context)
    g.add_node("verifier", verify)
    g.add_node("aggregator", aggregator)
    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", fan_out, ["file_reviewer", "context"])
    g.add_edge("file_reviewer", "context")
    g.add_edge("context", "verifier")
    g.add_edge("verifier", "aggregator")
    g.add_edge("aggregator", END)
    return g.compile()


def _cache_entry(outcome: HunkResult) -> dict[str, Any]:
    if outcome.status == "time":
        return {"not_reviewed": "time"}
    if outcome.comment is None:
        return {"not_reviewed": "model_failed", "error": outcome.error}
    return {"line": outcome.comment.line, "comment": outcome.comment.comment}


def head_sha(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip() or "local"
    except (OSError, subprocess.SubprocessError):
        return "local"


async def run_review(
    repo: Path,
    diff_text: str,
    *,
    gate: Gate,
    model: ReviewerModel,
    verifier: Verifier,
    options: ReviewOptions,
    repo_name: str | None = None,
    pr_number: int | None = None,
    cache: ReplyCache | None = None,
    context_fn: ContextFn | None = None,
) -> tuple[PRReview, ReviewState]:
    """Review a diff whose head is checked out at `repo`. Returns the review and final state."""
    started = time.monotonic()
    files = parse_diff(diff_text)
    churn = git_churn(repo, [f.path for f in files])
    graph = build_graph(gate, model, verifier, options, cache=cache, context_fn=context_fn)
    state: ReviewState = await graph.ainvoke(
        {"files": files, "churn": churn, "deadline": started + options.time_limit_s}
    )
    result = state["result"]
    review = PRReview(
        repo=repo_name or repo.resolve().name,
        pr_number=pr_number,
        head_sha=head_sha(repo),
        summary=result["summary"],
        verdict=result["verdict"],
        files=result["files"],
        duration_s=round(time.monotonic() - started, 2),
    )
    return review, state
