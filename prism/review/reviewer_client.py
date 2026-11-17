"""Calls to the reviewer model, under the concurrency limit.

`OllamaReviewer` sends one hunk in the training format (`prism.models.reviewer`) and returns the
raw reply text. `review_hunk` parses it, retries a reply that is not the JSON asked for, and gives
up after `retries` more tries. Every model call in a review shares one `asyncio.Semaphore` sized
by PRISM_MODEL_CONCURRENCY: Ollama only runs as many requests as it has parallel slots, so more
in flight would only queue there.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from prism.models.reviewer import SYSTEM_PROMPT, review_messages, to_raw_comment
from prism.schemas import RawComment

log = logging.getLogger(__name__)


class ReviewerModel(Protocol):
    name: str

    async def reply(self, hunk: str, attempt: int) -> str:
        """The model's raw reply for one hunk. `attempt` counts from 0."""
        ...


class OllamaReviewer:
    def __init__(
        self, url: str, model: str, *, timeout_s: float = 180.0, system: str = SYSTEM_PROMPT
    ) -> None:
        self.name = model
        self.system = system
        self._url = url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_s)

    async def reply(self, hunk: str, attempt: int) -> str:
        # Greedy, as in training and its evaluation; a retry samples a little so it can differ.
        options: dict[str, float | int] = {"temperature": 0.0 if attempt == 0 else 0.4}
        if attempt:
            options["seed"] = attempt
        resp = await self._client.post(
            f"{self._url}/api/chat",
            json={
                "model": self.name,
                "messages": review_messages(hunk, self.system),
                "stream": False,
                "options": options,
            },
        )
        resp.raise_for_status()
        return str(resp.json()["message"]["content"])

    async def aclose(self) -> None:
        await self._client.aclose()


# ok: a comment came back. invalid: every reply was malformed. error: the last try raised (server
# down, timeout), which may pass on a rerun. time: the deadline passed before a model slot was free.
Status = Literal["ok", "invalid", "error", "time"]


@dataclass(frozen=True)
class HunkResult:
    comment: RawComment | None
    attempts: int
    status: Status = "ok"
    error: str | None = None  # last error when no comment came back
    # Time spent waiting for a model slot, summed over attempts. A trace shows it apart from
    # the call itself: with every hunk queued at once, most of a span can be waiting.
    wait_s: float = 0.0


def with_context(hunk: str, context: str) -> str:
    """The reviewer's input: the hunk, or retrieved code and then the hunk."""
    if not context:
        return hunk
    return f"Code from the repository, for context:\n{context}\n\nThe hunk to review:\n{hunk}"


async def review_hunk(
    model: ReviewerModel,
    hunk: str,
    limit: asyncio.Semaphore,
    *,
    retries: int = 2,
    deadline: float | None = None,
    context: str = "",
) -> HunkResult:
    """Review one hunk. The deadline is checked once a model slot is held, not before: all hunks
    of a PR start at once and wait on the semaphore, so a check at start would always pass.

    With `context` (an evaluation setting) the model also sees retrieved code before the hunk;
    the comment is still placed on a line of the hunk alone."""
    prompt = with_context(hunk, context)
    error: str | None = None
    status: Status = "invalid"
    wait = 0.0
    for attempt in range(retries + 1):
        try:
            queued = time.monotonic()
            async with limit:
                wait += time.monotonic() - queued
                if deadline is not None and time.monotonic() > deadline:
                    return HunkResult(None, attempt, "time", wait_s=wait)
                text = await model.reply(prompt, attempt)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            error, status = f"{type(exc).__name__}: {exc}", "error"
            log.warning("reviewer call failed (attempt %d): %s", attempt + 1, error)
            continue
        comment = to_raw_comment(hunk, text)
        if comment is not None:
            return HunkResult(comment, attempt + 1, wait_s=wait)
        error, status = f"invalid reply: {text[:200]!r}", "invalid"
        log.info("reviewer reply not valid JSON (attempt %d)", attempt + 1)
    log.warning("dropping hunk after %d attempts: %s", retries + 1, error)
    return HunkResult(None, retries + 1, status, error, wait_s=wait)
