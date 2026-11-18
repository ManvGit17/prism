# Security policy

This page says how to report a vulnerability in PRism and describes the project's security
design in brief. It is for security researchers, users and contributors.

## Reporting a vulnerability

Report vulnerabilities privately through GitHub private vulnerability reporting:

1. Open the repository on GitHub: https://github.com/ManvGit17/prism.
2. Go to the **Security** tab and choose **Report a vulnerability**.
3. Describe the problem, the version or commit, and the steps to reproduce it.

Please do not open a public issue or pull request for a vulnerability, and do not post secrets
or exploit details publicly. Reports are handled on a best-effort basis by the maintainer, who
will reply in the same private thread.

## Scope

In scope:

- The code in this repository: the review pipeline, the CLI, the MCP server, the GitHub App
  (webhook handling, authentication, posting) and the dashboard API.
- Ways a pull request's content could make PRism leak secrets, take an action it should not
  take, or post a review that the content of the pull request dictates (prompt injection).
- Secrets or private keys committed to the repository or its history.
- The Compose files and their default network exposure.

Out of scope:

- Reviews that miss a bug or a vulnerability in the code they review. The reviewer is a review
  aid, not a security scanner.
- Vulnerabilities in third-party software PRism uses (Ollama, Langfuse, Neo4j, Postgres, Redis,
  Claude and so on) that are not caused by how PRism uses it. Report those upstream.
- Problems that need an attacker who already controls your machine, your `.env` or your App's
  private key.
- The local development defaults in `.env.example`, which are not secrets.

## Security design in brief

Details and the reasoning are in
[Security and privacy](docs/concepts/security-and-privacy.md) and
[ADR 0013](docs/adr/0013-routing-redaction-injection.md).

- **Secret redaction.** Every text field of every request sent to Claude is redacted first:
  known token formats, quoted values assigned to names that end in a secret word, and
  detect-secrets as a second net. The redacted text says what kind of secret was there. The local
  model runs on your machine through Ollama and is not an external call.
- **Untrusted pull request text.** Pull request text sits inside `<untrusted_pull_request>`
  delimiters that the text cannot close, and the prompts say to ignore instructions in it. The
  risk verdict is computed by code from the kept comments, not by a model. A built-in scan flags
  text that reads like an instruction to an AI reviewer and treats the hunk as security-sensitive.
- **GitHub App key handling.** The App's private key stays outside the repository
  (`PRISM_GITHUB_PRIVATE_KEY_PATH`); the App id and the webhook secret are in the git-ignored
  `.env`. Webhook signatures (`X-Hub-Signature-256`) are verified before the body is parsed. The
  installation token is short-lived and is passed to git per command, never stored in
  `.git/config` or a remote URL. The App posts reviews with the event `COMMENT` only; it never
  approves or requests changes. See [ADR 0017](docs/adr/0017-github-app.md).
- **No secrets in the repository.** `.env`, `*.pem`, model weights and datasets are git-ignored,
  and `.env.example` holds local development defaults only.
- **Local by default.** The dashboard API listens on 127.0.0.1 only and is read-only
  ([ADR 0020](docs/adr/0020-dashboard.md)).
