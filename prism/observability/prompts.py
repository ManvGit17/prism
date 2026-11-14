"""Prompts from Langfuse prompt management, by label (ADR 0014).

The code holds the reference text of every prompt. `prism prompts push --label staging` copies
them into Langfuse as a new version with that label; promoting one to `production` is done in
Langfuse. A review reads each prompt by `PRISM_PROMPT_LABEL` (default `production`) and falls
back to the code's text when Langfuse is off, unreachable or has no such prompt, so a review
never fails over a prompt.

The reviewer's system prompt is special: `prism-reviewer:v2` was fine-tuned on exactly the text
in `prism.models.reviewer`, and the Modelfile bakes it in too. Serving a different text to the
tuned model is allowed (it is how a staging prompt for the base model gets tried), but it is
logged as a warning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

REVIEWER = "prism-reviewer-system"
VERIFY = "prism-verify-rubric"
REVIEW = "prism-review-rubric"


def builtin() -> dict[str, str]:
    """Prompt name -> the reference text in code."""
    from prism.models.reviewer import SYSTEM_PROMPT
    from prism.review.handoff import REVIEW_RUBRIC, RUBRIC

    return {REVIEWER: SYSTEM_PROMPT, VERIFY: RUBRIC, REVIEW: REVIEW_RUBRIC}


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    source: str  # "langfuse vN (label)" or "code"
    client: Any = None  # the Langfuse prompt client, to link generations to it


class PromptSource:
    """Prompts from Langfuse by label, or the code's text."""

    def __init__(self, client: Any = None, label: str = "production") -> None:
        self.client = client
        self.label = label
        self._seen: dict[str, Prompt] = {}

    def get(self, name: str) -> Prompt:
        if name in self._seen:
            return self._seen[name]
        text = builtin()[name]
        prompt = Prompt(name, text, "code")
        if self.client is not None:
            try:
                got = self.client.get_prompt(
                    name, label=self.label, fallback=text, cache_ttl_seconds=300
                )
                if not getattr(got, "is_fallback", False):
                    prompt = Prompt(
                        name, got.prompt, f"langfuse v{got.version} ({self.label})", got
                    )
            except Exception as exc:  # the SDK raises several kinds; a review must go on
                log.warning("prompt %s from Langfuse failed, using the code's: %s", name, exc)
        if name == REVIEWER and prompt.text != text:
            log.warning(
                "the reviewer prompt from Langfuse (%s) differs from the one the tuned model "
                "was trained on",
                prompt.source,
            )
        self._seen[name] = prompt
        return prompt

    def summary(self) -> str:
        used = ", ".join(f"{p.name} {p.source}" for p in self._seen.values())
        return f"Prompts: {used}." if used else ""


def push(client: Any, label: str) -> list[str]:
    """Create a new version of every prompt with `label`. Returns what was pushed."""
    out = []
    for name, text in builtin().items():
        created = client.create_prompt(
            name=name,
            prompt=text,
            labels=[label],
            type="text",
            commit_message=f"pushed from code with label {label}",
        )
        out.append(f"{name} v{created.version} [{label}]")
    return out
