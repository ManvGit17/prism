import numpy as np
import pytest

from training.gate.threshold import choose_and_report, evaluate, pick_threshold, pr_auc


def test_reaches_target_recall_with_best_precision() -> None:
    labels = [1, 1, 0, 1, 0, 0, 1, 0, 0, 0]
    scores = [0.95, 0.9, 0.85, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
    t = pick_threshold(labels, scores, 0.75)
    assert t == 0.7  # 3 of 4 positives kept, precision 3/4
    report = evaluate(labels, scores, t)
    assert report.recall == pytest.approx(0.75)
    assert report.precision == pytest.approx(0.75)
    assert report.filtered_share == pytest.approx(0.6)


def test_full_recall_target() -> None:
    labels = [1, 0, 1, 0]
    scores = [0.9, 0.8, 0.3, 0.1]
    assert pick_threshold(labels, scores, 1.0) == 0.3


def test_ties_are_never_split() -> None:
    labels = [1, 0, 1, 0, 0]
    scores = [0.5, 0.5, 0.5, 0.2, 0.1]
    t = pick_threshold(labels, scores, 0.5)
    assert t == 0.5
    assert evaluate(labels, scores, t).recall == 1.0  # all three tied rows are kept


def test_all_positive_labels() -> None:
    labels = [1, 1, 1, 1]
    scores = [0.9, 0.7, 0.5, 0.1]
    t = pick_threshold(labels, scores, 0.9)
    r = evaluate(labels, scores, t)
    assert r.recall >= 0.9
    assert r.precision == 1.0
    assert r.pr_auc == pytest.approx(1.0)


def test_all_equal_scores_filter_nothing() -> None:
    labels = [1, 0, 0, 1]
    scores = [0.5, 0.5, 0.5, 0.5]
    r = evaluate(labels, scores, pick_threshold(labels, scores, 0.9))
    assert r.filtered_share == 0.0
    assert r.recall == 1.0


def test_no_positives_is_an_error() -> None:
    with pytest.raises(ValueError, match="no positive"):
        pick_threshold([0, 0, 0], [0.1, 0.2, 0.3])


def test_bad_inputs() -> None:
    with pytest.raises(ValueError):
        pick_threshold([1, 0], [0.5])
    with pytest.raises(ValueError):
        pick_threshold([1, 0], [0.5, float("nan")])
    with pytest.raises(ValueError):
        pick_threshold([1, 2], [0.5, 0.1])
    with pytest.raises(ValueError):
        pick_threshold([1, 0], [0.5, 0.1], target_recall=0.0)
    with pytest.raises(ValueError):
        pick_threshold([], [])


def test_pr_auc_perfect_and_known_value() -> None:
    assert pr_auc([1, 1, 0, 0], [0.9, 0.8, 0.2, 0.1]) == pytest.approx(1.0)
    # Positives ranked 1st and 3rd: AP = 0.5 * 1 + 0.5 * (2/3)
    assert pr_auc([1, 0, 1, 0], [0.9, 0.8, 0.7, 0.1]) == pytest.approx(5 / 6)


def test_target_recall_on_random_scores() -> None:
    rng = np.random.default_rng(0)
    labels = rng.integers(0, 2, 2000)
    scores = np.clip(labels * 0.3 + rng.normal(0.4, 0.2, 2000), 0, 1)
    for target in (0.5, 0.9, 0.99):
        t = pick_threshold(labels, scores, target)
        assert evaluate(labels, scores, t).recall >= target - 1e-9


def test_choose_and_report_uses_validation_threshold_for_test() -> None:
    out = choose_and_report([1, 0, 1, 0], [0.9, 0.6, 0.4, 0.1], [1, 0], [0.3, 0.2], 0.9)
    assert out["threshold"] == 0.4
    assert out["test"]["recall"] == 0.0  # type: ignore[index]
    assert out["validation"]["recall"] == 1.0  # type: ignore[index]
