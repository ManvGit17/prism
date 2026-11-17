"""The confidence threshold sweep: a finished run re-scored at other thresholds.

`PRISM_VERIFIER_MIN_CONFIDENCE` decides which of Claude's verdicts and comments are posted. In
PRism it also routes: a local comment Claude keeps below the threshold sends its hunk back to
Claude for a review of its own (ADR 0013). So a different threshold can ask Claude new questions.

Each case is reviewed once more from its caches, exactly as in the run, and the batch the
verifier was given is rebuilt from the final state. Then, for each threshold, a Claude verifier
answers that batch from the answers already in the case's work dir and writes nothing. A
threshold that would need an answer the run never asked for is reported with how many, and
its cases are not scored at that threshold.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from prism.bench.cases import CaseSet
from prism.bench.run import RunConfig, _timing, review_case, selected
from prism.bench.score import score_case
from prism.config import Settings
from prism.review.aggregate import cap
from prism.review.checks import Decision, VerifyBatch
from prism.review.graph import ReviewState
from prism.review.handoff import _FILES, ClaudeVerifier, Kind, read_answers
from prism.schemas import ReviewComment


class SavedAnswers:
    """A Claude backend that only reads the answers already in a work dir."""

    cost_usd = 0.0

    def __init__(self, directory: Path, name: str = "claude") -> None:
        self.dir = directory
        self.name = name

    def answer(
        self, kind: Kind, rubric: str, requests: Sequence[dict[str, Any]]
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        answers, errors = read_answers(self.dir, _FILES[kind][1])
        wanted = {r["id"] for r in requests}
        return {k: v for k, v in answers.items() if k in wanted}, errors


def batch_of(state: ReviewState) -> VerifyBatch:
    """What the graph's verifier node was given, rebuilt from the final state."""
    plan = state["plan"]
    order = {(t.path, t.index): i for i, t in enumerate(plan.tasks)}

    def key(x: Any) -> int:
        return order[(x.task.path, x.task.index)]

    return VerifyBatch(
        candidates=sorted(state.get("candidates", []), key=key),
        contexts=state.get("contexts", {}),
        risk={f.path: f.reasons for f in plan.files if f.reasons},
        tools=state.get("tools", {}),
        escalations=sorted(state.get("escalations", []), key=key),
    )


async def rescore(
    state: ReviewState,
    verify_dir: Path,
    threshold: float,
    *,
    whole_pr: bool = False,
    max_comments: int = 20,
) -> tuple[list[ReviewComment], list[Decision], int]:
    """(posted comments, decisions, answers missing) at `threshold`."""
    verifier = ClaudeVerifier(SavedAnswers(verify_dir), min_confidence=threshold, whole_pr=whole_pr)
    decisions = await verifier.verify(batch_of(state))
    if verifier.pending:
        return [], decisions, verifier.pending
    comments, _ = cap([d.kept for d in decisions if d.kept is not None], max_comments)
    return comments, decisions, 0


def _key(t: float) -> str:
    return f"{t:.2f}"


async def run_sweep(
    sets: CaseSet | Sequence[CaseSet],
    cfg: RunConfig,
    settings: Settings,
    thresholds: Sequence[float],
    notes: Callable[[str], None] = print,
    only: set[str] | None = None,
) -> dict[str, Any]:
    """Every case of a finished run, scored at every threshold. Written to `<run>/sweep.json`."""
    run_dir = cfg.root / "runs" / cfg.run
    out: dict[str, dict[str, Any]] = {
        _key(t): {"rows": [], "missing": 0, "cases_missing": []} for t in thresholds
    }
    for cs, case in selected(sets, only):
        m, outcome, _ = await review_case(cs, case, cfg, settings, notes)
        if outcome.pending:
            raise ValueError(
                f"{case.id} asks Claude something the run has no answer to: the run is not "
                "finished, or a setting or SAST tool differs from the run's"
            )
        for t in thresholds:
            comments, decisions, missing = await rescore(
                outcome.state,
                run_dir / case.id / "verify",
                t,
                whole_pr=settings.review_unit == "pr",
                max_comments=settings.review_max_comments,
            )
            slot = out[_key(t)]
            if missing:
                slot["missing"] += missing
                slot["cases_missing"].append(case.id)
                continue
            result = score_case(
                case.id,
                case.kind,
                case.path,
                m.lines,
                comments,
                decisions,
                _timing(run_dir / case.id).get("first_run_s"),
                group=case.group,
                repo=cs.repo,
            )
            slot["rows"].append(result.to_json())
        notes(f"{case.id}: scored at {len(thresholds)} threshold(s)")
    sweep = {"run": cfg.run, "thresholds": out}
    (run_dir / "sweep.json").write_text(json.dumps(sweep, indent=1), encoding="utf-8")
    return sweep


def render_sweep(columns: Sequence[tuple[str, dict[str, Any]]], title: str) -> str:
    """One table per measure: a row per threshold, a column per run."""
    keys = sorted({k for _, sw in columns for k in sw["thresholds"]})

    def cell(sw: dict[str, Any], k: str, measure: Callable[[list[dict[str, Any]]], str]) -> str:
        slot = sw["thresholds"].get(k)
        if slot is None:
            return "-"
        value = measure(slot["rows"])
        if slot["missing"]:
            value += f" ({len(slot['cases_missing'])} PR(s) unscored)"
        return value

    def recall(group: str) -> Callable[[list[dict[str, Any]]], str]:
        def f(rows: list[dict[str, Any]]) -> str:
            rs = [r for r in rows if r["group"] == group]
            return f"{sum(bool(r['hit']) for r in rs)}/{len(rs)}"

        return f

    def flagged(rows: list[dict[str, Any]]) -> str:
        rs = [r for r in rows if r["group"] == "clean"]
        return f"{sum(r['posted'] > 0 for r in rs)}/{len(rs)}"

    def per_pr(rows: list[dict[str, Any]]) -> str:
        rs = [r for r in rows if r["group"] != "clean"]
        return f"{sum(r['posted'] for r in rs) / len(rs):.2f}" if rs else "-"

    measures = [
        ("Injected recall", recall("injected")),
        ("Real-bug recall", recall("real")),
        ("Clean PRs with any comment", flagged),
        ("Comments per buggy PR", per_pr),
    ]
    lines = [
        f"# {title}",
        "",
        "Each run's saved Claude answers re-scored at each confidence threshold, with no new "
        "Claude work. A PR is unscored at a threshold where PRism would ask Claude something the "
        "run never asked (a local comment kept below the threshold goes back to Claude); counts "
        "then cover the scored PRs only.",
        "",
    ]
    for name, measure in measures:
        lines += [
            f"## {name}",
            "",
            "| Threshold | " + " | ".join(n for n, _ in columns) + " |",
            "|---|" + "---|" * len(columns),
        ]
        for k in keys:
            lines.append(f"| {k} | " + " | ".join(cell(sw, k, measure) for _, sw in columns) + " |")
        lines.append("")
    return "\n".join(lines)
