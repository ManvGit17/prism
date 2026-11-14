import json
from pathlib import Path

import pytest

from eval.sft_style import build, style


def test_style_counts() -> None:
    comments = [
        "I think `x` should be renamed.",
        "Why is this needed?",
        "This is not needed.",
        "This is not needed",
        "Line is too long. [90/80]",
    ]
    s = style(comments, ["@@\n+x = 1"] * 5)
    assert s["comments"] == 5
    assert s["backtick"] == 20.0
    assert s["question"] == 20.0
    assert s["starts_i_think"] == 20.0
    assert s["distinct"] == 4
    assert s["most_common"] == [["this is not needed", 2]]
    assert s["bot_or_lint"] == 20.0
    # "Why is this needed?" and both "This is not needed" are short and name nothing.
    assert s["vague"] == 60.0


def test_build_checks_runs_share_rows(tmp_path: Path) -> None:
    def write(name: str, ids: list[int]) -> Path:
        path = tmp_path / name
        rows = [
            {
                "id": i,
                "hunk": "@@\n+x = 1",
                "reference": "Rename `x`.",
                "base": '{"comment": "The `x` is fine."}',
                "tuned": '{"comment": "I think `x` is wrong."}',
            }
            for i in ids
        ]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return path

    report = build({"run1": write("a.jsonl", [0, 1]), "run2": write("b.jsonl", [0, 1])})
    assert list(report) == ["base", "run1", "run2", "human"]
    assert report["run2"]["starts_i_think"] == 100.0
    with pytest.raises(ValueError):
        build({"run1": write("c.jsonl", [0, 1]), "run2": write("d.jsonl", [1, 0])})
