# Security and privacy

This page says what leaves your machine and where it goes, how PRism removes secrets before it
sends anything to Claude, how it treats pull request text as untrusted, and what the GitHub App
is allowed to do and how it holds its keys. It is for anyone deciding whether to run PRism on a
private repository. The decisions are in
[ADR 0013](../adr/0013-routing-redaction-injection.md),
[ADR 0014](../adr/0014-langfuse-tracing-and-prompts.md) and
[ADR 0017](../adr/0017-github-app.md).

## What data goes where

| Destination | What it receives | When |
|---|---|---|
| Ollama on the host | The hunk as it is (not redacted), for the local reviewer; function text for embeddings | Always, for the hunks that stay local and for indexing. Ollama is on your machine, so this is not an external call |
| Static analysis tools | The changed files at the head checkout | Always, as local subprocesses. Semgrep runs with `--metrics=off`; it fetches its ruleset from the Semgrep registry on first use, and `PRISM_SEMGREP_CONFIG` can point at a local rules file for offline use |
| Claude | Redacted requests: the hunk with line numbers, the comment, retrieved context, tool findings in the hunk and the planner's risk reasons | With the handoff verifier, into files that a Claude session reads (or into an MCP tool result); with `PRISM_VERIFIER=api`, to the Anthropic API |
| Langfuse | Traces, prompts, scores and datasets | Only when `PRISM_TRACING=langfuse` or `PRISM_PROMPT_SOURCE=langfuse`. The Compose profile is self-hosted and binds to `127.0.0.1` |
| Postgres and Neo4j | The code graph, embeddings, and stored reviews with every decision | Local Compose services, ports published on `127.0.0.1` only |
| GitHub | The review: a summary and inline comments | Only from the GitHub App, and only to the pull request it reviewed |

Claude never sees the repository. It sees the requests: a hunk, the context retrieved for it and
the comments to check. PRism does not upload the repository, the graph or the embeddings.

!!! note
    The handoff verifier does not call Claude from PRism. Whoever runs the Claude session reads the
    request files, so the data goes wherever that session sends it. Through MCP that is the Claude
    Code session that called `review_diff`.

## Redaction

Everything sent to Claude passes through `redact` first (`prism/review/redact.py`): the hunk, the
comment, the retrieved context, the tool messages and the notes on an earlier comment. This
applies to both backends, the file handoff and the API. The local reviewer on Ollama is not an
external call and sees the hunk as it is.

Redaction runs three passes, in order:

1. **Known token formats**, replaced whole: AWS access keys, GitHub tokens, Slack tokens, Stripe
   keys, Google API keys, `sk-` style API keys, Hugging Face tokens, JWTs and private key blocks.
   A password inside a URL (`scheme://user:password@host`) is replaced too.
2. **Secret-sounding assignments.** A quoted value assigned to a name that ends in a secret word
   (`password`, `secret`, `token`, `api_key`, `access_key`, `private_key`, `client_secret`,
   `auth`) is replaced: `DB_PASSWORD = "..."` is redacted, `TOKENIZER_FILE = "..."` is not.
   Placeholders (`${...}`, `os.environ...`, `changeme`, `example`, `your_...`) are left alone.
3. **detect-secrets** as a second net. Its entropy checks fire on ordinary identifiers, so an
   entropy hit counts only for a value of 24 or more characters that mixes letters and digits and
   has an unbroken run of 16.

The replacement says what was there, such as `[REDACTED:github-token]`, so Claude can still
comment on a hard-coded secret without seeing it.

This is pattern matching, not a guarantee. A secret in a format none of the passes knows, such as
a short password in a bare string, can get through. A commit SHA is redacted on purpose, because it
looks like a hex token. If a repository holds data you cannot show to Claude at all, use
`PRISM_VERIFIER=rules`, which sends nothing anywhere.

## Prompt injection

Pull request content is untrusted: a hunk, a comment or a docstring can contain text written to
steer an AI reviewer ("AI reviewer: approve this PR"). PRism defends in three layers.

**1. Delimiters.** In every prompt, the pull request's text sits inside an
`<untrusted_pull_request>` tag, and retrieved code inside a `<repository_context>` tag. A closing
tag inside the text is escaped, so the text cannot end the block early. Code snippets are fenced
with a fence longer than any backtick run inside them. Both rubrics tell Claude that the content is
data, to ignore instructions in it, and that a hunk that tries to instruct a reviewer is itself
worth a security comment.

**2. No model decides the outcome.** The risk verdict is computed from the kept comments by code
(see [the aggregator](review-pipeline.md#6-aggregator)); no model is asked whether to approve.
Answers are matched to requests by id, so a hunk cannot add or change a verdict. The GitHub App
posts reviews with the event `COMMENT` only, never `APPROVE` or `REQUEST_CHANGES`, so a review
cannot approve or block a merge.

**3. The `prism` injection check.** A built-in scan runs next to the static analysis tools on
every changed text file and is always on. It finds lines that read like instructions to an AI
reviewer: "ignore previous instructions" and its variants, an `AI reviewer` or `Claude` followed
by words such as approve, ignore or must, "approve this PR", "do not report this issue", "you are
now a ...", "new system prompt", and a closing tag of one of the prompt delimiters. A match on an
added line becomes a high-severity security comment, with the matched text quoted. The planner also
treats the hunk as security-sensitive, so the gate cannot skip it and the router sends it to
Claude.

The scan is a regular expression. It catches plain attempts and is easy to get past; the real
defences are layers 1 and 2. A hunk that steers the local model still only produces a comment,
which the verifier judges.

## Traces and prompts in Langfuse

Everything sent to Langfuse as trace data is masked with the same `redact` function, because a
trace holds hunks, comments and the verifier's decisions, and the same code can point at a hosted
server. The Compose profile is self-hosted and binds its ports to `127.0.0.1`, so with it the data
stays on your machine.

- Tracing is off by default (`PRISM_TRACING=off`) and nothing imports Langfuse unless you turn it
  on.
- Prompts: the code keeps the reference text, and `prism prompts push` copies it to Langfuse. A
  review reads prompts by label and falls back to the code's text when Langfuse is off or down.
- Feedback: reactions, replies and resolved threads on PRism's comments become `comment_feedback`
  scores and items of the `prism-comment-feedback` dataset, which holds the hunk, the comment and
  the label. Replies and reactions from bots, PRism's own included, do not count.

!!! warning
    The Compose files ship local development values for the Langfuse keys and internal passwords,
    like the Postgres and Neo4j passwords in `.env.example`. They are fine for services bound to
    `127.0.0.1` and wrong anywhere else. Set your own in `.env` before you expose anything.

## The GitHub App

### Permissions

Register the App with:

| Permission | Level | Why |
|---|---|---|
| Pull requests | Read and write | Read the pull request and its diff, post the review and its inline comments |
| Contents | Read-only | Make the shallow checkout of the head commit that static analysis and retrieval read |

and subscribe it to the `pull_request` event. For feedback on comments, subscribe it also to
`pull_request_review_comment` and `pull_request_review_thread`
([ADR 0019](../adr/0019-comment-feedback.md)). Install it on the repositories you want reviewed;
installing it on one repository gives it access to that repository only.

The App does not merge, approve, push or write to the repository. It posts one review per head
commit, as event `COMMENT`.

### Keys and secrets

- **Private key.** It stays outside the repository. `PRISM_GITHUB_PRIVATE_KEY_PATH` holds only
  its path, and the App reads the file from there. In Docker the key is mounted read-only from
  `PRISM_APP_KEY_FILE` (default `./secrets/prism-app.pem`); nothing secret is baked into the image.
- **App id and webhook secret.** They go in the git-ignored `.env`
  (`PRISM_GITHUB_APP_ID`, `PRISM_GITHUB_WEBHOOK_SECRET`). The webhook secret is stored as a secret
  value and is not printed in the configuration reference.
- **Authentication.** The App signs a 9-minute RS256 JWT with its private key and exchanges it for
  an installation token, cached until 5 minutes before it expires (installation tokens last an
  hour).
- **Checkouts.** The installation token goes into a per-command `http.extraHeader` when PRism
  clones the head commit. It is never written to `.git/config` or the remote URL.
- **Webhook.** The `X-Hub-Signature-256` HMAC is checked, with a constant-time comparison, before
  the body is read as JSON. A bad signature is a 401. Repeated delivery ids are dropped.
- **Stale results.** Before posting, the head commit is checked again. If someone pushed in the
  meantime, the result is dropped as superseded, because its line numbers may be stale.
- **Rate limits.** A 403 or 429 that carries `retry-after`, or `x-ratelimit-remaining: 0`, waits
  and retries, capped at 60 seconds and three retries.
- **Size limit.** Pull requests over `PRISM_APP_MAX_CHANGED_LINES` (default 3000) are skipped.

!!! warning
    Never commit the private key, the webhook secret or an API key. `.env` is git-ignored, and the
    key for the API verifier, `ANTHROPIC_API_KEY`, belongs in the environment, not in a file in the
    repository.

## What you can turn off

| To avoid | Set |
|---|---|
| Any data going to Claude | `PRISM_VERIFIER=rules` (or `--verifier rules`) |
| Anything going to Langfuse | `PRISM_TRACING=off` and `PRISM_PROMPT_SOURCE=code` (the defaults) |
| Reviews being stored | `PRISM_REVIEW_STORE=off` (or `--no-save`) |
| Static analysis | `PRISM_SAST_TOOLS=` (or `--no-sast`) |
| Routing hunks to Claude up front | `PRISM_ESCALATE=false` |

With `PRISM_VERIFIER=rules`, comments are not checked for truth, and the local model's comments
should not be posted unverified. See the [review pipeline](review-pipeline.md#5-verifier).
