"""Postgres connection and schema migrations.

Migrations are plain SQL, numbered, applied in order and recorded in
`schema_migrations`. `migrate` takes a transaction-scoped advisory lock, so two
processes starting at once do not apply the same migration twice.
"""

import psycopg

from prism.config import Settings, get_settings

# Append only. Never edit a migration that has shipped; add a new one.
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE EXTENSION IF NOT EXISTS vector;

        -- No fixed dimension: the embedder decides it. Search is exact (ADR 0005).
        CREATE TABLE IF NOT EXISTS function_embeddings (
            repo_id        TEXT NOT NULL,
            qualified_name TEXT NOT NULL,
            path           TEXT NOT NULL,
            embedder       TEXT NOT NULL,
            content_hash   TEXT NOT NULL DEFAULT '',
            embedding      vector NOT NULL,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (repo_id, qualified_name)
        );
        CREATE INDEX IF NOT EXISTS function_embeddings_path
            ON function_embeddings (repo_id, path);

        CREATE TABLE IF NOT EXISTS index_runs (
            id           BIGSERIAL PRIMARY KEY,
            repo_id      TEXT NOT NULL,
            commit_sha   TEXT NOT NULL,
            kind         TEXT NOT NULL CHECK (kind IN ('full', 'incremental')),
            status       TEXT NOT NULL CHECK (status IN ('running', 'complete', 'failed')),
            started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at  TIMESTAMPTZ,
            error        TEXT
        );
        -- At most one running run per repo: the row is the lock (ADR 0006).
        CREATE UNIQUE INDEX IF NOT EXISTS index_runs_one_running
            ON index_runs (repo_id) WHERE status = 'running';
        CREATE INDEX IF NOT EXISTS index_runs_repo_started
            ON index_runs (repo_id, started_at DESC);
        """,
    ),
    (
        2,
        """
        -- Reviews and every verifier decision in them (ADR 0015).
        CREATE TABLE IF NOT EXISTS reviews (
            id          BIGSERIAL PRIMARY KEY,
            key         TEXT NOT NULL UNIQUE,  -- repo#pr#head_sha#diff_hash
            repo        TEXT NOT NULL,
            pr_number   INT,
            head_sha    TEXT NOT NULL,
            status      TEXT NOT NULL CHECK (status IN ('awaiting_verification', 'complete')),
            verdict     TEXT,
            summary     TEXT NOT NULL DEFAULT '',
            cost_usd    DOUBLE PRECISION NOT NULL DEFAULT 0,
            duration_s  DOUBLE PRECISION NOT NULL DEFAULT 0,
            trace_id    TEXT,
            work_dir    TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS reviews_repo_pr ON reviews (repo, pr_number, created_at DESC);

        CREATE TABLE IF NOT EXISTS review_findings (
            id                BIGSERIAL PRIMARY KEY,
            review_id         BIGINT NOT NULL REFERENCES reviews (id) ON DELETE CASCADE,
            path              TEXT NOT NULL,
            line              INT NOT NULL,
            hunk_header       TEXT NOT NULL,
            hunk              TEXT NOT NULL,
            author            TEXT NOT NULL,
            comment           TEXT NOT NULL,
            posted            BOOLEAN NOT NULL,
            decision          TEXT NOT NULL,
            verifier_note     TEXT NOT NULL DEFAULT '',
            severity          TEXT,
            category          TEXT,
            confidence        DOUBLE PRECISION,
            evidence          JSONB NOT NULL DEFAULT '[]',
            suggested_fix     TEXT,
            tools             JSONB NOT NULL DEFAULT '[]',
            github_comment_id BIGINT
        );
        CREATE INDEX IF NOT EXISTS review_findings_review ON review_findings (review_id);
        """,
    ),
)

_LOCK_KEY = 0x50524953  # "PRIS"


def connect(settings: Settings | None = None, *, timeout: int = 5) -> psycopg.Connection:
    s = settings or get_settings()
    return psycopg.connect(s.postgres_dsn, autocommit=True, connect_timeout=timeout)


def migrate(conn: psycopg.Connection) -> list[int]:
    """Apply pending migrations; returns the versions applied."""
    applied: list[int] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for version, sql in MIGRATIONS:
            if version in done:
                continue
            conn.execute(sql.encode())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied.append(version)
    return applied
