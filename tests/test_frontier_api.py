"""The Anthropic API backend, with a fake client: no key, no network, no cost."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from review_fakes import FakeModel, hunk_diff

pytest.importorskip("anthropic")

from prism.models.gate import PassAllGate
from prism.review.frontier_api import FALLBACK_BETA, ApiBackend, parse_lines
from prism.review.graph import ReviewOptions, run_review
from prism.review.handoff import ClaudeVerifier
from prism.schemas import PRReview

SAFE = hunk_diff("app/a.py", 1, ["total = price * qty"])
RISKY = hunk_diff("app/b.py", 1, ["subprocess.call(cmd, shell=True)"])


class FakeClaude:
    """Answers every request: verdicts keep with `confidence`, reviews write one comment."""

    def __init__(self, confidence: float = 0.9, stop_reason: str = "end_turn") -> None:
        self.calls: list[dict[str, Any]] = []
        self.confidence = confidence
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw: Any) -> SimpleNamespace:
        self.calls.append(kw)
        lines = []
        for raw in kw["messages"][0]["content"].splitlines():
            req = json.loads(raw)
            if req["id"].endswith("-review"):
                lines.append(
                    {
                        "id": req["id"],
                        "comments": [
                            {
                                "line": 2,
                                "comment": "`cmd` reaches a shell: command injection.",
                                "severity": "critical",
                                "category": "security",
                                "confidence": 0.95,
                                "evidence": [{"source": "hunk", "detail": "shell=True"}],
                                "suggested_fix": "pass a list",
                            }
                        ],
                    }
                )
            else:
                lines.append(
                    {
                        "id": req["id"],
                        "keep": True,
                        "severity": "minor",
                        "category": "bug",
                        "confidence": self.confidence,
                        "reason": "ok",
                        "escalate": False,
                    }
                )
        text = "```json\n" + "\n".join(json.dumps(x) for x in lines) + "\n```"
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text=text),
            ],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
            stop_reason=self.stop_reason,
        )


def review(tmp_path: Path, diff: str, fake: FakeClaude) -> tuple[PRReview, ClaudeVerifier]:
    verifier = ClaudeVerifier(ApiBackend(client=fake), min_confidence=0.6)
    result, _ = asyncio.run(
        run_review(
            tmp_path,
            diff,
            gate=PassAllGate(),
            model=FakeModel(),
            verifier=verifier,
            options=ReviewOptions(escalate=True),
        )
    )
    return result, verifier


def test_api_backend_finishes_in_one_run(tmp_path: Path) -> None:
    fake = FakeClaude()
    result, verifier = review(tmp_path, SAFE + RISKY, fake)
    assert verifier.pending == 0
    assert {(c.path, c.model) for c in result.comments} == {
        ("app/a.py", "fake-reviewer"),
        ("app/b.py", "claude-opus-5-5"),
    }
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert call["output_config"] == {"effort": "high"}
    assert "# Verifying review comments" in call["system"]
    assert result.cost_usd == pytest.approx(len(fake.calls) * (1000 * 4 + 200 * 20) / 1e6)


def test_second_round_runs_in_the_same_call(tmp_path: Path) -> None:
    fake = FakeClaude(confidence=0.4)  # local comment kept but weak: escalate
    result, verifier = review(tmp_path, SAFE, fake)
    assert verifier.pending == 0
    systems = [c["system"].splitlines()[0] for c in fake.calls]
    assert systems == ["# Verifying review comments", "# Reviewing escalated hunks"]
    assert [c.model for c in result.comments] == ["claude-opus-5-5"]


def test_refusal_leaves_answers_missing(tmp_path: Path) -> None:
    _, verifier = review(tmp_path, SAFE, FakeClaude(stop_reason="refusal"))
    assert verifier.pending == 1
    assert verifier.errors == ["the model declined this batch"]


def test_parse_lines_skips_fences_and_reports_junk() -> None:
    answers, errors = parse_lines('```json\n{"id": "a", "keep": true}\nnot json\n```')
    assert list(answers) == ["a"] and errors == ["reply line 3: JSONDecodeError"]
