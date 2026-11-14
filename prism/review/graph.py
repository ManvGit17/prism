"""The review graph in LangGraph (PLAN.md Phase 5, ADR 0010).

    planner ─► Send() per file ─► file_reviewer ─► verifier ─► aggregator

The planner runs the gate over every hunk first, because the hunk budget is per PR, not per
file. Each `Send` then carries one file's selected hunks; the file reviewers run at the same
time and share one semaphore, so the number of model calls in flight never exceeds the limit
however many files fan out. Their candidate comments are merged by an `operator.add` reducer.
"""

from __future__ import annotations

import asyncio
import operator
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from prism.models.gate import Gate
from prism.retrieval.diff import FileDiff, parse_diff
from prism.review.aggregate import cap, file_reviews, summary, verdict
from prism.review.checks import Candidate, Decision
from prism.review.plan import FilePlan, NotReviewed, ReviewPlan, git_churn, plan_review
from prism.review.reviewer_client import ReviewerModel, review_hunk
from prism.schemas import PRReview


class Verifier(Protocol):
    name: str

    async def verify(self, candidates: Sequence[Candidate]) -> list[Decision]: ...


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
    decisions: list[Decision]
    result: dict[str, Any]


class FileState(TypedDict):
    file: FilePlan
    deadline: float


def build_graph(
    gate: Gate, model: ReviewerModel, verifier: Verifier, options: ReviewOptions
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
        return sends or "verifier"

    async def file_reviewer(state: FileState) -> dict[str, Any]:
        file, deadline = state["file"], state["deadline"]

        async def one(task: Any) -> Candidate | NotReviewed:
            if time.monotonic() > deadline:
                return NotReviewed(task.path, task.hunk.header, "time")
            result = await review_hunk(model, task.text, limit, retries=options.retries)
            if result.comment is None:
                return NotReviewed(task.path, task.hunk.header, "model_failed")
            return Candidate(task=task, raw=result.comment, model=model.name)

        outcomes = await asyncio.gather(*(one(t) for t in file.tasks))
        return {
            "candidates": [o for o in outcomes if isinstance(o, Candidate)],
            "unreviewed": [o for o in outcomes if isinstance(o, NotReviewed)],
        }

    async def verify(state: ReviewState) -> dict[str, Any]:
        order = {(t.path, t.index): i for i, t in enumerate(state["plan"].tasks)}
        # Fan-in order depends on which file finished first; sort so results are repeatable.
        cands = sorted(
            state.get("candidates", []), key=lambda c: order[(c.task.path, c.task.index)]
        )
        return {"decisions": await verifier.verify(cands)}

    def aggregator(state: ReviewState) -> dict[str, Any]:
        plan = state["plan"]
        kept = [d.kept for d in state["decisions"] if d.kept is not None]
        comments, cut = cap(kept, options.max_comments)
        not_reviewed = [*plan.not_reviewed, *state.get("unreviewed", [])]
        v = verdict(comments, plan, len(not_reviewed))
        return {
            "result": {
                "summary": summary(comments, plan, not_reviewed, cut, v),
                "verdict": v,
                "files": file_reviews(plan, comments, not_reviewed),
            }
        }

    g = StateGraph(ReviewState)
    g.add_node("planner", planner)
    g.add_node("file_reviewer", file_reviewer)
    g.add_node("verifier", verify)
    g.add_node("aggregator", aggregator)
    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", fan_out, ["file_reviewer", "verifier"])
    g.add_edge("file_reviewer", "verifier")
    g.add_edge("verifier", "aggregator")
    g.add_edge("aggregator", END)
    return g.compile()


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
) -> tuple[PRReview, ReviewState]:
    """Review a diff whose head is checked out at `repo`. Returns the review and final state."""
    started = time.monotonic()
    files = parse_diff(diff_text)
    churn = git_churn(repo, [f.path for f in files])
    graph = build_graph(gate, model, verifier, options)
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
