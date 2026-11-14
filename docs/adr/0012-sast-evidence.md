# ADR 0012: Static analysis as evidence and as comments

Date: 2026-11-14. Status: accepted.

## Context

PLAN.md Phase 5 runs Semgrep (OWASP ruleset), Bandit, Ruff and PMD on the changed files and asks
that every security claim cite a tool finding or code context. ADR 0011 already drops a security
comment whose verdict cites no evidence. What was missing is the tools themselves.

The tuned reviewer is weak at security. On a test commit that added an SQL f-string, `shell=True`,
`pickle.loads` and MD5 password hashing, `prism-reviewer:v2` commented on none of them. Bandit
found all four.

## Decision

**Tools.** Bandit, Semgrep and Ruff for Python, Semgrep and PMD for Java, each run once per
review as a subprocess on the changed files at the head checkout (`prism/review/sast.py`).
Bandit and Semgrep come from an optional `sast` dependency group (`uv sync --group sast`), so
the default install stays small. PMD is a separate download pointed at by `PRISM_PMD_HOME`.
Semgrep runs with `--metrics=off`; its default ruleset `p/owasp-top-ten` is fetched from the
Semgrep registry on first use. Ruff runs with only the rules that point at bugs (`F`, `B`, `E9`,
`PLE`) and `--isolated`, so the repository's own Ruff config does not change what counts.

**A missing or failing tool never fails a review.** It is listed in the summary as not run, with
the reason.

**Where it runs.** SAST is one more branch of the planner's `Send` fan-out, so the tools run
while the reviewer model does.

**What a finding does.**
- A finding inside a reviewed hunk is attached to every verifier request for that hunk (`tools`),
  as evidence the verifier may cite.
- A finding on an added line, at medium severity or above (`PRISM_SAST_COMMENT_MIN_SEVERITY`),
  also becomes a comment of its own, authored by the tool. Only one tool comment per line is
  made: Bandit and Semgrep often report the same issue, and the strongest finding wins. The
  verifier checks tool comments like the model's: tool findings are usually true about the line
  but often do not matter (test code, input that is not user-controlled).
- A finding outside every reviewed hunk is ignored. Code the PR did not change is not this
  review's business.

## Consequences

- A security hunk the gate or the budget leaves out gets no SAST comment either, even with a high
  severity finding. Security-looking hunks already skip the gate (ADR 0010), so this matters only
  when the budget runs out.
- The first real run (the test commit above) went from 2 model comments to 6 verifier requests
  after one-per-line deduplication (9 before it).
- Semgrep needs network access the first time it fetches a registry ruleset. For offline use,
  point `PRISM_SEMGREP_CONFIG` at a local rules file.
- PMD is not installed on the build machine yet, so Java has Semgrep only until it is.
