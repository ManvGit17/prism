"""Generate review comments for held-out hunks and score them.

Base and tuned are the same loaded model: the base answers come from running it with the LoRA
adapter switched off, so both see the same weights, prompt and decoding (greedy).
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import torch
from transformers import PreTrainedTokenizerBase

from prism.models.reviewer import parse_reply
from training.sft.metrics import corpus_scores
from training.sft.train import render


def generate(
    model: Any,
    tokenizer: PreTrainedTokenizerBase,
    prompts: Sequence[list[dict[str, str]]],
    batch_size: int = 16,
    max_new_tokens: int = 128,
) -> list[str]:
    """Greedy replies, in the order of `prompts`. Batches are grouped by prompt length."""
    device = next(model.parameters()).device
    texts = [render(tokenizer, p, prompt=True) for p in prompts]
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    out = [""] * len(texts)
    side = tokenizer.padding_side
    tokenizer.padding_side = "left"
    try:
        for start in range(0, len(order), batch_size):
            idx = order[start : start + batch_size]
            batch = tokenizer(
                [texts[i] for i in idx],
                return_tensors="pt",
                padding=True,
                add_special_tokens=False,
            ).to(device)
            with torch.no_grad():
                ids = model.generate(
                    **batch,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    pad_token_id=tokenizer.pad_token_id,
                )
            new = ids[:, batch["input_ids"].shape[1] :]
            for i, text in zip(
                idx, tokenizer.batch_decode(new, skip_special_tokens=True), strict=True
            ):
                out[i] = text.strip()
    finally:
        tokenizer.padding_side = side
    return out


@contextlib.contextmanager
def adapter_off(model: Any) -> Iterator[None]:
    """Run the base model by disabling the LoRA adapter (a peft model), else do nothing."""
    disable = getattr(model, "disable_adapter", None)
    if disable is None:
        yield
        return
    with disable():
        yield


def score(references: Sequence[str], replies: Sequence[str]) -> dict[str, float]:
    """BLEU-4, ROUGE-L, valid-JSON share and mean comment length.

    For BLEU and ROUGE an invalid reply is scored on its raw text, so the text metrics and the
    JSON rate are measured separately.
    """
    parsed = [parse_reply(r) for r in replies]
    comments = [p.comment if p else r for p, r in zip(parsed, replies, strict=True)]
    n = len(replies) or 1
    return {
        **corpus_scores(references, comments),
        "valid_json": 100 * sum(p is not None for p in parsed) / n,
        "mean_words": sum(len(c.split()) for c in comments) / n,
    }


def evaluate(
    model: Any,
    tokenizer: PreTrainedTokenizerBase,
    examples: Sequence[dict[str, Any]],
    out_dir: Path,
    batch_size: int = 16,
    max_new_tokens: int = 128,
) -> dict[str, Any]:
    """Score base and tuned on `examples`; write `generations.jsonl`; return the metrics."""
    prompts = [ex["messages"][:-1] for ex in examples]
    references = [json.loads(ex["messages"][-1]["content"])["comment"] for ex in examples]
    model.eval()
    timings: dict[str, float] = {}
    started = time.perf_counter()
    tuned = generate(model, tokenizer, prompts, batch_size, max_new_tokens)
    timings["tuned_s"] = time.perf_counter() - started
    started = time.perf_counter()
    with adapter_off(model):
        base = generate(model, tokenizer, prompts, batch_size, max_new_tokens)
    timings["base_s"] = time.perf_counter() - started

    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "generations.jsonl").open("w", encoding="utf-8") as fh:
        for i, ex in enumerate(examples):
            row = {
                "id": i,
                "lang": ex.get("lang"),
                "hunk": ex["messages"][1]["content"],
                "reference": references[i],
                "base": base[i],
                "tuned": tuned[i],
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {
        "examples": len(examples),
        "max_new_tokens": max_new_tokens,
        "base": score(references, base),
        "tuned": score(references, tuned),
        "timings": timings,
    }
