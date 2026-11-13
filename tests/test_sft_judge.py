import json
from pathlib import Path

import pytest

from eval.sft_judge import blind, comment_text, tally, tuned_is_a, unblind, wilson


def row(i: int, base: str, tuned: str) -> dict[str, object]:
    return {
        "id": i,
        "lang": "py",
        "hunk": f"@@ -1 +1 @@\n-a{i}\n+b{i}",
        "base": base,
        "tuned": tuned,
    }


def test_comment_text_unwraps_valid_json_and_keeps_raw_text() -> None:
    assert comment_text('{"comment": "Rename `x`."}') == "Rename `x`."
    assert comment_text("not json") == "not json"


def test_blinding_round_trip() -> None:
    rows = [row(i, f'{{"comment": "base {i}"}}', f'{{"comment": "tuned {i}"}}') for i in range(40)]
    rows.append(row(99, '{"comment": "same"}', '{"comment": "same"}'))
    pairs, same = blind(rows, seed=13)
    assert same == [99]
    assert len(pairs) == 40
    assert {tuned_is_a(13, i) for i in range(40)} == {True, False}, "both sides are used"
    for p in pairs:
        side = "A" if p["A"].startswith("tuned") else "B"
        assert unblind(side, p["pair"], 13) == "tuned"
        assert unblind("B" if side == "A" else "A", p["pair"], 13) == "base"
        assert unblind("tie", p["pair"], 13) == "tie"
        assert "tuned" not in json.dumps({k: v for k, v in p.items() if k not in ("A", "B")})


def test_unblind_rejects_bad_winner() -> None:
    with pytest.raises(ValueError):
        unblind("C", 1, 13)


def test_tally() -> None:
    t = tally(["tuned"] * 6 + ["base"] * 2 + ["tie"] * 2)
    assert (t["tuned_win_rate"], t["base_win_rate"], t["tie_rate"]) == (60.0, 20.0, 20.0)
    assert t["tuned_share_of_decided"] == 75.0
    low, high = t["tuned_share_ci95"]
    assert low < 75.0 < high
    assert wilson(0, 0) == (0.0, 0.0)


def test_score_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import eval.sft_judge as judge

    monkeypatch.setattr(judge, "REPORTS", tmp_path / "reports")
    gens = tmp_path / "generations.jsonl"
    rows = [row(i, f'{{"comment": "base {i}"}}', f'{{"comment": "tuned {i}"}}') for i in range(6)]
    gens.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    judge.prepare(gens, tmp_path / "work", batch_size=4, seed=13)
    assert sorted(p.name for p in (tmp_path / "work").glob("batch_*")) == [
        "batch_00.jsonl",
        "batch_01.jsonl",
    ]
    # A judge that always picks the tuned comment.
    out = tmp_path / "work" / "sonnet"
    out.mkdir()
    lines = []
    for batch in sorted((tmp_path / "work").glob("batch_*")):
        for p in judge.read_jsonl(batch):
            side = "A" if p["A"].startswith("tuned") else "B"
            lines.append(json.dumps({"pair": p["pair"], "winner": side, "reason": "r"}))
    (out / "verdicts_00.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = judge.score(gens, tmp_path / "work", ["sonnet"], seed=13)
    assert report["sonnet"]["tuned_wins"] == 6
    assert report["sonnet"]["missing"] == 0
