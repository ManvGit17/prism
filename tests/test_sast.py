"""SAST runners, tying findings to hunks, and SAST in the review graph."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from review_fakes import FakeModel, hunk_diff, task_for

from prism.models.gate import PassAllGate
from prism.review import sast
from prism.review.graph import ReviewOptions, run_review, tie_findings
from prism.review.handoff import HandoffVerifier, ReplyCache
from prism.review.sast import Bandit, Finding, Pmd, Ruff, SastReport, Semgrep, find_exe, run_sast

VULN = """import pickle
import subprocess


def run(cmd, data):
    subprocess.call(cmd, shell=True)
    return pickle.loads(data) + undefined_name
"""


# Parsing tool output


def test_bandit_output_drops_low_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    out = {
        "results": [
            {
                "test_id": "B602",
                "filename": "./app/x.py",
                "line_number": 6,
                "issue_severity": "HIGH",
                "issue_confidence": "HIGH",
                "issue_text": "shell=True",
            },
            {
                "test_id": "B101",
                "filename": "app/x.py",
                "line_number": 9,
                "issue_severity": "LOW",
                "issue_confidence": "LOW",
                "issue_text": "assert",
            },
        ]
    }
    monkeypatch.setattr(sast, "_run", lambda *a, **k: json.dumps(out))
    findings = Bandit().run(Path("."), ["app/x.py"], 10)
    assert findings == [Finding("bandit", "B602", "app/x.py", 6, "high", "shell=True")]


def test_semgrep_output_maps_severity(monkeypatch: pytest.MonkeyPatch) -> None:
    out = {
        "results": [
            {
                "check_id": "python.lang.security.audit.subprocess-shell-true",
                "path": "app/x.py",
                "start": {"line": 6},
                "extra": {"severity": "ERROR", "message": "Found\n shell"},
            },
        ],
        "errors": [],
    }
    monkeypatch.setattr(sast, "_run", lambda *a, **k: json.dumps(out))
    (f,) = Semgrep("p/owasp-top-ten").run(Path("."), ["app/x.py"], 10)
    assert (f.rule, f.severity, f.message) == ("subprocess-shell-true", "high", "Found shell")


def test_ruff_marks_undefined_names_medium(monkeypatch: pytest.MonkeyPatch) -> None:
    out = [
        {"code": "F821", "filename": "app/x.py", "location": {"row": 7}, "message": "undefined"},
        {"code": "F401", "filename": "app/x.py", "location": {"row": 1}, "message": "unused"},
    ]
    monkeypatch.setattr(sast, "_run", lambda *a, **k: json.dumps(out))
    levels = {f.rule: f.severity for f in Ruff().run(Path("."), ["app/x.py"], 10)}
    assert levels == {"F821": "medium", "F401": "low"}


def test_pmd_output_maps_priority(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "pmd").write_text("", encoding="utf-8")
    out = {
        "files": [
            {
                "filename": "src/A.java",
                "violations": [
                    {
                        "beginline": 3,
                        "rule": "EmptyCatchBlock",
                        "priority": 3,
                        "description": "Empty",
                    },
                    {
                        "beginline": 5,
                        "rule": "AvoidFileStream",
                        "priority": 1,
                        "description": "Stream",
                    },
                ],
            }
        ]
    }

    def fake_run(*a: object, **k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 4, json.dumps(out), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    levels = {f.rule: f.severity for f in Pmd(str(tmp_path)).run(Path("."), ["src/A.java"], 10)}
    assert levels == {"EmptyCatchBlock": "medium", "AvoidFileStream": "high"}


# Running tools


class Broken:
    name = "broken"
    suffixes: tuple[str, ...] = (".py",)

    def unavailable(self) -> str | None:
        return None

    def run(self, root: Path, paths: Sequence[str], timeout_s: float) -> list[Finding]:
        raise RuntimeError("boom")


def test_run_sast_skips_missing_and_failing_tools(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "B.java").write_text("class B {}\n", encoding="utf-8")
    report = run_sast(tmp_path, ["a.py", "B.java", "gone.py"], [Broken(), Pmd("")])
    assert report.ran == []
    assert dict(report.skipped) == {
        "broken": "failed: RuntimeError",
        "pmd": "PRISM_PMD_HOME not set or has no bin/pmd",
    }
    assert "not run: broken" in report.summary()


def test_tool_with_no_matching_files_is_not_listed(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert run_sast(tmp_path, ["a.py"], [Pmd("")]).skipped == []


@pytest.mark.skipif(not (find_exe("bandit") and find_exe("ruff")), reason="sast group missing")
def test_real_bandit_and_ruff(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text(VULN, encoding="utf-8")
    report = run_sast(tmp_path, ["app/x.py"], [Bandit(), Ruff()])
    assert report.ran == ["bandit", "ruff"]
    rules = {(f.tool, f.rule, f.line) for f in report.findings}
    assert ("bandit", "B602", 6) in rules
    assert ("ruff", "F821", 7) in rules
    assert all(f.path == "app/x.py" for f in report.findings)


# Tying findings to hunks


def test_tie_findings() -> None:
    task = task_for(hunk_diff("a.py", 10, ["subprocess.call(cmd, shell=True)", "y = 2"]))
    findings = [
        Finding("bandit", "B602", "a.py", 11, "high", "shell=True"),  # added line: comment
        Finding("ruff", "F401", "a.py", 12, "low", "unused"),  # added, low: evidence only
        Finding("bandit", "B404", "a.py", 10, "medium", "subprocess"),  # context line
        Finding("bandit", "B101", "a.py", 40, "high", "assert"),  # outside the hunk
        Finding("semgrep", "x", "b.py", 11, "high", "other file"),
    ]
    tools, extra = tie_findings([task], findings, "medium")
    assert len(tools[("a.py", task.hunk.header)]) == 3
    assert [(c.model, c.raw.line) for c in extra] == [("bandit", 11)]
    assert extra[0].raw.comment == "shell=True (bandit B602)"


def test_one_tool_comment_per_line_strongest_wins() -> None:
    task = task_for(hunk_diff("a.py", 10, ["h = hashlib.md5(pw).hexdigest()"]))
    findings = [
        Finding("bandit", "B324", "a.py", 11, "medium", "weak md5"),
        Finding("semgrep", "md5-used-as-password", "a.py", 11, "high", "md5 as password hash"),
        Finding("semgrep", "insecure-hash-algorithm-md5", "a.py", 11, "medium", "md5"),
    ]
    tools, extra = tie_findings([task], findings, "medium")
    assert [c.raw.comment for c in extra] == ["md5 as password hash (semgrep md5-used-as-password)"]
    assert len(tools[("a.py", task.hunk.header)]) == 3


def test_sast_comments_reach_the_verifier(tmp_path: Path) -> None:
    diff = hunk_diff("app/x.py", 1, ["subprocess.call(cmd, shell=True)"])

    def fake_sast(paths: Sequence[str]) -> SastReport:
        assert list(paths) == ["app/x.py"]
        return SastReport(
            findings=[Finding("bandit", "B602", "app/x.py", 2, "high", "shell=True is unsafe")],
            ran=["bandit"],
        )

    verifier = HandoffVerifier(tmp_path)
    result, _ = asyncio.run(
        run_review(
            tmp_path,
            diff,
            gate=PassAllGate(),
            model=FakeModel(),
            verifier=verifier,
            options=ReviewOptions(),
            cache=ReplyCache(tmp_path / "replies.json"),
            sast_fn=fake_sast,
        )
    )
    assert verifier.pending == 2
    rows = [
        json.loads(line)
        for line in (tmp_path / "verify" / "requests_00.jsonl").read_text("utf-8").splitlines()
    ]
    assert [r["author"] for r in rows] == ["fake-reviewer", "bandit"]
    assert "B602" in rows[0]["tools"][0], "the model's comment also sees the finding"
    assert "SAST findings in reviewed hunks: bandit 1." in result.summary
