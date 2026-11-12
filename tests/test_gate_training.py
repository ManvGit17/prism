"""CPU smoke tests for the gate training pipeline on a tiny model. Need the `training` group."""

from pathlib import Path

import pytest

pytest.importorskip("torch")
pytest.importorskip("transformers")
pytest.importorskip("onnx")

import numpy as np

from prism.models.gate import OnnxGate
from training.gate.data import load_split
from training.gate.export_onnx import export, parity_check
from training.gate.run import build_parser, run
from training.gate.threshold import pr_auc

pytestmark = pytest.mark.training

FIXTURES = Path(__file__).parent / "fixtures" / "gate"
TINY = "hf-internal-testing/tiny-random-RobertaModel"


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("gate_out")
    args = build_parser().parse_args(
        [
            "--data", str(FIXTURES),
            "--out", str(out),
            "--model-name", TINY,
            "--max-length", "64",
            "--max-train-rows", "24",
            "--epochs", "2",
            "--batch-size", "4",
            "--grad-accum", "1",
            "--eval-batch-size", "4",
            "--eval-steps", "3",
            "--logging-steps", "1",
            "--learning-rate", "1e-3",
            "--no-fp16",
            "--parity-rows", "6",
        ]
    )  # fmt: skip
    run(args)
    return out


def test_pipeline_writes_outputs(pipeline: Path) -> None:
    import json

    assert (pipeline / "best").is_dir()
    onnx_dir = pipeline / "onnx"
    for name in ("model.onnx", "gate_config.json", "tokenizer.json"):
        assert (onnx_dir / name).is_file()
    metrics = json.loads((pipeline / "metrics.json").read_text(encoding="utf-8"))
    assert 0.0 <= metrics["threshold"]["threshold"] <= 1.0
    assert "filtered_share" in metrics["threshold"]["test"]
    assert metrics["onnx_parity_max_diff"] < 1e-3
    assert metrics["class_balance"]["train"]["rows"] == 24


def test_onnx_gate_matches_pytorch(pipeline: Path) -> None:
    from transformers import AutoModelForSequenceClassification

    from training.gate.train import make_tokenizer, predict_logits

    texts = [r.text for r in load_split(FIXTURES, "test")]
    gate = OnnxGate(pipeline / "onnx")
    model = AutoModelForSequenceClassification.from_pretrained(str(pipeline / "best"))
    tok = make_tokenizer(str(pipeline / "best"), "left")
    expected = predict_logits(model, tok, texts, 4, 64)
    np.testing.assert_allclose(gate.logits(texts), expected, atol=1e-3)
    # Batch size must not change the result.
    gate.batch_size = 1
    np.testing.assert_allclose(gate.logits(texts), expected, atol=1e-3)
    flags = gate.needs_review(texts)
    assert flags == [s >= gate.threshold for s in gate.score(texts)]


def test_long_hunk_is_truncated_the_same_way(pipeline: Path) -> None:
    long = "\n".join(f"+    value_{i} = compute({i})" for i in range(400))
    gate = OnnxGate(pipeline / "onnx")
    assert parity_check(pipeline / "best", pipeline / "onnx", [long, "x"]) < 1e-3
    assert 0.0 <= gate.score([long])[0] <= 1.0


def test_int8_export_and_parity(pipeline: Path, tmp_path: Path) -> None:
    cfg = export(pipeline / "best", tmp_path, TINY, 64, "left", 0.5, 0.9, quantize=True)
    assert cfg.quantized
    assert (tmp_path / "model.int8.onnx").is_file()
    texts = [r.text for r in load_split(FIXTURES, "test")]
    parity_check(pipeline / "best", tmp_path, texts)


def test_pr_auc_matches_scikit_learn() -> None:
    from sklearn.metrics import average_precision_score

    rng = np.random.default_rng(3)
    y = rng.integers(0, 2, 500)
    s = np.round(rng.random(500), 2)  # rounded, so there are ties
    assert pr_auc(y, s) == pytest.approx(average_precision_score(y, s))
