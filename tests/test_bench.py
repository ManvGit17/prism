"""BugBench: planting a defect, materializing a case, scoring, and batching the handoff."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from review_fakes import hunk_diff, task_for

from prism.bench.cases import Case, CaseSet, load, materialize, plant
from prism.bench.run import SEP, export_requests, import_answers
from prism.bench.score import score_case, summarize
from prism.review.checks import Candidate, Decision
from prism.review.plan import HunkTask
from prism.schemas import Category, RawComment, ReviewComment, Severity

ROOT = Path(__file__).resolve().parent.parent


def case(find: str, replace: str, path: str = "m.py", commit: str = "HEAD") -> Case:
    return Case(id="c1", commit=commit, path=path, kind="k", find=find, replace=replace)


def test_plant_reports_the_first_changed_line() -> None:
    text = "a = 1\nb = a + 1\nc = 3\n"
    new, line = plant(text, case("a + 1", "a - 1"))
    assert new == "a = 1\nb = a - 1\nc = 3\n"
    assert line == 2


def test_plant_a_deletion_points_at_the_line_now_in_its_place() -> None:
    text = "def close(self):\n    self.storage.close()\n    self.done = True\n"
    new, line = plant(text, case("\n    self.storage.close()\n", "\n"))
    assert new == "def close(self):\n    self.done = True\n"
    assert line == 2


@pytest.mark.parametrize("text", ["x = 1\n", "y = 2\ny = 2\n"])
def test_plant_needs_exactly_one_match(text: str) -> None:
    with pytest.raises(ValueError, match="occurs"):
        plant(text, case("y = 2", "y = 3"))


def test_the_slice_file_loads() -> None:
    cs = load(ROOT / "bugbench" / "cases" / "tinydb-slice.toml")
    assert cs.repo == "tinydb" and cs.license == "MIT"
    assert len(cs.cases) == 10
    assert len({c.id for c in cs.cases}) == 10


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    row = 'id = "a"\ncommit = "x"\npath = "p"\nkind = "k"\nfind = "f"\nreplace = "r"\n'
    f = tmp_path / "cases.toml"
    f.write_text(f'[repo]\nname = "r"\nurl = "u"\n[[case]]\n{row}[[case]]\n{row}', "utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load(f)


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()


def test_materialize_plants_the_defect_into_the_commit(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "m.py").write_text("def f(a):\n    return a\n", "utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "one")
    two = "def f(a):\n    if a:\n        return a + 1\n    return a\n"
    (repo / "m.py").write_text(two, "utf-8")
    _git(repo, "commit", "-q", "-am", "two")
    sha = _git(repo, "rev-parse", "HEAD")
    c = case("return a + 1", "return a - 1", commit=sha)
    cs = CaseSet("demo", "unused", "MIT", (c,))

    m = materialize(tmp_path, cs, c)
    assert m.lines == (3,)
    assert "+        return a - 1" in m.diff
    assert "+    if a:" in m.diff  # the real change is in the PR too
    # A second run starts from clean files and plants the same defect once.
    again = materialize(tmp_path, cs, c)
    assert again.diff == m.diff

    # A clean case is the commit as it is; a real-bug case keeps the lines it was given.
    clean = Case(id="c2", commit=sha, kind="clean", group="clean")
    real = Case(id="c3", commit=sha, kind="r", group="real", path="m.py", lines=(2, 3))
    cs2 = CaseSet("demo", "unused", "MIT", (clean, real))
    mc = materialize(tmp_path, cs2, clean)
    assert mc.lines == () and "+        return a + 1" in mc.diff
    assert materialize(tmp_path, cs2, real).lines == (2, 3)


@pytest.mark.parametrize(
    ("row", "msg"),
    [
        ({"group": "injected", "path": "p"}, "needs `path` and `find`"),
        ({"group": "real", "path": "p"}, "needs `path` and `lines`"),
        ({"group": "clean", "find": "x"}, "no defect"),
    ],
)
def test_each_group_is_checked(row: dict[str, str], msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        Case(id="x", commit="c", kind="k", **row).check()  # type: ignore[arg-type]


def _task(path: str) -> HunkTask:
    return task_for(hunk_diff(path, 48, ["x = 1"]))


def _comment(path: str, line: int, model: str = "prism-reviewer:v2") -> ReviewComment:
    return ReviewComment(
        path=path,
        line=line,
        comment="this is wrong",
        severity=Severity.MAJOR,
        category=Category.BUG,
        confidence=0.9,
        model=model,
    )


def _dropped(path: str, line: int) -> Decision:
    cand = Candidate(_task(path), RawComment(line=line, comment="maybe"), "prism-reviewer:v2")
    return Decision(cand, None, "verifier_drop")


def test_recall_counts_a_posted_comment_within_three_lines_on_the_file() -> None:
    near = score_case("c", "k", "a.py", [50], [_comment("a.py", 53, "claude")], [], 1.0)
    assert near.hit and near.found_by == "claude"
    far = score_case("c", "k", "a.py", [50], [_comment("a.py", 54)], [], 1.0)
    assert not far.hit
    other_file = score_case("c", "k", "a.py", [50], [_comment("b.py", 50)], [], 1.0)
    assert not other_file.hit


def test_a_dropped_comment_near_the_defect_is_flagged() -> None:
    r = score_case("c", "k", "a.py", [50], [], [_dropped("a.py", 49)], 2.0)
    assert not r.hit and r.near_but_dropped
    s = summarize([r, score_case("d", "k", "a.py", [9], [_comment("a.py", 9)], [], 4.0)])
    assert s["hits"] == 1 and s["recall"] == 0.5 and s["near_but_dropped"] == 1
    assert s["comments_per_pr"] == 0.5
    assert s["p95_first_run_s"] == 4.0


def test_groups_are_scored_apart() -> None:
    rows = [
        score_case("i", "k", "a.py", [9], [_comment("a.py", 9)], [], 1.0, repo="r1"),
        score_case("r", "k", "a.py", [20, 30], [_comment("a.py", 31)], [], 1.0, group="real"),
        score_case("c1", "clean", "", [], [_comment("a.py", 5)], [], 1.0, group="clean"),
        score_case("c2", "clean", "", [], [], [], 1.0, group="clean"),
    ]
    s = summarize(rows)
    assert s["cases"] == 1 and s["hits"] == 1  # the headline counts injected cases only
    assert s["real"]["hits"] == 1  # within 3 lines of any of the defect's lines
    assert s["clean"] == {
        "cases": 2,
        "with_comments": 1,
        "false_positive_rate": 0.5,
        "comments_per_pr": 0.5,
    }
    assert not rows[2].hit


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")


def test_handoff_is_batched_across_cases_and_answers_go_back(tmp_path: Path) -> None:
    run = tmp_path / "run"
    for c in ("c1", "c2"):
        _write(run / c / "verify" / "requests_00.jsonl", [{"id": "a.py#0-x", "comment": c}])
        (run / c / "verify" / "RUBRIC.md").write_text("rubric", "utf-8")
    _write(run / "c2" / "verify" / "escalations_00.jsonl", [{"id": "a.py#0-x-review"}])
    (run / "c1" / "verify" / "REVIEW_RUBRIC.md").write_text("review rubric", "utf-8")

    rd = export_requests(run, ["c1", "c2"])
    assert rd is not None and rd.name == "round_01"
    ids = [json.loads(x)["id"] for x in (rd / "requests_00.jsonl").read_text().splitlines()]
    assert ids == [f"c1{SEP}a.py#0-x", f"c2{SEP}a.py#0-x"]
    assert (rd / "escalations_00.jsonl").exists()
    assert (rd / "RUBRIC.md").exists() and (rd / "REVIEW_RUBRIC.md").exists()
    readme = (rd / "README.md").read_text("utf-8")
    assert "`requests_00.jsonl` -> `verdicts_00.jsonl`" in readme
    assert "`escalations_00.jsonl` -> `reviews_00.jsonl`" in readme
    assert export_requests(run, ["c1", "c2"]) == rd  # unchanged, unanswered: the same round

    _write(rd / "verdicts_00.jsonl", [{"id": f"c1{SEP}a.py#0-x", "keep": False}])
    assert import_answers(run, ["c1", "c2"]) == 1
    back = json.loads((run / "c1" / "verify" / "verdicts_bench.jsonl").read_text())
    assert back == {"id": "a.py#0-x", "keep": False}

    rd2 = export_requests(run, ["c1", "c2"])  # c1 is answered; c2's two requests remain
    assert rd2 is not None and rd2.name == "round_02"
    left = [json.loads(x)["id"] for x in (rd2 / "requests_00.jsonl").read_text().splitlines()]
    assert left == [f"c2{SEP}a.py#0-x"]


def test_a_hung_test_run_is_killed_with_its_children(tmp_path: Path) -> None:
    import sys
    import time

    from prism.bench.mutate import RepoSpec, run_tests

    # The runner starts a child that holds the pipes and never ends, like Maven's forked JVM.
    child = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
        "time.sleep(120)"
    )
    spec = RepoSpec("x", "", "", "python", ("x/",), (), (sys.executable, "-c", child), 60)
    started = time.monotonic()
    run = run_tests(tmp_path, spec, tmp_path, timeout=2)
    assert run.failing == "timeout" and not run.passed
    assert time.monotonic() - started < 30


@pytest.mark.parametrize(
    ("line", "language", "expected"),
    [
        ("    if ready:", "python", "    if not ready:"),
        ("    if a and b:", "python", "    if not (a and b):"),
        ("    while True:", "python", None),  # a negated literal is not a believable mistake
        ("    if x is None:", "python", None),  # comparisons have their own operators
        ("        if (node.hasAttr(key)) {", "java", "        if (!node.hasAttr(key)) {"),
        ("        if (i == -1) {", "java", None),
        ("    if sys.version_info >= (3, 8):", "python", None),  # depends on the interpreter
    ],
)
def test_negation_only_where_it_reads_like_a_real_mistake(
    line: str, language: str, expected: str | None
) -> None:
    from prism.bench.mutate import mutations_of_line

    negated = [new for op, new in mutations_of_line(line, language) if "negate" in op.name]
    assert negated == ([expected] if expected else [])


def test_handoff_endpoint_answers_known_prompts_and_queues_new_ones(tmp_path: Path) -> None:
    import httpx

    from prism.bench.llm_handoff import HandoffStore, export_round, import_answers, serve

    store = HandoffStore(tmp_path / "llm")
    store.tag = "case-1:review"
    msgs = [
        {"role": "system", "content": "You review."},
        {"role": "user", "content": "Today's Date: 2026-11-15\ndiff"},
    ]
    with serve(store) as url:
        miss = httpx.post(f"{url}/chat/completions", json={"model": "m", "messages": msgs})
        assert miss.status_code == 400  # not retried by the client
        rd = export_round(store)
        assert rd is not None and (rd / "README.md").exists()
        row = json.loads((rd / "prompts_00.jsonl").read_text("utf-8").splitlines()[0])
        (rd / "answers_00.jsonl").write_text(
            json.dumps({"id": row["id"], "content": "```yaml\nreview: {}\n```"}) + "\n", "utf-8"
        )
        assert import_answers(store) == 1
        # The same prompt on another day still hits: the date line is not part of the key.
        msgs[1]["content"] = "Today's Date: 2026-11-16\ndiff"
        hit = httpx.post(f"{url}/chat/completions", json={"model": "other", "messages": msgs})
    assert hit.status_code == 200
    assert hit.json()["choices"][0]["message"]["content"].startswith("```yaml")
    assert store.missed == {"case-1:review": {row["id"]}}
    assert not store.pending()


def test_pr_agent_findings_are_read_and_matched_by_range() -> None:
    from prism.bench.baseline import branch_name, findings_of, hits

    review = {
        "review": {
            "key_issues_to_review": [
                {
                    "relevant_file": "a.py\n",
                    "issue_header": "Bug",
                    "issue_content": "x",
                    "start_line": 31,
                    "end_line": 32,
                },
                {"relevant_file": "a.py", "issue_header": "Vague", "issue_content": "no lines"},
            ]
        }
    }
    improve = {
        "code_suggestions": [
            {
                "relevant_file": "b.py\n",
                "suggestion_content": "y",
                "relevant_lines_start": "10",
                "relevant_lines_end": "20",
            },
        ]
    }
    found = findings_of("review", review) + findings_of("improve", improve)
    assert [(f.tool, f.path, f.start, f.end) for f in found] == [
        ("review", "a.py", 31, 32),
        ("improve", "b.py", 10, 20),
    ]
    assert hits(found[0], "a.py", [34]) and not hits(found[0], "a.py", [36])
    assert hits(found[1], "b.py", [15]) and not hits(found[1], "a.py", [15])
    assert branch_name("fix: do not treat str/bytes as sequences (#641)", "6d7c4fcabc") == (
        "fix-do-not-treat-str-bytes-as-sequences-6d7c4fc"
    )


def test_the_ablation_table_compares_runs_on_shared_cases() -> None:
    from prism.bench.report import render_table

    prism_rows = [
        {"case_id": "a", "group": "injected", "hit": True, "posted": 1},
        {"case_id": "b", "group": "real", "hit": False, "posted": 0},
        {"case_id": "c", "group": "clean", "hit": False, "posted": 0},
        {"case_id": "only-here", "group": "injected", "hit": True, "posted": 1},
    ]
    agent_rows = [
        {"case_id": "a", "group": "injected", "hit": True, "findings": 2},
        {"case_id": "b", "group": "real", "hit": True, "findings": 1},
        {"case_id": "c", "group": "clean", "hit": False, "findings": 1},
    ]
    out = render_table([("PRism", prism_rows), ("PR-Agent", agent_rows)], "T")
    assert "3 pull requests shared" in out
    assert "| Injected recall | 1/1 | 1/1 |" in out
    assert "| Real-bug recall | 0/1 | 1/1 |" in out
    assert "| Clean PRs with any comment | 0/1 | 1/1 |" in out
    assert "| b | real | - | hit |" in out and "only-here" not in out


def test_round_files_are_cut_by_size_as_well_as_count() -> None:
    from prism.bench import run as bench_run

    big = [{"id": f"c::{i}", "x": "y" * 90_000} for i in range(5)]
    chunks = bench_run._chunks(big)
    assert [c.count("\n") for c in chunks] == [2, 2, 1]
    small = [{"id": f"c::{i}"} for i in range(30)]
    assert [c.count("\n") for c in bench_run._chunks(small)] == [25, 5]
