# Datasets

## CodeReviewer (Li et al., 2022)

Source: Zenodo record 6900648, "Automating Code Review Activities by Large-Scale Pre-Training".
Licence: CC BY 4.0.

| Archive | Download size | Status |
|---|---|---|
| `Comment_Generation.zip` | 847 MB | Downloaded and inspected 2026-11-11 |
| `Diff_Quality_Estimation.zip` | 2.8 GB | Used for the gate classifier |
| `Code_Refinement.zip` | 1.2 GB | Not needed |

Files live in `data/codereviewer/`, which is git-ignored.

### Comment generation

Checked by reading every row of each file inside the zip.

| File | Rows | Unpacked size |
|---|---|---|
| `msg-train.jsonl` | 117,739 | 3.6 GB |
| `msg-valid.jsonl` | 10,319 | 274 MB |
| `msg-test.jsonl` | 10,169 | 275 MB |

Fields:

| Field | Type | In | What it is |
|---|---|---|---|
| `patch` | str | all | One diff hunk, starting with the `@@` header |
| `msg` | str | all | The reviewer's comment |
| `oldf` | str | all | The whole file before the change |
| `id` | int | all | Row id |
| `y` | int | all | Label, 1 in the rows sampled |
| `idx` | int | valid, test | Index |
| `proj` | str | valid, test | Project, e.g. `inveniosoftware-invenio` |
| `lang` | str | valid, test | Language, e.g. `py`, `java` |

### What this means for the plan

1. **There is no line number, and it cannot be read off the hunk.** A row is a whole diff hunk
   (ending in its trailing context lines) and a comment that can be about any line in it. The
   guess that the hunk is cut at the commented line was checked during reviewer training and is wrong. On the
   valid split (`eval/comment_line_check.py`), the comment shares identifiers with exactly one
   changed line in 30% of rows, ties between several in 20%, and shares none in 51%; 10% of hunks
   have a single changed line. So the reviewer is trained to write the comment only, and the line
   is picked afterwards (ADR 0009).
2. **`oldf` is most of the size.** The 3.6 GB train file is mostly whole source files. Training on
   hunk and comment only means dropping `oldf`, which brings the data down to something a Colab
   session handles easily.
3. **There is file context after all.** The plan says the dataset has no repository context. That
   is true for callers, callees and tests, but the old file is there. An evaluation could use it for a
   "hunk plus surrounding file" variant.
4. **Train has no `lang` or `proj`.** Filtering train to Python and Java would need language
   detection from the code. Valid and test can be filtered directly.
5. `y` is 1 in all 10,319 valid rows: every row here is a hunk that got a comment. It is not used.
6. **Cleaning (`training/sft/format.py`).** Of 117,739 train rows, 8,439 are exact
   duplicates (same hunk and comment), 226 have comments over 120 words, 26 hunks are over 4,000
   characters and 20 comments are mostly non-ASCII; 109,028 are left. No comment is under 3 words
   and no train hunk also appears in valid or test, so those filters removed nothing.
7. **Many comments are linter output or say nothing about the hunk.** The first reviewer trained
   on the cleaned data learned "This is not needed" and wrote it on 7% of test hunks
   (`eval/reports/sft.md`). The second run also drops, from train and valid only: 3,354 train
   comments that are linter or bot output (RuboCop cops, checkpatch "(style)", line length,
   whitespace, quote style), 10,937 that are under 8 words and name nothing in the hunk, and
   320 repeats of a comment text already kept 3 times. 95,284 train rows are left. The test split
   keeps all of these, so every model is scored on the same rows.
8. **Comments start in many different ways.** In the filtered train set the most common first
   words are "this" and "I" (9.6% each), then "why" (4.3%); 44% of comments contain a question.
   With greedy decoding, a model trained on this picks the single most common opener every time:
   the tuned reviewer starts 89% of comments with "I think" and asks almost no questions.

## Quality estimation

Checked by reading every row of each file inside the zip (6 files, CRC test passed), without
extracting it. Token lengths come from the `microsoft/codebert-base` tokenizer on a random sample
of 3,000 hunks per file (seed 0), with the two special tokens counted and no truncation.

| File | Rows | Unpacked size | y = 1 | y = 0 |
|---|---|---|---|---|
| `cls-train-chunk-0.jsonl` | 66,459 | 3.0 GB | 33,166 | 33,293 |
| `cls-train-chunk-1.jsonl` | 66,459 | 3.0 GB | 33,233 | 33,226 |
| `cls-train-chunk-2.jsonl` | 66,459 | 3.0 GB | 33,272 | 33,187 |
| `cls-train-chunk-3.jsonl` | 66,459 | 2.9 GB | 33,247 | 33,212 |
| `cls-valid.jsonl` | 31,252 | 1.5 GB | 15,626 | 15,626 |
| `cls-test.jsonl` | 31,252 | 1.5 GB | 15,626 | 15,626 |

The train split is the four chunks together: 265,836 rows (about 11.9 GB unpacked), 132,918
positive and 132,918 negative. All three splits are exactly 50/50.

Fields:

| Field | Type | In | What it is |
|---|---|---|---|
| `patch` | str | all | One diff hunk, starting with the `@@` header |
| `y` | int | all | Label: 1 = the hunk got a review comment, 0 = it did not |
| `msg` | str | all | The reviewer's comment; empty in every row with `y = 0`, non-empty in every row with `y = 1` |
| `oldf` | str | all | The whole file before the change (most of the size) |
| `id` | int | all | Row id |
| `idx` | int | valid, test, train rows with `y = 0` | Index |
| `proj` | str | valid, test, train rows with `y = 0` | Project |
| `lang` | str | valid, test, train rows with `y = 0` | Language |

Languages in valid and test (share of rows, about the same in both): go 30%, java 16%, py 13%,
cpp 10%, js 9%, rb 6%, cs 6% (stored as `.cs`), c 6%, php 4%.

Token length of `patch`:

| Split | Median | 90th pct | 99th pct | Over 512 tokens |
|---|---|---|---|---|
| train (4 chunks, each sampled) | 254 to 265 | 1,169 to 1,207 | 5,451 to 6,514 | 24.7% to 25.3% |
| valid | 212 | 523 | 1,005 | 10.8% |
| test | 204 | 513 | 1,010 | 10.1% |

### What this means for the gate

1. **`y` is the label, and it is the same as "has a comment".** `y = 1` rows all have a non-empty
   `msg`, `y = 0` rows all have an empty one. The gate input is `patch` only; `msg` must never be
   an input.
2. **Train positives lack `idx`, `proj` and `lang`.** In train, only the `y = 0` rows carry them, so
   those fields would give the label away. They are not used. Filtering train by language is not
   possible for the positives.
3. **The data is balanced 50/50, real pull requests are not.** Precision and the share filtered on
   this test split do not carry over to real traffic, where hunks with a comment are rarer. Recall
   does carry over to hunks like these. BugBench measures the gate on real PRs (see the gate ablation).
4. **Train hunks are much longer than valid and test hunks.** A quarter of train hunks are over 512
   tokens (99th percentile above 5,000), against about 10% in valid and test (99th percentile about
   1,000). Why is not known; it may be a length filter applied to valid and test only. The model is
   cut at 512 tokens (ADR 0008), so training sees truncated hunks more often than evaluation does.
5. **Row order.** The first rows of each file are mixed 0 and 1 (checked on 300 rows per split), so
   a `--head` sample is usable for a dry run, but a random sample is used for real runs.
6. **Size.** The zip is 2.8 GB; unpacked the six files are about 14.9 GB, mostly `oldf`. The
   gate code reads the zip line by line and drops `oldf` as it goes.
7. **Train positives end with a newline, nothing else does.** In the train split every `y = 1`
   hunk ends with `\n` and no `y = 0` hunk does; in valid and test no hunk does. A first Kaggle
   run learned exactly this: train loss went to about 0.0001 inside half an epoch while validation
   F1 at 0.5 was 0. The gate now strips trailing whitespace and git's "no newline" marker from
   every hunk, in training and when served (`normalize_hunk`).
8. **Length still separates the labels, more in train than elsewhere.** Counting lines alone gives
   an AUC of about 0.77 on train, 0.62 on valid and 0.64 on test (first 6,000 rows of each, after
   normalising). Train hunks over 100 lines are dropped, which brings train to about 0.73. The
   mismatch is reduced, not removed. "Longer hunks get more comments" is also the baseline the
   trained gate has to beat.
