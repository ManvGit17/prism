"""The review graph in LangGraph (PLAN.md Phase 5, ADR 0010).

    planner ─► Send() per file ─► file_reviewer ─┐
            └► Send() once ─────► sast ───────────┴► context ─► verifier ─► aggregator

The planner runs the gate over every hunk first, because the hunk budget is per PR, not per
file. Each `Send` then carries one file's selected hunks; the file reviewers run at the same
time and share one semaphore, so the number of model calls in flight never exceeds the limit
however many files fan out. Their candidate comments are merged by an `operator.add` reducer.
SAST runs as one more branch of the same fan-out, so the tools work while the model does.
The context node retrieves code for the hunks that got a comment, ties SAST findings to hunks
and turns the stronger ones into comments; only the verifier reads context and findings.

The planner also routes (ADR 0013): a security-sensitive or large hunk skips the local model and
goes to the frontier model through the verifier, which can escalate more hunks after it has
judged the local comments.
"""

from __future__ import annotations

import asyncio
import operator
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from prism.models.gate import Gate
from prism.retrieval.diff import FileDiff, parse_diff
from prism.review.aggregate import cap, file_reviews, summary, verdict
from prism.review.checks import Candidate, Decision, Escalation, VerifyBatch, new_side_lines
from prism.review.handoff import ReplyCache
from prism.review.plan import FilePlan, HunkTask, NotReviewed, ReviewPlan, git_churn, plan_review
from prism.review.reviewer_client import HunkResult, ReviewerModel, review_hunk
from prism.review.router import RouteKey, route
from prism.review.sast import LEVEL_RANK, Finding, SastReport, at_least
from prism.schemas import PRReview, RawComment


class Verifier(Protocol):
    name: str
    # Lines the verifier adds to the summary (routing), and what its model calls cost.
    summary_lines: list[str]

    @property
    def cost_usd(self) -> float: ...

    async def verify(self, batch: VerifyBatch) -> list[Decision]: ...


# Rendered context per (path, hunk header), for the hunks given. Built from the head checkout.
ContextFn = Callable[[Sequence[HunkTask]], dict[tuple[str, str], str]]
# Static analysis of the given changed files at the head checkout.
SastFn = Callable[[Sequence[str]], SastReport]


@dataclass
class ReviewOptions:
    max_hunks: int = 40
    time_limit_s: float = 600.0
    max_comments: int = 20
    concurrency: int = 4
    retries: int = 2
    # A SAST finding on an added line at or above this becomes a comment of its own.
    sast_comment_min_severity: str = "medium"
    # Send security-sensitive and large hunks to the frontier model instead of the local one.
    escalate: bool = False
    escalate_min_lines: int = 60


class ReviewState(TypedDict, total=False):
    files: list[FileDiff]
    churn: dict[str, int]
    deadline: float
    plan: ReviewPlan
    routes: dict[RouteKey, tuple[str, ...]]
    escalations: Annotated[list[Escalation], operator.add]
    candidates: Annotated[list[Candidate], operator.add]
    unreviewed: Annotated[list[NotReviewed], operator.add]
    contexts: dict[tuple[str, str], str]
    sast: SastReport
    tools: dict[tuple[str, str], list[str]]
    decisions: list[Decision]
    result: dict[str, Any]


class FileState(TypedDict):
    file: FilePlan
    deadline: float
    routes: dict[RouteKey, tuple[str, ...]]


class SastState(TypedDict):
    paths: list[str]


def build_graph(
    gate: Gate,
    model: ReviewerModel,
    verifier: Verifier,
    options: ReviewOptions,
    *,
    cache: ReplyCache | None = None,
    context_fn: ContextFn | None = None,
    sast_fn: SastFn | None = None,
) -> Any:
    """A compiled graph. One semaphore per graph: build a new graph for each review."""
    limit = asyncio.Semaphore(options.concurrency)

    def planner(state: ReviewState) -> dict[str, Any]:
        plan = plan_review(
            state["files"], gate, max_hunks=options.max_hunks, churn=state.get("churn")
        )
        routes = route(plan, enabled=options.escalate, min_lines=options.escalate_min_lines)
        return {"plan": plan, "routes": routes}

    def fan_out(state: ReviewState) -> list[Send] | str:
        sends = [
            Send(
                "file_reviewer",
                FileState(file=f, deadline=state["deadline"], routes=state["routes"]),
            )
            for f in state["plan"].files
            if f.tasks
        ]
        if sends and sast_fn is not None:
            paths = [f.path for f in state["plan"].files if f.tasks]
            sends.append(Send("sast", SastState(paths=paths)))
        return sends or "context"

    async def sast(state: SastState) -> dict[str, Any]:
        assert sast_fn is not None
        # The tools are subprocesses; a thread keeps the model calls going meanwhile.
        return {"sast": await asyncio.to_thread(sast_fn, state["paths"])}

    async def file_reviewer(state: FileState) -> dict[str, Any]:
        file, deadline, routes = state["file"], state["deadline"], state["routes"]

        async def one(task: HunkTask) -> Candidate | NotReviewed | Escalation:
            reasons = routes.get((task.path, task.index))
            if reasons:
                return Escalation(task, reasons)
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
            "escalations": [o for o in outcomes if isinstance(o, Escalation)],
        }

    def ordered(state: ReviewState) -> list[Candidate]:
        order = {(t.path, t.index): i for i, t in enumerate(state["plan"].tasks)}
        # Fan-in order depends on which file finished first; sort so results are repeatable.
        return sorted(state.get("candidates", []), key=lambda c: order[(c.task.path, c.task.index)])

    def context(state: ReviewState) -> dict[str, Any]:
        if cache is not None:
            cache.save()
        report = state.get("sast")
        tools, extra = tie_findings(
            state["plan"].tasks,
            report.findings if report else [],
            options.sast_comment_min_severity,
        )
        tasks: dict[tuple[str, int], HunkTask] = {}
        items: list[Candidate | Escalation] = [*ordered(state), *extra, *escalated(state)]
        for c in items:
            tasks.setdefault((c.task.path, c.task.index), c.task)
        contexts = context_fn(list(tasks.values())) if context_fn is not None and tasks else {}
        return {"contexts": contexts, "tools": tools, "candidates": extra}

    def escalated(state: ReviewState) -> list[Escalation]:
        order = {(t.path, t.index): i for i, t in enumerate(state["plan"].tasks)}
        return sorted(
            state.get("escalations", []), key=lambda e: order[(e.task.path, e.task.index)]
        )

    async def verify(state: ReviewState) -> dict[str, Any]:
        batch = VerifyBatch(
            candidates=ordered(state),
            contexts=state.get("contexts", {}),
            risk={f.path: f.reasons for f in state["plan"].files if f.reasons},
            tools=state.get("tools", {}),
            escalations=escalated(state),
        )
        return {"decisions": await verifier.verify(batch)}

    def aggregator(state: ReviewState) -> dict[str, Any]:
        plan = state["plan"]
        kept = [d.kept for d in state["decisions"] if d.kept is not None]
        comments, cut = cap(kept, options.max_comments)
        not_reviewed = [*plan.not_reviewed, *state.get("unreviewed", [])]
        v = verdict(comments, plan, len(not_reviewed))
        dropped = sum(1 for d in state["decisions"] if d.kept is None)
        text = summary(comments, plan, not_reviewed, cut, v)
        report = state.get("sast")
        if report is not None:
            text += "\n" + report.summary()
        local = len(plan.tasks) - len(state.get("escalations", []))
        text += f"\nRouting: {local} hunk(s) to {model.name}."
        for line in verifier.summary_lines:
            text += "\n" + line
        text += f"\nVerifier: {verifier.name}; {dropped} comment(s) dropped."
        return {
            "result": {
                "summary": text,
                "verdict": v,
                "files": file_reviews(plan, comments, not_reviewed),
                "cost_usd": verifier.cost_usd,
            }
        }

    g = StateGraph(ReviewState)
    g.add_node("planner", planner)
    g.add_node("file_reviewer", file_reviewer)
    g.add_node("sast", sast)
    g.add_node("context", context)
    g.add_node("verifier", verify)
    g.add_node("aggregator", aggregator)
    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", fan_out, ["file_reviewer", "sast", "context"])
    g.add_edge("file_reviewer", "context")
    g.add_edge("sast", "context")
    g.add_edge("context", "verifier")
    g.add_edge("verifier", "aggregator")
    g.add_edge("aggregator", END)
    return g.compile()


def tie_findings(
    tasks: Sequence[HunkTask], findings: Sequence[Finding], min_severity: str
) -> tuple[dict[tuple[str, str], list[str]], list[Candidate]]:
    """Findings per hunk (as text for the verifier), and comments made from the strong ones.

    A finding outside every reviewed hunk is ignored: it is about code the PR did not change, or
    a hunk the gate or the budget left out. Only findings on added lines become comments.
    """
    tools: dict[tuple[str, str], list[str]] = {}
    # One tool comment per line: tools often report the same issue (Bandit and Semgrep both flag
    # shell=True). The strongest finding becomes the comment; the others stay as evidence.
    best: dict[tuple[str, int], tuple[HunkTask, Finding]] = {}
    for task in tasks:
        lines = new_side_lines(task)
        added = set(task.hunk.added_lines)
        for f in findings:
            if f.path != task.path or f.line not in lines:
                continue
            tools.setdefault((task.path, task.hunk.header), []).append(f.describe())
            if f.line not in added or not at_least(f.severity, min_severity):
                continue
            held = best.get((f.path, f.line))
            if held is None or LEVEL_RANK[f.severity] > LEVEL_RANK[held[1].severity]:
                best[(f.path, f.line)] = (task, f)
    extra = [
        Candidate(
            task=task,
            raw=RawComment(line=f.line, comment=f"{f.message} ({f.tool} {f.rule})"),
            model=f.tool,
        )
        for task, f in best.values()
    ]
    return tools, extra


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
    sast_fn: SastFn | None = None,
) -> tuple[PRReview, ReviewState]:
    """Review a diff whose head is checked out at `repo`. Returns the review and final state."""
    started = time.monotonic()
    files = parse_diff(diff_text)
    churn = git_churn(repo, [f.path for f in files])
    graph = build_graph(
        gate, model, verifier, options, cache=cache, context_fn=context_fn, sast_fn=sast_fn
    )
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
        cost_usd=round(result["cost_usd"], 6),
        duration_s=round(time.monotonic() - started, 2),
    )
    return review, state
