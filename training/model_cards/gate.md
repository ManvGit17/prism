# Model card: PRism gate classifier

Status: **not trained yet.** Every blank below (`____`) is filled from `metrics.json` after the
Kaggle run (`training/README.md`). No number goes in from anywhere else.

## Model

- Base model: `microsoft/codebert-base`
- Task: binary, does a diff hunk need a review comment (CodeReviewer quality estimation)
- Input: one hunk as in the dataset, cut to ____ tokens (`max_length`), keeping the ____ end
- Serving: ONNX, ____ (fp32 / int8), opset 17, in-process with onnxruntime
- Run: commit ____, date ____, device ____, runtime ____ min, seed ____

## Data

- CodeReviewer quality estimation, Zenodo record 6900648 (CC BY 4.0); see `docs/datasets.md`
- Rows used: train ____ (of ____), valid ____, test ____
- Positive share: train ____, valid ____, test ____

## Training

| Setting | Value |
|---|---|
| Batch size (per device x accumulation) | ____ x ____ |
| Learning rate, weight decay, warmup | ____, ____, ____ |
| Epochs planned, epochs run | ____, ____ |
| Early stopping | PR-AUC on validation, patience ____ evaluations |
| Best validation PR-AUC | ____ at step ____ |

## Threshold

Chosen on the validation split only: the highest-precision cut-off with recall at least ____
(target). The hunk goes to the reviewer when its score is at or above the cut-off.

Threshold: ____

| Split | Hunks | PR-AUC | Precision | Recall | F1 | Share filtered |
|---|---|---|---|---|---|---|
| Validation | ____ | ____ | ____ | ____ | ____ | ____ |
| Test | ____ | ____ | ____ | ____ | ____ | ____ |

Share filtered is the part of the hunks the gate stops from reaching the reviewer. ____ (state it
as it is, also if it is small; a gate that filters little is not worth its complexity).

## ONNX check

- Largest absolute logit difference, ONNX vs PyTorch, on ____ rows: ____ (limit ____)
- Latency per hunk on CPU: ____ (measure it; not part of the training run)

## Limits

- Filled in after the run: where the gate fails, by language and by hunk length.
- The labels come from where human reviewers commented, not from where bugs are.
- Hunks longer than ____ tokens lose their start.
