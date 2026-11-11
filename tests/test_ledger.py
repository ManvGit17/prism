"""Index-run ledger in Postgres. Needs the Compose services."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest
from repo_ids import repo_id

from prism.indexer.embedder import FakeEmbedder
from prism.indexer.pipeline import index_repository, update_repository
from prism.store.ledger import IndexInProgress, IndexLedger, raise_if_indexing
from prism.store.neo4j_store import Neo4jGraphStore
from prism.store.pgvector_store import PgVectorStore
from prism.store.postgres import connect

pytestmark = pytest.mark.smoke


@pytest.fixture
def rid(request: pytest.FixtureRequest) -> Iterator[str]:
    rid = repo_id(f"ledger-{request.node.name}")
    yield rid
    with _ledger() as ledger:
        ledger.delete_runs(rid)


@contextmanager
def _ledger() -> Iterator[IndexLedger]:
    ledger = IndexLedger.from_settings()
    try:
        yield ledger
    finally:
        ledger.close()


@pytest.fixture
def ledger() -> Iterator[IndexLedger]:
    with _ledger() as lg:
        yield lg


def _age_heartbeat(run_id: int, seconds: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE index_runs SET heartbeat_at = now() - make_interval(secs => %s) WHERE id = %s",
            (seconds, run_id),
        )


def test_complete_run_is_visible(ledger: IndexLedger, rid: str) -> None:
    assert ledger.latest_complete_run(rid) is None
    assert ledger.needs_full_index(rid)
    run = ledger.start(rid, "abc123")
    assert ledger.index_in_progress(rid)
    ledger.heartbeat(run)
    ledger.complete(run)
    latest = ledger.latest_complete_run(rid)
    assert latest is not None
    assert (latest.id, latest.commit_sha, latest.status) == (run.id, "abc123", "complete")
    assert latest.finished_at is not None
    assert not ledger.index_in_progress(rid)
    assert not ledger.needs_full_index(rid)
    raise_if_indexing(ledger, rid)  # does not raise


def test_running_run_blocks_readers_and_a_second_run(ledger: IndexLedger, rid: str) -> None:
    run = ledger.start(rid, "abc123")
    with pytest.raises(IndexInProgress) as caught:
        raise_if_indexing(ledger, rid)
    assert caught.value.run is not None
    assert caught.value.run.id == run.id
    # A second writer on its own connection is refused while the first is live.
    with _ledger() as other, pytest.raises(IndexInProgress):
        other.start(rid, "def456")
    ledger.fail(run, "boom")
    assert not ledger.index_in_progress(rid)
    assert ledger.needs_full_index(rid)


def test_stale_run_is_expired_and_redone(ledger: IndexLedger, rid: str) -> None:
    crashed = ledger.start(rid, "abc123")
    _age_heartbeat(crashed.id, ledger.stale_after_s + 60)
    # A crashed run does not block readers, but the store needs a full index.
    assert not ledger.index_in_progress(rid)
    assert ledger.needs_full_index(rid)
    redo = ledger.start(rid, "abc123")
    assert redo.id != crashed.id
    old = ledger.latest_run(rid)
    assert old is not None and old.id == redo.id
    with connect() as conn:
        row = conn.execute(
            "SELECT status, error FROM index_runs WHERE id = %s", (crashed.id,)
        ).fetchone()
    assert row is not None
    assert row[0] == "failed"
    assert "stale" in row[1]
    ledger.complete(redo)


class _Exploding(FakeEmbedder):
    @property
    def name(self) -> str:
        return "exploding"  # a new model name, so every function is re-embedded

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        raise RuntimeError("embedder down")


def test_index_repository_records_runs(ledger: IndexLedger, rid: str, sample_repo: Path) -> None:
    graph = Neo4jGraphStore.from_settings()
    embedder = FakeEmbedder(dimension=32)
    vectors = PgVectorStore.from_settings(embedder=embedder.name)
    try:
        report = index_repository(
            sample_repo,
            rid,
            graph,
            vectors=vectors,
            embedder=embedder,
            ledger=ledger,
            commit_sha="sha-1",
        )
        latest = ledger.latest_complete_run(rid)
        assert latest is not None and latest.id == report.run_id
        assert vectors.count(rid) == graph.stats(rid).functions == 29

        # A failure marks the run failed and leaves nothing running.
        with pytest.raises(RuntimeError):
            index_repository(
                sample_repo,
                rid,
                graph,
                vectors=vectors,
                embedder=_Exploding(32),
                ledger=ledger,
                commit_sha="sha-2",
            )
        last = ledger.latest_run(rid)
        assert (
            last is not None and last.status == "failed" and "embedder down" in (last.error or "")
        )
        assert not ledger.index_in_progress(rid)

        # After a failed run an incremental update is redone as a full index.
        redo = update_repository(
            sample_repo,
            rid,
            ["shop/pricing.py"],
            [],
            graph,
            vectors=vectors,
            embedder=embedder,
            ledger=ledger,
            commit_sha="sha-3",
        )
        assert redo.kind == "full"
        done = ledger.latest_complete_run(rid)
        assert done is not None and done.commit_sha == "sha-3"
        # Now an incremental update stays incremental.
        again = update_repository(
            sample_repo,
            rid,
            ["shop/pricing.py"],
            [],
            graph,
            vectors=vectors,
            embedder=embedder,
            ledger=ledger,
            commit_sha="sha-4",
        )
        assert again.kind == "incremental"
        assert vectors.count(rid) == 29
    finally:
        graph.delete_repo(rid)
        vectors.delete_repo(rid)
        graph.close()
        vectors.close()
