# ADR 0020: A small, read-only dashboard

Date: 2026-11-15. Status: accepted.

## Context

PLAN.md Phase 6 asks for a React dashboard: the PR list, comments with their accept rate, cost and
latency per PR, the model routing mix and the eval reports. Everything it needs is already in the
review store (ADR 0015) and its feedback (ADR 0019).

## Decision

**API** (`prism/dashboard.py`, `prism dashboard`, port 8090, 127.0.0.1 only). Read-only JSON:
`/api/summary`, `/api/reviews`, `/api/reviews/{id}` with every finding (posted and dropped, with
the verifier's note and the feedback), `/api/reports` and `/api/reports/{name}` for the Markdown
files in `eval/reports`. The same FastAPI app serves the built page from `dashboard/dist`. Report
names are checked against a plain-name pattern, so a request cannot read outside that folder.

**Numbers.** Comments posted out of comments considered (how much the verifier and the checks
keep); accept rate is good / (good + bad) over comments people rated by reactions; review time is
each review's last run, which for a held review is the fast rerun from cached replies, and the
page says so; cost is what API calls reported (the handoff costs nothing here). The routing mix is
read from each review's summary lines, since the store keeps no per-hunk routing record.

**Page** (`dashboard/`, React 19, TypeScript 7, Vite 8; each pinned to its newest release at least
two weeks old). One screen with a reviews tab and an eval reports tab: five headline figures,
two single-series bar charts (who wrote the posted comments, where hunks went), a table of reviews,
and a detail view per review with its summary, posted comments and the dropped ones folded away.
The look is deliberately quiet: warm off-white paper, near-black ink, hairline rules instead of
cards and shadows, one muted slate-green accent, desaturated tints with text labels for risk,
IBM Plex Sans and Mono bundled locally (no font requests), tabular figures, and a dark mode with
its own stepped colors. Bars carry their value as text and a hover tooltip; the table works by
keyboard. At phone width the figures go to two columns, the charts stack and the table drops
its secondary columns, with no sideways scroll.

**Build.** `npm ci && npm run build` (which typechecks first) writes `dashboard/dist`; it is not
committed. CI has a `dashboard` job that runs the same build.

## Consequences

- With Postgres down the dashboard shows an empty in-memory store; it reads, it does not review.
- The reports are this repo's own Markdown, rendered as written.
