"""Export the tuned reviewer as GGUF for Ollama, plus the Modelfile that registers it.

    ollama create prism-reviewer:v1 -f Modelfile

The GGUF is made by Unsloth (it merges the adapter and quantizes with llama.cpp), so this only
runs on the GPU machine. The Modelfile is plain text and is written by `modelfile()`.
"""

from __future__ import annotations

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


def export_gguf(model: Any, tokenizer: Any, out_dir: Path, quant: str = "q4_k_m") -> Path:
    """Write the GGUF and its Modelfile to `out_dir`; return the GGUF path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained_gguf(str(out_dir), tokenizer, quantization_method=quant)
    ggufs = sorted(out_dir.rglob("*.gguf"), key=lambda p: p.stat().st_size)
    quantized = [p for p in ggufs if quant.lower() in p.name.lower()] or ggufs
    if not quantized:
        raise FileNotFoundError(f"no .gguf written under {out_dir}")
    gguf = quantized[-1]
    (gguf.parent / "Modelfile").write_text(modelfile(gguf.name), encoding="utf-8")
    return gguf
