"""The gate: a cheap classifier that decides whether a diff hunk needs a review comment.

`PassAllGate` is the stub used until a trained model exists. `OnnxGate` serves the CodeBERT
classifier trained in `training/gate` (ADR 0008) with onnxruntime and `tokenizers`; this module
must never import torch.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from prism.config import Settings

CONFIG_FILE = "gate_config.json"
TOKENIZER_FILE = "tokenizer.json"


class GateError(RuntimeError):
    """The gate cannot be loaded or run."""


@runtime_checkable
class Gate(Protocol):
    name: str

    def score(self, hunks: Sequence[str]) -> list[float]:
        """Probability that each hunk needs a review comment, in [0, 1]."""
        ...

    def needs_review(self, hunks: Sequence[str]) -> list[bool]:
        """True where the hunk should go on to the reviewer."""
        ...


class GateConfig(BaseModel):
    """What the exported model needs to be served. Written by training/gate/export_onnx.py."""

    model_name: str
    onnx_file: str = "model.onnx"
    quantized: bool = False
    max_length: int = Field(ge=8)
    # Which end of a long hunk is kept (ADR 0008).
    truncation_side: Literal["left", "right"] = "left"
    # A hunk needs review when its score is at or above this.
    threshold: float = Field(ge=0.0, le=1.0)
    target_recall: float | None = None
    # Index 1 is the "needs a comment" class; the score is the softmax probability of it.
    labels: dict[str, str] = {"0": "no_comment", "1": "needs_comment"}
    positive_label: int = 1

    def save(self, directory: Path) -> Path:
        path = directory / CONFIG_FILE
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, directory: Path) -> GateConfig:
        path = directory / CONFIG_FILE
        if not path.is_file():
            raise GateError(f"{path} not found")
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))


class PassAllGate:
    """Every hunk needs review. Used until a trained gate exists."""

    name = "pass-all"

    def score(self, hunks: Sequence[str]) -> list[float]:
        return [1.0 for _ in hunks]

    def needs_review(self, hunks: Sequence[str]) -> list[bool]:
        return [True for _ in hunks]


def softmax_positive(logits: npt.NDArray[np.float32], positive: int = 1) -> npt.NDArray[np.float32]:
    """Softmax over the last axis, returning the probability of class `positive`."""
    shifted = logits - logits.max(axis=-1, keepdims=True)
    exp = np.exp(shifted)
    probs = (exp / exp.sum(axis=-1, keepdims=True)).astype(np.float32)
    out: npt.NDArray[np.float32] = probs[..., positive]
    return out


class OnnxGate:
    """CodeBERT classifier served with onnxruntime. Loads from a directory written by export."""

    def __init__(self, model_dir: str | Path, batch_size: int = 16) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        directory = Path(model_dir).expanduser()
        self.config = GateConfig.load(directory)
        onnx_path = directory / self.config.onnx_file
        tok_path = directory / TOKENIZER_FILE
        for path in (onnx_path, tok_path):
            if not path.is_file():
                raise GateError(f"{path} not found")
        self.name = f"onnx:{self.config.model_name}"
        self.batch_size = batch_size
        self.threshold = self.config.threshold

        tok = Tokenizer.from_file(str(tok_path))
        pad_id = tok.token_to_id("<pad>")
        if pad_id is None:
            raise GateError(f"{tok_path} has no <pad> token")
        tok.enable_truncation(
            max_length=self.config.max_length, direction=self.config.truncation_side
        )
        tok.enable_padding(pad_id=pad_id, pad_token="<pad>")
        self._tokenizer = tok
        self._session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        self._input_names = {i.name for i in self._session.get_inputs()}

    def logits(self, hunks: Sequence[str]) -> npt.NDArray[np.float32]:
        """Raw logits, shape (n, 2). Exposed for the parity tests."""
        chunks: list[npt.NDArray[np.float32]] = []
        for start in range(0, len(hunks), self.batch_size):
            batch = list(hunks[start : start + self.batch_size])
            encodings = self._tokenizer.encode_batch(batch)
            feeds = {
                "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
            }
            feeds = {k: v for k, v in feeds.items() if k in self._input_names}
            out = self._session.run(["logits"], feeds)[0]
            chunks.append(np.asarray(out, dtype=np.float32))
        if not chunks:
            return np.zeros((0, 2), dtype=np.float32)
        return np.concatenate(chunks, axis=0)

    def score(self, hunks: Sequence[str]) -> list[float]:
        if not hunks:
            return []
        probs = softmax_positive(self.logits(hunks), self.config.positive_label)
        return [float(p) for p in probs]

    def needs_review(self, hunks: Sequence[str]) -> list[bool]:
        return [s >= self.threshold for s in self.score(hunks)]


def load_gate(settings: Settings | None = None) -> Gate:
    """Build the gate chosen by PRISM_GATE. `onnx` with a missing model directory is an error."""
    if settings is None:
        from prism.config import get_settings

        settings = get_settings()
    if settings.gate == "pass-all":
        return PassAllGate()
    directory = Path(settings.gate_model_dir).expanduser()
    if not directory.is_dir():
        raise GateError(
            f"PRISM_GATE=onnx but PRISM_GATE_MODEL_DIR={settings.gate_model_dir!r} is not a "
            "directory. Train the gate (training/README.md) or set PRISM_GATE=pass-all."
        )
    return OnnxGate(directory)
