from pathlib import Path

from prism.retrieval.diff import FileStatus, parse_diff

DIFFS = Path(__file__).parent / "fixtures" / "diffs"


def _read(name: str) -> str:
    return (DIFFS / name).read_text(encoding="utf-8")


def test_multi_file_modify_add_delete() -> None:
    files = parse_diff(_read("multi.diff"))
    assert [(f.status, f.old_path, f.new_path) for f in files] == [
        (FileStatus.MODIFIED, "shop/pricing.py", "shop/pricing.py"),
        (FileStatus.ADDED, None, "shop/gift.py"),
        (FileStatus.DELETED, "shop/legacy.py", None),
    ]
    hunk = files[0].hunks[0]
    assert (hunk.old_start, hunk.old_count, hunk.new_start, hunk.new_count) == (4, 7, 4, 8)
    assert hunk.section == "TAX = 0.2"
    assert hunk.added_lines == [7, 8]
    assert hunk.removed_lines == [7]
    assert hunk.new_range == (4, 11)
    context = [ln for ln in hunk.lines if ln.kind == " "]
    assert (context[2].text, context[2].old_line, context[2].new_line) == (
        "def with_tax(amount):",
        6,
        6,
    )
    assert files[1].hunks[0].added_lines == [1, 2, 3]
    assert files[2].hunks[0].removed_lines == [1, 2]
    assert files[2].path == "shop/legacy.py"


def test_rename_with_and_without_changes() -> None:
    files = parse_diff(_read("rename.diff"))
    assert [(f.status, f.old_path, f.new_path, len(f.hunks)) for f in files] == [
        (FileStatus.RENAMED, "shop/pricing.py", "shop/prices.py", 1),
        (FileStatus.RENAMED, "docs/old.md", "docs/new.md", 0),
    ]
    assert files[0].hunks[0].added_lines == [7]
    assert files[0].hunks[0].removed_lines == [7]


def test_no_newline_marker() -> None:
    (f,) = parse_diff(_read("no_newline.diff"))
    lines = f.hunks[0].lines
    assert [(ln.kind, ln.text, ln.no_newline) for ln in lines] == [
        (" ", "first", False),
        ("-", "second", True),
        ("+", "second, edited", True),
    ]
    assert "\\ No newline at end of file" in f.hunks[0].text()


def test_binary_files_are_flagged_without_hunks() -> None:
    files = parse_diff(_read("binary.diff"))
    assert [(f.path, f.is_binary, len(f.hunks)) for f in files] == [
        ("logo.png", True, 0),
        ("app.py", False, 1),
    ]
    assert files[1].hunks[0].added_lines == [2]  # "@@ -1 +1,2 @@": counts default to 1


def test_plain_unified_diff_and_bom() -> None:
    files = parse_diff("\ufeff" + _read("plain.diff"))
    assert [(f.status, f.path) for f in files] == [(FileStatus.MODIFIED, "shop/util.py")]
    assert files[0].hunks[0].added_lines == [21]


def test_hunk_text_round_trips() -> None:
    hunk = parse_diff(_read("multi.diff"))[0].hunks[0]
    again = parse_diff("--- a/shop/pricing.py\n+++ b/shop/pricing.py\n" + hunk.text() + "\n")[
        0
    ].hunks[0]
    assert again == hunk
