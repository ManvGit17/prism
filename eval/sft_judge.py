"""Blinded A/B judging of the base and tuned reviewer on the held-out test hunks.

    uv run python eval/sft_judge.py prepare   # writes the blinded batches and the rubric
    (a judge model fills in one verdicts file per batch)
    uv run python eval/sft_judge.py score     # unblinds and writes the win rate

For each test hunk the judge sees the diff and two comments, A and B. Which one is the tuned
model is decided by a seeded coin flip per hunk and is not written anywhere the judge reads:
`score` repeats the flips with the same seed. The reference comment is not shown; the judge
rates usefulness, not similarity to one human's wording. Hunks where both models wrote the
same comment are not sent and count as ties.

Verdicts go to `<work>/<judge>/verdicts_NN.jsonl`, one line per pair:
{"pair": 17, "winner": "A" | "B" | "tie", "reason": "..."}.
`score --tag run1` writes eval/reports/sft_judge_run1.json and sft_judge_run1_verdicts.jsonl.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Any

from prism.models.reviewer import parse_reply

REPORTS = Path(__file__).parent / "reports"
SEED = 13

RUBRIC = """\
# Judging review comments

You are judging two code review comments, A and B, written for the same diff hunk. Pick the one
a developer would rather receive. In order of weight:

1. Correct: it is true about this code. A wrong claim is worse than a vague one.
2. Specific: it is about this hunk (names a line, identifier or behaviour), not advice that
   fits any diff.
3. Actionable: the author knows what to change or what question to answer.
4. Concise: short is fine; padding, restating the diff or several unrelated points is worse.

A comment that is not valid JSON is shown as the raw text; judge what it says, but text that is
not a review comment at all loses. Answer "tie" only when neither is clearly better (both
equally good or equally useless). Do not prefer a comment for being longer or more polite.

For each pair write one JSON line: {"pair": <pair>, "winner": "A" | "B" | "tie",
"reason": "<at most 20 words>"}.
"""


def comment_text(raw: str) -> str:
    reply = parse_reply(raw)
    return reply.comment if reply else raw.strip()[:600]


def tuned_is_a(seed: int, pair: int) -> bool:
    """The coin flip for one pair; the same seed and pair always give the same side."""
    return random.Random(f"{seed}:{pair}").random() < 0.5


def blind(rows: list[dict[str, Any]], seed: int) -> tuple[list[dict[str, Any]], list[int]]:
    """Pairs to judge (A/B shuffled) and the ids of pairs that are identical, i.e. ties."""
    pairs, same = [], []
    for row in rows:
        base, tuned = comment_text(row["base"]), comment_text(row["tuned"])
        if base == tuned:
            same.append(row["id"])
            continue
        a, b = (tuned, base) if tuned_is_a(seed, row["id"]) else (base, tuned)
        pairs.append({"pair": row["id"], "lang": row["lang"], "hunk": row["hunk"], "A": a, "B": b})
    return pairs, same


def unblind(winner: str, pair: int, seed: int) -> str:
    """'tuned', 'base' or 'tie' for a verdict on pair `pair`."""
    if winner == "tie":
        return "tie"
    if winner not in ("A", "B"):
        raise ValueError(f"pair {pair}: winner must be A, B or tie, not {winner!r}")
    return "tuned" if (winner == "A") == tuned_is_a(seed, pair) else "base"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (round(100 * (centre - half), 1), round(100 * (centre + half), 1))


def tally(outcomes: list[str]) -> dict[str, Any]:
    counts = Counter(outcomes)
    n = len(outcomes)
    decided = counts["tuned"] + counts["base"]
    return {
        "pairs": n,
        "tuned_wins": counts["tuned"],
        "base_wins": counts["base"],
        "ties": counts["tie"],
        "tuned_win_rate": round(100 * counts["tuned"] / n, 1) if n else 0.0,
        "base_win_rate": round(100 * counts["base"] / n, 1) if n else 0.0,
        "tie_rate": round(100 * counts["tie"] / n, 1) if n else 0.0,
        # Share of decided pairs the tuned model won, with a 95% Wilson interval.
        "tuned_share_of_decided": round(100 * counts["tuned"] / decided, 1) if decided else 0.0,
        "tuned_share_ci95": wilson(counts["tuned"], decided),
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    # Not splitlines(): it also splits on U+2028 and friends, which occur inside JSON strings.
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.split("\n") if line.strip()]


def read_verdicts(folder: Path) -> dict[int, dict[str, Any]]:
    verdicts: dict[int, dict[str, Any]] = {}
    for path in sorted(folder.glob("verdicts_*.jsonl")):
        for v in read_jsonl(path):
            verdicts[int(v["pair"])] = v
    return verdicts


def prepare(generations: Path, work: Path, batch_size: int, seed: int) -> None:
    rows = read_jsonl(generations)
    pairs, same = blind(rows, seed)
    work.mkdir(parents=True, exist_ok=True)
    (work / "RUBRIC.md").write_text(RUBRIC, encoding="utf-8")
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i : i + batch_size]
        text = "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in batch)
        (work / f"batch_{i // batch_size:02d}.jsonl").write_text(text, encoding="utf-8")
    print(f"{len(pairs)} pairs in {math.ceil(len(pairs) / batch_size)} batches, {len(same)} ties")


def score(
    generations: Path, work: Path, judges: list[str], seed: int, tag: str = "run1"
) -> dict[str, Any]:
    rows = {r["id"]: r for r in read_jsonl(generations)}
    pairs, same = blind(list(rows.values()), seed)
    report: dict[str, Any] = {"seed": seed, "test_rows": len(rows), "identical": len(same)}
    labelled: dict[str, dict[int, str]] = {}
    out_lines = []
    for judge in judges:
        verdicts = read_verdicts(work / judge)
        outcome = {p: unblind(v["winner"], p, seed) for p, v in verdicts.items()}
        labelled[judge] = outcome
        missing = [p["pair"] for p in pairs if p["pair"] not in verdicts]
        full = list(outcome.values()) + ["tie"] * len(same)
        report[judge] = {"judged": len(verdicts), "missing": len(missing), **tally(full)}
        for p, v in sorted(verdicts.items()):
            out_lines.append(
                {
                    "judge": judge,
                    "pair": p,
                    "winner": outcome[p],
                    # The reason names sides; this says which side was the tuned model.
                    "tuned_was": "A" if tuned_is_a(seed, p) else "B",
                    "reason": v["reason"],
                }
            )
    if len(judges) == 2:
        a, b = labelled[judges[0]], labelled[judges[1]]
        common = sorted(set(a) & set(b))
        agree = sum(a[p] == b[p] for p in common)
        report["agreement"] = {
            "judges": judges,
            "pairs": len(common),
            "agree": agree,
            "rate": round(100 * agree / len(common), 1) if common else 0.0,
        }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"sft_judge_{tag}.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    (REPORTS / f"sft_judge_{tag}_verdicts.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out_lines), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "score"])
    parser.add_argument(
        "--generations", type=Path, default=Path("models/reviewer/eval/generations.jsonl")
    )
    parser.add_argument("--work", type=Path, default=Path("models/reviewer/judge"))
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--judges", nargs="+", default=["sonnet"])
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--tag", default="run1", help="names the report files")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.generations, args.work, args.batch_size, args.seed)
    else:
        print(
            json.dumps(
                score(args.generations, args.work, args.judges, args.seed, args.tag), indent=2
            )
        )


if __name__ == "__main__":
    main()
