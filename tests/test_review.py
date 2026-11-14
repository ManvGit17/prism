"""The review pipeline with a fake reviewer model: planner, budget, checks, fan-out, aggregation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from prism.models.gate import PassAllGate
from prism.retrieval.diff import parse_diff
from prism.review.checks import Candidate, RuleVerifier, apply_rules, is_vague
from prism.review.graph import ReviewOptions, run_review
from prism.review.plan import HunkTask, file_risk, plan_review
from prism.schemas import Category, PRReview, RawComment, Severity

FIXTURES = Path(__file__).parent / "fixtures"


def hunk_diff(path: str, start: int, added: Sequence[str]) -> str:
    body = "\n".join(f"+{a}" for a in added)
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -{start},1 +{start},{len(added) + 1} @@\n context_line = 1\n{body}\n"
    )


def many_files(n: int, hunks_per_file: int = 1) -> str:
    parts = []
    for i in range(n):
        for h in range(hunks_per_file):
            parts.append(
                hunk_diff(f"pkg/mod{i}.py", 10 + 20 * h, [f"value_{i}_{h} = compute({h})"])
            )
    # git puts all hunks of one file under one header; repeated headers parse as one file each,
    # which is fine for these tests.
    return "".join(parts)


class ScoreGate:
    name = "fixed"

    def __init__(self, threshold: float, scores: dict[str, float]) -> None:
        self.threshold = threshold
        self.scores = scores

    def score(self, hunks: Sequence[str]) -> list[float]:
        return [next((v for k, v in self.scores.items() if k in h), 0.0) for h in hunks]

    def needs_review(self, hunks: Sequence[str]) -> list[bool]:
        return [s >= self.threshold for s in self.score(hunks)]


class FakeModel:
    """Replies with a comment naming the first added identifier; tracks concurrency."""

    name = "fake-reviewer"

    def __init__(self, replies: dict[str, list[str]] | None = None, delay: float = 0.01) -> None:
        self.replies = replies or {}
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls = 0

    async def reply(self, hunk: str, attempt: int) -> str:
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
        for key, seq in self.replies.items():
            if key in hunk:
                return seq[min(attempt, len(seq) - 1)]
        added = next(ln[1:] for ln in hunk.splitlines() if ln.startswith("+"))
        name = added.split("=")[0].strip()
        return json.dumps({"comment": f"`{name}` is computed but never used after this line."})


def review(diff: str, tmp_path: Path, **kw: object) -> tuple[PRReview, FakeModel]:
    model = kw.pop("model", None) or FakeModel()
    gate = kw.pop("gate", None) or PassAllGate()
    options = ReviewOptions(**kw)  # type: ignore[arg-type]
    result, _ = asyncio.run(
        run_review(
            tmp_path,
            diff,
            gate=gate,  # type: ignore[arg-type]
            model=model,  # type: ignore[arg-type]
            verifier=RuleVerifier(),
            options=options,
            repo_name="demo",
        )
    )
    return result, model  # type: ignore[return-value]


# Planner


def test_planner_skips_binary_deleted_and_lock_files() -> None:
    diff = (FIXTURES / "diffs" / "multi.diff").read_text(encoding="utf-8")
    diff += hunk_diff("uv.lock", 1, ["x = 1"])
    plan = plan_review(parse_diff(diff), PassAllGate(), max_hunks=10)
    skipped = dict(plan.skipped_files)
    assert skipped["shop/legacy.py"] == "deleted"
    assert skipped["uv.lock"] == "generated"
    assert {f.path for f in plan.files} >= {"shop/pricing.py", "shop/gift.py"}


def test_risk_flags_security_path_and_code() -> None:
    by_path = parse_diff(hunk_diff("app/auth/views.py", 1, ["x = 1"]))[0]
    by_code = parse_diff(hunk_diff("app/util.py", 1, ["subprocess.run(cmd, shell=True)"]))[0]
    plain = parse_diff(hunk_diff("app/util.py", 1, ["x = 1"]))[0]
    assert file_risk(by_path)[2] and "security-sensitive path" in file_risk(by_path)[1]
    assert file_risk(by_code)[2] and "security-sensitive code" in file_risk(by_code)[1]
    assert not file_risk(plain)[2]
    assert file_risk(by_code)[0] > file_risk(plain)[0]


def test_budget_keeps_highest_priority_hunks() -> None:
    diff = many_files(5) + hunk_diff("app/login.py", 1, ["token = make_token()"])
    plan = plan_review(parse_diff(diff), PassAllGate(), max_hunks=2)
    reviewed = [t.path for t in plan.tasks]
    assert len(reviewed) == 2
    assert "app/login.py" in reviewed  # security first
    assert len(plan.not_reviewed) == 4
    assert all(n.reason == "budget" for n in plan.not_reviewed)


def test_gate_skips_but_security_hunks_bypass_it() -> None:
    diff = hunk_diff("a.py", 1, ["dull = 1"]) + hunk_diff("b.py", 1, ["eval(user_input)"])
    gate = ScoreGate(0.5, {"dull": 0.1, "eval": 0.1})
    plan = plan_review(parse_diff(diff), gate, max_hunks=10)
    assert [t.path for t in plan.tasks] == ["b.py"]
    assert plan.files[0].skipped_by_gate == 1


# Checks


def task_for(diff: str) -> HunkTask:
    f = parse_diff(diff)[0]
    return HunkTask(f.path, 0, f.hunks[0], 1.0, False, 1.0)


def test_rules_drop_bad_line_vague_and_duplicates() -> None:
    task = task_for(hunk_diff("a.py", 10, ["total = price * qty"]))
    good = Candidate(task, RawComment(line=11, comment="`total` ignores the discount here."), "m")
    off = Candidate(task, RawComment(line=99, comment="`total` ignores the discount here."), "m")
    vague = Candidate(task, RawComment(line=11, comment="Is this needed?"), "m")
    dup = Candidate(task, RawComment(line=11, comment="`total` ignores the discount here!"), "m")
    passing, dropped = apply_rules([good, off, vague, dup])
    assert passing == [good]
    assert [d.reason for d in dropped] == ["line_not_in_diff", "vague", "duplicate"]


def test_vague_rule_matches_training_filter() -> None:
    hunk = "+total = price * qty"
    assert is_vague("Why?", hunk)
    assert not is_vague("Why is total computed here?", hunk)  # names an identifier
    assert not is_vague("This allocation happens on every request in a hot loop.", hunk)


# The graph


def test_review_returns_valid_prreview(tmp_path: Path) -> None:
    diff = (FIXTURES / "diffs" / "multi.diff").read_text(encoding="utf-8")
    result, _ = review(diff, tmp_path)
    PRReview.model_validate_json(result.model_dump_json())
    assert result.repo == "demo" and result.head_sha
    assert result.comments, "the fake model's comments name an identifier and should be kept"
    for c in result.comments:
        assert c.category in Category and c.severity in Severity
    assert "shop/legacy.py (deleted)" in result.summary
    for f in result.files:
        assert f.hunks_reviewed + f.hunks_skipped_by_gate + f.hunks_over_budget == f.hunks_total


def test_concurrency_limit_holds_across_files(tmp_path: Path) -> None:
    model = FakeModel(delay=0.05)
    result, model = review(many_files(6, 2), tmp_path, model=model, concurrency=3)
    assert model.calls == 12
    assert model.max_in_flight == 3
    assert sum(f.hunks_reviewed for f in result.files) == 12


def test_invalid_reply_is_retried_then_dropped(tmp_path: Path) -> None:
    diff = hunk_diff("a.py", 1, ["first = 1"]) + hunk_diff("b.py", 1, ["second = 2"])
    model = FakeModel(
        replies={
            "first": ["not json", '{"comment": "`first` should be a constant, it never changes."}'],
            "second": ["nope"],
        }
    )
    result, model = review(diff, tmp_path, model=model, retries=2)
    assert model.calls == 2 + 3  # one retry for a.py, three tries for b.py
    assert [c.path for c in result.comments] == ["a.py"]
    assert "b.py" in result.summary and "model_failed" in result.summary


def test_time_limit_lists_hunks_as_not_reviewed(tmp_path: Path) -> None:
    result, model = review(many_files(2), tmp_path, time_limit_s=1e-9)
    assert model.calls == 0
    assert "(time)" in result.summary
    assert all(f.hunks_reviewed == 0 for f in result.files)


def test_comment_cap_and_verdict(tmp_path: Path) -> None:
    result, _ = review(many_files(5), tmp_path, max_comments=2)
    assert len(result.comments) == 2
    assert "left out by the comment cap" in result.summary


@pytest.mark.parametrize("hunks", [0])
def test_empty_diff(tmp_path: Path, hunks: int) -> None:
    result, model = review("", tmp_path)
    assert model.calls == hunks and result.files == () and result.verdict.value == "low"
