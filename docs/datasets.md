# Datasets

## CodeReviewer (Li et al., 2022)

Source: Zenodo record 6900648, "Automating Code Review Activities by Large-Scale Pre-Training".
Licence: CC BY 4.0.

| Archive | Download size | Status |
|---|---|---|
| `Comment_Generation.zip` | 847 MB | Downloaded and inspected 2026-11-11 |
| `Diff_Quality_Estimation.zip` | 2.8 GB | Not downloaded yet, needed for Phase 3 |
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

1. **There is no line number.** A row is a hunk and a comment. The plan has the model output
   `{line, comment}`, so the `line` label has to be derived. The usual reading is that the hunk is
   cut at the line the comment was left on, which makes the last line of `patch` the target. This
   needs to be checked on a sample before Phase 4 formats the training data. If it does not hold,
   the model should output the comment only and the line gets picked some other way.
2. **`oldf` is most of the size.** The 3.6 GB train file is mostly whole source files. Training on
   hunk and comment only means dropping `oldf`, which brings the data down to something a Colab
   session handles easily.
3. **There is file context after all.** The plan says the dataset has no repository context. That
   is true for callers, callees and tests, but the old file is there. Phase 7 could use it for a
   "hunk plus surrounding file" variant.
4. **Train has no `lang` or `proj`.** Filtering train to Python and Java would need language
   detection from the code. Valid and test can be filtered directly.
5. Confirm that `y` is always 1 here, and what it means, before using it.
