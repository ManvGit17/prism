"""Running a BugBench case set through the review pipeline, with the Claude handoff batched.

Each case is reviewed in its own work dir under `<root>/runs/<run>/<case>`. With the handoff
verifier a case stops while Claude's answers are missing, so the runner gathers every case's
open requests into one folder, `<run>/handoff/round_NN`, with ids prefixed by the case id. A
Claude session answers those files with the usual rubrics, the next run hands each answer back
to its case, and the cases are reviewed again (replies are cached, so no local model runs
twice). Once no case is waiting, every case is scored and the results are written.
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prism.bench.cases import CaseSet, Materialized, materialize
from prism.bench.score import CaseResult, score_case, summarize
from prism.config import Settings
from prism.review.handoff import BATCH_SIZE, read_answers
from prism.review.service import ReviewRequest, review_async

SEP = "::"
_PAIRS = (("requests", "verdicts"), ("escalations", "reviews"))
_RUBRICS = ("RUBRIC.md", "REVIEW_RUBRIC.md")


@dataclass
class RunConfig:
    root: Path
    run: str
    mode: str = "graph"
    verifier: str = "handoff"
    model: str | None = None
    sast: bool = True


@dataclass
class RunResult:
    run_dir: Path
    waiting: list[str] = field(default_factory=list)  # case ids still waiting for answers
    round_dir: Path | None = None
    results: list[CaseResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def _rows(directory: Path, prefix: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob(f"{prefix}_*.jsonl")):
        rows += [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return rows


def _rounds(handoff: Path) -> list[Path]:
    return sorted(p for p in handoff.glob("round_*") if p.is_dir())


def import_answers(run_dir: Path, case_ids: list[str]) -> int:
    """Hand every answer in the handoff rounds back to its case. Returns how many."""
    handoff = run_dir / "handoff"
    count = 0
    for _, ans in _PAIRS:
        by_case: dict[str, list[dict[str, Any]]] = {c: [] for c in case_ids}
        for rd in _rounds(handoff):
            answers, _ = read_answers(rd, ans)
            for full, obj in answers.items():
                case, sep, rid = full.partition(SEP)
                if sep and case in by_case:
                    by_case[case].append({**obj, "id": rid})
        for case, rows in by_case.items():
            target = run_dir / case / "verify" / f"{ans}_bench.jsonl"
            if not rows:
                target.unlink(missing_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
            )
            count += len(rows)
    return count


def export_requests(run_dir: Path, case_ids: list[str]) -> Path | None:
    """Every open request of every waiting case in one handoff round (reused if unchanged)."""
    pending: dict[str, list[dict[str, Any]]] = {req: [] for req, _ in _PAIRS}
    rubrics: dict[str, Path] = {}
    for case in case_ids:
        verify = run_dir / case / "verify"
        for req, ans in _PAIRS:
            answered, _ = read_answers(verify, ans)
            for row in _rows(verify, req):
                if row["id"] not in answered:
                    pending[req].append({**row, "id": f"{case}{SEP}{row['id']}"})
        for name in _RUBRICS:
            if (verify / name).exists():
                rubrics.setdefault(name, verify / name)
    wanted = {r["id"] for rows in pending.values() for r in rows}
    if not wanted:
        return None
    handoff = run_dir / "handoff"
    rounds = _rounds(handoff)
    if rounds:
        last = rounds[-1]
        have = {r["id"] for req, _ in _PAIRS for r in _rows(last, req)}
        started = any(any(last.glob(f"{ans}_*.jsonl")) for _, ans in _PAIRS)
        if have == wanted and not started:
            return last
    rd = handoff / f"round_{len(rounds) + 1:02d}"
    rd.mkdir(parents=True, exist_ok=True)
    for req, rows in pending.items():
        for start in range(0, len(rows), BATCH_SIZE):
            chunk = rows[start : start + BATCH_SIZE]
            text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in chunk)
            (rd / f"{req}_{start // BATCH_SIZE:02d}.jsonl").write_text(text, encoding="utf-8")
    for name, src in rubrics.items():
        shutil.copyfile(src, rd / name)
    return rd


def _prefixed(notes: Callable[[str], None], case_id: str) -> Callable[[str], None]:
    return lambda msg: notes(f"{case_id}: {msg}")


def _timing(case_dir: Path) -> dict[str, Any]:
    path = case_dir / "bench.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


async def run_cases(
    cs: CaseSet,
    cfg: RunConfig,
    settings: Settings,
    notes: Callable[[str], None] = print,
    only: set[str] | None = None,
) -> RunResult:
    run_dir = cfg.root / "runs" / cfg.run
    cases = [c for c in cs.cases if only is None or c.id in only]
    ids = [c.id for c in cases]
    imported = import_answers(run_dir, ids)
    if imported:
        notes(f"{imported} answer(s) handed back to their cases")
    out = RunResult(run_dir)
    finished: list[tuple[Materialized, Any]] = []
    for case in cases:
        m = materialize(cfg.root, cs, case)
        case_dir = run_dir / case.id
        timing = _timing(case_dir)
        started = time.monotonic()
        outcome = await review_async(
            ReviewRequest(
                repo=m.checkout,
                diff_text=m.diff,
                repo_id=cs.repo,
                work=case_dir,
                verifier=cfg.verifier,
                model=cfg.model,
                store="memory",
                mode=cfg.mode,
                sast=cfg.sast,
                save=False,
            ),
            settings=settings,
            notes=_prefixed(notes, case.id),
        )
        elapsed = round(time.monotonic() - started, 2)
        if "first_run_s" not in timing:
            timing = {"first_run_s": elapsed, "line": m.line}
            case_dir.mkdir(parents=True, exist_ok=True)
            (case_dir / "bench.json").write_text(json.dumps(timing), encoding="utf-8")
        state = outcome.status
        notes(f"{case.id}: {state}, {len(outcome.review.comments)} comment(s), {elapsed}s")
        if outcome.pending:
            out.waiting.append(case.id)
        else:
            finished.append((m, outcome))
    if out.waiting:
        out.round_dir = export_requests(run_dir, out.waiting)
        return out
    for m, outcome in finished:
        out.results.append(
            score_case(
                m.case.id,
                m.case.kind,
                m.case.path,
                m.line,
                outcome.review.comments,
                outcome.state.get("decisions", []),
                _timing(run_dir / m.case.id).get("first_run_s"),
            )
        )
    out.summary = {
        **summarize(out.results),
        "repo": cs.repo,
        "run": cfg.run,
        "settings": {
            "reviewer": cfg.model or settings.reviewer_model,
            "gate": settings.gate,
            "context_mode": cfg.mode,
            "verifier": cfg.verifier,
            "sast": cfg.sast,
            "escalate": settings.escalate,
            "min_confidence": settings.verifier_min_confidence,
        },
    }
    with (run_dir / "results.jsonl").open("w", encoding="utf-8") as fh:
        for r in out.results:
            fh.write(json.dumps(r.to_json(), ensure_ascii=False) + "\n")
    (run_dir / "summary.json").write_text(json.dumps(out.summary, indent=2), encoding="utf-8")
    return out
