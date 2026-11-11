"""Pydantic contracts for review output.

Two shapes are kept apart on purpose (PLAN.md section 2b):

* `RawComment` is what a reviewer model emits: a line and a comment. The
  fine-tuned model has training labels for nothing else.
* `ReviewComment` is a comment the verifier has kept. The verifier adds the
  severity, the category and its confidence.

Every model output is validated against these, retried at most twice, then
dropped and logged.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
LineNumber = Annotated[int, Field(ge=1)]


class Severity(StrEnum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class Category(StrEnum):
    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    TESTING = "testing"
    STYLE = "style"
    OTHER = "other"


class Verdict(StrEnum):
    """Overall risk verdict for a pull request."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RawComment(_Strict):
    """A reviewer model's output for one hunk, before verification."""

    line: LineNumber = Field(description="Line in the new version of the file.")
    comment: NonEmptyStr


class Evidence(_Strict):
    """What a comment rests on: a static-analysis finding or retrieved code."""

    source: NonEmptyStr = Field(description="Tool or context kind, e.g. 'semgrep', 'caller'.")
    detail: NonEmptyStr


class ReviewComment(_Strict):
    """A comment the verifier kept. Only these are posted."""

    path: NonEmptyStr
    line: LineNumber
    comment: NonEmptyStr
    severity: Severity
    category: Category
    confidence: float = Field(ge=0.0, le=1.0, description="Verifier confidence.")
    model: NonEmptyStr = Field(description="Model that wrote the comment.")
    evidence: tuple[Evidence, ...] = ()
    suggested_fix: str | None = Field(
        default=None, description="Only the frontier path produces a fix."
    )


class FileReview(_Strict):
    path: NonEmptyStr
    comments: tuple[ReviewComment, ...] = ()
    hunks_total: int = Field(ge=0)
    hunks_reviewed: int = Field(ge=0, description="Hunks sent to a model.")
    hunks_skipped_by_gate: int = Field(default=0, ge=0)
    hunks_over_budget: int = Field(default=0, ge=0, description="Listed as not reviewed.")


class PRReview(_Strict):
    repo: NonEmptyStr
    pr_number: int | None = Field(default=None, ge=1, description="None for a local diff.")
    head_sha: NonEmptyStr
    summary: str
    verdict: Verdict
    files: tuple[FileReview, ...] = ()
    cost_usd: float = Field(default=0.0, ge=0.0)
    duration_s: float = Field(default=0.0, ge=0.0)

    @property
    def comments(self) -> tuple[ReviewComment, ...]:
        return tuple(c for f in self.files for c in f.comments)
