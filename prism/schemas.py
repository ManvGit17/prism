"""Pydantic contracts for review output.

Two shapes are kept apart on purpose (PLAN.md section 2b):

* `ReviewerReply` is what the reviewer model writes: a comment and nothing
  else. CodeReviewer has no line labels (ADR 0009), so the model is not asked
  for one.
* `RawComment` is a reply placed on a line. The line is picked outside the
  model (`prism.models.reviewer.anchor_line`).
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


class ReviewerReply(_Strict):
    """What the reviewer model writes for one hunk: `{"comment": "..."}`."""

    comment: NonEmptyStr


class RawComment(_Strict):
    """A reviewer comment placed on a line, before verification."""

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


# Retrieved context (Phase 2, ADR 0007). The verifier and the frontier path read it;
# the fine-tuned reviewer is served the hunk alone.


class ContextMode(StrEnum):
    """What retrieval may use. Phase 7 compares the four."""

    NONE = "none"
    VECTOR = "vector"
    GRAPH = "graph"
    GRAPH_VECTOR = "graph+vector"

    @property
    def uses_graph(self) -> bool:
        return self in (ContextMode.GRAPH, ContextMode.GRAPH_VECTOR)

    @property
    def uses_vector(self) -> bool:
        return self in (ContextMode.VECTOR, ContextMode.GRAPH_VECTOR)


class ContextKind(StrEnum):
    """Kinds of retrieved item, in packing priority order (first packs first)."""

    SIGNATURE = "signature"  # the changed symbol itself
    CONTRACT = "contract"  # supertype method it overrides or implements
    TEST = "test"
    CALLER = "caller"
    IMPLEMENTATION = "implementation"  # methods that override or implement it
    CLASS = "class"  # the class the changed method belongs to
    CALLEE = "callee"
    CLASS_TEST = "class_test"  # tests of its class, when it has no direct test
    SIMILAR = "similar"

    @property
    def priority(self) -> int:
        return list(ContextKind).index(self)


class ContextItem(_Strict):
    kind: ContextKind
    source: str = Field(description="'graph' or 'vector'.")
    qualified_name: str
    path: str
    start_line: LineNumber
    end_line: LineNumber
    reason: NonEmptyStr = Field(description="Why this item was retrieved.")
    snippet: str = Field(description="Code read from the head commit, possibly cut.")
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Edge confidence, or vector similarity."
    )
    score: float = Field(default=0.0, description="Rerank score; higher is more relevant.")
    tokens: int = Field(default=0, ge=0, description="Tokens of the rendered item.")
    truncated: bool = False


class DroppedItem(_Strict):
    kind: ContextKind
    qualified_name: str
    tokens: int = Field(ge=0)
    reason: NonEmptyStr


class HunkContext(_Strict):
    path: NonEmptyStr
    hunk_header: str
    new_start: LineNumber
    new_end: LineNumber
    symbols: tuple[str, ...] = Field(default=(), description="Changed symbols at the head.")
    deleted_symbols: tuple[str, ...] = Field(
        default=(), description="Symbols of the base graph the hunk removes or renames."
    )
    target: str = Field(description="symbol, module, deleted or non_code.")
    items: tuple[ContextItem, ...] = ()
    dropped: tuple[DroppedItem, ...] = ()
    tokens_used: int = Field(default=0, ge=0)
    budget: int = Field(ge=0)


class ContextBundle(_Strict):
    repo_id: NonEmptyStr
    mode: ContextMode
    token_counter: NonEmptyStr
    reranker: NonEmptyStr
    hunks: tuple[HunkContext, ...] = ()
    skipped_files: tuple[str, ...] = Field(default=(), description="Binary files.")

    @property
    def tokens_used(self) -> int:
        return sum(h.tokens_used for h in self.hunks)

    @property
    def items(self) -> int:
        return sum(len(h.items) for h in self.hunks)
