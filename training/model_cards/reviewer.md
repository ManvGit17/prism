# Model card: PRism reviewer (`prism-reviewer:v2`)

Every number here comes from `metrics.json` of the Kaggle run (kept next to the model in
`models/reviewer2/`) or from the reports named below. The full comparison with the base model
and the first run is in `eval/reports/sft.md`.

## Model

- Base model: `Qwen/Qwen2.5-Coder-3B-Instruct`, loaded as `unsloth/Qwen2.5-Coder-3B-Instruct-bnb-4bit`
- Method: QLoRA (4-bit NF4 base, 16-bit LoRA adapter), 29.9M trainable parameters (0.96%)
- Task: write one review comment for one diff hunk
- Input: the hunk after `normalize_hunk`, in the chat format of `prism/models/reviewer.py`
- Output: `{"comment": "..."}`; `anchor_line` picks the line afterwards (ADR 0009)
- Serving: merged and quantised to GGUF Q4_K_M (1.93 GB), Ollama, greedy decoding, at most 128
  new tokens
- Run: commit `833cbad`, 2026-11-13, Kaggle, one Tesla T4 (Unsloth uses one GPU), training
  214 min, seed 13. Python 3.13.16, torch 2.11.0+cu130, transformers 5.17.0, Unsloth 2026.11.4

## Data

- CodeReviewer comment generation, Zenodo record 6900648 (CC BY 4.0); see `docs/datasets.md`
- Train: 117,739 rows read, 95,284 kept, 40,000 sampled
- Dropped from train: 10,937 vague, 7,572 duplicates, 3,354 linter or bot output, 320 repeated
  comment text, 226 comments over 120 words, 26 hunks too long, 20 non-English comments
- 21 of the 40,000 were over 1,024 tokens and dropped by the trainer: 39,979 trained on
- Valid: 500 sampled from 8,951 kept, filtered like train
- Test: 500 from the dataset's test split, not filtered beyond length and duplicates, the same
  rows as run 1 and the base model

"Vague" is a comment under 8 words that shares no identifier with the hunk and has no
backtick, number, snake_case, camelCase or call in it ("Is this really necessary?"). Linter
output is matched by a regex in `training/sft/format.py` (RuboCop cops, checkpatch "(style)",
line length, whitespace, quote style).

## Training

| Setting | Value |
|---|---|
| LoRA rank, alpha, dropout | 16, 16, 0 |
| Target modules | q, k, v, o, gate, up, down projections |
| Batch size | 8 x 2 accumulation = 16 |
| Learning rate, schedule, warmup | 2e-4, cosine, 3% |
| Weight decay | 0.01 |
| Max length | 1,024 tokens |
| Epochs, steps | 1, 2,499 |
| Loss | on the reply only |
| Final train loss | 2.220 |

Validation loss every 250 steps:

| Step | Loss |
|---|---|
| 250 | 2.244 |
| 500 | 2.241 |
| 750 | 2.228 |
| 1,000 | 2.223 |
| 1,250 | 2.215 |
| 1,500 | 2.212 |
| 1,750 | 2.206 |
| 2,000 | 2.202 |
| 2,250 | 2.202 |
| 2,499 | 2.202 |

It was still falling slowly until step 2,000 and flat after. A second epoch would not change
much.

## Results

On the 500 test hunks (`eval/reports/sft.md`):

| | Base | This model |
|---|---|---|
| BLEU-4 | 5.36 | 5.56 |
| ROUGE-L | 13.20 | 14.21 |
| Valid JSON | 98.8% | 99.8% |
| Mean words | 16.2 | 12.0 |
| Blinded judge (Claude Sonnet, 500 pairs): share of decided pairs won | | 48.6% (95% interval 43.2 to 54.0) |

The judge cannot tell it from the base model on usefulness. It is used as the default because
its replies are shorter and almost always valid JSON, not because its comments are better.

Speed on CPU (Core Ultra 7 155H, Ollama, 4 slots): about 20 hunks per minute one at a time, and
34 to 46 per minute with 4 in flight. Measurements on this laptop move by up to a factor of two
between sessions (`eval/reports/reviewer_throughput.jsonl`).

## Limits

- **It does not know more about bugs than the base model.** Fine-tuning changed how comments are
  phrased, not what they find.
- **It sounds sure when it is wrong.** It suggests renames, removals and missing fields in a
  reviewer's voice, often about code it cannot see. The verifier (Phase 5) is meant to catch
  these.
- **Every comment sounds alike.** 89% start with "I think" and 1% ask a question, against 3% and
  43% for human reviewers. This comes from greedy decoding always taking the most common opener,
  not from the training data (`eval/reports/sft.md`).
- **It sees one hunk.** It was trained without repository context and is served without it.
  Whether context helps it is a Phase 7 question.
- **Scores are for the Kaggle version.** The served Q4_K_M model writes different text for the
  same hunk (0 of 20 identical, all 20 valid JSON); it was not scored separately.
- **The labels are what reviewers wrote on open-source projects in 2022 and earlier.** Comments
  about project habits and code outside the hunk are part of the data.
- **Licence.** The base model is under the Qwen research licence (non-commercial). The tuned
  weights are not published until the licence terms are checked.

## Use

Download `sft_out/gguf/` from the Kaggle run into `models/reviewer2/gguf/`, then:

```
ollama create prism-reviewer:v2 -f models/reviewer2/gguf/Modelfile
```

`PRISM_REVIEWER_MODEL=prism-reviewer:v2` is the default. Without the GGUF, set it to
`qwen2.5-coder:3b` after `ollama pull qwen2.5-coder:3b`.
