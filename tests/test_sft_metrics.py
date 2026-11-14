import pytest

from training.sft.metrics import corpus_scores, rouge_l, sentence_bleu4, tokenize


def test_tokenize() -> None:
    assert tokenize("Use `get()` here.") == ["use", "`", "get", "(", ")", "`", "here", "."]


def test_bleu_bounds() -> None:
    ref = "should this be cached instead of recomputed each time"
    assert sentence_bleu4(ref, ref) == pytest.approx(1.0)
    assert sentence_bleu4(ref, "totally unrelated words") == 0.0
    assert sentence_bleu4(ref, "") == 0.0
    partial = sentence_bleu4(ref, "should this be cached")
    assert 0.0 < partial < 1.0


def test_bleu_brevity_penalty() -> None:
    ref = "a b c d e f g h"
    assert sentence_bleu4(ref, "a b c d") < sentence_bleu4(ref, "a b c d e f g h")


def test_rouge_l() -> None:
    assert rouge_l("a b c d", "a b c d") == pytest.approx(1.0)
    assert rouge_l("a b c d", "x y") == 0.0
    # LCS "a c d": precision 3/3, recall 3/4.
    assert rouge_l("a b c d", "a c d") == pytest.approx(2 * 1 * 0.75 / 1.75)


def test_corpus_scores() -> None:
    scores = corpus_scores(["a b c d", "x y z w"], ["a b c d", "q"])
    assert scores["bleu4"] == pytest.approx(50.0)
    assert scores["rouge_l"] == pytest.approx(50.0)
    assert corpus_scores([], []) == {"bleu4": 0.0, "rouge_l": 0.0}
    with pytest.raises(ValueError):
        corpus_scores(["a"], [])
