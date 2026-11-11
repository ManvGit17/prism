"""Unified diff parsing (git format and plain `---`/`+++` diffs).

Handles several files, renames, new and deleted files, mode-only changes and
`\\ No newline at end of file`. Binary files are kept with `is_binary=True` and
no hunks, so callers can skip them.
"""

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
_GIT_HEADER = re.compile(r'^diff --git (?:"?a/(.+?)"?) (?:"?b/(.+?)"?)$')


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FileStatus(StrEnum):
    ADDED = "added"
    DELETED = "deleted"
    MODIFIED = "modified"
    RENAMED = "renamed"


class DiffLine(_Frozen):
    kind: Literal["+", "-", " "]
    text: str
    old_line: int | None = Field(description="Line in the old file; None for an added line.")
    new_line: int | None = Field(description="Line in the new file; None for a removed line.")
    no_newline: bool = Field(
        default=False, description="Followed by `\\ No newline at end of file`."
    )


class Hunk(_Frozen):
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str = Field(default="", description="Text after the second @@, often a def line.")
    lines: tuple[DiffLine, ...]

    @property
    def added_lines(self) -> list[int]:
        return [ln.new_line for ln in self.lines if ln.kind == "+" and ln.new_line is not None]

    @property
    def removed_lines(self) -> list[int]:
        return [ln.old_line for ln in self.lines if ln.kind == "-" and ln.old_line is not None]

    @property
    def new_range(self) -> tuple[int, int]:
        """First and last new-file line the hunk covers (a pure deletion covers one line)."""
        end = self.new_start + max(self.new_count, 1) - 1
        return (max(self.new_start, 1), max(end, 1))

    @property
    def header(self) -> str:
        return f"@@ -{self.old_start},{self.old_count} +{self.new_start},{self.new_count} @@" + (
            f" {self.section}" if self.section else ""
        )

    def text(self) -> str:
        """The hunk as it appears in the diff."""
        out = [self.header]
        for ln in self.lines:
            out.append(f"{ln.kind}{ln.text}")
            if ln.no_newline:
                out.append("\\ No newline at end of file")
        return "\n".join(out)

    def new_text(self) -> str:
        """Context and added lines: the hunk as the new file reads."""
        return "\n".join(ln.text for ln in self.lines if ln.kind != "-")


class FileDiff(_Frozen):
    old_path: str | None = Field(description="None for an added file.")
    new_path: str | None = Field(description="None for a deleted file.")
    status: FileStatus
    is_binary: bool = False
    hunks: tuple[Hunk, ...] = ()

    @property
    def path(self) -> str:
        """The path at the PR head, or the old path for a deleted file."""
        path = self.new_path or self.old_path
        assert path is not None
        return path


def _strip_prefix(raw: str) -> str | None:
    raw = raw.split("\t", 1)[0].strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
        raw = raw.encode("latin-1", "replace").decode("utf-8", "replace")
    if raw == "/dev/null":
        return None
    if raw[:2] in ("a/", "b/"):
        return raw[2:]
    return raw


class _Builder:
    def __init__(self) -> None:
        self.old_path: str | None = None
        self.new_path: str | None = None
        self.added = False
        self.deleted = False
        self.renamed = False
        self.binary = False
        self.hunks: list[Hunk] = []

    def build(self) -> FileDiff | None:
        if self.old_path is None and self.new_path is None:
            return None
        if self.added or (self.old_path is None):
            status = FileStatus.ADDED
            self.old_path = None
        elif self.deleted or self.new_path is None:
            status = FileStatus.DELETED
            self.new_path = None
        elif self.renamed or self.old_path != self.new_path:
            status = FileStatus.RENAMED
        else:
            status = FileStatus.MODIFIED
        return FileDiff(
            old_path=self.old_path,
            new_path=self.new_path,
            status=status,
            is_binary=self.binary,
            hunks=() if self.binary else tuple(self.hunks),
        )


def parse_diff(text: str) -> list[FileDiff]:
    """Parse a unified diff into files and hunks."""
    files: list[FileDiff] = []
    current: _Builder | None = None
    lines = text.removeprefix("\ufeff").splitlines()
    i = 0

    def finish() -> None:
        if current is not None:
            built = current.build()
            if built is not None:
                files.append(built)

    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            finish()
            current = _Builder()
            match = _GIT_HEADER.match(line)
            if match:
                current.old_path, current.new_path = match.group(1), match.group(2)
            i += 1
            continue
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            if current is None or current.hunks:
                finish()
                current = _Builder()
            current.old_path = _strip_prefix(line[4:])
            current.new_path = _strip_prefix(lines[i + 1][4:])
            i += 2
            continue
        if current is None:
            i += 1
            continue
        if line.startswith("new file mode"):
            current.added = True
        elif line.startswith("deleted file mode"):
            current.deleted = True
        elif line.startswith("rename from "):
            current.renamed = True
            current.old_path = line[len("rename from ") :]
        elif line.startswith("rename to "):
            current.renamed = True
            current.new_path = line[len("rename to ") :]
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            current.binary = True
        elif line.startswith("@@"):
            hunk, i = _parse_hunk(lines, i)
            if hunk is not None:
                current.hunks.append(hunk)
            continue
        i += 1
    finish()
    return files


def _parse_hunk(lines: list[str], i: int) -> tuple[Hunk | None, int]:
    match = _HUNK.match(lines[i])
    if not match:
        return None, i + 1
    old_start, old_count = int(match.group(1)), int(match.group(2) or "1")
    new_start, new_count = int(match.group(3)), int(match.group(4) or "1")
    section = match.group(5).strip()
    old, new = old_start, new_start
    old_left, new_left = old_count, new_count
    out: list[DiffLine] = []
    i += 1
    while i < len(lines) and (old_left > 0 or new_left > 0):
        line = lines[i]
        tag, body = (line[:1], line[1:]) if line else (" ", "")
        if tag == "\\":
            if out:
                out[-1] = out[-1].model_copy(update={"no_newline": True})
            i += 1
            continue
        if tag == "+":
            out.append(DiffLine(kind="+", text=body, old_line=None, new_line=new))
            new += 1
            new_left -= 1
        elif tag == "-":
            out.append(DiffLine(kind="-", text=body, old_line=old, new_line=None))
            old += 1
            old_left -= 1
        elif tag == " ":
            out.append(DiffLine(kind=" ", text=body, old_line=old, new_line=new))
            old += 1
            new += 1
            old_left -= 1
            new_left -= 1
        else:
            break
        i += 1
    # A trailing marker belongs to the last line.
    if i < len(lines) and lines[i].startswith("\\") and out:
        out[-1] = out[-1].model_copy(update={"no_newline": True})
        i += 1
    hunk = Hunk(
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        section=section,
        lines=tuple(out),
    )
    return hunk, i
