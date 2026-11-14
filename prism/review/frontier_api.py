"""Claude through the Anthropic API, as a backend for `ClaudeVerifier` (ADR 0013).

The file handoff (`HandoffBackend`) is the default on the build machine. This backend is for
anyone with a key: `PRISM_VERIFIER=api`, and `ANTHROPIC_API_KEY` (or an `ant auth login`
profile) in the environment. It needs the optional `frontier` group: `uv sync --group frontier`.

It sends the same rubric and the same requests the handoff writes, a batch at a time, and reads
back the same JSON lines a Claude session would write into `verdicts_NN.jsonl` or
`reviews_NN.jsonl`. So the two backends are interchangeable and the rubric is the one prompt.

PLAN.md named LiteLLM for this. Routing lives in `prism.review.router` now, and the only
frontier model is Claude, so the official SDK is used directly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from prism.review.handoff import Kind

log = logging.getLogger(__name__)

# Server-side refusal fallback: on a policy decline the API re-runs the request on a fallback
# model in the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
BATCH_SIZE = 10

INSTRUCTIONS = (
    "\n\n## Output for this call\n\nThe requests follow as JSON lines. Reply with exactly one "
    "JSON object per line, one line per request, in the format the rubric gives and nothing "
    "else: no code fences, no prose."
)


def parse_lines(text: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """JSON objects by id from a reply, and the lines that did not parse."""
    answers: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        try:
            obj = json.loads(line)
            answers[str(obj["id"])] = obj
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            errors.append(f"reply line {n}: {exc.__class__.__name__}")
    return answers, errors


class ApiBackend:
    def __init__(
        self,
        model: str = "claude-opus-5-5",
        *,
        effort: str = "high",
        max_tokens: int = 16000,
        price_in_per_mtok: float = 4.0,
        price_out_per_mtok: float = 20.0,
        client: Any = None,
    ) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.name = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.price_in = price_in_per_mtok
        self.price_out = price_out_per_mtok
        self.cost_usd = 0.0
        self._done: dict[str, dict[str, Any]] = {}  # answers already paid for in this run

    def answer(
        self, kind: Kind, rubric: str, requests: Sequence[dict[str, Any]]
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        errors: list[str] = []
        todo = [r for r in requests if r["id"] not in self._done]
        for start in range(0, len(todo), BATCH_SIZE):
            batch = todo[start : start + BATCH_SIZE]
            got, errs = self._call(rubric, batch)
            self._done.update(got)
            errors += errs
        wanted = {r["id"] for r in requests}
        return {k: v for k, v in self._done.items() if k in wanted}, errors

    def _call(
        self, rubric: str, batch: Sequence[dict[str, Any]]
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        import anthropic

        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in batch)
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=rubric + INSTRUCTIONS,
                messages=[{"role": "user", "content": body}],
                output_config={"effort": self.effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIConnectionError as exc:
            return {}, [f"connection error: {exc}"]
        except anthropic.APIStatusError as exc:
            return {}, [f"API error {exc.status_code}: {exc.message}"]
        usage = response.usage
        self.cost_usd += (
            usage.input_tokens * self.price_in + usage.output_tokens * self.price_out
        ) / 1e6
        if response.stop_reason == "refusal":
            return {}, ["the model declined this batch"]
        if response.stop_reason == "max_tokens":
            log.warning("reply cut at max_tokens; later answers in this batch are missing")
        text = "".join(b.text for b in response.content if b.type == "text")
        answers, errors = parse_lines(text)
        ids = {r["id"] for r in batch}
        return {k: v for k, v in answers.items() if k in ids}, errors
