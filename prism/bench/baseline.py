"""The PR-Agent baseline (PLAN.md Phase 7): an existing open-source reviewer on the same cases.

Qodo's PR-Agent (pinned in its own venv, not a PRism dependency) reviews each BugBench case the
way it reviews a local branch: `--pr_url <base branch>` with the local git provider, so it gets
the full files, the PR title (the branch name) and description (the commit message). It runs
the tools its GitHub App runs on a new PR that can find bugs: `review` (key issues) and
`improve` (code suggestions). Its model is Claude through `prism.bench.llm_handoff`, answered by
Sonnet subagents in batched rounds like PRism's own handoff.

A finding is a key issue or a suggestion with its file and line range. It hits a defect when it
is on the defect's file and its range comes within 3 lines of a defect line, the same tolerance
PRism's single-line comments get.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from prism.bench.cases import CaseSet, git, materialize
from prism.bench.llm_handoff import HandoffStore, export_round, import_answers, serve
from prism.bench.score import LINE_TOLERANCE, percentile

TOOLS = ("review", "improve")
# Fixed dates make the amended commit, and so every prompt, the same on every run.
_GIT_DATES = {
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


@dataclass
class Finding:
    tool: str
    path: str
    start: int
    end: int
    text: str


@dataclass
class BaselineResult:
    case_id: str
    repo: str
    group: str
    kind: str
    path: str
    lines: list[int]
    hit: bool
    hit_by: str | None  # the tool whose finding hit
    hit_span: int | None  # lines covered by the finding that hit
    findings: int
    first_run_s: float | None
    details: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def branch_name(subject: str, commit: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:40].strip("-") or "change"
    return f"{slug}-{commit[:7]}"


def prepare(root: Path, cs: CaseSet, case: Any) -> tuple[Path, str]:
    """The case's worktree on a branch named after the commit, the defect (if any) amended into
    that commit, and a base branch at the parent. Returns (worktree, base branch)."""
    m = materialize(root, cs, case)
    wt = m.checkout
    subject = git(wt, "log", "-1", "--format=%s", case.commit).strip()
    git(wt, "checkout", "-q", "-B", branch_name(subject, case.commit))
    base = f"main-{case.commit[:7]}"
    git(wt, "branch", "-f", base, f"{case.commit}^")
    if case.group == "injected":
        subprocess.run(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-C",
                str(wt),
                "commit",
                "-q",
                "-a",
                "--amend",
                "--no-edit",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            env={**os.environ, **_GIT_DATES},
        )
    return wt, base


def _config(url: str, out: Path) -> str:
    return f"""[config]
git_provider = "local"
model = "openai/handoff"
fallback_models = []
custom_model_max_tokens = 200000
num_retries = 0
publish_output_progress = false

[openai]
key = "handoff"
api_base = "{url}"

[local]
review_path = "{(out / "review.md").as_posix()}"
improve_path = "{(out / "improve.md").as_posix()}"
description_path = "{(out / "description.md").as_posix()}"
"""


def run_pr_agent(exe: Path, wt: Path, base: str, cfg: Path, out_json: Path, tool: str) -> str:
    out_json.unlink(missing_ok=True)
    proc = subprocess.run(
        [
            str(exe),
            "--pr_url",
            base,
            "--extra_config_url",
            str(cfg),
            "--json-output",
            str(out_json),
            tool,
        ],
        cwd=wt,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    return proc.stdout + proc.stderr


# Reading PR-Agent's output


def _int(v: Any) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def findings_of(tool: str, data: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    if tool == "review":
        review = data.get("review", data)
        issues = review.get("key_issues_to_review") or []
        for issue in issues if isinstance(issues, list) else []:
            start, end = _int(issue.get("start_line")), _int(issue.get("end_line"))
            path = str(issue.get("relevant_file") or "").strip()
            if path and start is not None:
                text = f"{issue.get('issue_header', '')}: {issue.get('issue_content', '')}"
                out.append(Finding(tool, path, start, end if end is not None else start, text))
    else:
        for s in data.get("code_suggestions") or []:
            start, end = _int(s.get("relevant_lines_start")), _int(s.get("relevant_lines_end"))
            path = str(s.get("relevant_file") or "").strip()
            if path and start is not None:
                text = str(s.get("suggestion_content") or s.get("one_sentence_summary") or "")
                out.append(Finding(tool, path, start, end if end is not None else start, text))
    return out


def hits(f: Finding, path: str, lines: Sequence[int]) -> bool:
    lo, hi = min(f.start, f.end), max(f.start, f.end)
    return f.path == path and any(lo - LINE_TOLERANCE <= ln <= hi + LINE_TOLERANCE for ln in lines)


@dataclass
class BaselineRun:
    run_dir: Path
    waiting: list[str] = field(default_factory=list)
    round_dir: Path | None = None
    results: list[BaselineResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def run_baseline(
    sets: Sequence[CaseSet],
    root: Path,
    run: str,
    exe: Path,
    notes: Callable[[str], None] = print,
    only: set[str] | None = None,
) -> BaselineRun:
    run_dir = root / "runs" / run
    store = HandoffStore(run_dir / "llm")
    got = import_answers(store)
    if got:
        notes(f"{got} answer(s) added")
    out = BaselineRun(run_dir)
    done: list[tuple[CaseSet, Any, list[Finding], float | None]] = []
    with serve(store) as url:
        for cs in sets:
            for case in cs.cases:
                if only is not None and case.id not in only:
                    continue
                case_dir = run_dir / case.id
                case_dir.mkdir(parents=True, exist_ok=True)
                cfg = case_dir / "pr_agent.toml"
                cfg.write_text(_config(url, case_dir), encoding="utf-8")
                timing_path = case_dir / "timing.json"
                timing = json.loads(timing_path.read_text("utf-8")) if timing_path.exists() else {}
                wt, base = prepare(root, cs, case)
                findings: list[Finding] = []
                waiting = False
                for tool in TOOLS:
                    store.tag = f"{case.id}:{tool}"
                    out_json = case_dir / f"{tool}.json"
                    started = time.monotonic()
                    log = run_pr_agent(exe, wt, base, cfg, out_json, tool)
                    (case_dir / f"{tool}.log").write_text(log, encoding="utf-8")
                    if store.missed.get(store.tag):
                        waiting = True
                        continue
                    timing.setdefault(tool, round(time.monotonic() - started, 2))
                    if out_json.exists():
                        findings += findings_of(tool, json.loads(out_json.read_text("utf-8")))
                timing_path.write_text(json.dumps(timing), encoding="utf-8")
                notes(f"{case.id}: {'waiting' if waiting else f'{len(findings)} finding(s)'}")
                if waiting:
                    out.waiting.append(case.id)
                else:
                    done.append((cs, case, findings, sum(timing.values()) if timing else None))
    if out.waiting:
        out.round_dir = export_round(store)
        return out
    for cs, case, findings, took in done:
        lines = list(_defect_lines(root, cs, case))
        hit = next((f for f in findings if hits(f, case.path, lines)), None) if lines else None
        out.results.append(
            BaselineResult(
                case_id=case.id,
                repo=cs.repo,
                group=case.group,
                kind=case.kind,
                path=case.path,
                lines=lines,
                hit=hit is not None,
                hit_by=hit.tool if hit else None,
                hit_span=abs(hit.end - hit.start) + 1 if hit else None,
                findings=len(findings),
                first_run_s=took,
                details=[asdict(f) | {"near_defect": hits(f, case.path, lines)} for f in findings],
            )
        )
    out.summary = summarize(out.results) | {
        "run": run,
        "reviewer": "PR-Agent 0.49.0 + Claude (handoff)",
    }
    with (run_dir / "results.jsonl").open("w", encoding="utf-8") as fh:
        for r in out.results:
            fh.write(json.dumps(r.to_json(), ensure_ascii=False) + "\n")
    (run_dir / "summary.json").write_text(json.dumps(out.summary, indent=2), encoding="utf-8")
    return out


def _defect_lines(root: Path, cs: CaseSet, case: Any) -> tuple[int, ...]:
    if case.group == "clean":
        return ()
    return materialize(root, cs, case).lines


def _recall(rows: Sequence[BaselineResult]) -> dict[str, Any]:
    n = len(rows)
    found = sum(r.hit for r in rows)
    by: dict[str, int] = {}
    for r in rows:
        if r.hit_by:
            by[r.hit_by] = by.get(r.hit_by, 0) + 1
    spans = sorted(r.hit_span for r in rows if r.hit_span is not None)
    return {
        "cases": n,
        "hits": found,
        "recall": round(found / n, 3) if n else None,
        "findings_per_pr": round(sum(r.findings for r in rows) / n, 2) if n else None,
        "hits_by_tool": by,
        "median_hit_span": spans[len(spans) // 2] if spans else None,
    }


def summarize(results: Sequence[BaselineResult]) -> dict[str, Any]:
    injected = [r for r in results if r.group == "injected"]
    real = [r for r in results if r.group == "real"]
    clean = [r for r in results if r.group == "clean"]
    out: dict[str, Any] = {"injected": _recall(injected), "real": _recall(real)}
    if clean:
        flagged = sum(r.findings > 0 for r in clean)
        out["clean"] = {
            "cases": len(clean),
            "with_findings": flagged,
            "false_positive_rate": round(flagged / len(clean), 3),
            "findings_per_pr": round(sum(r.findings for r in clean) / len(clean), 2),
        }
    repos = sorted({r.repo for r in injected})
    out["by_repo"] = {name: _recall([r for r in injected if r.repo == name]) for name in repos}
    times = [r.first_run_s for r in results if r.first_run_s is not None]
    out["p50_run_s"] = percentile(times, 0.5)
    out["p95_run_s"] = percentile(times, 0.95)
    return out
