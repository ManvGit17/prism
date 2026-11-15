"""Routing to the frontier model, frontier comments, and the verifier's second round."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from review_fakes import FakeModel, hunk_diff

from prism.models.gate import PassAllGate
from prism.retrieval.diff import parse_diff
from prism.review.graph import ReviewOptions, run_review
from prism.review.handoff import HandoffVerifier, ReplyCache
from prism.review.plan import plan_review
from prism.review.router import route
from prism.schemas import PRReview, Severity

SAFE = hunk_diff("app/a.py", 1, ["total = price * qty"])
RISKY = hunk_diff("app/b.py", 1, ["subprocess.call(cmd, shell=True)"])
BIG = hunk_diff("app/c.py", 1, [f"v{i} = {i}" for i in range(70)])


def test_route_reasons() -> None:
    plan = plan_review(parse_diff(SAFE + RISKY + BIG), PassAllGate(), max_hunks=10)
    routes = route(plan, enabled=True, min_lines=60)
    assert routes == {("app/b.py", 0): ("security",), ("app/c.py", 0): ("large",)}
    assert route(plan, enabled=False, min_lines=60) == {}


def review(work: Path, diff: str, model: FakeModel) -> tuple[PRReview, HandoffVerifier]:
    verifier = HandoffVerifier(work)
    result, _ = asyncio.run(
        run_review(
            work,
            diff,
            gate=PassAllGate(),
            model=model,
            verifier=verifier,
            options=ReviewOptions(escalate=True, escalate_min_lines=60),
            repo_name="demo",
            cache=ReplyCache(work / "replies.json"),
        )
    )
    return result, verifier


def rows(work: Path, prefix: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted((work / "verify").glob(f"{prefix}_*.jsonl")):
        out += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return out


def answer(work: Path, prefix: str, items: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(i) + "\n" for i in items)
    (work / "verify" / f"{prefix}_00.jsonl").write_text(text, encoding="utf-8")


def keep(rid: str, **kw: Any) -> dict[str, Any]:
    return {
        "id": rid,
        "keep": True,
        "severity": "minor",
        "category": "bug",
        "confidence": 0.9,
        "evidence": [{"source": "hunk", "detail": "x"}],
        **kw,
    }


def test_security_hunk_goes_to_claude_not_the_local_model(tmp_path: Path) -> None:
    model = FakeModel()
    first, verifier = review(tmp_path, SAFE + RISKY, model)
    assert model.calls == 1, "only the safe hunk reaches the local model"
    (esc,) = rows(tmp_path, "escalations")
    assert esc["reasons"] == ["security"] and esc["path"] == "app/b.py"
    assert (tmp_path / "verify" / "REVIEW_RUBRIC.md").is_file()
    assert verifier.pending == 2  # one verdict, one review
    assert "Escalated to claude: 1 hunk(s) (security 1)." in first.summary

    (req,) = rows(tmp_path, "requests")
    answer(tmp_path, "verdicts", [{"id": req["id"], "keep": False, "reason": "wrong"}])
    answer(
        tmp_path,
        "reviews",
        [
            {
                "id": esc["id"],
                "comments": [
                    {
                        "line": 2,
                        "comment": "`cmd` reaches a shell: command injection.",
                        "severity": "critical",
                        "category": "security",
                        "confidence": 0.95,
                        "evidence": [{"source": "hunk", "detail": "shell=True"}],
                        "suggested_fix": "subprocess.run(shlex.split(cmd), check=True)",
                    },
                    {
                        "line": 99,
                        "comment": "`cmd` is on a line outside the hunk here.",
                        "confidence": 0.9,
                    },
                    {"line": 2, "comment": "Maybe log `cmd` before running it?", "confidence": 0.3},
                ],
            }
        ],
    )
    done, verifier = review(tmp_path, SAFE + RISKY, model)
    assert verifier.pending == 0 and model.calls == 1
    (c,) = done.comments
    assert (c.model, c.severity, c.line) == ("claude", Severity.CRITICAL, 2)
    assert c.suggested_fix == "subprocess.run(shlex.split(cmd), check=True)"
    assert done.verdict.value == "high"
    for f in done.files:
        assert f.hunks_reviewed + f.hunks_skipped_by_gate + f.hunks_over_budget == f.hunks_total


def test_verdict_escalation_opens_a_second_round(tmp_path: Path) -> None:
    model = FakeModel()
    review(tmp_path, SAFE, model)
    (req,) = rows(tmp_path, "requests")
    assert rows(tmp_path, "escalations") == []
    answer(
        tmp_path,
        "verdicts",
        [{"id": req["id"], "keep": False, "reason": "misses the bug", "escalate": True}],
    )

    second, verifier = review(tmp_path, SAFE, model)
    assert verifier.pending == 1
    (esc,) = rows(tmp_path, "escalations")
    assert esc["reasons"] == ["verifier"]
    assert "misses the bug" in esc["notes"][0]
    assert "Escalated to claude: 1 hunk(s) (verifier 1)." in second.summary

    answer(tmp_path, "reviews", [{"id": esc["id"], "comments": []}])
    done, verifier = review(tmp_path, SAFE, model)
    assert verifier.pending == 0 and done.comments == ()


def test_weak_local_comment_escalates(tmp_path: Path) -> None:
    review(tmp_path, SAFE, FakeModel())
    (req,) = rows(tmp_path, "requests")
    answer(tmp_path, "verdicts", [keep(req["id"], confidence=0.4)])
    _, verifier = review(tmp_path, SAFE, FakeModel())
    assert verifier.pending == 1 and len(rows(tmp_path, "escalations")) == 1


def test_routing_off_keeps_everything_local(tmp_path: Path) -> None:
    model = FakeModel()
    verifier = HandoffVerifier(tmp_path)
    asyncio.run(
        run_review(
            tmp_path,
            SAFE + RISKY,
            gate=PassAllGate(),
            model=model,
            verifier=verifier,
            options=ReviewOptions(escalate=False),
        )
    )
    assert model.calls == 2 and rows(tmp_path, "escalations") == []


def test_same_line_same_category_keeps_the_strongest() -> None:
    from prism.review.aggregate import cap
    from prism.schemas import Category, ReviewComment

    def rc(model: str, sev: Severity, conf: float) -> ReviewComment:
        return ReviewComment(
            path="a.py",
            line=3,
            comment=f"{model} says injection",
            severity=sev,
            category=Category.SECURITY,
            confidence=conf,
            model=model,
        )

    kept, cut = cap([rc("bandit", Severity.MAJOR, 0.9), rc("claude", Severity.CRITICAL, 0.95)], 5)
    assert [c.model for c in kept] == ["claude"] and cut == 0


def test_route_all_sends_every_planned_hunk_up_front() -> None:
    from types import SimpleNamespace

    from review_fakes import hunk_diff, task_for

    from prism.review.router import route

    tasks = [task_for(hunk_diff("a.py", 1, ["x = 1"])), task_for(hunk_diff("b.py", 5, ["y = 2"]))]
    plan: Any = SimpleNamespace(tasks=tasks)  # route reads only the planned tasks
    assert route(plan, enabled=False, min_lines=60, everything=True) == {
        ("a.py", 0): ("all",),
        ("b.py", 0): ("all",),
    }
    assert route(plan, enabled=False, min_lines=60) == {}
