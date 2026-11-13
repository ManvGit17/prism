"""CPU throughput of the reviewer model in Ollama at 1, 2 and 4 requests in flight.

Usage: uv run python eval/reviewer_throughput.py [--hunks models/reviewer/eval/generations.jsonl]

Start the server with room for parallel requests first, or Ollama queues them one by one:

    OLLAMA_NUM_PARALLEL=4 ollama serve

The same hunks are sent at each level (after one warm-up request). Per level it records wall
time, hunks per minute, generated tokens per second over all requests, the per-request decode
speed Ollama reports, and latency percentiles. Phase 5 sets its concurrency limit from this.
Appends one JSON line per run to eval/reports/reviewer_throughput.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from prism.config import get_settings
from prism.models.reviewer import review_messages, to_raw_comment

REPORT = Path(__file__).parent / "reports" / "reviewer_throughput.jsonl"


def review(client: httpx.Client, url: str, model: str, hunk: str) -> dict[str, Any]:
    started = time.perf_counter()
    resp = client.post(
        f"{url}/api/chat",
        json={"model": model, "messages": review_messages(hunk), "stream": False},
    )
    resp.raise_for_status()
    body = resp.json()
    return {
        "latency_s": time.perf_counter() - started,
        "prompt_tokens": body.get("prompt_eval_count", 0),
        "gen_tokens": body.get("eval_count", 0),
        "decode_s": body.get("eval_duration", 0) / 1e9,
        "valid": to_raw_comment(hunk, body["message"]["content"]) is not None,
    }


def level(url: str, model: str, hunks: list[str], parallel: int) -> dict[str, Any]:
    with httpx.Client(timeout=600) as client, ThreadPoolExecutor(parallel) as pool:
        started = time.perf_counter()
        results = list(pool.map(lambda h: review(client, url, model, h), hunks))
        wall = time.perf_counter() - started
    latencies = sorted(r["latency_s"] for r in results)
    gen = sum(r["gen_tokens"] for r in results)
    return {
        "parallel": parallel,
        "hunks": len(hunks),
        "wall_s": round(wall, 1),
        "hunks_per_min": round(len(hunks) / wall * 60, 2),
        "gen_tokens_per_s": round(gen / wall, 2),
        "decode_tokens_per_s_per_request": round(
            statistics.mean(r["gen_tokens"] / r["decode_s"] for r in results if r["decode_s"]), 2
        ),
        "mean_prompt_tokens": round(statistics.mean(r["prompt_tokens"] for r in results), 1),
        "mean_gen_tokens": round(gen / len(results), 1),
        "latency_p50_s": round(latencies[len(latencies) // 2], 2),
        "latency_p95_s": round(latencies[min(int(0.95 * len(latencies)), len(latencies) - 1)], 2),
        "valid": sum(r["valid"] for r in results),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--hunks", type=Path, default=Path("models/reviewer/eval/generations.jsonl")
    )
    parser.add_argument("--n", type=int, default=24, help="hunks per level")
    parser.add_argument("--model", default="prism-reviewer:v1")
    parser.add_argument("--parallel", type=int, nargs="+", default=[1, 2, 4])
    args = parser.parse_args()

    url = get_settings().ollama_url
    with args.hunks.open(encoding="utf-8") as fh:
        hunks = [json.loads(line)["hunk"] for line in fh][: args.n]
    with httpx.Client(timeout=600) as client:
        review(client, url, args.model, hunks[0])  # load the model before timing

    levels = []
    for parallel in args.parallel:
        result = level(url, args.model, hunks, parallel)
        print(json.dumps(result), flush=True)
        levels.append(result)

    record = {
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": args.model,
        "machine": {"cpu": platform.processor(), "logical_cpus": os.cpu_count()},
        "ollama_num_parallel": os.environ.get("OLLAMA_NUM_PARALLEL"),
        "levels": levels,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
