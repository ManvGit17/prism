"""Where the trained gate misses: recall and filtered share by language and by hunk length.

Usage: uv run python eval/gate_breakdown.py <Diff_Quality_Estimation.zip> [--rows 3000]

Scores a seeded random sample of the test split with the ONNX gate in PRISM_GATE_MODEL_DIR
(on the CPU, as served) and writes eval/reports/gate_breakdown.json. The train split has no
language for its positives, so this uses test only. Also records hunks per second.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prism.config import get_settings
from prism.models.gate import OnnxGate, normalize_hunk
from training.gate.data import iter_lines

REPORT = Path(__file__).parent / "reports" / "gate_breakdown.json"
LENGTH_BUCKETS = ((1, 10), (11, 25), (26, 50), (51, 10**9))


def sample_rows(source: Path, n: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    reservoir: list[tuple[int, str]] = []
    for i, line in enumerate(iter_lines(source, "test")):
        if i < n:
            reservoir.append((i, line))
        else:
            j = rng.randint(0, i)
            if j < n:
                reservoir[j] = (i, line)
    reservoir.sort(key=lambda item: item[0])
    rows = []
    for _, line in reservoir:
        obj = json.loads(line)
        text = normalize_hunk(str(obj["patch"]))
        rows.append(
            {
                "text": text,
                "label": int(obj["y"]),
                "lang": str(obj.get("lang", "?")).lstrip("."),
                "lines": text.count("\n") + 1,
            }
        )
    return rows


def length_bucket(lines: int) -> str:
    for lo, hi in LENGTH_BUCKETS:
        if lo <= lines <= hi:
            return f"{lo}+" if hi == 10**9 else f"{lo}-{hi}"
    return "?"


def summary(items: list[tuple[int, bool]]) -> dict[str, Any]:
    """items: (label, sent to reviewer)."""
    pos = [kept for label, kept in items if label == 1]
    kept_all = [kept for _, kept in items]
    tp = sum(pos)
    return {
        "hunks": len(items),
        "positives": len(pos),
        "recall": tp / len(pos) if pos else None,
        "precision": tp / sum(kept_all) if sum(kept_all) else None,
        "filtered_share": 1 - sum(kept_all) / len(items) if items else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    parser.add_argument("--rows", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    settings = get_settings()
    gate = OnnxGate(settings.gate_model_dir)
    rows = sample_rows(args.data, args.rows, args.seed)

    started = time.perf_counter()
    kept = gate.needs_review([r["text"] for r in rows])
    seconds = time.perf_counter() - started

    by_lang: dict[str, list[tuple[int, bool]]] = defaultdict(list)
    by_len: dict[str, list[tuple[int, bool]]] = defaultdict(list)
    for r, k in zip(rows, kept, strict=True):
        by_lang[r["lang"]].append((r["label"], k))
        by_len[length_bucket(r["lines"])].append((r["label"], k))

    report = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model_dir": settings.gate_model_dir,
        "threshold": gate.threshold,
        "split": "test",
        "rows": len(rows),
        "seed": args.seed,
        "hunks_per_second_cpu": len(rows) / seconds,
        "overall": summary([(r["label"], k) for r, k in zip(rows, kept, strict=True)]),
        "by_language": {k: summary(v) for k, v in sorted(by_lang.items())},
        "by_lines": {
            k: summary(by_len[k])
            for k in sorted(by_len, key=lambda b: int(b.split("-")[0].rstrip("+")))
        },
    }
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
