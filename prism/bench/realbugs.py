"""Finding real-bug cases with SZZ: a bug-fix commit, blamed back to the commit that introduced
the lines it changed.

For each recent commit whose message looks like a fix, the source lines it removed or changed
are blamed at the fix's parent. The commit those lines come from is the pull request that
introduced the bug, and the lines (at their positions in that commit) are where a reviewer
should have commented. The output is a list of candidates to read and curate by hand: a fix
message can lie, a blamed commit can be a refactor that only moved the line, and a fix that
only adds lines (a missing check) has nothing to blame.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from prism.bench.cases import git
from prism.bench.mutate import MAX_CHANGED_LINES, RepoSpec, _changed_lines, _prose_lines

_FIX = re.compile(r"\b(fix(es|ed)?|bug|regression|broke|crash|wrong|incorrect)\b", re.I)
# Fixes to prose, style, type hints, tooling or old interpreters change no behaviour.
_NOT_A_BUG = re.compile(
    r"\b(docs?|typos?|docstring|style|pep ?8|mypy|pyright|ruff|black|flake8|ty|typing|types?|"
    r"hints?|annotations?|lint|chore|format(ting)?|deps|compat(ibility)?|py\s?\d[\d.]*)\b",
    re.I,
)


@dataclass
class RealCandidate:
    fix: str
    fix_subject: str
    introduced: str
    introduced_subject: str
    path: str
    lines: list[int] = field(default_factory=list)  # in `introduced`'s version of `path`
    changed_lines: int = 0  # size of the introducing commit


def fix_commits(clone: Path, spec: RepoSpec, window: int) -> Iterator[tuple[str, str]]:
    log = git(clone, "log", "--no-merges", "--format=%h%x09%s", f"-n{window}", "--", *spec.source)
    for row in log.splitlines():
        sha, _, subject = row.partition("\t")
        if _FIX.search(subject) and not _NOT_A_BUG.search(subject):
            yield sha, subject


def _removed_lines(clone: Path, fix: str, spec: RepoSpec) -> dict[str, list[int]]:
    """Old-side line numbers (at fix^) of source lines the fix removed or changed."""
    diff = git(clone, "diff", "--no-color", "--unified=0", f"{fix}^", fix)
    out: dict[str, list[int]] = {}
    path, old_no = "", 0
    for raw in diff.splitlines():
        if raw.startswith("--- "):
            path = raw[6:] if raw.startswith("--- a/") else ""
        elif raw.startswith("@@"):
            m = re.match(r"@@ -(\d+)", raw)
            old_no = int(m.group(1)) if m else 0
        elif raw.startswith("-") and not raw.startswith("---") and path and spec.is_source(path):
            if raw[1:].strip():
                out.setdefault(path, []).append(old_no)
            old_no += 1
    return out


def _blame(clone: Path, rev: str, path: str, line: int) -> tuple[str, int] | None:
    out = git(clone, "blame", "--porcelain", "-L", f"{line},{line}", rev, "--", path)
    head = out.splitlines()[0].split() if out else []
    if len(head) < 2:
        return None
    return head[0][:7], int(head[1])  # commit, line number in that commit's file


def _subject(clone: Path, sha: str) -> str:
    return git(clone, "log", "-1", "--format=%s", sha).strip()


def candidates(clone: Path, spec: RepoSpec, window: int = 300) -> list[RealCandidate]:
    found: dict[tuple[str, str, str], RealCandidate] = {}
    for fix, subject in fix_commits(clone, spec, window):
        for path, lines in _removed_lines(clone, fix, spec).items():
            old = git(clone, "show", f"{fix}^:{path}")
            prose = _prose_lines(old, spec.language)
            text = old.split("\n")
            for ln in lines:
                stripped = text[ln - 1].strip() if ln - 1 < len(text) else ""
                if ln in prose or stripped.startswith(("#", "//", "*", "/*")):
                    continue  # a comment or a docstring, not code
                blamed = _blame(clone, f"{fix}^", path, ln)
                if blamed is None:
                    continue
                sha, orig = blamed
                key = (fix, sha, path)
                if key not in found:
                    found[key] = RealCandidate(fix, subject, sha, _subject(clone, sha), path)
                found[key].lines.append(orig)
    out: list[RealCandidate] = []
    for c in found.values():
        try:
            parents = git(clone, "rev-list", "--parents", "-n1", c.introduced).split()
            if len(parents) != 2:  # a root commit or a merge
                continue
            c.changed_lines = _changed_lines(clone, c.introduced)
        except subprocess.CalledProcessError:  # a blamed commit outside the history
            continue
        if c.changed_lines <= MAX_CHANGED_LINES:
            c.lines = sorted(set(c.lines))
            out.append(c)
    return out
