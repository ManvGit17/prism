"""Token counting for the context budget.

The default counts characters and divides by a fixed ratio, which needs no
download. `eval/token_counter_check.py` measures how far it is from the
Qwen2.5-Coder tokenizer on real code (ADR 0007). `QwenTokenCounter` counts
exactly with that tokenizer (about 7 MB, downloaded once).
"""

import math
from pathlib import Path
from typing import Protocol

from prism.config import get_settings

QWEN_TOKENIZER_REPO = "Qwen/Qwen2.5-Coder-3B-Instruct"


class TokenCounter(Protocol):
    @property
    def name(self) -> str: ...

    def count(self, text: str) -> int: ...


class CharTokenCounter:
    """`ceil(len(text) / chars_per_token)`. Errs high on most code, so budgets rarely overflow."""

    def __init__(self, chars_per_token: float | None = None) -> None:
        self.chars_per_token = chars_per_token or get_settings().token_chars_per_token

    @property
    def name(self) -> str:
        return f"chars/{self.chars_per_token:g}"

    def count(self, text: str) -> int:
        return math.ceil(len(text) / self.chars_per_token) if text else 0


class QwenTokenCounter:
    """Exact counts with the Qwen2.5-Coder tokenizer, the reviewer model's own."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        cache = cache_dir or Path(get_settings().model_cache_dir).expanduser() / "hf"
        path = hf_hub_download(QWEN_TOKENIZER_REPO, "tokenizer.json", cache_dir=str(cache))
        self._tokenizer: Tokenizer = Tokenizer.from_file(path)

    @property
    def name(self) -> str:
        return "qwen2.5-coder"

    def count(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids) if text else 0


def token_counter(kind: str | None = None) -> TokenCounter:
    chosen = kind or get_settings().token_counter
    if chosen == "qwen":
        return QwenTokenCounter()
    return CharTokenCounter()
