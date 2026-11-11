"""Measure embedding speed of a full index, cold and warm, into the persistent stores.

Usage: uv run python eval/embedding_speed.py <repo path> [--repo-id ID]

Cold: the repo's vectors are deleted first, so every function is embedded.
Warm: the same index again; functions whose text hash is unchanged are skipped.
Also times one Ollama request through "localhost" and through "127.0.0.1".
Appends one JSON line per run to eval/reports/embedding_speed.jsonl.
"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from prism.config import get_settings
from prism.indexer.pipeline import index_repository
from prism.store.factory import open_stores

REPORT = Path(__file__).parent / "reports" / "embedding_speed.jsonl"


def request_latency(host: str, model: str, n: int = 5) -> float:
    with httpx.Client(timeout=120) as client:
        started = time.perf_counter()
        for _ in range(n):
            client.post(
                f"http://{host}:11434/api/embed", json={"model": model, "input": ["def f(): pass"]}
            ).raise_for_status()
        return (time.perf_counter() - started) / n


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--repo-id")
    args = parser.parse_args()
    rid = args.repo_id or args.path.resolve().name
    settings = get_settings()
    stores = open_stores("persistent")
    assert stores.vectors is not None and stores.embedder is not None
    rows = []
    try:
        for host in ("localhost", "127.0.0.1"):
            rows.append(
                {"run": f"request via {host}", "seconds_per_request": request_latency(
                    host, settings.embedding_model
                )}
            )  # fmt: skip
        stores.vectors.delete_repo(rid)
        for run in ("cold", "warm"):
            report = index_repository(
                args.path,
                rid,
                stores.graph,
                vectors=stores.vectors,
                embedder=stores.embedder,
                ledger=stores.ledger,
            )
            functions = report.vectors + report.vectors_unchanged
            rows.append(
                {
                    "run": run,
                    "functions": functions,
                    "embedded": report.vectors,
                    "unchanged": report.vectors_unchanged,
                    "embed_seconds": round(report.embed_seconds, 2),
                    "total_seconds": round(report.seconds, 2),
                    "ms_per_embedded_function": round(
                        1000 * report.embed_seconds / report.vectors, 1
                    )
                    if report.vectors
                    else None,
                }
            )
    finally:
        stores.close()
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("a", encoding="utf-8") as out:
        for row in rows:
            line = {
                "at": stamp,
                "repo": rid,
                "model": settings.embedding_model,
                "batch_size": settings.embedding_batch_size,
                **row,
            }
            out.write(json.dumps(line) + "\n")
            print(json.dumps(line))


if __name__ == "__main__":
    main()
