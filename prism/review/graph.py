"""The review graph in LangGraph (ADR 0010).

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
from prism.observability.tracing import NoopSpan, NoopTracer, Span, Tracer
from prism.retrieval.diff import FileDiff, parse_diff
from prism.review.aggregate import cap, file_reviews, summary, verdict
from prism.review.checks import Candidate, Decision, Escalation, VerifyBatch, new_side_lines
from prism.review.handoff import ReplyCache
from prism.review.plan import FilePlan, HunkTask, NotReviewed, ReviewPlan, git_churn, plan_review
from prism.review.reviewer_client import HunkResult, ReviewerModel, review_hunk, with_context
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
    route_all: bool = False
    # Evaluation setting: the local reviewer also sees retrieved code before each hunk.
    reviewer_context: bool = False


class ReviewState(TypedDict, total=False):
    files: list[FileDiff]
    churn: dict[str, int]
    deadline: float
    plan: ReviewPlan
    routes: dict[RouteKey, tuple[str, ...]]
    reviewer_contexts: dict[tuple[str, str], str]
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
    contexts: dict[tuple[str, str], str]


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
    root: Span | None = None,
    reviewer_prompt: Any = None,
) -> Any:
    """A compiled graph. One semaphore per graph: build a new graph for each review.

    `root` is the review's trace; every node and every model call is a child of it.
    `reviewer_prompt` is the Langfuse prompt the local model's calls are linked to, if any.
    """
    limit = asyncio.Semaphore(options.concurrency)
    trace: Span = root or NoopSpan()

    def planner(state: ReviewState) -> dict[str, Any]:
        span = trace.child("planner", input={"files": len(state["files"])})
        plan = plan_review(
            state["files"], gate, max_hunks=options.max_hunks, churn=state.get("churn")
        )
        routes = route(
            plan,
            enabled=options.escalate,
            min_lines=options.escalate_min_lines,
            everything=options.route_all,
        )
        span.end(
            output={
                "files": [
                    {
                        "path": f.path,
                        "risk": f.risk,
                        "reasons": list(f.reasons),
                        "hunks": f.hunks_total,
                        "selected": len(f.tasks),
                        "skipped_by_gate": f.skipped_by_gate,
                    }
                    for f in plan.files
                ],
                "skipped_files": [list(x) for x in plan.skipped_files],
                "over_budget": len(plan.not_reviewed),
                "escalated": {f"{p}#{i}": list(r) for (p, i), r in routes.items()},
            },
            metadata={"gate": gate.name, "max_hunks": options.max_hunks},
        )
        contexts: dict[tuple[str, str], str] = {}
        if options.reviewer_context and context_fn is not None:
            local = [t for t in plan.tasks if (t.path, t.index) not in routes]
            contexts = context_fn(local) if local else {}
        return {"plan": plan, "routes": routes, "reviewer_contexts": contexts}

    def fan_out(state: ReviewState) -> list[Send] | str:
        sends = [
            Send(
                "file_reviewer",
                FileState(
                    file=f,
                    deadline=state["deadline"],
                    routes=state["routes"],
                    contexts=state.get("reviewer_contexts", {}),
                ),
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
        span = trace.child("sast", kind="tool", input={"paths": state["paths"]})
        # The tools are subprocesses; a thread keeps the model calls going meanwhile.
        report = await asyncio.to_thread(sast_fn, state["paths"])
        span.end(
            output={"findings": [f.describe() for f in report.findings]},
            metadata={"ran": report.ran, "skipped": [list(x) for x in report.skipped]},
        )
        return {"sast": report}

    async def file_reviewer(state: FileState) -> dict[str, Any]:
        file, deadline, routes = state["file"], state["deadline"], state["routes"]
        file_span = trace.child(
            f"file {file.path}",
            input={"hunks": len(file.tasks)},
            metadata={"risk": file.risk, "reasons": list(file.reasons)},
        )

        async def one(task: HunkTask) -> Candidate | NotReviewed | Escalation:
            name = f"hunk {task.hunk.header}"
            reasons = routes.get((task.path, task.index))
            if reasons:
                file_span.child(name, input=task.text).end(
                    output="escalated to the frontier model", metadata={"reasons": list(reasons)}
                )
                return Escalation(task, reasons)
            ctx = state["contexts"].get((task.path, task.hunk.header), "")
            prompt = with_context(task.text, ctx)
            cached = cache.get(model.name, task.path, prompt) if cache else None
            if cached is not None:
                file_span.child(name, input=task.text).end(
                    output=cached, metadata={"cached": True, "model": model.name}
                )
                if "not_reviewed" in cached:
                    return NotReviewed(task.path, task.hunk.header, cached["not_reviewed"])
                raw = RawComment(line=cached["line"], comment=cached["comment"])
                return Candidate(task=task, raw=raw, model=model.name)
            gen = file_span.child(
                name,
                kind="generation",
                model=model.name,
                input=prompt,
                prompt=reviewer_prompt,
                metadata={"gate_score": task.gate_score},
            )
            result = await review_hunk(
                model, task.text, limit, retries=options.retries, deadline=deadline, context=ctx
            )
            gen.end(
                output=result.comment.model_dump() if result.comment else result.error,
                metadata={
                    "status": result.status,
                    "attempts": result.attempts,
                    "wait_for_slot_s": round(result.wait_s, 3),
                },
                level="WARNING" if result.status not in ("ok", "time") else None,
            )
            # A raised error (server down, timeout) is not cached, so a rerun tries again.
            if cache is not None and result.status != "error":
                cache.put(model.name, task.path, prompt, _cache_entry(result))
            if result.status == "time":
                return NotReviewed(task.path, task.hunk.header, "time")
            if result.comment is None:
                return NotReviewed(task.path, task.hunk.header, "model_failed")
            return Candidate(task=task, raw=result.comment, model=model.name)

        outcomes = await asyncio.gather(*(one(t) for t in file.tasks))
        file_span.end(
            output={
                "comments": sum(isinstance(o, Candidate) for o in outcomes),
                "escalated": sum(isinstance(o, Escalation) for o in outcomes),
                "not_reviewed": sum(isinstance(o, NotReviewed) for o in outcomes),
            }
        )
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
        span = trace.child("context", kind="retriever")
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
        span.end(
            output={
                "hunks_with_context": sum(1 for v in contexts.values() if v),
                "tool_comments": len(extra),
            },
        )
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
        span = trace.child(
            "verifier",
            kind="evaluator",
            input={"comments": len(batch.candidates), "escalations": len(batch.escalations)},
            metadata={"verifier": verifier.name},
        )
        decisions = await verifier.verify(batch)
        span.end(
            output=[
                {
                    "path": d.candidate.path,
                    "line": d.candidate.raw.line,
                    "author": d.candidate.model,
                    "decision": d.reason,
                    "severity": d.kept.severity.value if d.kept else None,
                }
                for d in decisions
            ],
            metadata={"pending": getattr(verifier, "pending", 0), "cost_usd": verifier.cost_usd},
        )
        return {"decisions": decisions}

    def aggregator(state: ReviewState) -> dict[str, Any]:
        span = trace.child("aggregator")
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
        span.end(output={"comments": len(comments), "cut": cut, "verdict": v.value})
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
            stdin=subprocess.DEVNULL,
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
    tracer: Tracer | None = None,
    reviewer_prompt: Any = None,
) -> tuple[PRReview, ReviewState]:
    """Review a diff whose head is checked out at `repo`. Returns the review and final state."""
    started = time.monotonic()
    files = parse_diff(diff_text)
    churn = git_churn(repo, [f.path for f in files])
    name = repo_name or repo.resolve().name
    sha = head_sha(repo)
    tracer = tracer or NoopTracer()
    tags = [f"repo:{name}", f"model:{model.name}", f"verifier:{verifier.name}"]
    if pr_number is not None:
        tags.append(f"pr:{pr_number}")
    with tracer.review(
        name="review",
        tags=tags,
        metadata={"repo": name, "pr": pr_number or "", "head_sha": sha, "model": model.name},
        input={"files": [f.path for f in files], "diff_chars": len(diff_text)},
    ) as root:
        graph = build_graph(
            gate,
            model,
            verifier,
            options,
            cache=cache,
            context_fn=context_fn,
            sast_fn=sast_fn,
            root=root,
            reviewer_prompt=reviewer_prompt,
        )
        state: ReviewState = await graph.ainvoke(
            {"files": files, "churn": churn, "deadline": started + options.time_limit_s}
        )
        result = state["result"]
        review = PRReview(
            repo=name,
            pr_number=pr_number,
            head_sha=sha,
            summary=result["summary"],
            verdict=result["verdict"],
            files=result["files"],
            cost_usd=round(result["cost_usd"], 6),
            duration_s=round(time.monotonic() - started, 2),
        )
        root.end(
            output={
                "verdict": review.verdict.value,
                "comments": len(review.comments),
                "summary": review.summary,
            },
            metadata={"duration_s": review.duration_s, "cost_usd": review.cost_usd},
        )
    tracer.flush()
    return review, state
