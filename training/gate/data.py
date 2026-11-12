"""Read the CodeReviewer quality-estimation data without loading it all into memory.

The archive holds jsonl files, one JSON object per line. Rows carry the whole old file (`oldf`)
next to the hunk, which makes the train split gigabytes large, so rows are read line by line and
only the hunk and the label are kept. The dataset's own train/valid/test split is used as is.
"""

from __future__ import annotations

import itertools
import json
import random
import re
import zipfile
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Split = Literal["train", "valid", "test"]
SPLITS: tuple[Split, ...] = ("train", "valid", "test")

# Field names in the quality-estimation files. See docs/datasets.md.
TEXT_FIELD = "patch"
LABEL_FIELD = "y"


@dataclass(frozen=True)
class Example:
    text: str
    label: int


def split_of(name: str) -> Split | None:
    """The split a file belongs to, from its name (`cls-train-chunk-0.jsonl` -> train)."""
    base = re.split(r"[\/]", name)[-1].lower()
    if not base.endswith(".jsonl"):
        return None
    for split in SPLITS:
        if re.search(rf"(^|[^a-z]){split}([^a-z]|$)", base):
            return split
    return None


def list_files(source: Path, split: Split) -> list[str]:
    """Names of the jsonl files of one split, in a stable order. `source` is a zip or a folder."""
    if source.is_dir():
        names = [str(p.relative_to(source)) for p in source.rglob("*.jsonl")]
    else:
        with zipfile.ZipFile(source) as zf:
            names = [i.filename for i in zf.infolist() if not i.is_dir()]
    return sorted(n for n in names if split_of(n) == split)


def iter_lines(source: Path, split: Split) -> Iterator[str]:
    """Raw JSON lines of a split, file by file. Nothing is parsed here."""
    files = list_files(source, split)
    if not files:
        raise FileNotFoundError(f"no {split} jsonl file in {source}")
    for name in files:
        if source.is_dir():
            with (source / name).open("r", encoding="utf-8") as fh:
                yield from (line for line in fh if line.strip())
        else:
            with zipfile.ZipFile(source) as zf, zf.open(name) as raw:
                for line in raw:
                    if line.strip():
                        yield line.decode("utf-8")


def parse_row(line: str) -> Example:
    """Keep the hunk and the label; the large `oldf` field is dropped with the rest."""
    obj = json.loads(line)
    return Example(text=str(obj[TEXT_FIELD]), label=int(obj[LABEL_FIELD]))


def iter_examples(source: Path, split: Split) -> Iterator[Example]:
    for line in iter_lines(source, split):
        yield parse_row(line)


def load_split(
    source: Path, split: Split, max_rows: int | None = None, seed: int = 13, head: bool = False
) -> list[Example]:
    """Rows of a split, optionally a seeded uniform sample of `max_rows` of them.

    The sample is reservoir sampling over line positions, so the choice does not depend on row
    content, only the kept lines are parsed, and the result keeps the file order. The same seed
    and data always give the same rows. With `head=True` the first `max_rows` rows are taken
    instead: fast (no pass over the whole split), for dry runs only.
    """
    if max_rows is None:
        return list(iter_examples(source, split))
    if max_rows < 1:
        raise ValueError("max_rows must be at least 1")
    if head:
        return [parse_row(line) for line in itertools.islice(iter_lines(source, split), max_rows)]
    rng = random.Random(seed)
    reservoir: list[tuple[int, str]] = []
    for i, line in enumerate(iter_lines(source, split)):
        if i < max_rows:
            reservoir.append((i, line))
        else:
            j = rng.randint(0, i)
            if j < max_rows:
                reservoir[j] = (i, line)
    reservoir.sort(key=lambda item: item[0])
    return [parse_row(line) for _, line in reservoir]


def class_balance(examples: list[Example]) -> dict[str, float | int]:
    """Row count per label and the share of positives, for the run report."""
    counts = Counter(e.label for e in examples)
    n = len(examples)
    return {
        "rows": n,
        "label_0": counts.get(0, 0),
        "label_1": counts.get(1, 0),
        "positive_share": counts.get(1, 0) / n if n else 0.0,
    }
