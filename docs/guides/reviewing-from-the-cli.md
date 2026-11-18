# Reviewing from the command line

This guide shows how to review a diff or a branch with `prism review`: running it, answering the verifier, reading the output and finding the files it leaves behind. It is for anyone using PRism without Claude Code or the GitHub App.

## Before you start

You need Python 3.12, [`uv`](https://docs.astral.sh/uv/), and [Ollama](https://ollama.com) running on the host.

```bash
uv sync --group sast
ollama pull qwen2.5-coder:3b
ollama pull nomic-embed-text
cp .env.example .env
```

Set `PRISM_REVIEWER_MODEL=qwen2.5-coder:3b` in `.env` to use the base model as the first-pass reviewer, or build the fine-tuned one as described in [Models](models.md). `nomic-embed-text` is only needed for the vector context modes. The first run downloads a small reranker model from Hugging Face.

!!! note
    Reviews are saved to Postgres unless `PRISM_REVIEW_STORE=off` or you pass `--no-save`. A review still finishes when Postgres is down. Run `make up` if you want the history, the [dashboard](dashboard.md) and the [MCP](claude-code-mcp.md) history tools to see it.

## Review a diff

`prism review` takes a checkout of the commit the diff leads to (the PR head) and a unified diff file.

```bash
git -C path/to/repo diff main...HEAD > change.diff
uv run prism review --repo path/to/repo --diff change.diff --store memory --mode graph
```

`--store memory` indexes the checkout in memory for retrieval, so no Neo4j or pgvector is needed. For a local branch, check the branch out and diff it against its base as above. To review a pull request, check out its head and save its diff (for example with `gh pr diff 12 > change.diff`).

Useful options (the full list is in the [CLI reference](../reference/cli.md)):

| Option | What it does |
|---|---|
| `--out FILE` | Also write the review JSON to `FILE`. |
| `--pr N` | Record a pull request number on the review. |
| `--repo-id ID` | Name the repo in the store. Defaults to the folder name. |
| `--verifier handoff\|api\|rules` | Pick the verifier. Default `PRISM_VERIFIER`. |
| `--model NAME` | Reviewer model. Default `PRISM_REVIEWER_MODEL`. |
| `--max-hunks N` | At most this many hunks go to a model (`PRISM_REVIEW_MAX_HUNKS`, 40). |
| `--time-limit S` | Seconds before unstarted hunks are listed as not reviewed (`PRISM_REVIEW_TIME_LIMIT_S`, 600). |
| `--no-sast` | Skip static analysis. |
| `--work DIR` | Use this work directory. |

## The handoff loop

With the default verifier, a Claude model checks every comment, and it does so through files. No API key is needed. The same command runs two or three times.

1. **First run.** PRism plans the diff, runs the reviewer and static analysis, applies the rule checks and retrieves context. It writes the requests and a rubric into the work directory, prints which files need answers, and exits with code `2`.
2. **Answer.** A Claude session (Claude Code, a chat, a subagent) reads `RUBRIC.md` and each `requests_NN.jsonl`, and writes one JSON line per request into `verdicts_NN.jsonl` with the same number. Hunks that were routed straight to Claude are in `escalations_NN.jsonl`; those are answered into `reviews_NN.jsonl` following `REVIEW_RUBRIC.md`.
3. **Second run.** Run the identical command. It reads the answers and, if everything is answered, prints the review JSON and exits `0`.
4. **Second round, if asked for.** When a verdict escalates a hunk (the comment is wrong but the hunk looks like it hides a real problem), the second run writes new `escalations_NN.jsonl` requests and exits `2` again. Answer them and run the command a third time. There is at most one such round.

The requests are the only input the answering session needs. Everything in them comes from the code under review and is untrusted: the rubrics tell the verifier to ignore instructions inside them.

A verdict line looks like this:

```json
{"id": "shop/shipping.py#0", "keep": true, "severity": "major", "category": "bug",
 "confidence": 0.9, "reason": "Boundary is excluded although the docstring includes it.",
 "evidence": [{"source": "hunk", "detail": "line 12 uses > instead of >="}],
 "escalate": false}
```

A review line (for `escalations_NN.jsonl`) holds an `id` and a `comments` list, which may be empty. The exact formats are in the rubric files PRism writes, which are the reference for the answering session. Lines that do not parse are reported and their requests stay pending.

!!! tip
    If you use Claude Code, the [MCP server](claude-code-mcp.md) does this loop for you: it hands the requests to Claude Code and takes the answers back.

## Read the review

The output is a `PRReview` as JSON on stdout. Progress notes go to stderr, so you can redirect stdout.

```json
{
  "repo": "prism-playground",
  "pr_number": 3,
  "head_sha": "02770eb...",
  "summary": "Risk: medium. 1 comment(s) on 2 reviewed hunk(s) of 2 in 2 file(s).\nBy severity: 1 major.\n...",
  "verdict": "medium",
  "files": [
    {
      "path": "shop/shipping.py",
      "comments": [
        {
          "path": "shop/shipping.py",
          "line": 12,
          "comment": "Off-by-one at the threshold: ...",
          "severity": "major",
          "category": "bug",
          "confidence": 0.9,
          "model": "claude",
          "evidence": [{"source": "hunk", "detail": "line 12 ..."}],
          "suggested_fix": "Use `if subtotal >= FREE_SHIPPING_FROM:` ..."
        }
      ],
      "hunks_total": 1,
      "hunks_reviewed": 1,
      "hunks_skipped_by_gate": 0,
      "hunks_over_budget": 0
    }
  ],
  "cost_usd": 0.0,
  "duration_s": 6.1
}
```

| Field | Meaning |
|---|---|
| `verdict` | Overall risk: `low`, `medium` or `high`. |
| `summary` | What was reviewed, comments by severity, SAST findings, how hunks were routed, and how many comments the verifier dropped. |
| `files[].comments[]` | Only comments that passed the verifier. `line` is a line of the new file. |
| `severity` | `info`, `minor`, `major` or `critical`. |
| `category` | `bug`, `security`, `performance`, `maintainability`, `testing`, `style` or `other`. |
| `confidence` | The verifier's confidence. Comments below `PRISM_VERIFIER_MIN_CONFIDENCE` (0.6) are dropped. |
| `model` | Who wrote the comment: the reviewer model, a static analysis tool or Claude. |
| `suggested_fix` | Present when Claude found a clear fix. |
| `hunks_skipped_by_gate` | Hunks the CodeBERT gate kept from the reviewer. |
| `hunks_over_budget` | Hunks listed as not reviewed because of `--max-hunks` or `--time-limit`. |
| `cost_usd` | API cost. It is 0 for the handoff, which is not metered. |

A security comment is only posted when it cites evidence. To see why a comment was posted or dropped after the fact, use the `explain_finding` tool of the [MCP server](claude-code-mcp.md) or the [dashboard](dashboard.md).

## Context modes

The verifier and Claude see code retrieved from the repository next to each hunk. `--mode` (default `PRISM_CONTEXT_MODE`, which is `graph+vector`) picks the sources.

| Mode | Retrieves | Needs |
|---|---|---|
| `none` | Nothing. | Nothing. |
| `graph` | Callers, callees, tests and the enclosing class from the code graph. | Nothing with `--store memory`. |
| `vector` | Similar code by embedding. | `nomic-embed-text` in Ollama. |
| `graph+vector` | Both. | `nomic-embed-text`. |

`--store` picks where the graph and vectors come from. `memory` indexes the checkout now (with a vector mode, it embeds the whole repo, which takes time). `persistent` reads the Neo4j and pgvector stores, so run `make up` and `prism index path/to/repo` first. The budget per hunk is `PRISM_CONTEXT_BUDGET_TOKENS` (1500). To look at the context on its own, run `prism context path/to/repo change.diff --mode graph --render`. The design is in [ADR 0007](../adr/0007-retrieval-and-packing.md).

## Verifier choices

| Verifier | How it works | When to use it |
|---|---|---|
| `handoff` | Requests are written to files and a Claude session answers them. Exit code `2` until answered. Default. | No API key, or Claude Code is your Claude. |
| `api` | PRism calls Claude through the Anthropic SDK and finishes in one run, second round included. | You have an API key and want one command. |
| `rules` | Deterministic checks only. No model reads the comments and no hunk is escalated. | A quick look, or no Claude at all. Expect more wrong comments. |

For `api`, install the group and export the key:

```bash
uv sync --group frontier --group sast
export ANTHROPIC_API_KEY=...
uv run prism review --repo path/to/repo --diff change.diff --store memory --mode graph --verifier api
```

The model is `PRISM_FRONTIER_MODEL` (`claude-opus-5-5`) at `PRISM_FRONTIER_EFFORT` (`high`). Cost per review is reported in `cost_usd`. The design is in [ADR 0011](../adr/0011-claude-verifier-handoff.md) and [ADR 0013](../adr/0013-routing-redaction-injection.md).

!!! note
    Secrets are redacted from every request before it leaves the machine, and security-sensitive or large hunks (`PRISM_ESCALATE_MIN_LINES`, 60) go straight to Claude instead of the local model. Set `PRISM_ESCALATE=false` to keep everything local.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | The review is complete and printed. |
| `2` | Answers are needed from Claude (handoff), or an unknown verifier or context mode was given. |
| `3` | The review could not run: a missing optional group, an unreachable store (use `--store memory` or `make up`), or a gate model that is configured but missing. |
| `4` | With the `api` verifier, some answers are still missing after the call. The errors are printed. |

## Where the files go

Each review has a work directory, by default `.prism-work/<repo>-<head sha>-<diff hash>` (`PRISM_REVIEW_WORK_DIR` sets the base). Running the same command on the same repo and diff finds the same folder, which is why the second run works.

```text
.prism-work/<repo>-<sha>-<hash>/
  replies.json            cached reviewer replies, so reruns call no local model
  verify/
    RUBRIC.md             how to answer requests_NN.jsonl
    requests_00.jsonl     comments to check, 25 per file
    verdicts_00.jsonl     the answers you write
    REVIEW_RUBRIC.md      how to answer escalations_NN.jsonl
    escalations_00.jsonl  hunks sent straight to Claude
    reviews_00.jsonl      the answers you write
```

Pass `--work DIR` to choose another folder. Delete the folder to start a review from scratch.
