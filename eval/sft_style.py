"""What the reviewer's comments look like: base, each tuned run and the human references.

    uv run python eval/sft_style.py --run run1=models/reviewer/eval/generations.jsonl \
        --run run2=models/reviewer2/eval/generations.jsonl

All runs must be on the same test rows. Base and the human reference are taken from the first
run. For each source it counts how long comments are, how many name code (a backtick), ask a
question, open with "I think", repeat word for word, look like linter output, or would be
dropped as vague by the run 2 training filter (`training/sft/format.py`). Writes
eval/reports/sft_style.json.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from eval.sft_judge import REPORTS, comment_text, read_jsonl
from training.sft.format import FilterConfig, Row, is_bot_comment, reject_reason


def style(comments: list[str], hunks: list[str]) -> dict[str, Any]:
    n = len(comments)
    keys = Counter(c.lower().strip(" .!?") for c in comments)
    cfg = FilterConfig()

    def share(hits: int) -> float:
        return round(100 * hits / n, 1)

    return {
        "comments": n,
        "mean_words": round(sum(len(c.split()) for c in comments) / n, 1),
        "backtick": share(sum("`" in c for c in comments)),
        "question": share(sum("?" in c for c in comments)),
        "starts_i_think": share(sum(c.lower().startswith("i think") for c in comments)),
        "distinct": len(keys),
        "most_common": [[text, count] for text, count in keys.most_common(3) if count > 1],
        "bot_or_lint": share(sum(is_bot_comment(c) for c in comments)),
        "vague": share(
            sum(
                reject_reason(Row(h, c), cfg) == "vague"
                for c, h in zip(comments, hunks, strict=True)
            )
        ),
    }


def build(runs: dict[str, Path]) -> dict[str, Any]:
    loaded = {name: read_jsonl(path) for name, path in runs.items()}
    first = next(iter(loaded.values()))
    ids = [r["id"] for r in first]
    for name, rows in loaded.items():
        if [r["id"] for r in rows] != ids:
            raise ValueError(f"{name} is not on the same test rows as the first run")
    hunks = [r["hunk"] for r in first]
    report = {
        "base": style([comment_text(r["base"]) for r in first], hunks),
        **{
            name: style([comment_text(r["tuned"]) for r in rows], hunks)
            for name, rows in loaded.items()
        },
        "human": style([r["reference"] for r in first], hunks),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="append", required=True, help="name=generations.jsonl")
    args = parser.parse_args()
    runs = {}
    for item in args.run:
        name, _, path = item.partition("=")
        runs[name] = Path(path)
    report = build(runs)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "sft_style.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
