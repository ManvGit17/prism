"""The whole BugBench evaluation from one config file (`make eval-bugbench`).

`bugbench/configs.toml` lists every configuration: a name for the table, a run folder, and what
it changes (context mode, local model, verifier, settings), plus PR-Agent as the baseline. For
each one the suite runs the benchmark (from cached answers where they exist), then judges every
posted comment, sweeps the confidence threshold where asked, and writes one report. While Claude
answers are missing it stops and lists the handoff rounds to answer; run it again afterwards.

A run can be seeded from earlier runs: case folders it does not have yet are copied from the
first seed that has them, so their cached answers are reused. Only seed from a run that asked
the same questions (same context mode, model and routing); request ids do not cover the
retrieved context.
"""

from __future__ import annotations

import glob
import shutil
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prism.bench.cases import CaseSet, load
from prism.config import Settings

_KEEP_OUT = {"handoff", "results.jsonl", "summary.json", "sweep.json"}


@dataclass
class RunSpec:
    name: str
    run: str
    kind: str = "prism"  # or "pr-agent"
    mode: str = "graph"
    model: str | None = None
    verifier: str = "handoff"
    settings: dict[str, Any] = field(default_factory=dict)
    seed: list[str] = field(default_factory=list)
    # Runs to take only the local model's cached replies from (same model and prompt, but
    # different Claude requests, as with another context mode).
    seed_replies: list[str] = field(default_factory=list)
    sweep: bool = False


@dataclass
class Suite:
    cases: list[Path]
    report: Path
    settings: dict[str, Any]
    runs: list[RunSpec]
    thresholds: list[float]


def load_suite(path: Path) -> Suite:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    base = path.parent.parent  # configs live in bugbench/, paths are from the repo root
    skip = {str((base / p).resolve()) for p in data.get("exclude", [])}
    cases = sorted(
        {
            Path(p).resolve()
            for pattern in data["cases"]
            for p in glob.glob(str(base / pattern))
            if str(Path(p).resolve()) not in skip
        }
    )
    runs = [RunSpec(**r) for r in data["run"]]
    names = [r.run for r in runs]
    if len(names) != len(set(names)):
        raise ValueError("every configuration needs its own run folder")
    return Suite(
        cases=cases,
        report=base / data["report"],
        settings=data.get("settings", {}),
        runs=runs,
        thresholds=[float(t) for t in data.get("thresholds", [])],
    )


def seed(root: Path, spec: RunSpec) -> int:
    """Copy into the run what it lacks from its seeds. Returns how many entries were copied."""
    target = root / "runs" / spec.run
    copied = 0
    for src_name in spec.seed:
        src = root / "runs" / src_name
        if not src.is_dir():
            continue
        target.mkdir(parents=True, exist_ok=True)
        for entry in src.iterdir():
            dest = target / entry.name
            if entry.name in _KEEP_OUT or dest.exists():
                continue
            if entry.is_dir():
                shutil.copytree(entry, dest)
            else:
                shutil.copy2(entry, dest)
            copied += 1
    for src_name in spec.seed_replies:
        for replies in (root / "runs" / src_name).glob("*/replies.json"):
            dest = target / replies.parent.name / "replies.json"
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(replies, dest)
                copied += 1
    return copied


def settings_for(base: Settings, suite: Suite, spec: RunSpec) -> Settings:
    update = {
        "review_store": "off",
        "tracing": "off",
        **suite.settings,
        **spec.settings,
    }
    return base.model_copy(update=update)


async def run_suite(
    suite: Suite,
    root: Path,
    base: Settings,
    *,
    pr_agent: Path,
    notes: Callable[[str], None] = print,
) -> tuple[list[Path], str | None]:
    """(handoff rounds still to answer, report text once everything is answered)."""
    from prism.bench.baseline import run_baseline
    from prism.bench.judge import judge_runs
    from prism.bench.report import render_judged, render_table
    from prism.bench.run import RunConfig, run_cases
    from prism.bench.sweep import render_sweep, run_sweep

    sets: list[CaseSet] = [load(p) for p in suite.cases]
    waiting: list[Path] = []
    for spec in suite.runs:
        if seed(root, spec):
            notes(f"{spec.run}: seeded from {', '.join(spec.seed + spec.seed_replies)}")
        if spec.kind == "pr-agent":
            got = run_baseline(sets, root, spec.run, pr_agent, notes=notes)
            if got.waiting and got.round_dir is not None:
                waiting.append(got.round_dir)
            continue
        cfg = RunConfig(root, spec.run, mode=spec.mode, verifier=spec.verifier, model=spec.model)
        result = await run_cases(sets, cfg, settings_for(base, suite, spec), notes=notes)
        if result.waiting and result.round_dir is not None:
            waiting.append(result.round_dir)
    if waiting:
        return waiting, None

    judged, judge_round = judge_runs(sets, root, [s.run for s in suite.runs])
    if judge_round is not None:
        return [judge_round], None

    import json

    def rows(run: str) -> list[dict[str, Any]]:
        text = (root / "runs" / run / "results.jsonl").read_text("utf-8")
        return [json.loads(x) for x in text.splitlines() if x.strip()]

    text = render_table([(s.name, rows(s.run)) for s in suite.runs], "BugBench results")
    text += "\n" + render_judged([(s.name, judged[s.run]) for s in suite.runs])
    sweeps = []
    for spec in suite.runs:
        if spec.sweep and suite.thresholds:
            cfg = RunConfig(
                root, spec.run, mode=spec.mode, verifier=spec.verifier, model=spec.model
            )
            sw = await run_sweep(
                sets, cfg, settings_for(base, suite, spec), suite.thresholds, notes
            )
            sweeps.append((spec.name, sw))
    if sweeps:
        text += "\n" + render_sweep(sweeps, "Confidence threshold sweep")
    suite.report.write_text(text, encoding="utf-8", newline="\n")
    return [], text
