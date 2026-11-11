"""How far the character-based token estimate is from the Qwen2.5-Coder tokenizer.

Usage: uv run python eval/token_counter_check.py <repo path> [<repo path> ...]

Tokenizes every function's source with the real tokenizer (downloaded once,
about 7 MB) and with `CharTokenCounter`, and appends a summary per repo to
eval/reports/token_counter.jsonl.
"""

import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

from prism.indexer.pipeline import discover_files, parse_sources
from prism.retrieval.tokens import CharTokenCounter, QwenTokenCounter

REPORT = Path(__file__).parent / "reports" / "token_counter.jsonl"


def main() -> None:
    qwen = QwenTokenCounter()
    approx = CharTokenCounter()
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    for arg in sys.argv[1:]:
        root = Path(arg)
        paths, _ = discover_files(root)
        parsed = parse_sources("check", {p: (root / p).read_bytes() for p in paths})
        ratios: list[float] = []
        errors: list[float] = []
        for pf in parsed:
            for fn in pf.functions:
                start, end = fn.span
                text = pf.source[start:end].decode("utf-8", "replace")
                real = qwen.count(text)
                if real < 20:
                    continue
                ratios.append(len(text) / real)
                errors.append((approx.count(text) - real) / real)
        errors.sort()
        row = {
            "at": stamp,
            "repo": root.name,
            "functions": len(errors),
            "chars_per_token_median": round(statistics.median(ratios), 2),
            "chars_per_token_p10": round(statistics.quantiles(ratios, n=10)[0], 2),
            "chars_per_token_p90": round(statistics.quantiles(ratios, n=10)[-1], 2),
            "estimate_chars_per_token": approx.chars_per_token,
            "estimate_error_median": round(statistics.median(errors), 3),
            "estimate_error_p5": round(errors[int(0.05 * len(errors))], 3),
            "estimate_error_p95": round(errors[int(0.95 * len(errors))], 3),
            "share_underestimated": round(sum(e < 0 for e in errors) / len(errors), 3),
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        with REPORT.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row) + "\n")
        print(json.dumps(row))


if __name__ == "__main__":
    main()
