# ADR 0008: CodeBERT gate classifier, trained on Kaggle, served as ONNX

Date: 2026-11-12. Status: accepted (the model itself is not trained yet).

## Context

The reviewer LLM runs on a CPU. Most hunks in a pull request (renames, imports, formatting) get no
comment from a human, so a cheap classifier in front of the reviewer can save time. PLAN.md
section 2b asks for a CodeBERT classifier on the CodeReviewer quality-estimation data with a
threshold for about 90% recall, and says to report the share of hunks filtered, however small.

## Decision

**Data.** `Diff_Quality_Estimation.zip` from Zenodo 6900648, read in place with `zipfile`. Fields,
sizes and label balance are in `docs/datasets.md`. The dataset's own train, valid and test splits
are used; test is only scored, never trained on, and the threshold is chosen on valid. The large
`oldf` field (the whole old file) is dropped at read time.

**Input.** The `patch` field as it is: one hunk, starting with its `@@` header, with `+`, `-` and
space prefixes. No file context, no comment text (`msg` is the label's source and is never an
input). This matches how the reviewer is served (hunk only, PLAN.md 2b).

**Length and truncation.** `max_length` 512, the limit of CodeBERT (514 positions). A longer hunk
keeps its **end** and loses its start (`truncation_side="left"`, `direction="left"` in
`tokenizers`). The dataset cuts a hunk at the line a comment was left on, so the lines nearest the
end are the ones the human reacted to; the dropped part is mostly the `@@` header and leading
context. This is a judgement, not a measurement: nothing here compares it with keeping the start
or with head plus tail. How many hunks it affects is in `docs/datasets.md`. The tokenizer saved
with the model and the one used by `OnnxGate` apply the same rule, and a test checks that a long
hunk gives the same logits in PyTorch and ONNX.

**Training.** `Trainer` with fp16, per-device batch 16 and 2 accumulation steps (32), learning rate
2e-5, 3 epochs at most, evaluation every 2000 steps, early stopping on validation PR-AUC with
patience 3, seed 13. 16 x 512 in fp16 fits a T4 with `codebert-base`; 32 is a common fine-tuning
batch size. Plain cross-entropy: class weights would shift the scores, and the threshold step
already handles the operating point. These values are untuned defaults.

**Threshold.** Scores are the softmax probability of "needs a comment". On validation, take every
distinct score as a cut-off (predict positive at or above it), keep those with recall at least
0.90, and pick the one with the best precision; among equals, the highest, which filters most.
The same cut-off is then applied to test. Reported on both: precision, recall, F1, PR-AUC and the
share of hunks filtered. The cut-off is an exact validation score, so a little overfit to
validation is possible; the test row shows how much recall it keeps.

**ONNX.** `torch.onnx.export` with the legacy exporter (`dynamo=False`), opset 17, dynamic batch and
sequence axes, eager attention. Optional int8 dynamic quantisation (`--quantize`), with a looser
parity limit (0.5 logits) than fp32 (1e-3), because int8 is lossy. The parity check runs at the
end of every export. `gate_config.json` stores the model name, ONNX file, threshold, max length,
truncation side and label mapping.

**Serving.** `prism/models/gate.py`: a `Gate` protocol (`score`, `needs_review`, `name`),
`PassAllGate` (the default, for Phase 5 until a model exists) and `OnnxGate` (onnxruntime plus the
`tokenizers` library; no torch under `prism/`). `PRISM_GATE` picks `pass-all` or `onnx`;
`onnx` with a missing `PRISM_GATE_MODEL_DIR` raises instead of passing everything.

**Where it runs.** Training is on a free Kaggle T4 from a thin notebook. This machine has no GPU,
so locally the logic is unit-tested and a CPU smoke run uses a tiny RoBERTa model. The `training`
dependency group (CPU torch) is opt-in; `uv sync` and CI do not install it.

## Not done

- No trained model, no metrics yet. `training/model_cards/gate.md` is a template.
- Not compared: UniXcoder, other truncation rules, other lengths, class weights, learning rates,
  more than one seed.
- Not measured: ONNX latency per hunk on CPU, int8 accuracy loss, how the gate does by language.
- Labels say where a human commented, not where a bug is, so a hunk the gate drops may still be
  wrong; the recall target limits that but does not remove it.
- Not decided: whether Phase 5 uses the gate's score for anything besides the filter.
- The gate has not been checked on hunks from PRs that look unlike the training data (the bench
  repos); Phase 7 should.

## Consequences

- A small share filtered at 90% recall is a valid result. If it is small, the gate stays as a
  `pass-all` stub or is removed.
- Changing `max_length`, truncation or the tokenizer means retraining; `gate_config.json` makes
  the serving side follow the model.
- Two exporters exist in torch; the legacy one is deprecated. The parity test will show if a torch
  upgrade breaks it.

## Addendum, 2026-11-12: input normalisation

The first Kaggle run failed in an instructive way. Train loss fell to about 0.0001 within half an
epoch and validation F1 at 0.5 was 0. The cause was in the data, not the model: in the train split
every commented hunk ends with a newline and no uncommented hunk does, and with left truncation
that newline is the last token before the end marker. Validation and test hunks never end with a
newline, so the model called everything there negative.

Changes:

- `normalize_hunk` strips trailing whitespace and git's "no newline at end of file" marker. It is
  applied when rows are read for training and inside `OnnxGate`, so training and serving see the
  same form.
- Train hunks over 100 lines are dropped (`--max-train-lines`). Only commented train hunks are
  that long; everything else in the dataset stops at about 85 lines.

Not solved: length alone still predicts the label better in train than in valid and test (see
`docs/datasets.md`). If validation PR-AUC ends up near what line count alone gives, the gate has
learned little beyond length, and that is reported as the result.
