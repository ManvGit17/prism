"""Fine-tune CodeBERT as the gate classifier with the Hugging Face Trainer.

Defaults fit a free Kaggle T4 (16 GB): fp16, per-device batch 16 at 512 tokens, 2 gradient
accumulation steps (effective batch 32). Early stopping watches validation PR-AUC. Every
hyperparameter is a command-line argument of `training.gate.run`.
"""

from __future__ import annotations

import inspect
import json
import os
import platform
import shutil
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import torch
import transformers
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    EarlyStoppingCallback,
    PreTrainedModel,
    PreTrainedTokenizerBase,
    Trainer,
    TrainingArguments,
    set_seed,
)

from prism.models.gate import softmax_positive
from training.gate.data import Example, class_balance
from training.gate.threshold import pr_auc

LABELS = {0: "no_comment", 1: "needs_comment"}


@dataclass
class TrainConfig:
    model_name: str = "microsoft/codebert-base"
    # CodeBERT has 514 positions, so 512 tokens is its limit. Hunks longer than this lose their
    # start (ADR 0008).
    max_length: int = 512
    truncation_side: str = "left"
    epochs: float = 3.0
    # 16 x 512 tokens in fp16 fits a T4 with room to spare; 2 accumulation steps give 32.
    batch_size: int = 16
    grad_accum: int = 2
    eval_batch_size: int = 64
    learning_rate: float = 2e-5
    weight_decay: float = 0.01
    warmup_ratio: float = 0.06
    # Evaluate and checkpoint this often (in optimizer steps).
    eval_steps: int = 2000
    early_stopping_patience: int = 3
    seed: int = 13
    fp16: bool = True
    num_workers: int = 0
    logging_steps: int = 100
    # Set by the runner when WANDB_API_KEY is present; "none" writes local files only.
    report_to: str = "none"


def wandb_report_target() -> str:
    """Return "wandb" only when a key is set and the package is installed; never required."""
    if not os.environ.get("WANDB_API_KEY"):
        return "none"
    try:
        import wandb  # noqa: F401
    except ImportError:
        return "none"
    return "wandb"


class TextDataset:
    """Map-style dataset over examples; tokenising happens per batch in the collator."""

    def __init__(self, examples: Sequence[Example]) -> None:
        self.examples = list(examples)

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, i: int) -> dict[str, Any]:
        e = self.examples[i]
        return {"text": e.text, "label": e.label}


class Collator:
    """Tokenise a batch, padded to its longest item, cut to max_length."""

    def __init__(self, tokenizer: PreTrainedTokenizerBase, max_length: int) -> None:
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __call__(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        enc = self.tokenizer(
            [it["text"] for it in items],
            truncation=True,
            max_length=self.max_length,
            padding=True,
            return_tensors="pt",
        )
        batch: dict[str, Any] = dict(enc)
        batch["labels"] = torch.tensor([it["label"] for it in items], dtype=torch.long)
        return batch


def make_tokenizer(name_or_path: str, truncation_side: str) -> PreTrainedTokenizerBase:
    tok = AutoTokenizer.from_pretrained(name_or_path)
    tok.truncation_side = truncation_side
    return tok


def compute_metrics(eval_pred: Any) -> dict[str, float]:
    logits, labels = eval_pred
    scores = softmax_positive(np.asarray(logits, dtype=np.float32))
    labels = np.asarray(labels)
    pred = scores >= 0.5
    tp = float(np.sum(pred & (labels == 1)))
    precision = tp / max(float(pred.sum()), 1.0)
    recall = tp / max(float((labels == 1).sum()), 1.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    auc = pr_auc(labels, scores.astype(np.float64)) if (labels == 1).any() else 0.0
    return {"pr_auc": auc, "f1_at_0.5": f1}


def predict_logits(
    model: PreTrainedModel,
    tokenizer: PreTrainedTokenizerBase,
    texts: Sequence[str],
    batch_size: int,
    max_length: int,
) -> npt.NDArray[np.float32]:
    """Logits, shape (n, 2), in the order of `texts`. Batches are grouped by length for speed."""
    device = next(model.parameters()).device
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    out = np.zeros((len(texts), 2), dtype=np.float32)
    collate = Collator(tokenizer, max_length)
    model.train(False)
    with torch.no_grad():
        for start in range(0, len(order), batch_size):
            idx = order[start : start + batch_size]
            batch = collate([{"text": texts[i], "label": 0} for i in idx])
            batch.pop("labels")
            batch = {k: v.to(device) for k, v in batch.items()}
            use_amp = device.type == "cuda"
            with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=use_amp):
                logits = model(**batch).logits
            out[idx] = logits.float().cpu().numpy()
    return out


def train(
    cfg: TrainConfig,
    train_rows: Sequence[Example],
    valid_rows: Sequence[Example],
    out_dir: Path,
) -> dict[str, Any]:
    """Fine-tune, keep the best checkpoint by validation PR-AUC, return the metrics dict.

    Writes `out_dir/best` (model and tokenizer) and `out_dir/metrics.json`.
    """
    if not any(e.label == 1 for e in valid_rows):
        raise ValueError("the validation rows have no positives, so PR-AUC is undefined")
    out_dir.mkdir(parents=True, exist_ok=True)
    set_seed(cfg.seed)

    tokenizer = make_tokenizer(cfg.model_name, cfg.truncation_side)
    model = AutoModelForSequenceClassification.from_pretrained(
        cfg.model_name, num_labels=2, id2label=LABELS, label2id={v: k for k, v in LABELS.items()}
    )
    on_gpu = torch.cuda.is_available()
    # transformers 5 folded warmup_ratio into warmup_steps (a float below 1 is a ratio).
    warmup: dict[str, Any] = (
        {"warmup_ratio": cfg.warmup_ratio}
        if "warmup_ratio" in inspect.signature(TrainingArguments.__init__).parameters
        else {"warmup_steps": cfg.warmup_ratio}
    )
    args = TrainingArguments(
        output_dir=str(out_dir / "checkpoints"),
        num_train_epochs=cfg.epochs,
        per_device_train_batch_size=cfg.batch_size,
        per_device_eval_batch_size=cfg.eval_batch_size,
        gradient_accumulation_steps=cfg.grad_accum,
        learning_rate=cfg.learning_rate,
        weight_decay=cfg.weight_decay,
        **warmup,
        eval_strategy="steps",
        eval_steps=cfg.eval_steps,
        save_strategy="steps",
        save_steps=cfg.eval_steps,
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="pr_auc",
        greater_is_better=True,
        logging_steps=cfg.logging_steps,
        fp16=cfg.fp16 and on_gpu,
        dataloader_num_workers=cfg.num_workers,
        seed=cfg.seed,
        data_seed=cfg.seed,
        report_to=cfg.report_to,
        remove_unused_columns=False,
        disable_tqdm=True,
    )
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=TextDataset(train_rows),
        eval_dataset=TextDataset(valid_rows),
        data_collator=Collator(tokenizer, cfg.max_length),
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=cfg.early_stopping_patience)],
    )
    result = trainer.train()
    best_dir = out_dir / "best"
    trainer.save_model(str(best_dir))
    tokenizer.save_pretrained(str(best_dir))

    state = trainer.state
    metrics: dict[str, Any] = {
        "config": asdict(cfg),
        "train_balance": class_balance(list(train_rows)),
        "valid_balance": class_balance(list(valid_rows)),
        "best_metric_pr_auc": state.best_metric,
        "best_checkpoint": state.best_model_checkpoint,
        "global_step": state.global_step,
        "epochs_run": state.epoch,
        "train_runtime_s": result.metrics.get("train_runtime"),
        "device": torch.cuda.get_device_name(0) if on_gpu else "cpu",
        "versions": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
        },
        "log_history": state.log_history,
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(out_dir / "checkpoints", ignore_errors=True)
    return metrics
