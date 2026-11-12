"""One command for the whole gate pipeline: data -> train -> threshold -> export -> parity.

    python -m training.gate.run --data data/codereviewer/Diff_Quality_Estimation.zip --out gate_out

Outputs in `--out`: `best/` (PyTorch checkpoint), `onnx/` (model, tokenizer, gate_config.json)
and `metrics.json`.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

import torch
from transformers import AutoModelForSequenceClassification

from prism.models.gate import softmax_positive
from training.gate.data import class_balance, drop_long, load_split
from training.gate.export_onnx import export, parity_check
from training.gate.threshold import choose_and_report
from training.gate.train import (
    TrainConfig,
    make_tokenizer,
    predict_logits,
    train,
    wandb_report_target,
)


def build_parser() -> argparse.ArgumentParser:
    d = TrainConfig()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--data", type=Path, required=True, help="the zip, or a folder of jsonl")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--max-train-rows", type=int, default=None, help="seeded sample; default all")
    p.add_argument(
        "--head",
        action="store_true",
        help="with --max-*-rows take the first rows instead of a random sample (fast; dry runs)",
    )
    p.add_argument(
        "--max-train-lines",
        type=int,
        default=100,
        help="drop train hunks longer than this many lines (valid and test stop at about 85)",
    )
    p.add_argument("--max-valid-rows", type=int, default=None)
    p.add_argument("--max-test-rows", type=int, default=None)
    p.add_argument("--target-recall", type=float, default=0.90)
    p.add_argument("--quantize", action="store_true", help="export int8 as well")
    p.add_argument("--parity-rows", type=int, default=32)
    for f in fields(TrainConfig):
        if f.name == "report_to":
            continue
        default = getattr(d, f.name)
        flag = "--" + f.name.replace("_", "-")
        if isinstance(default, bool):
            p.add_argument(flag, action=argparse.BooleanOptionalAction, default=default)
        else:
            p.add_argument(flag, type=type(default), default=default)
    return p


def run(args: argparse.Namespace) -> dict[str, Any]:
    cfg_values = {
        f.name: getattr(args, f.name) for f in fields(TrainConfig) if f.name != "report_to"
    }
    cfg = TrainConfig(**cfg_values, report_to=wandb_report_target())
    out: Path = args.out

    print("reading data", flush=True)
    sampled = load_split(args.data, "train", args.max_train_rows, cfg.seed, args.head)
    train_rows = drop_long(sampled, args.max_train_lines)
    print(f"train rows over {args.max_train_lines} lines dropped: {len(sampled) - len(train_rows)}")
    valid_rows = load_split(args.data, "valid", args.max_valid_rows, cfg.seed, args.head)
    test_rows = load_split(args.data, "test", args.max_test_rows, cfg.seed, args.head)
    balance = {
        "train": class_balance(train_rows),
        "valid": class_balance(valid_rows),
        "test": class_balance(test_rows),
    }
    print("class balance:", json.dumps(balance), flush=True)

    metrics = train(cfg, train_rows, valid_rows, out)
    best = out / "best"

    # from_pretrained loads onto the CPU. Scoring 60k hunks there takes hours, so move it.
    model = AutoModelForSequenceClassification.from_pretrained(str(best))
    model.to("cuda" if torch.cuda.is_available() else "cpu")
    tokenizer = make_tokenizer(str(best), cfg.truncation_side)

    def scores(rows: list[Any]) -> list[float]:
        logits = predict_logits(
            model, tokenizer, [r.text for r in rows], cfg.eval_batch_size, cfg.max_length
        )
        return [float(s) for s in softmax_positive(logits)]

    valid_scores = scores(valid_rows)
    test_scores = scores(test_rows)
    result = choose_and_report(
        [r.label for r in valid_rows],
        valid_scores,
        [r.label for r in test_rows],
        test_scores,
        args.target_recall,
    )
    threshold = float(result["threshold"])  # type: ignore[arg-type]
    print(json.dumps(result, indent=2), flush=True)

    onnx_dir = out / "onnx"
    export(
        best,
        onnx_dir,
        cfg.model_name,
        cfg.max_length,
        cfg.truncation_side,
        threshold,
        args.target_recall,
        args.quantize,
    )
    texts = [r.text for r in valid_rows[: args.parity_rows]]
    max_diff = parity_check(best, onnx_dir, texts)
    print(f"onnx parity: max abs logit difference {max_diff:.2e}", flush=True)

    metrics.update(
        {"class_balance": balance, "threshold": result, "onnx_parity_max_diff": max_diff}
    )
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return metrics


def main(argv: list[str] | None = None) -> None:
    run(build_parser().parse_args(argv))


if __name__ == "__main__":
    main()
