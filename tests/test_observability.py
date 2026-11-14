"""Tracing (the span tree a review produces) and prompts from Langfuse, with fakes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from review_fakes import FakeModel, hunk_diff

from prism.models.gate import PassAllGate
from prism.observability.prompts import REVIEW, REVIEWER, VERIFY, PromptSource, builtin, push
from prism.observability.tracing import mask
from prism.review.checks import RuleVerifier
from prism.review.graph import ReviewOptions, run_review
from prism.review.reviewer_client import review_hunk
from prism.review.sast import Finding, SastReport

GH = "gh" + "p_" + "A1b2C3d4" * 5


class RecSpan:
    def __init__(self, name: str, kind: str = "span", **kw: Any) -> None:
        self.name, self.kind, self.kw = name, kind, kw
        self.children: list[RecSpan] = []
        self.ended: dict[str, Any] | None = None

    def child(self, name: str, *, kind: str = "span", **kw: Any) -> RecSpan:
        span = RecSpan(name, kind, **kw)
        self.children.append(span)
        return span

    def end(self, **kw: Any) -> None:
        assert self.ended is None, f"{self.name} ended twice"
        self.ended = kw

    def find(self, prefix: str) -> list[RecSpan]:
        return [c for c in self.children if c.name.startswith(prefix)]


class RecTracer:
    name = "rec"

    def __init__(self) -> None:
        self.root: RecSpan | None = None
        self.tags: list[str] = []
        self.flushed = False

    @contextmanager
    def review(
        self, *, name: str, tags: Sequence[str], metadata: Mapping[str, Any], input: Any
    ) -> Iterator[RecSpan]:
        self.tags = list(tags)
        self.root = RecSpan(name, "chain", input=input, metadata=metadata)
        yield self.root

    def flush(self) -> None:
        self.flushed = True

    def trace_url(self) -> str | None:
        return None

    def trace_id(self) -> str | None:
        return None


def test_review_produces_a_span_per_step_file_and_hunk(tmp_path: Path) -> None:
    diff = (
        hunk_diff("app/a.py", 1, ["total = price * qty"])
        + hunk_diff("app/b.py", 1, ["rate = load_rate()"])
        + hunk_diff("app/c.py", 1, ["subprocess.call(cmd, shell=True)"])
    )
    tracer = RecTracer()
    prompt = object()

    def fake_sast(paths: Sequence[str]) -> SastReport:
        return SastReport(
            findings=[Finding("bandit", "B602", "app/c.py", 2, "high", "x")], ran=["bandit"]
        )

    asyncio.run(
        run_review(
            tmp_path,
            diff,
            gate=PassAllGate(),
            model=FakeModel(),
            verifier=RuleVerifier(),
            options=ReviewOptions(escalate=True),
            repo_name="demo",
            pr_number=7,
            sast_fn=fake_sast,
            tracer=tracer,
            reviewer_prompt=prompt,
        )
    )
    root = tracer.root
    assert root is not None and root.ended is not None and tracer.flushed
    assert tracer.tags == ["repo:demo", "model:fake-reviewer", "verifier:rules", "pr:7"]
    names = [c.name for c in root.children]
    for step in ("planner", "sast", "context", "verifier", "aggregator"):
        assert step in names
    files = sorted(c.name for c in root.find("file "))
    assert files == ["file app/a.py", "file app/b.py", "file app/c.py"]

    (a,) = root.find("file app/a.py")
    (gen,) = a.children
    assert gen.kind == "generation" and gen.kw["model"] == "fake-reviewer"
    assert gen.kw["prompt"] is prompt
    assert gen.ended is not None and gen.ended["metadata"]["status"] == "ok"

    (c,) = root.find("file app/c.py")
    (esc,) = c.children
    assert esc.kind == "span" and esc.ended is not None
    assert esc.ended["output"] == "escalated to the frontier model"
    assert all(s.ended is not None for s in root.children)


def test_mask_redacts_nested_payloads() -> None:
    out = mask(data={"input": [f"token = '{GH}'", 3], "ok": "plain"})
    assert GH not in str(out) and out["ok"] == "plain" and out["input"][1] == 3


def test_wait_for_a_model_slot_is_measured() -> None:
    async def go() -> list[float]:
        limit = asyncio.Semaphore(1)
        model = FakeModel(delay=0.05)
        hunks = [f"@@ -1,1 +1,2 @@\n x = 1\n+value_{i} = f()" for i in range(3)]
        results = await asyncio.gather(*(review_hunk(model, h, limit) for h in hunks))
        return sorted(r.wait_s for r in results)

    waits = asyncio.run(go())
    assert waits[0] < 0.02 and waits[-1] > 0.08


# Prompts


class FakeLangfuse:
    def __init__(self, texts: dict[str, str] | None = None, fail: bool = False) -> None:
        self.texts = texts or {}
        self.fail = fail
        self.created: list[dict[str, Any]] = []

    def get_prompt(self, name: str, *, label: str, fallback: str, **_: Any) -> SimpleNamespace:
        if self.fail:
            raise RuntimeError("server down")
        if name not in self.texts:
            return SimpleNamespace(prompt=fallback, version=None, is_fallback=True)
        return SimpleNamespace(prompt=self.texts[name], version=3, is_fallback=False)

    def create_prompt(self, **kw: Any) -> SimpleNamespace:
        self.created.append(kw)
        return SimpleNamespace(version=len(self.created))


def test_prompts_come_from_langfuse_with_a_label() -> None:
    src = PromptSource(FakeLangfuse({VERIFY: "rubric from langfuse"}), label="staging")
    assert src.get(VERIFY).text == "rubric from langfuse"
    assert src.get(VERIFY).source == "langfuse v3 (staging)"
    assert src.get(REVIEW).text == builtin()[REVIEW] and src.get(REVIEW).source == "code"
    assert "prism-verify-rubric langfuse v3 (staging)" in src.summary()


def test_prompts_fall_back_to_code_when_langfuse_fails() -> None:
    src = PromptSource(FakeLangfuse(fail=True))
    assert src.get(REVIEWER).text == builtin()[REVIEWER]
    assert PromptSource(None).get(VERIFY).source == "code"


def test_changed_reviewer_prompt_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    src = PromptSource(FakeLangfuse({REVIEWER: "Be terse."}))
    with caplog.at_level(logging.WARNING):
        assert src.get(REVIEWER).text == "Be terse."
    assert "trained on" in caplog.text


def test_push_creates_a_version_of_each_prompt_with_the_label() -> None:
    client = FakeLangfuse()
    lines = push(client, "staging")
    assert [c["name"] for c in client.created] == [REVIEWER, VERIFY, REVIEW]
    assert all(c["labels"] == ["staging"] for c in client.created)
    assert lines[0] == "prism-reviewer-system v1 [staging]"
