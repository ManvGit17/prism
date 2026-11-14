# Reviewer fine-tuning: base vs tuned

Phase 4. QLoRA on Qwen2.5-Coder-3B-Instruct with CodeReviewer comment generation, two runs on
Kaggle, compared with the untuned model on the same 500 held-out test hunks.

**Short version.** The first run lost to the base model with a blinded judge. Its training data
was full of linter output and two-word comments, and it learned to write "This is not needed".
The second run trained on the same amount of data with those comments filtered out. It ties
with the base model with the judge (48.6% of decided pairs, 95% interval 43.2 to 54.0), beats it
on BLEU-4 and ROUGE-L, writes shorter replies and fails JSON less often. It does not write
better comments than the base model, and it still has habits human reviewers do not. Run 2 is
the default reviewer, `prism-reviewer:v2`, mainly because it is shorter and sounds like a review;
`qwen2.5-coder:3b` stays one setting away.

Every number here comes from `metrics.json` of each Kaggle run, `eval/reports/sft_judge_run1.json`,
`sft_judge_run2.json`, `sft_style.json` or `reviewer_throughput.jsonl`.

## Setup

| | Run 1 | Run 2 |
|---|---|---|
| Commit | `c8ac930` | `833cbad` |
| Training rows | 40,000 sampled from 109,028 kept | 40,000 sampled from 95,284 kept |
| Filter | clean, dedupe, length, language | the same, plus no linter output, no short comments that name nothing, the same text at most 3 times |
| Steps | 2,499 (1 epoch, batch 16) | 2,499 |
| Training time on one T4 | 3.9 h | 3.6 h |
| Final train / eval loss | 2.159 / 2.186 | 2.220 / 2.202 |

Everything else is the same in both runs: LoRA r=16, alpha=16 on all attention and MLP
projections, learning rate 2e-4 with cosine decay, loss on the reply only, seed 13 (ADR 0009).
The two eval losses are on different validation rows (run 2 filters valid the same way as
train), so they cannot be compared with each other.

The test split is not filtered: both runs and the base model are scored on the same 500 rows of
the dataset's test split, with greedy decoding and up to 128 new tokens. The base model's 500
outputs are byte-identical between the two runs.

## Results

| | Base | Run 1 | Run 2 |
|---|---|---|---|
| BLEU-4 | 5.36 | 5.04 | **5.56** |
| ROUGE-L | 13.20 | 13.81 | **14.21** |
| Valid JSON | 98.8% | 99.8% | 99.8% |
| Mean words | 16.2 | 10.6 | 12.0 |
| Judge: tuned share of decided pairs (Sonnet, 500) | | 43.7% (38.0 to 49.5) | **48.6% (43.2 to 54.0)** |

BLEU-4 and ROUGE-L compare the comment with the one comment a human left on that hunk. Human
comments on the same code vary a lot, so a few points either way says little about usefulness.
That is why the judge is there.

### Blinded judge

`eval/sft_judge.py`. For each test hunk the judge sees the diff and two comments, A and B. A
seeded coin flip per hunk decides which side the tuned model is on, and is not written anywhere
the judge can read. The human comment is not shown: the judge rates which comment a developer
would rather get (correct first, then specific, actionable, concise), not which one matches a
human's wording. Claude Sonnet judged all 500 pairs in 10 batches of 50; Claude Opus judged one
batch (07) as a check.

| | Tuned wins | Base wins | Ties | Tuned share of decided |
|---|---|---|---|---|
| Run 1, Sonnet | 124 | 160 | 216 | 43.7% (38.0 to 49.5) |
| Run 2, Sonnet | 156 | 165 | 179 | 48.6% (43.2 to 54.0) |
| Run 1, Opus, batch 07 | 15 | 13 | 22 | |
| Run 2, Opus, batch 07 | 18 | 12 | 20 | |

- The run 1 interval sits below 50%: base was better. The run 2 interval contains 50%: a tie.
- Opus and Sonnet agreed on 37 of 50 pairs in run 1 and 28 of 50 in run 2. On many pairs both
  comments are weak in different ways and the call is close, so a single judge's verdict on one
  pair means little. The totals over 500 pairs are what to read.
- Opus favoured run 2 on its batch (18 to 12); 50 pairs is too few to say more.

### What the comments look like

`eval/sft_style.py`, on the same 500 test hunks. "Vague" means the run 2 training filter would
drop it: under 8 words and naming nothing in the hunk.

| | Base | Run 1 | Run 2 | Human |
|---|---|---|---|---|
| Mean words | 16.2 | 10.6 | 12.0 | 17.8 |
| Names code (backtick) | 75.8% | 67.0% | 77.6% | 37.4% |
| Asks a question | 1.0% | 2.8% | 1.0% | 43.0% |
| Starts with "I think" | 0.0% | 77.0% | 89.0% | 3.0% |
| Different comments | 500 | 448 | 495 | 499 |
| Linter-style | 0.0% | 1.6% | 0.0% | 1.2% |
| Vague | 0.2% | 13.0% | 0.4% | 9.2% |

- **Run 1 learned the dataset's most common shapes.** "This is not needed" (22 times) and
  "This is not needed anymore" (15) alone were 7% of its comments.
- **The filter fixed that.** Run 2 repeats nothing more than 3 times and has no linter-style or
  vague comments.
- **Run 2 opens almost every comment with "I think", and neither run asks questions.** Neither
  comes from the training data. In run 2's 40,000 training comments, 3.1% start with "I think"
  and 44% contain a question (43% in run 1), so the filter did not remove the questions. The
  cause is greedy decoding: the model always takes its single most likely next word. In the
  training data "this" and "I" are the two most common first words (9.6% each), while questions
  are spread over many openers (why 4.3%, can 3.1%, is 3.0%, should 2.5%, do 1.7%). So every
  comment starts with the top choice. Run 1 started with "This" and ended in "This is not
  needed". Run 2's filter removed many of those short "This is..." comments, so "I" won instead.
- **The base model describes; the tuned model suggests.** Base comments read like a summary of
  the diff with advice attached ("The package imports have been updated to reflect...").
  Run 2's read like a reviewer ("I think we should rename this to `getAppender()`").

### Examples

Judge reasons are Sonnet's, shortened. Human is the comment in the dataset.

Run 1 to run 2 on the same hunk (pair 487, Java):

- Run 1: "This is not needed."
- Run 2: "I think we should rename this to `getAppender()`."
- Base: "Add a method to return the `FileAppender` instance."
- Human: "Do we need to expose it? It looks like it is only used in tests..."
- Judge (run 2): tuned, "a plausible naming nit; the other restates the diff".

Run 2 wins (pair 126, Go):

- Base: "The import statement for `iotextypes` is not used anywhere in the file. It should be
  removed..."
- Run 2: "I think we should keep the `iotextypes` package."
- Judge: tuned, the base comment wrongly says the import is unused.

Base wins (pair 420, Go):

- Base: "The `Git` and `Local` fields are mutually exclusive. Only one should be set."
- Run 2: "I think we need to add a `Remote` field here too."
- Judge: base, the tuned model suggests a field nothing calls for.

The last one is the common failure: run 2 sounds sure of itself and is often wrong about code it
cannot see. CodeReviewer comments are often about things outside the hunk (tests, other files,
project habits), and the model learned to make that kind of claim without the context behind it.

## Throughput on CPU

`eval/reviewer_throughput.py`: the same 24 test hunks at 1, 2 and 4 requests in flight, Ollama
started with `OLLAMA_NUM_PARALLEL=4`, Q4_K_M, Core Ultra 7 155H (22 logical CPUs), Windows 11.

| Model, time (UTC) | In flight | Hunks/min | Generated tok/s | p50 latency | p95 latency |
|---|---|---|---|---|---|
| v1, 2026-11-13 14:42 | 1 | 19.6 | 7.7 | 3.0 s | 4.2 s |
| | 2 | 35.1 | 14.4 | 3.1 s | 5.3 s |
| | 4 | 57.1 | 21.9 | 3.7 s | 6.3 s |
| v2, 2026-11-14 02:14 | 1 | 21.4 | 11.2 | 2.7 s | 4.3 s |
| | 2 | 31.8 | 14.5 | 3.3 s | 6.7 s |
| | 4 | 34.3 | 18.4 | 6.3 s | 13.7 s |
| v1, 2026-11-14 02:16 | 4 | 24.8 | 10.0 | 8.6 s | 15.8 s |
| v2, 2026-11-14 02:17 | 4 | 45.6 | 22.5 | 4.6 s | 8.1 s |

v1 and v2 are the same architecture and size, so they should run at the same speed. The same v1
setting measured 57 hunks/min one evening and 25 the next morning: on this laptop the
measurement moves by a factor of two between sessions (power and heat, other programs). What
holds in every run: one request at a time gives about 20 hunks/min at about 3 s each, and 4 in
flight is never slower than 2, at the cost of longer waits per hunk. So Phase 5 should start with
a concurrency limit of 4 on a server with 4 slots, and measure again on the machine it runs on.

## Kaggle vs Ollama

On Kaggle the model is evaluated in 4-bit NF4 with the adapter attached. Ollama serves the merged
model quantised to Q4_K_M, a different 4-bit format. With greedy decoding the two give different
text: 1 of 20 test hunks identical for v1, 0 of 20 for v2. All 20 were valid JSON in both, and
the style is the same. The scores above are for the Kaggle version; the served model was not
scored separately.

## Decision

`prism-reviewer:v2` is the default (`PRISM_REVIEWER_MODEL`). Reasons:

- The judge cannot tell it from the base model on usefulness.
- Its replies are about a quarter shorter, so each hunk takes less time to generate.
- It fails JSON once in 500 against 6 for the base model.
- Its comments read like review comments, which matters once the verifier and the output
  format are built on top.

`qwen2.5-coder:3b` works with a plain `ollama pull` and scores the same. Anyone without the GGUF
should use it. The tuned 3B weights are not published until the Qwen research licence is
checked (ADR 0003).

## What did not work, and what might

- Fine-tuning on CodeReviewer did not teach the model to find more real problems. It changed
  how comments are phrased. Usefulness on real pull requests is Phase 7's question, with
  repository context and the verifier in the loop.
- The data filter is what moved the result, from a loss to a tie.
- The "I think" habit and the lack of questions come from greedy decoding picking the most likely
  opener every time. Sampling with a moderate temperature would spread the openers out, but
  then the same hunk gets a different comment each run, and it was not tested here. DPO
  (PLAN.md, stretch) is the other way to change it. Not tried.
- 1.5B was not trained; the quota went to the second 3B run.

## Reproduce

```
# On Kaggle: training/sft/sft_kaggle.ipynb with REF at the commit above, then download sft_out
ollama create prism-reviewer:v2 -f models/reviewer2/gguf/Modelfile
uv run python eval/sft_judge.py prepare --generations models/reviewer2/eval/generations.jsonl --work models/reviewer2/judge
# judges write models/reviewer2/judge/<judge>/verdicts_NN.jsonl
uv run python eval/sft_judge.py score --generations models/reviewer2/eval/generations.jsonl --work models/reviewer2/judge --judges sonnet opus --tag run2
uv run python eval/sft_style.py --run run1=models/reviewer/eval/generations.jsonl --run run2=models/reviewer2/eval/generations.jsonl
uv run python eval/reviewer_throughput.py --model prism-reviewer:v2 --hunks models/reviewer2/eval/generations.jsonl
```
