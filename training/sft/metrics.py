"""Text metrics for generated review comments: smoothed BLEU-4 and ROUGE-L.

Pure Python so they can be unit-tested without torch. BLEU-4 is sentence-level with add-one
smoothing on the 2- to 4-gram precisions (Lin and Och, 2004), averaged over examples, which is
close to the "smoothed BLEU" the CodeReviewer paper reports but not their exact script, so the
numbers are comparable between our runs, not with the paper's table.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

_TOKEN = re.compile(r"\w+|[^\w\s]")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _ngrams(tokens: Sequence[str], n: int) -> Counter[tuple[str, ...]]:
    return Counter(tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1))


def sentence_bleu4(reference: str, hypothesis: str) -> float:
    """Smoothed sentence BLEU-4 in [0, 1]."""
    ref, hyp = tokenize(reference), tokenize(hypothesis)
    if not ref or not hyp:
        return 0.0
    log_precision = 0.0
    for n in range(1, 5):
        hyp_ngrams = _ngrams(hyp, n)
        overlap = sum((hyp_ngrams & _ngrams(ref, n)).values())
        total = max(len(hyp) - n + 1, 0)
        if n == 1:
            if overlap == 0:
                return 0.0
            p = overlap / total
        else:
            p = (overlap + 1) / (total + 1)
        log_precision += math.log(p) / 4
    brevity = 1.0 if len(hyp) > len(ref) else math.exp(1 - len(ref) / len(hyp))
    return brevity * math.exp(log_precision)


def _lcs(a: Sequence[str], b: Sequence[str]) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, start=1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def rouge_l(reference: str, hypothesis: str) -> float:
    """ROUGE-L F1 on tokens, in [0, 1]."""
    ref, hyp = tokenize(reference), tokenize(hypothesis)
    lcs = _lcs(ref, hyp)
    if lcs == 0:
        return 0.0
    precision, recall = lcs / len(hyp), lcs / len(ref)
    return 2 * precision * recall / (precision + recall)


def corpus_scores(references: Sequence[str], hypotheses: Sequence[str]) -> dict[str, float]:
    """Mean BLEU-4 and ROUGE-L, both scaled to 0-100."""
    if len(references) != len(hypotheses):
        raise ValueError("references and hypotheses differ in length")
    if not references:
        return {"bleu4": 0.0, "rouge_l": 0.0}
    n = len(references)
    return {
        "bleu4": 100 * sum(map(sentence_bleu4, references, hypotheses)) / n,
        "rouge_l": 100 * sum(map(rouge_l, references, hypotheses)) / n,
    }
