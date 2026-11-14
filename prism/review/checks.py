"""Checks every reviewer comment must pass before anything else looks at it (ADR 0010).

These are deterministic and cheap, so they run first and the model verifier (stage 2) only
sees what is left:

* the line the comment is placed on is in the hunk's new side;
* the comment is not vague: under 8 words and naming nothing in the hunk is dropped, the same
  rule the run 2 training filter used;
* it does not repeat a comment already kept (same text anywhere in the PR, or a near copy on
  the same line).

`RuleVerifier` keeps what passes and labels it with keyword rules. Its confidence is fixed low:
nothing has checked the claim itself.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from prism.models.reviewer import identifiers
from prism.review.plan import HunkTask
from prism.schemas import Category, RawComment, ReviewComment, Severity

MIN_WORDS_UNANCHORED = 8
NEAR_DUPLICATE = 0.6
RULES_CONFIDENCE = 0.3

_CODE_TOKEN = re.compile(r"\w+\(|\w+\.\w+|\w+_\w+|[a-z]+[A-Z]\w*|==|!=|<=|>=|->")
_WORD = re.compile(r"[a-z0-9]+")

_CATEGORY_WORDS: list[tuple[Category, re.Pattern[str]]] = [
    (
        Category.SECURITY,
        re.compile(
            r"inject|sql|xss|csrf|secret|password|credential|vulnerab|sanitiz|escap|unsafe|"
            r"shell|deserializ|pickle|eval\b|exec\b|privilege|auth",
            re.I,
        ),
    ),
    (
        Category.BUG,
        re.compile(
            r"\bbug|wrong|incorrect|crash|exception|\bnull\b|\bnone\b|off.by.one|race|leak|"
            r"overflow|never|always|fails?\b|broken|missing return|infinite|undefined|typeerror|"
            r"keyerror|indexerror|nullpointer|unreachable|shadow",
            re.I,
        ),
    ),
    (
        Category.PERFORMANCE,
        re.compile(r"performan|slow|n\+1|cache|complexity|quadratic|memory|allocat|efficien", re.I),
    ),
    (Category.TESTING, re.compile(r"\btests?\b|coverage|assert|unit test", re.I)),
    (
        Category.MAINTAINABILITY,
        re.compile(
            r"refactor|duplicat|simplif|readab|extract|constant|magic|dead code|unused|"
            r"hard.?coded|reuse",
            re.I,
        ),
    ),
    (
        Category.STYLE,
        re.compile(r"naming|rename|\bname\b|typo|format|indent|whitespace|style|docstring", re.I),
    ),
]


@dataclass(frozen=True)
class Candidate:
    """A reviewer comment with what it was written about."""

    task: HunkTask
    raw: RawComment
    model: str

    @property
    def path(self) -> str:
        return self.task.path


@dataclass(frozen=True)
class Decision:
    candidate: Candidate
    kept: ReviewComment | None
    reason: str  # "kept" or why it was dropped


def new_side_lines(task: HunkTask) -> set[int]:
    lines = {ln.new_line for ln in task.hunk.lines if ln.kind != "-" and ln.new_line is not None}
    if not lines:  # a pure deletion: the comment sits where the lines were
        start, end = task.hunk.new_range
        lines = set(range(start, end + 1))
    return lines


def is_anchored(comment: str, hunk: str) -> bool:
    """True if the comment points at something in the hunk: a shared identifier or code."""
    return (
        bool(identifiers(comment) & identifiers(hunk))
        or "`" in comment
        or bool(_CODE_TOKEN.search(comment))
    )


def is_vague(comment: str, hunk: str) -> bool:
    return len(comment.split()) < MIN_WORDS_UNANCHORED and not is_anchored(comment, hunk)


def normalized(comment: str) -> str:
    return " ".join(_WORD.findall(comment.lower()))


def similarity(a: str, b: str) -> float:
    wa, wb = set(_WORD.findall(a.lower())), set(_WORD.findall(b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def categorize(comment: str) -> Category:
    for category, pattern in _CATEGORY_WORDS:
        if pattern.search(comment):
            return category
    return Category.OTHER


def default_severity(category: Category, comment: str) -> Severity:
    if category is Category.SECURITY:
        return Severity.MAJOR
    if category in (Category.STYLE, Category.OTHER) or comment.rstrip().endswith("?"):
        return Severity.INFO
    return Severity.MINOR


def rule_reason(candidate: Candidate, kept: Sequence[Candidate]) -> str | None:
    """Why a candidate fails the deterministic checks, given those kept before it."""
    raw = candidate.raw
    if raw.line not in new_side_lines(candidate.task):
        return "line_not_in_diff"
    if is_vague(raw.comment, candidate.task.text):
        return "vague"
    text = normalized(raw.comment)
    for other in kept:
        if normalized(other.raw.comment) == text:
            return "duplicate"
        if (
            other.path == candidate.path
            and other.raw.line == raw.line
            and similarity(other.raw.comment, raw.comment) >= NEAR_DUPLICATE
        ):
            return "duplicate"
    return None


def apply_rules(candidates: Sequence[Candidate]) -> tuple[list[Candidate], list[Decision]]:
    """(passing candidates, decisions for the dropped ones), in input order."""
    passing: list[Candidate] = []
    dropped: list[Decision] = []
    for cand in candidates:
        reason = rule_reason(cand, passing)
        if reason:
            dropped.append(Decision(cand, None, reason))
        else:
            passing.append(cand)
    return passing, dropped


def label(candidate: Candidate, confidence: float = RULES_CONFIDENCE) -> ReviewComment:
    category = categorize(candidate.raw.comment)
    return ReviewComment(
        path=candidate.path,
        line=candidate.raw.line,
        comment=candidate.raw.comment,
        severity=default_severity(category, candidate.raw.comment),
        category=category,
        confidence=confidence,
        model=candidate.model,
    )


class RuleVerifier:
    """Deterministic checks and keyword labels; no model call."""

    name = "rules"

    async def verify(
        self,
        candidates: Sequence[Candidate],
        contexts: Mapping[tuple[str, str], str] | None = None,
        risk: Mapping[str, Sequence[str]] | None = None,
    ) -> list[Decision]:
        passing, dropped = apply_rules(candidates)
        return [*dropped, *(Decision(c, label(c), "kept") for c in passing)]
