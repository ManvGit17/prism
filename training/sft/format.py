"""Turn CodeReviewer comment-generation rows into chat examples for the reviewer.

    python -m training.sft.format --data data/codereviewer/Comment_Generation.zip --out sft_data

Rows are read line by line from the zip (the `oldf` field makes the train file 3.6 GB), cleaned,
filtered, deduplicated and sampled. Train rows whose hunk also appears in valid or test are
dropped. The dataset's own test split is the held-out set and is never trained on.

Writes `train.jsonl`, `valid.jsonl`, `test.jsonl` (one `{"messages": [...], ...}` per line) and
`stats.json` with how many rows each filter removed.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from prism.models.gate import normalize_hunk
from prism.models.reviewer import identifiers, training_messages
from training.gate.data import Split, iter_lines

_WS = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class Row:
    hunk: str
    comment: str
    lang: str | None = None


@dataclass(frozen=True)
class FilterConfig:
    min_words: int = 3
    max_words: int = 120
    max_hunk_lines: int = 60
    max_hunk_chars: int = 4000
    # Share of letters that must be ASCII; drops comments written mostly in other scripts.
    min_ascii_share: float = 0.9
    # Train on specific human comments only (ADR 0009, run 2). The first run learned the
    # data's most common shapes: "This is not needed." and linter output. Off for the test split.
    specific_only: bool = True
    # A comment that names nothing in the hunk (no shared identifier, no code) needs this many
    # words to stay: "Why is this removed?" goes, a sentence that explains a concern stays.
    min_words_unanchored: int = 8
    # The same comment text (case-insensitive) is kept at most this often per split.
    max_same_comment: int = 3


# Linter and bot output that sits in the dataset as review comments: RuboCop cops
# ("Layout/AlignHash: ..."), checkpatch "(style)", line length, whitespace and quote style.
_BOT = re.compile(
    r"""(?ix)
    ^\(style\) | ^[a-z]+/[a-z]+:\s | line\ is\ too\ long | line\ over\ \d+\ characters
    | trailing\ whitespace | prefer\ (single|double)-quoted\ strings | ruby\ 1\.9\ hash\ syntax
    | ^align\ the\ (parameters|elements|arguments) | space\ (inside|before|after|around|missing)
    | ^missing\ space | ^please\ use\ `string\#\w+\(.*stringcomparison\)` | ^standards:
    | may\ be\ misspelled | indent\ should\ use\ tabs | no\ space\ before\ tabs
    | ^(extra|unnecessary)\ (empty|blank)\ line | ^remove\ (empty|blank)\ line | <!--
    """
)
# Something code-like in a comment: a number, snake_case, camelCase or a call.
_CODE_TOKEN = re.compile(r"\d|\w_\w|[a-z][A-Z]|\w\(")


def is_bot_comment(comment: str) -> bool:
    return bool(_BOT.search(comment))


def is_anchored(row: Row) -> bool:
    """True if the comment points at something: an identifier from the hunk or code."""
    c = row.comment
    return bool(identifiers(c) & identifiers(row.hunk)) or "`" in c or bool(_CODE_TOKEN.search(c))


@dataclass
class SplitStats:
    read: int = 0
    kept: int = 0
    sampled: int = 0
    dropped: Counter[str] = field(default_factory=Counter)


def clean_comment(msg: str) -> str:
    text = msg.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def ascii_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(c.isascii() for c in letters) / len(letters)


def reject_reason(row: Row, cfg: FilterConfig) -> str | None:
    """Why a cleaned row is dropped, or None to keep it."""
    words = len(row.comment.split())
    if not row.hunk.strip():
        return "empty_hunk"
    if words < cfg.min_words:
        return "comment_too_short"
    if words > cfg.max_words:
        return "comment_too_long"
    if ascii_share(row.comment) < cfg.min_ascii_share:
        return "comment_not_english"
    if row.hunk.count("\n") + 1 > cfg.max_hunk_lines:
        return "hunk_too_many_lines"
    if len(row.hunk) > cfg.max_hunk_chars:
        return "hunk_too_long"
    if cfg.specific_only:
        if is_bot_comment(row.comment):
            return "bot_or_lint"
        if words < cfg.min_words_unanchored and not is_anchored(row):
            return "vague"
    return None


def parse_row(line: str) -> Row:
    obj = json.loads(line)
    lang = obj.get("lang")
    return Row(
        hunk=normalize_hunk(str(obj["patch"])),
        comment=clean_comment(str(obj["msg"])),
        lang=str(lang).lstrip(".") if lang else None,
    )


def hunk_key(hunk: str) -> str:
    return hashlib.sha1(hunk.encode("utf-8")).hexdigest()


def filter_rows(
    lines: Iterable[str],
    cfg: FilterConfig,
    stats: SplitStats,
    exclude_hunks: set[str] | None = None,
) -> Iterator[Row]:
    """Clean, filter and deduplicate (on hunk and comment) a stream of raw JSON lines."""
    seen: set[str] = set()
    same_comment: Counter[str] = Counter()
    for line in lines:
        stats.read += 1
        row = parse_row(line)
        reason = reject_reason(row, cfg)
        if reason is None and exclude_hunks is not None and hunk_key(row.hunk) in exclude_hunks:
            reason = "hunk_in_eval_split"
        if reason is None:
            key = hunk_key(row.hunk + "\0" + row.comment)
            if key in seen:
                reason = "duplicate"
            seen.add(key)
        if reason is None and cfg.specific_only:
            text = row.comment.lower().strip(" .!?")
            same_comment[text] += 1
            if same_comment[text] > cfg.max_same_comment:
                reason = "repeated_comment"
        if reason:
            stats.dropped[reason] += 1
            continue
        stats.kept += 1
        yield row


def sample(rows: Iterable[Row], n: int | None, seed: int) -> list[Row]:
    """Seeded reservoir sample of `n` rows, in their original order. None keeps all."""
    if n is None:
        return list(rows)
    rng = random.Random(seed)
    reservoir: list[tuple[int, Row]] = []
    for i, row in enumerate(rows):
        if i < n:
            reservoir.append((i, row))
        else:
            j = rng.randint(0, i)
            if j < n:
                reservoir[j] = (i, row)
    reservoir.sort(key=lambda item: item[0])
    return [row for _, row in reservoir]


def to_example(row: Row) -> dict[str, Any]:
    return {"messages": training_messages(row.hunk, row.comment), "lang": row.lang}


def load_rows(
    source: Path,
    split: Split,
    n: int | None,
    seed: int,
    cfg: FilterConfig,
    exclude_hunks: set[str] | None = None,
    head: bool = False,
) -> tuple[list[Row], SplitStats]:
    """Filtered rows of a split: a seeded sample of `n`, or with `head` the first `n` (fast)."""
    stats = SplitStats()
    kept = filter_rows(iter_lines(source, split), cfg, stats, exclude_hunks)
    # With head, reading stops early, so `read` and `kept` count only what was looked at.
    rows = list(itertools.islice(kept, n)) if head and n is not None else sample(kept, n, seed)
    stats.sampled = len(rows)
    return rows, stats


def write_jsonl(path: Path, rows: Iterable[Row]) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(to_example(row), ensure_ascii=False) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def build(
    source: Path,
    out: Path,
    train_rows: int | None,
    valid_rows: int | None,
    test_rows: int | None,
    seed: int = 13,
    cfg: FilterConfig | None = None,
    head: bool = False,
) -> dict[str, Any]:
    """Write the three splits and `stats.json` to `out`; return the stats."""
    cfg = cfg or FilterConfig()
    out.mkdir(parents=True, exist_ok=True)
    # The test split keeps every kind of human comment, so base vs tuned is measured on the
    # same rows whatever the training filters are.
    test_cfg = replace(cfg, specific_only=False)
    test, test_stats = load_rows(source, "test", test_rows, seed, test_cfg, head=head)
    valid, valid_stats = load_rows(source, "valid", valid_rows, seed, cfg, head=head)
    # Exclude against the whole of valid and test, not only the sampled rows.
    held_out = {
        hunk_key(normalize_hunk(str(json.loads(line)["patch"])))
        for split in ("test", "valid")
        for line in iter_lines(source, split)
    }
    train, train_stats = load_rows(source, "train", train_rows, seed, cfg, held_out, head)
    for name, rows in (("train", train), ("valid", valid), ("test", test)):
        write_jsonl(out / f"{name}.jsonl", rows)
    stats: dict[str, Any] = {
        "filters": asdict(cfg),
        "test_filters": asdict(test_cfg),
        "seed": seed,
        "splits": {
            name: {
                "read": s.read,
                "kept": s.kept,
                "sampled": s.sampled,
                "dropped": dict(s.dropped.most_common()),
            }
            for name, s in (("train", train_stats), ("valid", valid_stats), ("test", test_stats))
        },
    }
    (out / "stats.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    return stats


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--data", type=Path, required=True, help="Comment_Generation.zip or a folder")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--train-rows", type=int, default=40000)
    p.add_argument("--valid-rows", type=int, default=1000)
    p.add_argument("--test-rows", type=int, default=500)
    p.add_argument("--seed", type=int, default=13)
    args = p.parse_args(argv)
    stats = build(args.data, args.out, args.train_rows, args.valid_rows, args.test_rows, args.seed)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
