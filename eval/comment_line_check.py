"""How often a CodeReviewer comment can be tied to one line of its hunk (ADR 0009).

Usage: uv run python eval/comment_line_check.py <Comment_Generation.zip> [--split valid]

The dataset gives a hunk and a comment, no line. This counts, per row, whether the comment shares
identifiers with exactly one changed line (the rule `anchor_line` uses first), with several, or
with none, and how many hunks have a single changed line. Writes
eval/reports/comment_line_check.json.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from prism.models.gate import normalize_hunk
from prism.models.reviewer import hunk_lines, identifiers
from training.gate.data import iter_lines

REPORT = Path(__file__).parent / "reports" / "comment_line_check.json"


def classify(hunk: str, comment: str) -> str:
    changed = [text for kind, _, text in hunk_lines(hunk) if kind != " "]
    words = identifiers(comment)
    scores = [len(words & identifiers(text)) for text in changed]
    best = max(scores, default=0)
    if best == 0:
        return "no_shared_identifier"
    return "one_best_line" if scores.count(best) == 1 else "tie"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    parser.add_argument("--split", default="valid", choices=["train", "valid", "test"])
    args = parser.parse_args()

    kinds: Counter[str] = Counter()
    y_values: Counter[int] = Counter()
    single_changed = 0
    lines_per_hunk: list[int] = []
    words_per_comment: list[int] = []
    for line in iter_lines(args.data, args.split):
        obj = json.loads(line)
        y_values[int(obj["y"])] += 1
        hunk, comment = normalize_hunk(str(obj["patch"])), str(obj["msg"])
        kinds[classify(hunk, comment)] += 1
        single_changed += sum(kind != " " for kind, _, _ in hunk_lines(hunk)) == 1
        lines_per_hunk.append(hunk.count("\n") + 1)
        words_per_comment.append(len(comment.split()))

    n = sum(kinds.values())

    def pct(values: list[int], q: float) -> int:
        return sorted(values)[min(int(q * len(values)), len(values) - 1)]

    report = {
        "split": args.split,
        "rows": n,
        "share": {k: v / n for k, v in kinds.most_common()},
        "y_values": {str(k): v for k, v in sorted(y_values.items())},
        "single_changed_line_share": single_changed / n,
        "hunk_lines": {
            "median": pct(lines_per_hunk, 0.5),
            "p90": pct(lines_per_hunk, 0.9),
            "p99": pct(lines_per_hunk, 0.99),
        },
        "comment_words": {
            "median": pct(words_per_comment, 0.5),
            "p90": pct(words_per_comment, 0.9),
        },
    }
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
