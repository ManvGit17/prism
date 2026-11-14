"""CPU smoke tests for the reviewer fine-tuning pipeline on a tiny random Qwen2.5 (16 MB).

Needs the `training` group. Unsloth and 4-bit loading need an NVIDIA GPU, so this runs the `hf`
backend: same formatting, masking, Trainer, generation and scoring, LoRA without quantization.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("torch")
pytest.importorskip("transformers")
pytest.importorskip("peft")

from transformers import AutoTokenizer

from prism.models.reviewer import training_messages
from training.sft.run import build_parser, run
from training.sft.train import encode_example

pytestmark = pytest.mark.training

FIXTURES = Path(__file__).parent / "fixtures" / "sft"
TINY = "yujiepan/qwen2.5-tiny-random"


def test_only_the_reply_is_trained_on() -> None:
    tokenizer = AutoTokenizer.from_pretrained(TINY)
    messages = training_messages("@@ -1,1 +1,1 @@\n-a\n+b", "Why rename `a`?")
    enc = encode_example(tokenizer, messages, max_len=4096)
    assert enc is not None
    labelled = [t for t, lab in zip(enc["input_ids"], enc["labels"], strict=True) if lab != -100]
    text = str(tokenizer.decode(labelled))
    assert text.startswith('{"comment": "Why rename `a`?"}')
    assert text.rstrip().endswith("<|im_end|>")
    assert "```diff" not in text
    assert encode_example(tokenizer, messages, max_len=10) is None


def test_pipeline(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "--data", str(FIXTURES),
            "--out", str(tmp_path),
            "--model-name", TINY,
            "--backend", "hf",
            "--train-rows", "12",
            "--valid-rows", "4",
            "--test-rows", "4",
            "--max-seq-length", "512",
            "--batch-size", "2",
            "--grad-accum", "1",
            "--eval-batch-size", "2",
            "--eval-steps", "3",
            "--logging-steps", "1",
            "--gen-batch-size", "2",
            "--max-new-tokens", "8",
            "--no-export",
        ]
    )  # fmt: skip
    metrics = run(args)

    assert (tmp_path / "adapter" / "adapter_config.json").is_file()
    assert not (tmp_path / "checkpoints").exists(), "checkpoints are removed after a full run"
    saved = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert set(saved["stages"]) == {"format", "train", "evaluate"}
    assert saved["train"]["train_examples"] == 12
    assert saved["eval_loss"], "eval loss was never logged"
    test = metrics["test"]
    assert test["examples"] == 4
    for side in ("base", "tuned"):
        assert set(test[side]) == {"bleu4", "rouge_l", "valid_json", "mean_words"}
    rows = (tmp_path / "eval" / "generations.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 4
    assert set(json.loads(rows[0])) == {"id", "lang", "hunk", "reference", "base", "tuned"}
