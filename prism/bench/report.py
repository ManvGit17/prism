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


def render(summary: dict[str, Any], results: Sequence[CaseResult]) -> str:
    st = summary.get("settings", {})
    found = ", ".join(f"{k} {v}" for k, v in sorted(summary.get("found_by", {}).items())) or "none"
    lines = [
        f"# BugBench: {summary.get('repo')} ({summary.get('run')})",
        "",
        f"{summary['cases']} pull requests, each a real commit with one planted defect at a known "
        f"line. A hit is a posted comment on the defect's file within {LINE_TOLERANCE} lines of "
        "it.",
        "",
        "| | |",
        "|---|---|",
        f"| Recall | {summary['hits']}/{summary['cases']} ({_fmt(summary['recall'])}) |",
        f"| Near the defect but dropped | {summary['near_but_dropped']} |",
        f"| Comments per PR | {_fmt(summary['comments_per_pr'])} |",
        f"| Hits by model | {found} |",
        f"| First run, p50 / p95 (s) | {_fmt(summary['p50_first_run_s'])} / "
        f"{_fmt(summary['p95_first_run_s'])} |",
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
        "| Case | Defect | Line | Hit | By | Posted | Considered |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        hit = "yes" if r.hit else ("dropped" if r.near_but_dropped else "no")
        lines.append(
            f"| {r.case_id} | {r.kind} | `{r.path}:{r.line}` | {hit} | {r.found_by or '-'} | "
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
