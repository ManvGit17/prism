import json
import subprocess
import sys
from pathlib import Path

import pytest

from prism.config import Settings
from prism.models.gate import (
    Gate,
    GateConfig,
    GateError,
    PassAllGate,
    load_gate,
    softmax_positive,
)


def test_pass_all_gate() -> None:
    gate = PassAllGate()
    assert isinstance(gate, Gate)
    assert gate.name == "pass-all"
    assert gate.score(["a", "b"]) == [1.0, 1.0]
    assert gate.needs_review(["a", "b", "c"]) == [True, True, True]
    assert gate.needs_review([]) == []


def test_default_setting_is_pass_all() -> None:
    assert isinstance(load_gate(Settings()), PassAllGate)


def test_onnx_with_missing_directory_fails_loudly(tmp_path: Path) -> None:
    settings = Settings(gate="onnx", gate_model_dir=str(tmp_path / "nope"))
    with pytest.raises(GateError, match="PRISM_GATE_MODEL_DIR"):
        load_gate(settings)


def test_onnx_with_empty_directory_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(GateError, match=r"gate_config\.json"):
        load_gate(Settings(gate="onnx", gate_model_dir=str(tmp_path)))


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PRISM_GATE", "onnx")
    monkeypatch.setenv("PRISM_GATE_MODEL_DIR", str(tmp_path / "gone"))
    with pytest.raises(GateError):
        load_gate()


def test_config_round_trip(tmp_path: Path) -> None:
    cfg = GateConfig(
        model_name="microsoft/codebert-base",
        max_length=512,
        threshold=0.37,
        target_recall=0.9,
        quantized=True,
        onnx_file="model.int8.onnx",
    )
    path = cfg.save(tmp_path)
    assert path.name == "gate_config.json"
    assert GateConfig.load(tmp_path) == cfg
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["labels"] == {"0": "no_comment", "1": "needs_comment"}
    assert raw["truncation_side"] == "left"


def test_config_rejects_bad_threshold(tmp_path: Path) -> None:
    (tmp_path / "gate_config.json").write_text(
        json.dumps({"model_name": "m", "max_length": 512, "threshold": 1.5}), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        GateConfig.load(tmp_path)


def test_softmax_positive() -> None:
    import numpy as np

    probs = softmax_positive(np.array([[0.0, 0.0], [0.0, 100.0], [100.0, 0.0]], dtype=np.float32))
    assert probs[0] == pytest.approx(0.5)
    assert probs[1] == pytest.approx(1.0)
    assert probs[2] == pytest.approx(0.0)


def test_serving_module_does_not_import_torch() -> None:
    code = "import sys, prism.models.gate; assert 'torch' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)
