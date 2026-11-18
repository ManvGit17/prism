# The models

This guide covers the two models PRism ships, how to get them, how to use the base model instead, and how to retrain both on a free Kaggle GPU. It is for anyone setting PRism up or improving its models.

## The two models

| Model | Job | Format | Where |
|---|---|---|---|
| Reviewer, `prism-reviewer-3b` | Writes one comment per diff hunk. Qwen2.5-Coder-3B-Instruct fine-tuned with QLoRA on CodeReviewer comment data. | GGUF Q4_K_M (1.93 GB) served by Ollama | `Maverick0824/prism-reviewer-3b` on Hugging Face |
| Gate, `prism-gate` | Decides which hunks are worth a reviewer's time. CodeBERT fine-tuned on CodeReviewer quality estimation. | ONNX, in-process with onnxruntime | `Maverick0824/prism-gate` on Hugging Face |

The reviewer is under the Qwen Research License, which is non-commercial. Both have model cards in `training/model_cards/` (`reviewer.md` and `gate.md`) with the training settings, data, results and limits. The design is in [ADR 0008](../adr/0008-gate-classifier.md) and [ADR 0009](../adr/0009-reviewer-fine-tuning.md).

You do not need either model to try PRism. The base model reviews about as well in the blinded judge, and the gate defaults to letting every hunk through.

## Download the reviewer and register it in Ollama

Install [Ollama](https://ollama.com), then download the GGUF and the Modelfile:

```bash
uv run hf download Maverick0824/prism-reviewer-3b --local-dir models/reviewer
ollama create prism-reviewer:v2 -f models/reviewer/Modelfile
ollama run prism-reviewer:v2 "hello"
```

`ollama create` reads the Modelfile, which points at the GGUF next to it and bakes in the chat template, the system prompt, temperature 0 and a limit of 128 new tokens. `prism-reviewer:v2` is the default for `PRISM_REVIEWER_MODEL`, so no setting changes.

!!! note
    Open the Modelfile before you create the model. Its `FROM` line must name the GGUF file that was downloaded.

Pull the embedding model too if you use vector context:

```bash
ollama pull nomic-embed-text
```

## Download the gate

```bash
uv run hf download Maverick0824/prism-gate --local-dir models/gate
```

Then turn it on in `.env`:

```bash
PRISM_GATE=onnx
PRISM_GATE_MODEL_DIR=models/gate
```

The folder holds `model.onnx`, `gate_config.json` (with the threshold, chosen for 90% recall on validation) and the tokenizer files. With `PRISM_GATE=onnx` and a missing folder PRism raises an error; it never falls back silently. `models/` is git-ignored.

## Use the base model instead

Without the fine-tuned reviewer, pull the base model and point PRism at it:

```bash
ollama pull qwen2.5-coder:3b
```

```bash
# .env
PRISM_REVIEWER_MODEL=qwen2.5-coder:3b
```

You can also switch for one run with `prism review --model qwen2.5-coder:3b`. The base model is a first-pass reviewer only; Claude's verification decides what is posted either way. The fine-tuned model writes shorter comments and almost always valid JSON; in the blinded judge it ties with the base model on usefulness (see `eval/reports/sft.md`).

## Switch models

Everything is a setting, so switching needs no code change:

| Variable | Default | What it picks |
|---|---|---|
| `PRISM_REVIEWER_MODEL` | `prism-reviewer:v2` | The Ollama model that writes first-pass comments. Any model Ollama serves works. |
| `PRISM_OLLAMA_URL` | `http://127.0.0.1:11434` | Which Ollama to use. |
| `PRISM_GATE` | `pass-all` | `onnx` to use the gate model. |
| `PRISM_GATE_MODEL_DIR` | `models/gate` | Where the gate files are. |
| `PRISM_EMBEDDING_MODEL` | `nomic-embed-text` | The model for vector context. |
| `PRISM_FRONTIER_MODEL` | `claude-opus-5-5` | Claude, for the `api` verifier. |

!!! tip
    After changing the reviewer, run `prism review` on a diff you know. Reviewer replies are cached per model in the work directory, so a different model gets fresh replies.

## Retrain both models

Both models were trained on free Kaggle T4 GPUs, from thin notebooks that clone the repo and run the code in `training/`. The logic is importable modules; the notebooks only call them. `training/README.md` has the full detail.

| Model | Code | Notebook |
|---|---|---|
| Gate | `training/gate/` | `training/gate/gate_kaggle.ipynb` |
| Reviewer | `training/sft/` | `training/sft/sft_kaggle.ipynb` |

**Test the pipeline locally first.** `make train-test` installs the `training` group (CPU torch, about 700 MB) and runs a short train, threshold, export and parity check on tiny models. It needs no GPU.

**Run on Kaggle.**

1. Create a GitHub token with read access to your copy of the repo (a fine-grained token with Contents: read).
2. On kaggle.com choose Create, New Notebook, then File, Import Notebook, and upload one of the notebooks above.
3. In Session options, set Accelerator to **GPU T4 x1** (not P100) and Internet to **On**. Internet needs a phone-verified account.
4. Under Add-ons, Secrets, add `GITHUB_TOKEN`. Optionally add `WANDB_API_KEY` and `HF_TOKEN`. Attach each secret to the notebook.
5. In the first code cell, set `REPO` to your repo (it ships as `OWNER/prism`) and `REF` to the branch, tag or commit. `MAX_TRAIN_ROWS` (gate) or `TRAIN_ROWS` (reviewer) sets how much data to use.
6. Run the cells down to the dry run first. If it fails, fix that before spending a full session.
7. Run the real run, or use Save Version, then Save & Run All, which keeps the output even if you close the tab.

Kaggle sessions stop after 12 hours. The reviewer run used about 214 minutes on one T4 for 40,000 training rows. The gate run, on 2 x T4 with 92,664 rows, took about 280 minutes for 3 epochs; the model card says 2 epochs are enough.

**Take the result.**

- The gate: download `gate_out/` (`model.onnx`, `gate_config.json`, tokenizer files, `metrics.json`) into `models/gate/`, set `PRISM_GATE=onnx`, and fill in `training/model_cards/gate.md` from `metrics.json`. `uv run python eval/gate_breakdown.py data/codereviewer/Diff_Quality_Estimation.zip` scores a sample by language and hunk length for the card's limits.
- The reviewer: download `sft_out/gguf/` (the `.gguf` and the `Modelfile`) into a folder, run `ollama create` as above, and fill in `training/model_cards/reviewer.md`. To judge it against the base model, see `eval/sft_judge.py` and the steps in `eval/reports/sft.md`.

**Training data.** Both models train on the CodeReviewer dataset (Zenodo record 6900648, CC BY 4.0), described in `docs/datasets.md`. It is not in the repo; place `Comment_Generation.zip` and `Diff_Quality_Estimation.zip` in `data/codereviewer/`.

**Train on your own feedback.** `prism app feedback-export` produces preference pairs from reactions on real pull requests. See [the feedback loop](langfuse.md#the-feedback-loop).
