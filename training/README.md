# Training

Phase 3 (the gate classifier) lives in `training/gate/`, Phase 4 (the reviewer model) in
`training/sft/`.
Logic is in importable modules; the notebook only calls them.

| Module | Job |
|---|---|
| `gate/data.py` | Stream rows from the zip (or extracted jsonl), keep hunk and label, seeded sampling |
| `gate/train.py` | HF `Trainer` fine-tune, early stopping on validation PR-AUC |
| `gate/threshold.py` | Threshold for a target recall, metrics, share filtered |
| `gate/export_onnx.py` | ONNX export, optional int8, parity check, `gate_config.json` |
| `gate/run.py` | The whole chain in one command |
| `gate/gate_kaggle.ipynb` | Thin Kaggle notebook around `run.py` |

## Local smoke test (CPU, tiny model)

```
make train-test
```

Installs the `training` dependency group (CPU torch, about 700 MB) and runs the tests marked
`training`: a short train, threshold, export and parity run on `hf-internal-testing/tiny-random-RobertaModel`
(downloaded once, under 1 MB) with a few fixture rows. `make unit`, `make check` and CI do not
install the group and skip these tests.

To run the pipeline by hand on the fixtures:

```
uv run --group training python -m training.gate.run --data tests/fixtures/gate --out data/smoke \
    --model-name hf-internal-testing/tiny-random-RobertaModel --max-length 64 --epochs 1 \
    --eval-steps 3 --batch-size 4 --no-fp16
```

`python -m training.gate.run --help` lists every hyperparameter. Weights & Biases logging turns on
only when `WANDB_API_KEY` is set; otherwise `metrics.json` holds the log history.

## Real run on Kaggle (free T4)

1. Make a GitHub token with read access to the private repo (fine-grained, "Contents: read" on
   `ManvGit17/prism`).
2. On kaggle.com: Create, New Notebook. File, Import Notebook, upload
   `training/gate/gate_kaggle.ipynb`.
3. Right panel, Session options: Accelerator, **GPU T4 x1** (not P100). Internet: **On** (needs a
   phone-verified account).
4. Add-ons, Secrets, add `GITHUB_TOKEN`. Optionally add `WANDB_API_KEY` and `HF_TOKEN`. Make sure
   each secret is attached (ticked) for this notebook.
5. In the first code cell, set `REF` to the branch or commit to train from.
6. Run the cells one by one down to the **dry run** cell. It needs a few minutes. If it fails,
   fix it before spending a full session.
7. Run the **real run** cell, or use Save Version, then Save & Run All, which runs the whole
   notebook in the background and keeps the output even if you close the tab.
8. When it finishes, the output tab holds `gate_out/`: `model.onnx` (or `model.int8.onnx`),
   `gate_config.json`, the tokenizer files and `metrics.json`.

Kaggle sessions stop after 12 hours, so for a large train split set `MAX_TRAIN_ROWS` in the first
cell. With 100,000 rows on 2 x T4, one epoch took about 78 minutes plus about 8 minutes per
validation pass; 2 epochs are enough (see the model card).

## Using the result

1. Download `gate_out` from the notebook's output tab and put its files in a folder, for example
   `models/gate/` in the repo root (`models/` is git-ignored). On Kaggle, use right-click, Save
   link as, for the JSON files; a plain click opens them in the browser.
2. In `.env`:
   ```
   PRISM_GATE=onnx
   PRISM_GATE_MODEL_DIR=models/gate
   ```
   `PRISM_GATE=pass-all` (the default) lets every hunk through. With `onnx` and a missing folder
   the code raises an error; it never falls back silently.
3. Fill `training/model_cards/gate.md` from `metrics.json`.
4. `uv run python eval/gate_breakdown.py data/codereviewer/Diff_Quality_Estimation.zip` scores a
   sample of the test split by language and hunk length, for the model card's limits.

## Reviewer model (Phase 4)

| Module | Job |
|---|---|
| `sft/format.py` | Clean, dedupe and sample comment-generation rows into chat examples |
| `sft/train.py` | QLoRA with Unsloth (or plain peft for the CPU test), loss on the reply only |
| `sft/evaluate.py` | Greedy generation for base and tuned, BLEU-4, ROUGE-L, valid-JSON share |
| `sft/metrics.py` | BLEU-4 and ROUGE-L in plain Python |
| `sft/export.py` | GGUF Q4_K_M through Unsloth, and the Ollama Modelfile |
| `sft/run.py` | The whole chain in one command |
| `sft/sft_kaggle.ipynb` | Thin Kaggle notebook around `run.py` |

The prompt, reply parsing and line placement are in `prism/models/reviewer.py` (ADR 0009), shared
with serving. `make train-test` also runs the reviewer pipeline on a 16 MB random Qwen2.5.

On Kaggle: same setup as the gate (secrets, GPU T4, Internet on), notebook
`training/sft/sft_kaggle.ipynb`, `REF` set to the branch. Run the dry run first and note its
`stage ... done in` lines. Unsloth uses one GPU, so the run is started with
`CUDA_VISIBLE_DEVICES=0`. Output goes straight to `/kaggle/working/sft_out`: `adapter/` (LoRA
weights, saved right after training), `eval/generations.jsonl`, `gguf/` and `metrics.json`.

To use it locally, download `sft_out/gguf/` (the `.gguf` and `Modelfile`) into a folder, then:

```
ollama create prism-reviewer:v1 -f Modelfile
ollama run prism-reviewer:v1
```
