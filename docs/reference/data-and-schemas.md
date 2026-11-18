# Data and schemas

This page describes the review output schema, the datasets PRism was built on, and the database
tables PRism creates. It is for anyone who reads PRism's JSON output, builds on it or inspects
its stores.

## Review output schema

The schemas are pydantic models in `prism/schemas.py`. They are frozen and reject unknown
fields. The reasoning for keeping model output and verified comments apart is in
[ADR 0001](../adr/0001-raw-and-verified-comment-schemas.md) and
[ADR 0009](../adr/0009-reviewer-fine-tuning.md).

### Models

`prism review` prints a `PRReview`. A `PRReview` holds one `FileReview` per file, and each
`FileReview` holds the `ReviewComment` items that were kept.

**`PRReview`**

| Field | Type | Default | Description |
|---|---|---|---|
| `repo` | string, non-empty | | Repository |
| `pr_number` | integer, 1 or more, or null | null | Null for a local diff |
| `head_sha` | string, non-empty | | Head commit |
| `summary` | string | | Summary of the review, including the routing |
| `verdict` | `Verdict` | | Overall risk verdict |
| `files` | list of `FileReview` | empty | Per-file results |
| `cost_usd` | number, 0 or more | 0.0 | Cost reported by the verifier backend |
| `duration_s` | number, 0 or more | 0.0 | Duration in seconds |

**`FileReview`**

| Field | Type | Default | Description |
|---|---|---|---|
| `path` | string, non-empty | | File path |
| `comments` | list of `ReviewComment` | empty | Kept comments |
| `hunks_total` | integer, 0 or more | | Hunks in the file |
| `hunks_reviewed` | integer, 0 or more | | Hunks sent to a model |
| `hunks_skipped_by_gate` | integer, 0 or more | 0 | Hunks the gate skipped |
| `hunks_over_budget` | integer, 0 or more | 0 | Hunks listed as not reviewed |

**`ReviewComment`**: a comment the verifier kept. Only these are posted.

| Field | Type | Default | Description |
|---|---|---|---|
| `path` | string, non-empty | | File path |
| `line` | integer, 1 or more | | Line in the new version of the file |
| `comment` | string, non-empty | | The comment |
| `severity` | `Severity` | | Severity |
| `category` | `Category` | | Category |
| `confidence` | number, 0.0 to 1.0 | | Verifier confidence |
| `model` | string, non-empty | | Model that wrote the comment |
| `evidence` | list of `Evidence` | empty | What the comment rests on |
| `suggested_fix` | string or null | null | Only the frontier path produces a fix |

**`Evidence`**

| Field | Type | Description |
|---|---|---|
| `source` | string, non-empty | Tool or context kind, for example `semgrep` or `caller` |
| `detail` | string, non-empty | The finding or the retrieved code |

**`RawComment`**: a reviewer comment placed on a line, before verification.

| Field | Type | Description |
|---|---|---|
| `line` | integer, 1 or more | Line in the new version of the file |
| `comment` | string, non-empty | The comment |

**`ReviewerReply`**: what the reviewer model writes for one hunk.

| Field | Type | Description |
|---|---|---|
| `comment` | string, non-empty | The comment |

The model writes `{"comment": "..."}` only. The line is picked afterwards by
`prism.models.reviewer.anchor_line`, which turns a reply into a `RawComment`.

### Enums

| Enum | Values |
|---|---|
| `Severity` | `info`, `minor`, `major`, `critical` |
| `Category` | `bug`, `security`, `performance`, `maintainability`, `testing`, `style`, `other` |
| `Verdict` | `low`, `medium`, `high` |

### Example

```json
{
  "repo": "fresh-playground",
  "pr_number": null,
  "head_sha": "02770ebc822b1febafef968ef7c81f79f862e65d",
  "summary": "Risk: medium. 1 comment(s) on 2 reviewed hunk(s) of 2 in 2 file(s).\nBy severity: 1 major.\nSAST findings in the changed files: bandit 2, semgrep 0, ruff 0, prism 0.\nRouting: 2 hunk(s) to qwen2.5-coder:3b.\nEscalated to claude: 1 hunk(s) (verifier 1).\nVerifier: claude (handoff); 3 comment(s) dropped.",
  "verdict": "medium",
  "files": [
    {
      "path": "shop/shipping.py",
      "comments": [
        {
          "path": "shop/shipping.py",
          "line": 12,
          "comment": "Docstring says orders of at least FREE_SHIPPING_FROM ship free, but the check uses >, so an order of exactly 50.0 pays shipping. Boundary is off by one and untested.",
          "severity": "major",
          "category": "bug",
          "confidence": 0.9,
          "model": "claude",
          "evidence": [
            {
              "source": "hunk",
              "detail": "line 11 docstring 'at least' vs line 12 'if subtotal > FREE_SHIPPING_FROM'"
            },
            {
              "source": "test",
              "detail": "tests only use 10.0 and 80.0, never the 50.0 threshold"
            }
          ],
          "suggested_fix": "Use `>=` on line 12 and add a test for shipping_cost(50.0, 2) == 0.0."
        }
      ],
      "hunks_total": 1,
      "hunks_reviewed": 1,
      "hunks_skipped_by_gate": 0,
      "hunks_over_budget": 0
    },
    {
      "path": "tests/test_shipping.py",
      "comments": [],
      "hunks_total": 1,
      "hunks_reviewed": 1,
      "hunks_skipped_by_gate": 0,
      "hunks_over_budget": 0
    }
  ],
  "cost_usd": 0.0,
  "duration_s": 8.12
}
```

This is the real output of the [Quickstart](../getting-started/quickstart.md) review, on a
change with a planted off-by-one.

### Retrieved context

Retrieval has its own schemas in the same file: `ContextBundle`, `HunkContext`, `ContextItem`
and `DroppedItem`, with the enums `ContextMode` (`none`, `vector`, `graph`, `graph+vector`) and
`ContextKind` (`signature`, `contract`, `test`, `caller`, `implementation`, `class`, `callee`,
`class_test`, `similar`, listed in packing priority order). See
[ADR 0007](../adr/0007-retrieval-and-packing.md).

## Datasets

PRism's models were trained on the CodeReviewer data, and the benchmark is built from real
commits. The full inspection notes, with file sizes, row counts and what they mean for the
design, are in [Datasets](../datasets.md).

**CodeReviewer** (Li et al., 2022): Zenodo record 6900648, "Automating Code Review Activities by
Large-Scale Pre-Training", licence CC BY 4.0. The files live in `data/codereviewer/`, which is
git-ignored.

| Archive | Download size | Used for |
|---|---|---|
| `Comment_Generation.zip` | 847 MB | Fine-tuning the reviewer ([ADR 0009](../adr/0009-reviewer-fine-tuning.md)) |
| `Diff_Quality_Estimation.zip` | 2.8 GB | Training the gate ([ADR 0008](../adr/0008-gate-classifier.md)) |
| `Code_Refinement.zip` | 1.2 GB | Not used |

Comment generation files:

| File | Rows |
|---|---|
| `msg-train.jsonl` | 117,739 |
| `msg-valid.jsonl` | 10,319 |
| `msg-test.jsonl` | 10,169 |

Each row has a `patch` (one diff hunk), a `msg` (the reviewer's comment), `oldf` (the whole file
before the change) and an `id`; valid and test rows also have `idx`, `proj` and `lang`. There is
no line number. After cleaning, 109,028 train rows are left, and 95,284 after the second run's
extra filters.

Quality estimation files:

| File | Rows |
|---|---|
| `cls-train-chunk-0.jsonl` to `cls-train-chunk-3.jsonl` | 66,459 each (265,836 in total) |
| `cls-valid.jsonl` | 31,252 |
| `cls-test.jsonl` | 31,252 |

Each row has a `patch`, a label `y` (1 if the hunk got a review comment, 0 if not), `msg`
(empty when `y` is 0), `oldf` and an `id`. All three splits are 50/50. The gate reads `patch`
only.

**BugBench**: PRism's own benchmark of 198 pull requests (139 with a planted bug, 30 with a
real bug that was fixed later, 29 clean) on TinyDB, aiosqlite, sqlite-utils and jsoup. See
[ADR 0021](../adr/0021-bugbench.md); the repositories are listed in `bugbench/repos.toml` and the
runs in `bugbench/configs.toml`.

## Database tables

PRism creates its Postgres tables itself. The migrations are plain numbered SQL in
`prism/store/postgres.py`, applied in order inside a transaction under an advisory lock and
recorded in `schema_migrations`. Migration 1 enables the `vector` extension.

| Migration | Adds |
|---|---|
| 1 | `function_embeddings`, `index_runs` |
| 2 | `reviews`, `review_findings` |
| 3 | `reviews.github_review_id`, `reviews.posted_at` |
| 4 | `reviews.installation_id`, `comment_feedback` |

### `schema_migrations`

| Column | Type | Notes |
|---|---|---|
| `version` | INT | Primary key |
| `applied_at` | TIMESTAMPTZ | Default `now()` |

### `function_embeddings`

One embedding per function of the default branch head ([ADR 0005](../adr/0005-embeddings.md)).

| Column | Type | Notes |
|---|---|---|
| `repo_id` | TEXT | Primary key, with `qualified_name` |
| `qualified_name` | TEXT | |
| `path` | TEXT | Indexed with `repo_id` |
| `embedder` | TEXT | Model that made the embedding |
| `content_hash` | TEXT | Default empty |
| `embedding` | vector | No fixed dimension |
| `updated_at` | TIMESTAMPTZ | Default `now()` |

### `index_runs`

The index-run ledger ([ADR 0006](../adr/0006-index-consistency.md)).

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL | Primary key |
| `repo_id` | TEXT | At most one `running` run per repository |
| `commit_sha` | TEXT | |
| `kind` | TEXT | `full` or `incremental` |
| `status` | TEXT | `running`, `complete` or `failed` |
| `started_at` | TIMESTAMPTZ | Default `now()` |
| `heartbeat_at` | TIMESTAMPTZ | Default `now()` |
| `finished_at` | TIMESTAMPTZ | |
| `error` | TEXT | |

### `reviews`

One row per review ([ADR 0015](../adr/0015-stored-reviews.md)).

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL | Primary key |
| `key` | TEXT | Unique: `repo#pr#head_sha#diff_hash` |
| `repo` | TEXT | |
| `pr_number` | INT | Null for a local diff |
| `head_sha` | TEXT | |
| `status` | TEXT | `awaiting_verification` or `complete` |
| `verdict` | TEXT | |
| `summary` | TEXT | Default empty |
| `cost_usd` | DOUBLE PRECISION | Default 0 |
| `duration_s` | DOUBLE PRECISION | Default 0 |
| `trace_id` | TEXT | Langfuse trace |
| `work_dir` | TEXT | Handoff work directory |
| `created_at` | TIMESTAMPTZ | Default `now()` |
| `updated_at` | TIMESTAMPTZ | Default `now()` |
| `github_review_id` | BIGINT | Set when the App posts the review (migration 3) |
| `posted_at` | TIMESTAMPTZ | Set when the App posts the review (migration 3) |
| `installation_id` | BIGINT | GitHub App installation (migration 4) |

### `review_findings`

Every comment the pipeline considered, posted or dropped.

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL | Primary key |
| `review_id` | BIGINT | References `reviews (id)`, deleted with the review |
| `path` | TEXT | |
| `line` | INT | |
| `hunk_header` | TEXT | |
| `hunk` | TEXT | |
| `author` | TEXT | Where the comment came from |
| `comment` | TEXT | |
| `posted` | BOOLEAN | |
| `decision` | TEXT | `kept`, or why it was dropped |
| `verifier_note` | TEXT | Default empty |
| `severity` | TEXT | |
| `category` | TEXT | |
| `confidence` | DOUBLE PRECISION | |
| `evidence` | JSONB | Default `[]` |
| `suggested_fix` | TEXT | |
| `tools` | JSONB | Default `[]`; static-analysis findings in the hunk |
| `github_comment_id` | BIGINT | Indexed (migration 4) |

### `comment_feedback`

Feedback on posted comments ([ADR 0019](../adr/0019-comment-feedback.md)).

| Column | Type | Notes |
|---|---|---|
| `finding_id` | BIGINT | Primary key, references `review_findings (id)` |
| `thumbs_up` | INT | Default 0 |
| `thumbs_down` | INT | Default 0 |
| `replies` | INT | Default 0 |
| `resolved` | BOOLEAN | Default false |
| `etag` | TEXT | ETag of the last reactions response |
| `checked_at` | TIMESTAMPTZ | |
| `sent` | TEXT | What was last sent to Langfuse |
| `updated_at` | TIMESTAMPTZ | Default `now()` |

### Neo4j

The code graph lives in Neo4j, not Postgres. Nodes are labelled `Node` plus `File`, `Class` or
`Function`, and are unique per `(repo_id, qualified_name)`. Relationships are `DEFINES`,
`IMPORTS`, `CALLS`, `TESTS`, `INHERITS` and `OVERRIDES`; `CALLS` and `TESTS` carry a confidence
([ADR 0004](../adr/0004-call-resolution.md)).
