"""Index-run ledger in Postgres ("Two stores, one ledger"; ADR 0006).

Neo4j and pgvector cannot share a transaction. An index run is recorded as
running, both stores are written, then the run is marked complete. Readers use
the latest complete run, and a review waits while a run for its repo is in
progress.

Only one run per repo can be running: a unique partial index on
`index_runs (repo_id) WHERE status = 'running'` makes a second insert fail. A
run whose heartbeat is older than `index_run_stale_after_s` is taken as
crashed; the next run marks it failed and starts over.
"""

from datetime import datetime

import psycopg
from psycopg import Connection
from psycopg.rows import TupleRow
from pydantic import BaseModel, ConfigDict

from prism.config import Settings, get_settings
from prism.store.postgres import connect, migrate

_COLUMNS = "id, repo_id, commit_sha, kind, status, started_at, heartbeat_at, finished_at, error"


class IndexRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    repo_id: str
    commit_sha: str
    kind: str
    status: str
    started_at: datetime
    heartbeat_at: datetime
    finished_at: datetime | None = None
    error: str | None = None


class IndexInProgress(Exception):
    """An index run for the repo is running. A review task retries later."""

    def __init__(self, repo_id: str, run: IndexRun | None = None) -> None:
        self.repo_id = repo_id
        self.run = run
        detail = f" (run {run.id} since {run.started_at:%Y-%m-%d %H:%M:%S})" if run else ""
        super().__init__(f"an index run for {repo_id!r} is in progress{detail}")


def _run(row: tuple[object, ...]) -> IndexRun:
    keys = _COLUMNS.split(", ")
    return IndexRun.model_validate(dict(zip(keys, row, strict=True)))


class IndexLedger:
    def __init__(self, conn: Connection[TupleRow], *, stale_after_s: int | None = None) -> None:
        self._conn = conn
        self.stale_after_s = (
            stale_after_s if stale_after_s is not None else get_settings().index_run_stale_after_s
        )
        migrate(conn)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "IndexLedger":
        s = settings or get_settings()
        return cls(connect(s), stale_after_s=s.index_run_stale_after_s)

    def close(self) -> None:
        self._conn.close()

    def _one(self, sql: str, params: tuple[object, ...]) -> IndexRun | None:
        row = self._conn.execute(sql, params).fetchone()
        return None if row is None else _run(row)

    def start(self, repo_id: str, commit_sha: str, *, kind: str = "full") -> IndexRun:
        """Record a new running run. Raises IndexInProgress if a live run exists.

        A running run with a stale heartbeat is marked failed first, so a crashed
        run never blocks the repo for longer than the staleness limit.
        """
        run: IndexRun | None = None
        blocker: IndexRun | None = None
        with self._conn.transaction():
            self.expire_stale(repo_id)
            try:
                with self._conn.transaction():
                    run = self._one(
                        f"INSERT INTO index_runs (repo_id, commit_sha, kind, status) "
                        f"VALUES (%s, %s, %s, 'running') RETURNING {_COLUMNS}",
                        (repo_id, commit_sha, kind),
                    )
            except psycopg.errors.UniqueViolation:
                blocker = self.running(repo_id)
        if run is None:
            raise IndexInProgress(repo_id, blocker)
        return run

    def expire_stale(self, repo_id: str) -> list[int]:
        rows = self._conn.execute(
            "UPDATE index_runs SET status = 'failed', finished_at = now(), "
            "error = 'stale: no heartbeat for ' || %s || ' s' "
            "WHERE repo_id = %s AND status = 'running' "
            "AND heartbeat_at < now() - make_interval(secs => %s) RETURNING id",
            (self.stale_after_s, repo_id, self.stale_after_s),
        ).fetchall()
        return [int(r[0]) for r in rows]

    def heartbeat(self, run: IndexRun) -> None:
        self._conn.execute(
            "UPDATE index_runs SET heartbeat_at = now() WHERE id = %s AND status = 'running'",
            (run.id,),
        )

    def complete(self, run: IndexRun) -> None:
        self._finish(run, "complete", None)

    def fail(self, run: IndexRun, error: str) -> None:
        self._finish(run, "failed", error[:2000])

    def _finish(self, run: IndexRun, status: str, error: str | None) -> None:
        self._conn.execute(
            "UPDATE index_runs SET status = %s, finished_at = now(), error = %s "
            "WHERE id = %s AND status = 'running'",
            (status, error, run.id),
        )

    # Reads

    def running(self, repo_id: str) -> IndexRun | None:
        """The live running run, if any. A stale one does not count."""
        return self._one(
            f"SELECT {_COLUMNS} FROM index_runs WHERE repo_id = %s AND status = 'running' "
            "AND heartbeat_at >= now() - make_interval(secs => %s)",
            (repo_id, self.stale_after_s),
        )

    def index_in_progress(self, repo_id: str) -> bool:
        return self.running(repo_id) is not None

    def latest_complete_run(self, repo_id: str) -> IndexRun | None:
        return self._one(
            f"SELECT {_COLUMNS} FROM index_runs WHERE repo_id = %s AND status = 'complete' "
            "ORDER BY finished_at DESC, id DESC LIMIT 1",
            (repo_id,),
        )

    def latest_run(self, repo_id: str) -> IndexRun | None:
        return self._one(
            f"SELECT {_COLUMNS} FROM index_runs WHERE repo_id = %s "
            "ORDER BY started_at DESC, id DESC LIMIT 1",
            (repo_id,),
        )

    def needs_full_index(self, repo_id: str) -> bool:
        """True when the store cannot be trusted: no run yet, or the last one did not finish."""
        latest = self.latest_run(repo_id)
        return latest is None or latest.status != "complete"

    def delete_runs(self, repo_id: str) -> None:
        """Forget a repo's runs. For tests and for removing a repo."""
        self._conn.execute("DELETE FROM index_runs WHERE repo_id = %s", (repo_id,))


def raise_if_indexing(ledger: IndexLedger, repo_id: str) -> None:
    """Raise IndexInProgress while the repo is being indexed. A review task calls this first
    and retries on the exception."""
    run = ledger.running(repo_id)
    if run is not None:
        raise IndexInProgress(repo_id, run)
