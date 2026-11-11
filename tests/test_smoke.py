"""Phase 0 smoke test: every backing service answers.

Needs `make up` and the host Ollama with the reviewer model pulled. CI has
neither, so it runs with `-m "not smoke"`.
"""

import httpx
import psycopg
import pytest
import redis
from neo4j import GraphDatabase

from prism.config import get_settings

pytestmark = pytest.mark.smoke


def test_postgres_has_pgvector() -> None:
    with psycopg.connect(get_settings().postgres_dsn, connect_timeout=5) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        row = conn.execute("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector").fetchone()
    assert row is not None
    assert row[0] == pytest.approx(1.0)


def test_neo4j_creates_constraint() -> None:
    s = get_settings()
    auth = (s.neo4j_user, s.neo4j_password.get_secret_value())
    with GraphDatabase.driver(s.neo4j_uri, auth=auth) as driver:
        driver.execute_query(
            "CREATE CONSTRAINT smoke_unique IF NOT EXISTS "
            "FOR (n:SmokeTest) REQUIRE (n.repo_id, n.qualified_name) IS UNIQUE"
        )
        names = driver.execute_query("SHOW CONSTRAINTS YIELD name RETURN name").records
        driver.execute_query("DROP CONSTRAINT smoke_unique IF EXISTS")
    assert "smoke_unique" in [r["name"] for r in names]


def test_redis_pings() -> None:
    client = redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=5)
    try:
        assert client.ping()
    finally:
        client.close()


def test_host_ollama_completes() -> None:
    s = get_settings()
    response = httpx.post(
        f"{s.ollama_url}/api/generate",
        json={
            "model": s.reviewer_model,
            "prompt": "Reply with the single word: ok",
            "stream": False,
            "options": {"num_predict": 8},
        },
        timeout=300,
    )
    response.raise_for_status()
    assert response.json()["response"].strip()
