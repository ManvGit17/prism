"""BugBench runs as Langfuse experiments, so every configuration sits side by side in one UI.

The benchmark becomes a Langfuse dataset (one item per case, the defect's location as the
expected output). Each finished run is then replayed as an experiment over that dataset: the
task returns what the run posted for the case, read from its results, so nothing is reviewed
again and no model is called. Item scores are the hit at ±3 lines, the number of comments and,
once the comments are judged, whether one names the defect and how many are valid; run scores
are recall per group and the share of clean PRs with a comment.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from prism.bench.cases import CaseSet
from prism.bench.judge import posted

DATASET = "bugbench"


def item_payload(repo: str, case: Any) -> dict[str, Any]:
    return {
        "id": f"{DATASET}-{case.id}",
        "input": {
            "case": case.id,
            "repo": repo,
            "group": case.group,
            "kind": case.kind,
            "commit": case.commit,
        },
        "expected_output": None if case.group == "clean" else {"path": case.path},
        "metadata": {"group": case.group, "repo": repo},
    }


def item_scores(
    row: dict[str, Any] | None, verdicts: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Scores for one case of one run (`name`, `value`)."""
    if row is None:
        return []
    ps = posted(row)
    scores = [{"name": "comments", "value": len(ps)}]
    if row["group"] != "clean":
        scores.append({"name": "hit", "value": 1 if row["hit"] else 0})
    judged = [verdicts[p.key] for p in ps if p.key in verdicts]
    if ps and len(judged) == len(ps):
        scores.append(
            {"name": "valid_share", "value": sum(bool(v.get("valid")) for v in judged) / len(ps)}
        )
        if row["group"] != "clean":
            named = any(v.get("names_defect") for v in judged)
            scores.append({"name": "names_defect", "value": 1 if named else 0})
    return scores


def run_scores(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for group in ("injected", "real"):
        rs = [r for r in rows if r["group"] == group]
        if rs:
            out.append(
                {"name": f"recall_{group}", "value": sum(bool(r["hit"]) for r in rs) / len(rs)}
            )
    clean = [r for r in rows if r["group"] == "clean"]
    if clean:
        flagged = sum(len(posted(r)) > 0 for r in clean)
        out.append({"name": "clean_flagged", "value": flagged / len(clean)})
    return out


def publish(
    client: Any,
    sets: Sequence[CaseSet],
    root: Path,
    runs: Sequence[tuple[str, str]],
) -> list[str]:
    """Create the dataset and one experiment per (name, run). Returns the experiment names."""
    from langfuse import Evaluation

    # It may exist already; items are upserted by id below.
    with contextlib.suppress(Exception):
        client.create_dataset(name=DATASET, description="BugBench: planted, real and clean PRs")
    for cs in sets:
        for case in cs.cases:
            client.create_dataset_item(dataset_name=DATASET, **item_payload(cs.repo, case))
    ids = {c.id for cs in sets for c in cs.cases}
    judge = root / "judge"
    verdicts: dict[str, dict[str, Any]] = {}
    if judge.is_dir():
        from prism.bench.judge import answers

        verdicts = answers(judge)
    dataset = client.get_dataset(DATASET)
    done: list[str] = []
    for name, run in runs:
        text = (root / "runs" / run / "results.jsonl").read_text("utf-8")
        rows = {r["case_id"]: r for r in map(json.loads, text.splitlines()) if r["case_id"] in ids}

        def task(*, item: Any, rows: dict[str, dict[str, Any]] = rows, **_: Any) -> Any:
            row = rows.get(item.input["case"])
            return (
                [
                    {"path": p.path, "lines": [p.start, p.end], "comment": p.text}
                    for p in posted(row)
                ]
                if row
                else []
            )

        def evaluate(*, input: Any, rows: dict[str, dict[str, Any]] = rows, **_: Any) -> list[Any]:
            return [Evaluation(**s) for s in item_scores(rows.get(input["case"]), verdicts)]

        def summarize(*, rows: dict[str, dict[str, Any]] = rows, **_: Any) -> list[Any]:
            return [Evaluation(**s) for s in run_scores(list(rows.values()))]

        dataset.run_experiment(
            name=name,
            run_name=name,
            description=f"BugBench run {run}, replayed from its results",
            task=task,
            evaluators=[evaluate],
            run_evaluators=[summarize],
            max_concurrency=8,
            metadata={"run": run},
        )
        done.append(name)
    client.flush()
    return done
