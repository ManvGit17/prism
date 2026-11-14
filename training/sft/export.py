"""Export the tuned reviewer as GGUF for Ollama, plus the Modelfile that registers it.

    ollama create prism-reviewer:v1 -f Modelfile

The GGUF is made by Unsloth (it merges the adapter and quantizes with llama.cpp), so this only
runs on the GPU machine. The Modelfile is plain text and is written by `modelfile()`.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from prism.models.reviewer import SYSTEM_PROMPT

# Qwen2.5 chat format. Ollama fills .System and .Prompt; the reply ends at <|im_end|>.
TEMPLATE = (
    "{{ if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n{{ end }}"
    "{{ if .Prompt }}<|im_start|>user\n{{ .Prompt }}<|im_end|>\n{{ end }}"
    "<|im_start|>assistant\n"
)


def modelfile(gguf_name: str) -> str:
    """Modelfile text for a GGUF next to it. Greedy decoding, the training system prompt."""
    return (
        f"FROM ./{gguf_name}\n"
        f'TEMPLATE """{TEMPLATE}"""\n'
        f'SYSTEM """{SYSTEM_PROMPT}"""\n'
        "PARAMETER temperature 0\n"
        "PARAMETER num_predict 128\n"
        'PARAMETER stop "<|im_end|>"\n'
        'PARAMETER stop "<|im_start|>"\n'
    )


def export_gguf(
    model: Any,
    tokenizer: Any,
    out_dir: Path,
    quant: str = "q4_k_m",
    scratch_root: Path | None = None,
) -> Path:
    """Write the quantized GGUF and its Modelfile to `out_dir`; return the GGUF path.

    Unsloth saves the merged 16-bit model (about 6 GB) in the folder it is given and puts the
    GGUF in a sibling folder named `<folder>_gguf`. Both are scratch: the GGUF is moved into
    `out_dir` and the rest is deleted, so a run under /kaggle/working keeps only what is needed.
    `scratch_root` (default: next to `out_dir`) is where those scratch folders go.
    """
    scratch = (scratch_root or out_dir.parent) / f"{out_dir.name}_merged"
    scratch_gguf = scratch.parent / f"{scratch.name}_gguf"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    model.save_pretrained_gguf(str(scratch), tokenizer, quantization_method=quant)
    ggufs = sorted(
        (p for d in (scratch_gguf, scratch) if d.is_dir() for p in d.rglob("*.gguf")),
        key=lambda p: p.stat().st_size,
    )
    quantized = [p for p in ggufs if quant.lower() in p.name.lower()] or ggufs
    if not quantized:
        raise FileNotFoundError(f"no .gguf written under {scratch} or {scratch_gguf}")
    out_dir.mkdir(parents=True, exist_ok=True)
    gguf = Path(shutil.move(str(quantized[-1]), str(out_dir / quantized[-1].name)))
    (out_dir / "Modelfile").write_text(modelfile(gguf.name), encoding="utf-8")
    for folder in (scratch, scratch_gguf):
        shutil.rmtree(folder, ignore_errors=True)
    return gguf
