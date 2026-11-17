# Model card: PRism gate classifier

Every number here comes from `metrics.json` of the Kaggle run (copied next to the model in
`models/gate/`) or from `eval/reports/gate_breakdown.json`.

## Model

- Base model: `microsoft/codebert-base`
- Task: binary, does a diff hunk need a review comment (CodeReviewer quality estimation)
- Input: one hunk after `normalize_hunk`, cut to 512 tokens (`max_length`), keeping the end
  (`truncation_side` left)
- Serving: ONNX, fp32, opset 17, in-process with onnxruntime (498.8 MB)
- Run: commit `144863d`, 2026-11-12, Kaggle 2 x Tesla T4, training 280 min, seed 13. Python
  3.13.16, torch 2.11.0+cu130, transformers 5.20.2

## Data

- CodeReviewer quality estimation, Zenodo record 6900648 (CC BY 4.0); see `docs/datasets.md`
- Rows used: train 92,664 (a seeded sample of 100,000 of the 265,836, minus 7,336 hunks over
  100 lines), valid 31,252, test 31,252
- Positive share: train 0.461, valid 0.5, test 0.5

## Training

| Setting | Value |
|---|---|
| Batch size | 16 per GPU x 2 GPUs x 2 accumulation = 64 |
| Learning rate, weight decay, warmup | 2e-5, 0.01, 6% |
| Epochs planned, epochs run | 3, 3 (4,344 steps) |
| Early stopping | PR-AUC on validation every 750 steps, patience 3 evaluations (not triggered) |
| Best validation PR-AUC | 0.811 at step 3,000 (epoch 2.07) |

Validation during training:

| Step | Epoch | PR-AUC | F1 at 0.5 | Loss |
|---|---|---|---|---|
| 750 | 0.52 | 0.786 | 0.689 | 1.163 |
| 1,500 | 1.04 | 0.795 | 0.688 | 1.155 |
| 2,250 | 1.55 | 0.809 | 0.700 | 1.107 |
| 3,000 | 2.07 | **0.811** | 0.715 | 1.115 |
| 3,750 | 2.59 | 0.808 | 0.702 | 1.142 |
| 4,344 | 3.00 | 0.810 | 0.704 | 1.138 |

Nothing was gained after epoch 2; the next run can stop there.

## Threshold

Chosen on the validation split only: the highest-precision cut-off with recall at least 0.90
(target). The hunk goes to the reviewer when its score is at or above the cut-off.

Threshold: 0.1953

| Split | Hunks | PR-AUC | Precision | Recall | F1 | Share filtered |
|---|---|---|---|---|---|---|
| Validation | 31,252 | 0.811 | 0.610 | 0.900 | 0.727 | 0.263 |
| Test | 31,252 | 0.806 | 0.613 | 0.901 | 0.729 | 0.265 |

Share filtered is the part of the hunks the gate stops from reaching the reviewer. On test it
skips about a quarter of all hunks while keeping 90% of the ones that got a comment. That is on
50/50 data. On real pull requests, where most hunks get no comment, the share filtered should be
higher and precision lower; BugBench measures it on real PRs.

For scale: counting lines alone separates the labels with an AUC of about 0.64 on test
(`docs/datasets.md`), so the model learned more than "longer hunks get comments".

## ONNX check

- Largest absolute logit difference, ONNX vs PyTorch, on 32 validation rows: 2.3e-06 (limit 1e-3)
- Speed on CPU: 1.4 hunks per second (Core Ultra 7 155H, onnxruntime defaults, batches of 16,
  3,000 random test hunks; `eval/gate_breakdown.py`). Batches are not grouped by length, so most
  of them are padded to the longest hunk. Slow enough to matter on a large PR.

## Limits

From `eval/gate_breakdown.py`: 3,000 random test hunks (seed 0), same threshold. Overall on
this sample: recall 0.908, share filtered 0.263, which matches the full test split.

| Hunk length (lines) | Hunks | Recall | Share filtered |
|---|---|---|---|
| 1 to 10 | 1,212 | 0.812 | 0.433 |
| 11 to 25 | 1,276 | 0.925 | 0.195 |
| 26 to 50 | 442 | 0.997 | 0.034 |
| 51 and over | 70 | 1.000 | 0.000 |

| Language | Hunks | Recall | Share filtered |
|---|---|---|---|
| c | 166 | 0.812 | 0.349 |
| cpp | 282 | 0.879 | 0.397 |
| cs | 190 | 0.990 | 0.116 |
| go | 914 | 0.857 | 0.327 |
| java | 450 | 0.919 | 0.289 |
| js | 276 | 0.937 | 0.152 |
| php | 118 | 0.892 | 0.169 |
| py | 397 | 0.922 | 0.249 |
| rb | 207 | 0.994 | 0.034 |

- **Almost all of the filtering is on short hunks, and that is where it misses.** Hunks of 26
  lines or more nearly always pass. Hunks of 10 lines or fewer are where 43% get held back and
  recall falls to 0.81. Length is still doing a lot of the work.
- **C, Go and C++ lose the most** (recall 0.81 to 0.88). Ruby and C# pass nearly everything, so the
  gate saves little there. Python and Java, the two languages PRism indexes, are close to the
  overall 0.90.
- Per-language groups are small (118 to 914 hunks), so a few points either way is noise.
- The labels come from where human reviewers commented, not from where bugs are.
- Hunks longer than 512 tokens lose their start. About 10% of test hunks are that long.
- Train hunks are longer than valid and test hunks even after the 100-line cap, so length is a
  stronger signal in training than it will be in use (`docs/datasets.md`, point 8).
