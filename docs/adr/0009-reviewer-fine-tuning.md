# ADR 0009: Reviewer fine-tuning writes the comment only; the line is picked afterwards

Date: 2026-11-13. Status: accepted. Trained twice; run 2 (the data filter below) is the default
reviewer, `prism-reviewer:v2`. Results in `eval/reports/sft.md`.

## Context

PLAN.md Phase 4 fine-tunes a small Qwen coder model on CodeReviewer comment generation with
QLoRA on a free T4, compares it with the base model, and serves it from Ollama. ADR 0001 has the
reviewer output `{line, comment}`, on the assumption that a line could be derived from the data.

It cannot. Each row is a whole diff hunk and one comment, and the comment can be about any line in
the hunk (`docs/datasets.md`, comment generation point 1). On the valid split the comment names an
identifier from exactly one changed line in 30% of rows and from none in 51%
(`eval/comment_line_check.py`). Training a `line` field would mean training on labels that are
made up for most rows.

## Decision

**Output.** The model writes `{"comment": "..."}` (`ReviewerReply` in `prism/schemas.py`).
`prism.models.reviewer.anchor_line` then places it: the changed line sharing the most identifiers
with the comment (added lines first on a tie), else the last added line, else the first removed
line. `RawComment` stays `{line, comment}`, so nothing after the reviewer changes. The verifier
(Phase 5) may move the line. The prompt, reply parsing and anchoring live in
`prism/models/reviewer.py`, which training and serving both import, so the model is served exactly
the messages it was trained on.

**Base model.** `Qwen2.5-Coder-3B-Instruct`, the same model the pipeline uses untuned (ADR 0003),
loaded in 4-bit through Unsloth (`unsloth/Qwen2.5-Coder-3B-Instruct-bnb-4bit`).

**QLoRA settings.** The base weights are frozen and stored in 4-bit NF4; only small low-rank
adapter matrices are trained, in 16-bit. For a weight matrix W the update is W + (alpha/r)·BA, with
B and A of rank r.

| Setting | Value | Why |
|---|---|---|
| Rank r | 16 | Common default for a narrow task; 16 x (in + out) parameters per matrix keeps the adapter around 30M parameters |
| alpha | 16 | Scale alpha/r = 1, so the learning rate is the only knob for update size |
| Target modules | q, k, v, o, gate, up, down projections | Adapting the MLP as well as attention usually does better than attention only at the same rank |
| Dropout | 0 | One epoch on 40k rows; Unsloth's fast path needs 0 |
| Learning rate | 2e-4, cosine, 3% warmup | Usual range for LoRA, which needs a larger rate than full fine-tuning |
| Batch | 8 x 2 accumulation = 16 | Examples are short (median 244 tokens), so 8 fits a T4 |
| Max length | 1,024 tokens | 99th percentile is 478; the 0.1% longer are dropped, not cut |
| Data | 40,000 sampled train rows, 1 epoch | PLAN.md says 20-50k and 1 epoch to start |
| Packing | off | Simpler; padding waste is small with length-grouped batches |

Loss is on the reply only (prompt tokens are labelled -100), so the model is not trained to
reproduce diffs.

**Data cleaning.** Exact duplicates (hunk and comment) are dropped, as are comments under 3 or
over 120 words, comments that are mostly non-ASCII, and hunks over 60 lines or 4,000 characters.
Train rows whose hunk appears in valid or test are dropped. Hunks go through the gate's
`normalize_hunk`, the same as at serving time.

**Run 2: specific comments only.** Run 1 used only the cleaning above and lost to the base model
with the judge (tuned won 43.7% of decided pairs). Its most common comment was "This is not
needed". Run 2 also drops, from train and valid but not test (`FilterConfig.specific_only`):
linter and bot output (a regex for RuboCop cops, checkpatch, line length, whitespace and quote
style), comments under 8 words that share no identifier with the hunk and contain no code-like
token, and any comment text seen more than 3 times. Nothing else changed. Run 2 ties with the base
model (48.6%, 95% interval 43.2 to 54.0) and is the default because its replies are shorter and
almost always valid JSON. `qwen2.5-coder:3b` stays a supported setting.

**Evaluation.** 500 rows from the dataset's test split, never trained on. Base and tuned are the
same loaded model with the adapter switched off and on, with greedy decoding and the same prompt.
Metrics: smoothed BLEU-4 and ROUGE-L (our implementation, close to but not the paper's script,
so only comparable across our runs), share of replies that are valid JSON, and a blinded A/B
LLM-judge win rate done locally from the saved generations.

**Serving.** Unsloth merges the adapter and writes a Q4_K_M GGUF. A Modelfile registers it in
Ollama as `prism-reviewer:v1` (run 1) or `prism-reviewer:v2` (run 2) with greedy decoding.

## Consequences

- Phase 7's recall metric (bug flagged at the right line, plus or minus 3) depends on
  `anchor_line` as much as on the model. It should be reported with that in mind, and the
  verifier's line choice compared against it.
- 51% of comments share no identifier with a changed line, so for those the anchor falls back to
  the last added line. Many such comments are about the hunk as a whole ("why this change?").
- The 3B model is under the Qwen research licence (non-commercial). The tuned weights are not
  pushed to Hugging Face until the licence terms are checked.
- Unsloth runs on one GPU, so the second Kaggle T4 sits idle.
- Greedy decoding makes the tuned model open 89% of comments with "I think" and ask almost no
  questions, though 44% of its training comments are questions. Sampling or DPO could change
  that; neither is tried.
- The default model is built from a local GGUF, so a fresh clone without it has to set
  `PRISM_REVIEWER_MODEL=qwen2.5-coder:3b`.
