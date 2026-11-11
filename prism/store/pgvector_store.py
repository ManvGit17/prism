"""pgvector store for function embeddings.

One row per (repo_id, qualified_name), with the path, the embedder name and
the vector. Search is exact cosine distance over one repo's rows for one
embedder; there is no ANN index (ADR 0005).
"""

import math
from collections.abc import Collection, Iterable, Sequence

from psycopg import Connection
from psycopg.rows import TupleRow

from prism.config import Settings
from prism.store.base import VectorHit, VectorItem
from prism.store.postgres import connect, migrate


def _literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _parse(text: str) -> tuple[float, ...]:
    return tuple(float(x) for x in text.strip("[]").split(",") if x)


class PgVectorStore:
    def __init__(self, conn: Connection[TupleRow], *, embedder: str = "unknown") -> None:
        self._conn = conn
        self.embedder = embedder
        migrate(conn)

    @classmethod
    def from_settings(
        cls, settings: Settings | None = None, *, embedder: str = "unknown"
    ) -> "PgVectorStore":
        return cls(connect(settings), embedder=embedder)

    def close(self) -> None:
        self._conn.close()

    def _dimension(self, repo_id: str) -> int | None:
        row = self._conn.execute(
            "SELECT vector_dims(embedding) FROM function_embeddings "
            "WHERE repo_id = %s AND embedder = %s LIMIT 1",
            (repo_id, self.embedder),
        ).fetchone()
        return None if row is None else int(row[0])

    def upsert(self, repo_id: str, items: Iterable[VectorItem]) -> None:
        batch = list(items)
        if not batch:
            return
        dim = self._dimension(repo_id) or len(batch[0].vector)
        for item in batch:
            if len(item.vector) != dim:
                raise ValueError(f"vector has {len(item.vector)} dimensions, repo uses {dim}")
        rows = [
            (repo_id, i.qualified_name, i.path, self.embedder, i.content_hash, _literal(i.vector))
            for i in batch
        ]
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO function_embeddings "
                "(repo_id, qualified_name, path, embedder, content_hash, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s::vector) "
                "ON CONFLICT (repo_id, qualified_name) DO UPDATE SET "
                "path = EXCLUDED.path, embedder = EXCLUDED.embedder, "
                "content_hash = EXCLUDED.content_hash, embedding = EXCLUDED.embedding, "
                "updated_at = now()",
                rows,
            )

    def remove_files(self, repo_id: str, paths: Iterable[str]) -> None:
        self._conn.execute(
            "DELETE FROM function_embeddings WHERE repo_id = %s AND path = ANY(%s)",
            (repo_id, list(paths)),
        )

    def delete_repo(self, repo_id: str) -> None:
        self._conn.execute("DELETE FROM function_embeddings WHERE repo_id = %s", (repo_id,))

    def repo_ids(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT repo_id FROM function_embeddings ORDER BY repo_id"
        ).fetchall()
        return [r[0] for r in rows]

    def count(self, repo_id: str) -> int:
        row = self._conn.execute(
            "SELECT count(*) FROM function_embeddings WHERE repo_id = %s AND embedder = %s",
            (repo_id, self.embedder),
        ).fetchone()
        return 0 if row is None else int(row[0])

    def get(self, repo_id: str, qualified_name: str) -> VectorItem | None:
        row = self._conn.execute(
            "SELECT path, content_hash, embedding::text FROM function_embeddings "
            "WHERE repo_id = %s AND qualified_name = %s AND embedder = %s",
            (repo_id, qualified_name, self.embedder),
        ).fetchone()
        if row is None:
            return None
        return VectorItem(
            qualified_name=qualified_name, path=row[0], content_hash=row[1], vector=_parse(row[2])
        )

    def search(
        self,
        repo_id: str,
        vector: Sequence[float],
        *,
        k: int = 10,
        exclude_paths: Collection[str] = (),
    ) -> list[VectorHit]:
        if k < 1:
            return []
        rows = self._conn.execute(
            "SELECT qualified_name, path, 1 - (embedding <=> %s::vector) AS score "
            "FROM function_embeddings "
            "WHERE repo_id = %s AND embedder = %s AND NOT (path = ANY(%s)) "
            "ORDER BY embedding <=> %s::vector, qualified_name LIMIT %s",
            (
                _literal(vector),
                repo_id,
                self.embedder,
                list(exclude_paths),
                _literal(vector),
                k,
            ),
        ).fetchall()
        hits = []
        for qn, path, score in rows:
            value = float(score)
            # A zero vector has no direction; pgvector returns NaN, the in-memory store 0.
            hits.append(
                VectorHit(qualified_name=qn, path=path, score=0.0 if math.isnan(value) else value)
            )
        return hits
