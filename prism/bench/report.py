"""A BugBench run as a Markdown report for eval/reports (and the dashboard's reports tab)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from prism.bench.score import LINE_TOLERANCE, CaseResult


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _found(block: dict[str, Any]) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(block.get("found_by", {}).items())) or "none"


def _recall_row(label: str, b: dict[str, Any]) -> str:
    return (
        f"| {label} | {b['hits']}/{b['cases']} ({_fmt(b['recall'])}) | {b['near_but_dropped']} | "
        f"{_fmt(b['comments_per_pr'])} | {_found(b)} |"
    )


def render(summary: dict[str, Any], results: Sequence[CaseResult]) -> str:
    st = summary.get("settings", {})
    lines = [
        f"# BugBench: {summary.get('repo')} ({summary.get('run')})",
        "",
        "Each pull request is a real commit. Injected cases carry one planted defect at a known "
        "line; real-bug cases are commits that introduced a bug a later commit fixed; clean cases "
        f"are left as they are. A hit is a posted comment on the defect's file within "
        f"{LINE_TOLERANCE} lines of it.",
        "",
        "| Set | Recall | Near but dropped | Comments per PR | Hits by model |",
        "|---|---|---|---|---|",
        _recall_row("Injected", summary),
    ]
    if "real" in summary:
        lines.append(_recall_row("Real bugs", summary["real"]))
    lines.append("")
    if "clean" in summary:
        c = summary["clean"]
        lines += [
            f"Clean PRs: {c['with_comments']} of {c['cases']} got at least one comment "
            f"(false-positive rate {_fmt(c['false_positive_rate'])}), "
            f"{_fmt(c['comments_per_pr'])} comments per PR. A comment on a clean PR may still be "
            "a true issue in the original code; this counts it as a false positive.",
            "",
        ]
    if "by_repo" in summary:
        lines += [
            "| Repo (injected) | Recall | Near but dropped | Comments per PR | Hits by model |",
            "|---|---|---|---|---|",
            *(_recall_row(name, b) for name, b in summary["by_repo"].items()),
            "",
        ]
    lines += [
        f"First run p50 / p95: {_fmt(summary['p50_first_run_s'])} s / "
        f"{_fmt(summary['p95_first_run_s'])} s.",
        "",
        f"Reviewer `{st.get('reviewer')}`, gate `{st.get('gate')}`, context "
        f"`{st.get('context_mode')}`, verifier `{st.get('verifier')}`, SAST "
        f"{'on' if st.get('sast') else 'off'}, escalation "
        f"{'on' if st.get('escalate') else 'off'}, minimum confidence "
        f"{_fmt(st.get('min_confidence'))}. First-run time covers the local reviewer, SAST and "
        "indexing the checkout in memory; Claude's answers come from a handoff and are not timed.",
        "",
        "## Cases",
        "",
        "| Case | Set | Defect | Where | Hit | By | Posted | Considered |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.group == "clean":
            hit, where = "-", "-"
        else:
            hit = "yes" if r.hit else ("dropped" if r.near_but_dropped else "no")
            where = f"`{r.path}:{','.join(map(str, r.lines))}`"
        lines.append(
            f"| {r.case_id} | {r.group} | {r.kind} | {where} | {hit} | {r.found_by or '-'} | "
            f"{r.posted} | {r.considered} |"
        )
    lines += ["", "## Comments near the defects", ""]
    any_near = False
    for r in results:
        for c in r.comments:
            if c["near_defect"]:
                any_near = True
                text = " ".join(str(c["comment"]).split())
                if len(text) > 240:
                    text = text[:237] + "..."
                lines.append(f"- **{r.case_id}** `{c['path']}:{c['line']}` ({c['model']}): {text}")
    if not any_near:
        lines.append("None.")
    return "\n".join(lines) + "\n"
