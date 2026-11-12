import zipfile
from pathlib import Path

import pytest

from training.gate.data import class_balance, list_files, load_split, split_of

FIXTURES = Path(__file__).parent / "fixtures" / "gate"


@pytest.fixture
def zip_path(tmp_path: Path) -> Path:
    path = tmp_path / "qe.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Diff_Quality_Estimation/", "")
        for f in sorted(FIXTURES.glob("*.jsonl")):
            zf.write(f, f"Diff_Quality_Estimation/{f.name}")
    return path


def test_split_of() -> None:
    assert split_of("Diff_Quality_Estimation/cls-train-chunk-3.jsonl") == "train"
    assert split_of("cls-valid.jsonl") == "valid"
    assert split_of("a/cls-test.jsonl") == "test"
    assert split_of("Diff_Quality_Estimation/") is None
    assert split_of("readme.txt") is None


@pytest.mark.parametrize("kind", ["folder", "zip"])
def test_load_splits(kind: str, zip_path: Path) -> None:
    source = FIXTURES if kind == "folder" else zip_path
    assert len(list_files(source, "train")) == 2
    train = load_split(source, "train")
    assert (len(train), len(load_split(source, "valid")), len(load_split(source, "test"))) == (
        30,
        12,
        12,
    )
    assert train[0].text.startswith("@@")
    assert train[0].label in (0, 1)
    # oldf and the other fields are not carried.
    assert not hasattr(train[0], "oldf")


def test_class_balance() -> None:
    train = load_split(FIXTURES, "train")
    bal = class_balance(train)
    assert bal["rows"] == 30
    assert bal["label_0"] + bal["label_1"] == 30
    assert bal["positive_share"] == pytest.approx(10 / 30)
    assert class_balance([])["positive_share"] == 0.0


def test_subsample_is_deterministic_and_keeps_order(zip_path: Path) -> None:
    a = load_split(zip_path, "train", max_rows=7, seed=1)
    b = load_split(zip_path, "train", max_rows=7, seed=1)
    c = load_split(zip_path, "train", max_rows=7, seed=2)
    assert a == b
    assert a != c
    assert len(a) == 7
    full = load_split(zip_path, "train")
    positions = [full.index(e) for e in a]
    assert positions == sorted(positions)


def test_subsample_larger_than_data(zip_path: Path) -> None:
    assert len(load_split(zip_path, "valid", max_rows=1000)) == 12


def test_missing_split(tmp_path: Path) -> None:
    (tmp_path / "other.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        load_split(tmp_path, "train")
    with pytest.raises(ValueError):
        load_split(FIXTURES, "train", max_rows=0)


def test_head_takes_first_rows(zip_path: Path) -> None:
    full = load_split(zip_path, "train")
    assert load_split(zip_path, "train", max_rows=5, head=True) == full[:5]
