"""One command for the reviewer pipeline: format -> train -> evaluate -> export GGUF.

    python -m training.sft.run --data Comment_Generation.zip --out sft_out

Unsloth runs on one GPU; on a 2-GPU Kaggle machine start it with CUDA_VISIBLE_DEVICES=0.

Everything is written to `--out` as soon as it exists, and `metrics.json` is rewritten after
every stage, so a run cut off by a time limit still leaves what it finished:
`data/` (the formatted splits), `adapter/` (LoRA weights), `eval/generations.jsonl`, `gguf/`
(model and Modelfile) and `metrics.json`.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import fields
from pathlib import Path
from typing import Any

from training.gate.train import wandb_report_target
from training.sft.evaluate import evaluate
from training.sft.export import export_gguf
from training.sft.format import build, read_jsonl
from training.sft.train import TrainConfig, train


def build_parser() -> argparse.ArgumentParser:
    d = TrainConfig()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--data", type=Path, required=True, help="Comment_Generation.zip or a folder")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--train-rows", type=int, default=40000)
    p.add_argument("--valid-rows", type=int, default=500, help="for eval loss during training")
    p.add_argument("--test-rows", type=int, default=500, help="held-out rows for base vs tuned")
    p.add_argument("--head", action="store_true", help="first rows instead of a sample (dry runs)")
    p.add_argument("--gen-batch-size", type=int, default=16)
    p.add_argument("--max-new-tokens", type=int, default=128)
    p.add_argument("--no-export", action="store_true", help="skip the GGUF export")
    p.add_argument("--quant", default="q4_k_m")
    p.add_argument(
        "--scratch", type=Path, default=None, help="where the export's ~6 GB scratch goes"
    )
    for f in fields(TrainConfig):
        if f.name == "report_to":
            continue
        default = getattr(d, f.name)
        p.add_argument("--" + f.name.replace("_", "-"), type=type(default), default=default)
    return p


def run(args: argparse.Namespace) -> dict[str, Any]:
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    metrics: dict[str, Any] = {"stages": {}}

    def save() -> None:
        (out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")

    def stage(name: str, started: float) -> None:
        metrics["stages"][name] = round(time.perf_counter() - started, 1)
        print(f"stage {name} done in {metrics['stages'][name]} s", flush=True)
        save()

    started = time.perf_counter()
    data_dir = out / "data"
    metrics["data"] = build(
        args.data, data_dir, args.train_rows, args.valid_rows, args.test_rows, head=args.head
    )
    stage("format", started)

    cfg = TrainConfig(
        **{f.name: getattr(args, f.name) for f in fields(TrainConfig) if f.name != "report_to"},
        report_to=wandb_report_target(),
    )
    started = time.perf_counter()
    model, tokenizer, train_metrics = train(
        cfg, read_jsonl(data_dir / "train.jsonl"), read_jsonl(data_dir / "valid.jsonl"), out
    )
    metrics["train"] = {k: v for k, v in train_metrics.items() if k != "log_history"}
    metrics["eval_loss"] = [
        {"step": h["step"], "eval_loss": h["eval_loss"]}
        for h in train_metrics["log_history"]
        if "eval_loss" in h
    ]
    stage("train", started)

    started = time.perf_counter()
    if cfg.backend == "unsloth":
        from unsloth import FastLanguageModel

        FastLanguageModel.for_inference(model)
    metrics["test"] = evaluate(
        model,
        tokenizer,
        read_jsonl(data_dir / "test.jsonl"),
        out / "eval",
        args.gen_batch_size,
        args.max_new_tokens,
    )
    print(json.dumps(metrics["test"], indent=2), flush=True)
    stage("evaluate", started)

    if not args.no_export:
        started = time.perf_counter()
        gguf = export_gguf(model, tokenizer, out / "gguf", args.quant, args.scratch)
        metrics["gguf"] = {"file": gguf.name, "bytes": gguf.stat().st_size}
        stage("export", started)
    return metrics


def main(argv: list[str] | None = None) -> None:
    run(build_parser().parse_args(argv))


if __name__ == "__main__":
    main()
