"""BugBench cases, materialized as git worktrees.

A case file (TOML) names a repository and its cases. Each case is a real commit, checked out in
its own worktree; the pull request is the working tree against the commit's parent. A case is
one of three groups:

* `injected`: `find` is replaced by `replace` in `path`, so the real change has a planted
  defect inside it. The defect's line is computed from the replacement.
* `real`: the commit introduced a bug that a later commit (`fix`) fixed; `lines` are the lines
  of `path` (at this commit) that the fix changed.
* `clean`: the commit as it is, with no known defect, to count false positives.
"""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Group = Literal["injected", "real", "clean"]


@dataclass(frozen=True)
class Case:
    id: str
    commit: str
    kind: str  # the kind of defect, or "clean"
    group: Group = "injected"
    path: str = ""
    find: str = ""
    replace: str = ""
    lines: tuple[int, ...] = ()  # real bugs: the defect's lines at `commit`
    fix: str = ""  # real bugs: the commit that fixed it
    operator: str = ""  # injected by the generator: which mutation
    failing: str = ""  # injected and test-checked: a test the defect makes fail
    note: str = ""

    def check(self) -> None:
        if self.group == "injected" and not (self.path and self.find):
            raise ValueError(f"{self.id}: an injected case needs `path` and `find`")
        if self.group == "real" and not (self.path and self.lines):
            raise ValueError(f"{self.id}: a real-bug case needs `path` and `lines`")
        if self.group == "clean" and (self.find or self.lines):
            raise ValueError(f"{self.id}: a clean case has no defect")


def _case(row: dict[str, Any]) -> Case:
    row = {**row, "lines": tuple(row.get("lines", ()))}
    c = Case(**row)
    c.check()
    return c


@dataclass(frozen=True)
class CaseSet:
    repo: str
    url: str
    license: str
    cases: tuple[Case, ...]


@dataclass(frozen=True)
class Materialized:
    case: Case
    checkout: Path
    diff: str
    lines: tuple[int, ...]  # the defect's lines in the new file; empty for a clean case


def load(path: Path) -> CaseSet:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    repo = data["repo"]
    cases = tuple(_case(c) for c in data["case"])
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate case ids")
    return CaseSet(repo["name"], repo["url"], repo.get("license", ""), cases)


def plant(text: str, case: Case) -> tuple[str, int]:
    """The file with the defect, and the defect's line in it."""
    if text.count(case.find) != 1:
        raise ValueError(f"{case.id}: `find` occurs {text.count(case.find)} times in {case.path}")
    new = text.replace(case.find, case.replace)
    start = next(
        (i for i, (a, b) in enumerate(zip(text, new, strict=False)) if a != b),
        min(len(text), len(new)),
    )
    return new, new.count("\n", 0, start) + 1


def git(repo: Path, *args: str, timeout: int = 300) -> str:
    out = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-C", str(repo), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=True,
    )
    return out.stdout


def ensure_clone(root: Path, cs: CaseSet) -> Path:
    clone = root / cs.repo
    if not (clone / ".git").is_dir():
        root.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "-c", "core.autocrlf=false", "clone", "-q", cs.url, str(clone)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=900,
            check=True,
        )
    return clone


def materialize(root: Path, cs: CaseSet, case: Case) -> Materialized:
    """A worktree at the case's commit with the defect planted (reused if already there)."""
    wt = checkout(ensure_clone(root, cs), root / "worktrees" / case.id, case.commit)
    lines = case.lines
    if case.group == "injected":
        target = wt / case.path
        new, line = plant(target.read_bytes().decode("utf-8"), case)
        target.write_bytes(new.encode("utf-8"))
        lines = (line,)
    diff = git(wt, "diff", "--no-color", "--no-ext-diff", f"{case.commit}^")
    return Materialized(case, wt, diff, lines)


def checkout(clone: Path, wt: Path, commit: str) -> Path:
    """A worktree of `clone` at `commit` with clean files (created, or reset if it exists)."""
    if not (wt / ".git").exists():
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(clone, "worktree", "add", "-f", "--detach", str(wt), commit)
    # Start from the commit every time, so a rerun plants the same defect on clean files.
    git(wt, "checkout", "-q", "-f", commit)
    git(wt, "clean", "-q", "-fdx")
    return wt
