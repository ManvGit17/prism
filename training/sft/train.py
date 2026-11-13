"""QLoRA fine-tuning of the reviewer model with the Hugging Face Trainer.

Two backends:

* `unsloth` (Kaggle T4): Qwen2.5-Coder in 4-bit NF4 through Unsloth, LoRA on all attention and
  MLP projections. Needs an NVIDIA GPU.
* `hf`: plain transformers + peft, no quantization. Only for the CPU smoke test on a tiny model.

Loss is on the assistant reply only: prompt tokens get the label -100. No packing (each example
is padded on its own), cosine learning rate, eval loss on the validation rows.
"""

from __future__ import annotations

import inspect
import json
import platform
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import torch
import transformers
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    DataCollatorForSeq2Seq,
    PreTrainedTokenizerBase,
    Trainer,
    TrainingArguments,
    set_seed,
)

# Qwen's own template, used only when a tiny test model ships without one.
CHATML_TEMPLATE = (
    "{% for m in messages %}<|im_start|>{{ m['role'] }}\n{{ m['content'] }}<|im_end|>\n"
    "{% endfor %}{% if add_generation_prompt %}<|im_start|>assistant\n{% endif %}"
)
LORA_TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")


@dataclass
class TrainConfig:
    model_name: str = "unsloth/Qwen2.5-Coder-3B-Instruct-bnb-4bit"
    backend: str = "unsloth"  # or "hf" for the CPU smoke test
    # Examples: median 244 tokens, 99th pct 478; 0.1% are over 1,024 and are dropped.
    max_seq_length: int = 1024
    # LoRA: rank of the update matrices, its scale (alpha / r) and dropout. ADR 0009.
    lora_r: int = 16
    lora_alpha: int = 16
    lora_dropout: float = 0.0
    epochs: float = 1.0
    # 8 x 2 = effective batch 16; short examples, so 8 fits a T4 in 4-bit.
    batch_size: int = 8
    grad_accum: int = 2
    eval_batch_size: int = 8
    learning_rate: float = 2e-4
    weight_decay: float = 0.01
    warmup_ratio: float = 0.03
    eval_steps: int = 250
    logging_steps: int = 25
    seed: int = 13
    report_to: str = "none"


def ensure_chat_template(tokenizer: PreTrainedTokenizerBase) -> PreTrainedTokenizerBase:
    if not getattr(tokenizer, "chat_template", None):
        tokenizer.chat_template = CHATML_TEMPLATE
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer


def render(tokenizer: PreTrainedTokenizerBase, messages: list[dict[str, str]], prompt: bool) -> str:
    """Chat template as text. `prompt=True` ends with the assistant header, for generation."""
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=prompt)
    return str(text)


def encode_example(
    tokenizer: PreTrainedTokenizerBase, messages: list[dict[str, str]], max_len: int
) -> dict[str, list[int]] | None:
    """input_ids and labels with the prompt masked. None if the example is longer than max_len."""
    if messages[-1]["role"] != "assistant":
        raise ValueError("the last message must be the assistant reply")
    prompt_text = render(tokenizer, messages[:-1], prompt=True)
    full_text = render(tokenizer, messages, prompt=False)
    if not full_text.startswith(prompt_text):
        raise ValueError("chat template does not extend the prompt; cannot mask it")
    prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
    reply_ids = tokenizer(full_text[len(prompt_text) :], add_special_tokens=False)["input_ids"]
    ids = list(prompt_ids) + list(reply_ids)
    if len(ids) > max_len:
        return None
    return {
        "input_ids": ids,
        "attention_mask": [1] * len(ids),
        "labels": [-100] * len(prompt_ids) + list(reply_ids),
    }


def encode_all(
    tokenizer: PreTrainedTokenizerBase, examples: Sequence[dict[str, Any]], max_len: int
) -> tuple[list[dict[str, list[int]]], int]:
    """Encoded examples and how many were dropped for being too long."""
    out, dropped = [], 0
    for ex in examples:
        enc = encode_example(tokenizer, ex["messages"], max_len)
        if enc is None:
            dropped += 1
        else:
            out.append(enc)
    return out, dropped


def load_model(cfg: TrainConfig, for_training: bool = True) -> tuple[Any, PreTrainedTokenizerBase]:
    """The model with LoRA adapters attached, and its tokenizer."""
    if cfg.backend == "unsloth":
        from unsloth import FastLanguageModel

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=cfg.model_name,
            max_seq_length=cfg.max_seq_length,
            dtype=None,
            load_in_4bit=True,
        )
        if for_training:
            model = FastLanguageModel.get_peft_model(
                model,
                r=cfg.lora_r,
                lora_alpha=cfg.lora_alpha,
                lora_dropout=cfg.lora_dropout,
                target_modules=list(LORA_TARGETS),
                bias="none",
                use_gradient_checkpointing="unsloth",
                random_state=cfg.seed,
            )
        return model, ensure_chat_template(tokenizer)
    if cfg.backend != "hf":
        raise ValueError(f"unknown backend {cfg.backend!r}")
    from peft import LoraConfig, get_peft_model

    tokenizer = ensure_chat_template(AutoTokenizer.from_pretrained(cfg.model_name))
    model = AutoModelForCausalLM.from_pretrained(cfg.model_name)
    if for_training:
        present = {name.rsplit(".", 1)[-1] for name, _ in model.named_modules()}
        targets = [t for t in LORA_TARGETS if t in present]
        model = get_peft_model(
            model,
            LoraConfig(
                r=cfg.lora_r,
                lora_alpha=cfg.lora_alpha,
                lora_dropout=cfg.lora_dropout,
                target_modules=targets,
                task_type="CAUSAL_LM",
            ),
        )
    return model, tokenizer


def _supported(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Keep only TrainingArguments that this transformers version still accepts."""
    params = inspect.signature(TrainingArguments.__init__).parameters
    return {k: v for k, v in kwargs.items() if k in params}


def train(
    cfg: TrainConfig,
    train_examples: Sequence[dict[str, Any]],
    valid_examples: Sequence[dict[str, Any]],
    out_dir: Path,
) -> tuple[Any, PreTrainedTokenizerBase, dict[str, Any]]:
    """Fine-tune and save the adapter to `out_dir/adapter` straight away.

    Returns the trained model (adapters attached), the tokenizer and a metrics dict.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    set_seed(cfg.seed)
    model, tokenizer = load_model(cfg)
    train_set, train_dropped = encode_all(tokenizer, train_examples, cfg.max_seq_length)
    valid_set, valid_dropped = encode_all(tokenizer, valid_examples, cfg.max_seq_length)
    if not train_set:
        raise ValueError("no training example fits max_seq_length")

    on_gpu = torch.cuda.is_available()
    bf16 = on_gpu and torch.cuda.is_bf16_supported()
    warmup: dict[str, Any] = (
        {"warmup_ratio": cfg.warmup_ratio}
        if "warmup_ratio" in inspect.signature(TrainingArguments.__init__).parameters
        else {"warmup_steps": cfg.warmup_ratio}
    )
    optional = _supported({"group_by_length": True, "data_seed": cfg.seed})
    args = TrainingArguments(
        output_dir=str(out_dir / "checkpoints"),
        num_train_epochs=cfg.epochs,
        per_device_train_batch_size=cfg.batch_size,
        per_device_eval_batch_size=cfg.eval_batch_size,
        gradient_accumulation_steps=cfg.grad_accum,
        learning_rate=cfg.learning_rate,
        weight_decay=cfg.weight_decay,
        lr_scheduler_type="cosine",
        **warmup,
        eval_strategy="steps" if valid_set else "no",
        eval_steps=cfg.eval_steps,
        save_strategy="no",
        logging_steps=cfg.logging_steps,
        fp16=on_gpu and not bf16,
        bf16=bf16,
        optim="adamw_8bit" if cfg.backend == "unsloth" else "adamw_torch",
        seed=cfg.seed,
        **optional,
        report_to=cfg.report_to,
        remove_unused_columns=False,
        disable_tqdm=True,
    )
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=train_set,
        eval_dataset=valid_set or None,
        data_collator=DataCollatorForSeq2Seq(tokenizer, padding=True, label_pad_token_id=-100),
    )
    result = trainer.train()
    adapter_dir = out_dir / "adapter"
    model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))

    metrics: dict[str, Any] = {
        "config": asdict(cfg),
        "train_examples": len(train_set),
        "train_dropped_too_long": train_dropped,
        "valid_examples": len(valid_set),
        "valid_dropped_too_long": valid_dropped,
        "global_step": trainer.state.global_step,
        "train_runtime_s": result.metrics.get("train_runtime"),
        "train_loss": result.metrics.get("train_loss"),
        "device": torch.cuda.get_device_name(0) if on_gpu else "cpu",
        "versions": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
        },
        "log_history": trainer.state.log_history,
    }
    (out_dir / "train_metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    return model, tokenizer, metrics
