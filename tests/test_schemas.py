import pytest
from pydantic import ValidationError

from prism.schemas import (
    Category,
    FileReview,
    PRReview,
    RawComment,
    ReviewComment,
    Severity,
    Verdict,
)


def _comment(path: str = "app.py", line: int = 3) -> ReviewComment:
    return ReviewComment(
        path=path,
        line=line,
        comment="Possible None dereference.",
        severity=Severity.MAJOR,
        category=Category.BUG,
        confidence=0.8,
        model="qwen2.5-coder:1.5b",
    )


def test_raw_comment_parses_model_json() -> None:
    raw = RawComment.model_validate_json('{"line": 12, "comment": "  Check for None.  "}')
    assert raw.line == 12
    assert raw.comment == "Check for None."


@pytest.mark.parametrize(
    "payload",
    [
        '{"line": 0, "comment": "x"}',
        '{"line": 5, "comment": "   "}',
        '{"line": 5}',
        # The reviewer model must not set what only the verifier assigns.
        '{"line": 5, "comment": "x", "severity": "major"}',
    ],
)
def test_raw_comment_rejects_bad_output(payload: str) -> None:
    with pytest.raises(ValidationError):
        RawComment.model_validate_json(payload)


def test_review_comment_requires_verifier_fields() -> None:
    with pytest.raises(ValidationError):
        ReviewComment.model_validate({"path": "a.py", "line": 1, "comment": "x"})


def test_review_comment_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        ReviewComment.model_validate({**_comment().model_dump(), "confidence": 1.5})


def test_pr_review_collects_comments_and_round_trips() -> None:
    review = PRReview(
        repo="owner/name",
        head_sha="abc123",
        summary="One issue.",
        verdict=Verdict.MEDIUM,
        files=(
            FileReview(path="app.py", comments=(_comment(),), hunks_total=2, hunks_reviewed=1),
            FileReview(path="util.py", hunks_total=1, hunks_reviewed=1),
        ),
    )
    assert [c.path for c in review.comments] == ["app.py"]
    assert PRReview.model_validate_json(review.model_dump_json()) == review
