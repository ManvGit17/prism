# ADR 0006: Base graph, PR overlays and the index-run ledger

Status: accepted.

## Context

PRism reviews pull requests on many repos at once. Each repo has a graph in Neo4j and function
embeddings in pgvector. Several PRs on one repo can be open at the same time, on different
commits. Neo4j and Postgres cannot share a transaction, and Neo4j is updated in place.

## Decision

**The base graph follows the default branch only.** For each repo, Neo4j and pgvector hold the
graph and embeddings of the default branch head. When that branch moves, `update_repository`
re-parses the touched files and re-resolves their dependents. A dependent is a file with an edge
into a node of a touched file (read from the store before the update), or a file whose imports
resolve to a touched module afterwards (this catches imports of a module the update adds).

**PRs never write to the base.** A review parses the PR's changed files at its head commit into
an in-memory overlay (`build_overlay`). Queries read the base with those files swapped in. Two
overlays on one base do not see each other.

**Every repo shares one database.** Every node carries `repo_id`, uniqueness constraints are on
(`repo_id`, `qualified_name`) per label, and every query filters on `repo_id`.

**One ledger for two stores.** Each index run is a row in the Postgres table `index_runs`:
repo, commit sha, kind (full or incremental), status (running, complete, failed), start,
heartbeat and finish times. A run is inserted as running, writes Neo4j, then pgvector, then is
marked complete. Any error marks it failed. Readers use `latest_complete_run(repo_id)`.

**One run per repo at a time.** A unique partial index on `index_runs (repo_id) WHERE status =
'running'` refuses a second running row. I chose this over a Postgres advisory lock because the
row is both the lock and the record: any process can see it with a plain query, it survives
connection pooling, and it needs no session held open for the length of a run. The cost is that
a crashed run leaves its row behind, which the heartbeat handles.

**Crashed runs.** A run updates `heartbeat_at` between stages and between embedding batches. A
running row whose heartbeat is older than `PRISM_INDEX_RUN_STALE_AFTER_S` (default 900 s) is
taken as crashed. The next run marks it failed and starts. An incremental update whose last run
did not complete becomes a full index, since a crashed run may have left the graph half-written.

**A review waits while its repo is being indexed.** Neo4j is updated in place, so the ledger
alone does not give a reader a consistent snapshot. A reader in the middle of an update could
see a file's new nodes with another file's old edges. The review task calls
`raise_if_indexing(ledger, repo_id)` first and retries on `IndexInProgress`. A stale run does
not block reviews; `needs_full_index` tells the caller to schedule a full index instead.

## Alternative: tag nodes with a run id

Each run could write new nodes and edges tagged with its run id, and readers would filter on the
latest complete run. Old runs would be deleted afterwards. Readers would then never wait. I did
not do this: every query needs the extra filter, a run briefly doubles the graph, and garbage
collection becomes a job of its own. If reviews wait too often, this is the next step.

## Known approximation

The base graph follows the default branch head, which can be newer than a PR's merge base. The
overlay replaces only the files the PR changed, so code that changed on the default branch after
the PR branched shows up in the PR's context as if the PR had it. For short-lived PRs the
difference is small. A review could detect it by comparing the merge base with the commit of the
latest complete run, and say so in its output.

## Consequences

- An index run and a review of the same repo never overlap; a review can be delayed by up to one
  run's length.
- A crashed indexer blocks reviews for at most the staleness limit.
- Incremental updates miss a file whose only link to a change is a name match the change creates
  (ADR 0004). A periodic full index corrects that.
