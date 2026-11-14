import json
from pathlib import Path

import pytest

from training.sft.format import (
    FilterConfig,
    Row,
    ascii_share,
    build,
    clean_comment,
    read_jsonl,
    reject_reason,
    sample,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sft"


def test_clean_comment() -> None:
    assert clean_comment("  Fix   this\r\n\r\n\r\n\r\nplease \t now ") == "Fix this\n\nplease now"


def test_ascii_share() -> None:
    assert ascii_share("hello") == 1.0
    assert ascii_share("这个变量") == 0.0
    assert ascii_share("123 !!") == 0.0


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (Row("@@\n+x", "Rename `x` please."), None),
        (Row("@@\n+retries = 3", "Why three retries here?"), None),
        (Row("@@\n+x", "Why 200? Shouldn't it be 400?"), None),
        (Row("@@\n+x", "Is this really necessary?"), "vague"),
        (Row("@@\n+x", "This is not needed."), "vague"),
        (Row("@@\n+x", "Metrics/LineLength: Line is too long. [90/80]"), "bot_or_lint"),
        (Row("@@\n+x", "(style) line over 80 characters"), "bot_or_lint"),
        (
            Row("@@\n+x", "Prefer double-quoted strings unless you need single quotes."),
            "bot_or_lint",
        ),
        (Row("", "Rename this variable please."), "empty_hunk"),
        (Row("@@\n+x", "ok then"), "comment_too_short"),
        (Row("@@\n+x", "word " * 200), "comment_too_long"),
        (Row("@@\n+x", "请 改 一下 这个 名字"), "comment_not_english"),
        (Row("@@\n" + "+x\n" * 80, "Rename this variable please."), "hunk_too_many_lines"),
        (Row("@@\n+" + "x" * 5000, "Rename this variable please."), "hunk_too_long"),
    ],
)
def test_reject_reason(row: Row, reason: str | None) -> None:
    assert reject_reason(row, FilterConfig()) == reason


def test_sample_is_seeded_and_ordered() -> None:
    rows = [Row(f"h{i}", "c") for i in range(50)]
    a, b = sample(rows, 10, seed=1), sample(rows, 10, seed=1)
    assert a == b and len(a) == 10
    assert [rows.index(r) for r in a] == sorted(rows.index(r) for r in a)
    assert sample(rows, None, 1) == rows


def test_build(tmp_path: Path) -> None:
    stats = build(FIXTURES, tmp_path, train_rows=None, valid_rows=None, test_rows=4)
    train = stats["splits"]["train"]
    assert train["read"] == 24
    assert train["dropped"] == {
        # The Chinese comment has no spaces, so it is one "word" and goes as too short.
        "comment_too_short": 2,
        "duplicate": 1,
        "hunk_in_eval_split": 1,
    }
    assert train["sampled"] == 20
    assert stats["splits"]["test"]["sampled"] == 4

    rows = read_jsonl(tmp_path / "train.jsonl")
    assert len(rows) == 20
    roles = [m["role"] for m in rows[0]["messages"]]
    assert roles == ["system", "user", "assistant"]
    assert json.loads(rows[0]["messages"][2]["content"])["comment"].startswith("Should")
    test = read_jsonl(tmp_path / "test.jsonl")
    assert {r["lang"] for r in test} <= {"java", "cs"}
    assert json.loads((tmp_path / "stats.json").read_text(encoding="utf-8")) == stats


def test_build_head(tmp_path: Path) -> None:
    stats = build(FIXTURES, tmp_path, train_rows=3, valid_rows=2, test_rows=2, head=True)
    assert stats["splits"]["train"]["sampled"] == 3
    first = read_jsonl(tmp_path / "train.jsonl")[0]
    assert "total_0" in first["messages"][1]["content"]


def test_specific_only_off_keeps_vague_comments() -> None:
    cfg = FilterConfig(specific_only=False)
    assert reject_reason(Row("@@\n+x", "This is not needed."), cfg) is None
    assert reject_reason(Row("@@\n+x", "Line is too long. [90/80]"), cfg) is None


def test_repeated_comment_is_capped() -> None:
    from training.sft.format import SplitStats, filter_rows

    lines = [
        json.dumps(
            {"patch": f"@@ -1 +1 @@\n+v{i} = 1", "msg": "Please rename `v` here.", "lang": ".py"}
        )
        for i in range(6)
    ]
    stats = SplitStats()
    kept = list(filter_rows(lines, FilterConfig(max_same_comment=2), stats))
    assert len(kept) == 2
    assert stats.dropped == {"repeated_comment": 4}
