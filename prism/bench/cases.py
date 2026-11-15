"""BugBench cases: a real commit plus one planted defect, materialized as a git worktree.

A case file (TOML) names a repository and its cases. For each case the commit is checked out
in its own worktree, `find` is replaced by `replace` in `path`, and the pull request is the
working tree against the commit's parent: the real change with the defect inside it.
"""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Case:
    id: str
    commit: str
    path: str
    kind: str
    find: str
    replace: str
    note: str = ""


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
    line: int  # the defect's line in the new file


def load(path: Path) -> CaseSet:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    repo = data["repo"]
    cases = tuple(Case(**c) for c in data["case"])
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
    clone = ensure_clone(root, cs)
    wt = root / "worktrees" / case.id
    if not (wt / ".git").exists():
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(clone, "worktree", "add", "-f", "--detach", str(wt), case.commit)
    # Start from the commit every time, so a rerun plants the same defect on clean files.
    git(wt, "checkout", "-q", "-f", case.commit)
    git(wt, "clean", "-q", "-fdx")
    target = wt / case.path
    text = target.read_bytes().decode("utf-8")
    new, line = plant(text, case)
    target.write_bytes(new.encode("utf-8"))
    diff = git(wt, "diff", "--no-color", "--no-ext-diff", f"{case.commit}^")
    return Materialized(case, wt, diff, line)
