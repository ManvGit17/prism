# Quickstart

Review a real change in about ten minutes, with nothing but `uv`, Ollama and Claude. No Docker.
Every step here was run on a fresh clone.

## 1. Install

```bash
git clone https://github.com/ManvGit17/prism.git
cd prism
uv sync --group sast
cp .env.example .env
ollama pull qwen2.5-coder:3b
```

Put these two lines in `.env`, so PRism uses the base reviewer and does not look for Postgres:

```bash
PRISM_REVIEWER_MODEL=qwen2.5-coder:3b
PRISM_REVIEW_STORE=off
```

## 2. Make a diff to review

PRism reviews a diff against a checkout of the code at the pull request's head. Any branch of
any Python or Java repository works:

```bash
git -C path/to/repo checkout my-branch
git -C path/to/repo diff main...my-branch > change.diff
```

## 3. Run the review

```bash
uv run prism review --repo path/to/repo --diff change.diff --store memory --mode graph
```

`--store memory` indexes the repository in memory, so no database is needed. The first run
downloads a small reranker model from Hugging Face.

The local reviewer comments on each hunk, the static analysers run, and then the run stops with
**exit code 2**:

```text
2 answer(s) needed from Claude in .prism-work/<repo>-<sha>-<diff>/verify: verdicts_NN.jsonl
for requests_NN.jsonl (RUBRIC.md) and reviews_NN.jsonl for escalations_NN.jsonl
(REVIEW_RUBRIC.md). Then run the same command again.
```

## 4. Let Claude verify

Exit code 2 means PRism is waiting for Claude. Pick one:

- **Claude Code** (no API key): open Claude Code in the repo and ask it to answer the requests
  in that `verify` folder following `RUBRIC.md` and `REVIEW_RUBRIC.md`. Or set up the
  [MCP server](../guides/claude-code-mcp.md), and Claude Code runs the whole loop for you.
- **API key**: put `ANTHROPIC_API_KEY` in your environment, run `uv sync --group frontier`, set
  `PRISM_VERIFIER=api`, and the review finishes in one run.
- **No Claude**: add `--verifier rules` to keep only the deterministic checks.

## 5. Get the review

Run the same command again. When Claude escalated a hunk, there is one more short round; then
the command exits 0 and prints the review as JSON:

```json
{
  "repo": "fresh-playground",
  "verdict": "medium",
  "files": [
    {
      "path": "shop/shipping.py",
      "hunks_total": 1,
      "hunks_reviewed": 1,
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
            {"source": "hunk", "detail": "line 11 docstring 'at least' vs line 12 'if subtotal > FREE_SHIPPING_FROM'"},
            {"source": "test", "detail": "tests only use 10.0 and 80.0, never the 50.0 threshold"}
          ],
          "suggested_fix": "Use `>=` on line 12 and add a test for shipping_cost(50.0, 2) == 0.0."
        }
      ]
    }
  ]
}
```

That is the real result of this quickstart (shortened), on a change with a planted off-by-one.
The full schema is in [data and schemas](../reference/data-and-schemas.md).

## Next

- Review from Claude Code without copying files around: [Claude Code and MCP](../guides/claude-code-mcp.md)
- Review every pull request on a repository: [GitHub App](../guides/github-app.md)
- Use the fine-tuned reviewer and the gate: [Models](../guides/models.md)
- All the options of `prism review`: [Reviewing from the CLI](../guides/reviewing-from-the-cli.md)
