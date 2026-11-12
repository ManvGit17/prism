"""Export the trained classifier to ONNX and check it against PyTorch.

Writes `model.onnx` (opset 17, dynamic batch and sequence length), the tokenizer and
`gate_config.json` into the output directory. With `--quantize` it also writes an int8
dynamically quantised model and uses that one for serving.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt
import torch
from transformers import AutoModelForSequenceClassification

from prism.models.gate import GateConfig, OnnxGate
from training.gate.train import make_tokenizer, predict_logits

OPSET = 17
# fp32 export: logits differ by float rounding only. int8 is lossy, so it gets a looser bound.
PARITY_ATOL = 1e-3
PARITY_ATOL_INT8 = 0.5


class _Wrapper(torch.nn.Module):
    """Plain tensors in, logits out, so the ONNX graph has a clean signature."""

    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        out: torch.Tensor = self.model(input_ids=input_ids, attention_mask=attention_mask).logits
        return out


def export(
    checkpoint_dir: Path,
    out_dir: Path,
    model_name: str,
    max_length: int,
    truncation_side: str,
    threshold: float,
    target_recall: float | None = None,
    quantize: bool = False,
) -> GateConfig:
    """Export `checkpoint_dir` (a saved model and tokenizer) to `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        str(checkpoint_dir), attn_implementation="eager"
    )
    model.eval().float().cpu()
    tokenizer = make_tokenizer(str(checkpoint_dir), truncation_side)
    tokenizer.save_pretrained(str(out_dir))

    sample = tokenizer(
        ["x = 1", "def f(a, b):\n    return a + b"], padding=True, return_tensors="pt"
    )
    onnx_path = out_dir / "model.onnx"
    with torch.no_grad():
        torch.onnx.export(
            _Wrapper(model),
            (sample["input_ids"], sample["attention_mask"]),
            str(onnx_path),
            input_names=["input_ids", "attention_mask"],
            output_names=["logits"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "seq"},
                "attention_mask": {0: "batch", 1: "seq"},
                "logits": {0: "batch"},
            },
            opset_version=OPSET,
            dynamo=False,
        )
    served = onnx_path.name
    if quantize:
        from onnxruntime.quantization import QuantType, quantize_dynamic

        served = "model.int8.onnx"
        quantize_dynamic(str(onnx_path), str(out_dir / served), weight_type=QuantType.QInt8)

    config = GateConfig(
        model_name=model_name,
        onnx_file=served,
        quantized=quantize,
        max_length=max_length,
        truncation_side="left" if truncation_side == "left" else "right",
        threshold=threshold,
        target_recall=target_recall,
    )
    config.save(out_dir)
    return config


def parity_check(
    checkpoint_dir: Path,
    gate_dir: Path,
    texts: Sequence[str],
    atol: float | None = None,
) -> float:
    """Largest absolute logit difference between PyTorch and the ONNX gate on `texts`.

    Raises AssertionError above `atol` (default: 1e-3 for fp32, 0.5 for int8).
    """
    gate = OnnxGate(gate_dir)
    cfg = gate.config
    if atol is None:
        atol = PARITY_ATOL_INT8 if cfg.quantized else PARITY_ATOL
    model = AutoModelForSequenceClassification.from_pretrained(str(checkpoint_dir))
    tokenizer = make_tokenizer(str(checkpoint_dir), cfg.truncation_side)
    expected: npt.NDArray[np.float32] = predict_logits(
        model.float().cpu(), tokenizer, texts, 4, cfg.max_length
    )
    actual = gate.logits(texts)
    diff = float(np.max(np.abs(expected - actual)))
    if diff > atol:
        raise AssertionError(f"ONNX and PyTorch logits differ by {diff:.2e} (limit {atol:.0e})")
    return diff


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Export a trained gate checkpoint to ONNX.")
    p.add_argument("checkpoint_dir", type=Path)
    p.add_argument("out_dir", type=Path)
    p.add_argument("--model-name", default="microsoft/codebert-base")
    p.add_argument("--max-length", type=int, default=512)
    p.add_argument("--truncation-side", choices=["left", "right"], default="left")
    p.add_argument("--threshold", type=float, required=True)
    p.add_argument("--target-recall", type=float, default=0.90)
    p.add_argument("--quantize", action="store_true", help="int8 dynamic quantisation")
    a = p.parse_args(argv)
    export(
        a.checkpoint_dir,
        a.out_dir,
        a.model_name,
        a.max_length,
        a.truncation_side,
        a.threshold,
        a.target_recall,
        a.quantize,
    )


if __name__ == "__main__":
    main()
