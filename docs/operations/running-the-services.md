# Running the services

PRism's supporting services run in Docker Compose: Postgres with pgvector, Neo4j and Redis as the core, and optional profiles for self-hosted Langfuse, the review worker, and Prometheus and Grafana. This page is for whoever installs and looks after them. Ollama is not in Compose: it runs on the host.

## What runs where

| Service | Profile | Purpose | Host port |
|---|---|---|---|
| `postgres` (pgvector, Postgres 17) | core | Embeddings, the index ledger, stored reviews and feedback | 5432 (`PRISM_POSTGRES_PORT`) |
| `neo4j` (5 Community) | core | The code graph | 7474 (browser), 7687 (Bolt) |
| `redis` | core | The review queue's broker and coordination state | 6379 |
| `langfuse-web` | `langfuse` | Langfuse UI and API | 3001 |
| `langfuse-worker`, `langfuse-clickhouse`, `langfuse-minio`, `langfuse-redis`, `langfuse-postgres` | `langfuse` | Langfuse's own storage and workers | MinIO on 9092 |
| `worker`, `beat` | `worker` | The review queue's Celery worker and scheduler | none |
| `prometheus` | `ops` | Metrics | 9090 |
| `grafana` | `ops` | Dashboards | 3000 |

Every port is published on `127.0.0.1` only, so nothing is reachable from other machines. The Langfuse profile has its own Postgres and Redis, separate from the core ones (Langfuse needs Redis with `noeviction`). See [ADR 0002](../adr/0002-host-ollama-and-compose-layout.md) and [ADR 0014](../adr/0014-langfuse-tracing-and-prompts.md).

Ollama runs on the host at `PRISM_OLLAMA_URL` (default `http://127.0.0.1:11434`). Containers reach it at `host.docker.internal`.

## Starting and stopping

You need Docker with Compose and `make`.

```bash
make up            # core: Postgres, Neo4j, Redis, waits until they are healthy
make ps            # what is running
make logs          # the last 100 log lines
make down          # stop and remove the containers; volumes are kept
```

```bash
make langfuse-up   # self-hosted Langfuse, UI on http://localhost:3001
make langfuse-down # stop the Langfuse services (the data is kept)
```

The first Langfuse start creates the `prism` organisation and project with API keys. Open http://localhost:3001 and sign up: you become the owner of the `prism` project. Set `PRISM_TRACING=langfuse` and install the `observability` group (`uv sync --group observability`) to send traces.

```bash
docker compose --profile ops up -d       # Prometheus and Grafana
docker compose --profile worker up -d --build   # review worker and beat in containers
```

!!! note
    The `worker` profile builds the image from the `Dockerfile` and is meant for Linux hosts. It needs a `.env` file, the GitHub App's private key mounted from `PRISM_APP_KEY_FILE` (default `./secrets/prism-app.pem`), and a host Ollama that listens on an address containers can reach (`PRISM_WORKER_OLLAMA_URL`, default `http://host.docker.internal:11434`). PMD is not in the image, so Java files get Semgrep only. On Windows, run `prism app worker` and `prism app beat` on the host instead. See [ADR 0018](../adr/0018-review-queue.md).

## Configuration

Copy `.env.example` to `.env`. The Compose file reads the `PRISM_POSTGRES_*` and `PRISM_NEO4J_*` values from it and falls back to local development defaults. Langfuse takes `LANGFUSE_*` variables (`LANGFUSE_POSTGRES_PASSWORD`, `LANGFUSE_NEXTAUTH_SECRET`, `LANGFUSE_SALT`, `LANGFUSE_ENCRYPTION_KEY`, `LANGFUSE_CLICKHOUSE_PASSWORD`, `LANGFUSE_MINIO_PASSWORD`, `LANGFUSE_REDIS_PASSWORD`) and the project keys `PRISM_LANGFUSE_PUBLIC_KEY` and `PRISM_LANGFUSE_SECRET_KEY`. All settings are listed in the [configuration reference](../reference/configuration.md).

!!! warning
    The default passwords and keys are local development values, not secrets. They are fine for services bound to `127.0.0.1` on your own machine. Set your own in `.env` before exposing anything.

## Volumes

| Volume | Holds |
|---|---|
| `postgres-data` | The core Postgres database |
| `neo4j-data` | The code graph |
| `worker-work` | The worker's work directory (`/app/.prism-work`) |
| `langfuse-postgres-data` | Langfuse's metadata |
| `langfuse-clickhouse-data`, `langfuse-clickhouse-logs` | Langfuse's trace storage |
| `langfuse-minio-data` | Langfuse's event and media storage |

`make down` keeps volumes. `docker compose down -v` deletes them, along with everything in them.

## Memory

The memory use of each service and the limits set in Compose are in the [memory budget](../memory-budget.md). In short, the core services use about 0.95 GiB at rest, the Langfuse profile about 1.9 GiB, and the reviewer model on the host about 2.2 GB for `qwen2.5-coder:3b`. The Compose limits add up to more than a 6 GB Docker allocation, so start the Langfuse profile only when you are looking at traces.

Neo4j's page cache is small (256 MB, heap 512 MB). If indexing a mid-size repository is slow, it is the first setting to raise (`NEO4J_server_memory_pagecache_size` in `docker-compose.yml`).

## What needs what

Most of PRism works with the services stopped. `prism review ... --store memory` indexes the checkout in memory and needs only Ollama, and the MCP server falls back to an in-memory store when Postgres is down. Persistent indexing (`prism index`, the default `PRISM_STORE=persistent`) needs Neo4j and Postgres. The GitHub App's queue (`PRISM_APP_QUEUE=celery`) needs Redis. More on what degrades and how in [Troubleshooting](troubleshooting.md).

## Backups

The data worth keeping is in Postgres (stored reviews, feedback, the index ledger and embeddings) and, if you want to avoid re-indexing, in Neo4j. The graph can always be rebuilt with `prism index PATH`, so Neo4j is the less important of the two. The commands below are the standard tools for each database; adjust names and paths to your setup.

### Postgres

```bash
mkdir -p backups
docker compose exec -T postgres pg_dump -U prism prism > backups/prism.sql
```

To restore into an empty database:

```bash
docker compose exec -T postgres psql -U prism prism < backups/prism.sql
```

Use your own user and database names if you changed `PRISM_POSTGRES_USER` or `PRISM_POSTGRES_DB`.

### Neo4j

Neo4j Community cannot back up a running database, so stop it, dump, and start it again.

```bash
docker compose stop neo4j
docker compose run --rm --no-deps -v "$(pwd)/backups:/backups" neo4j \
  neo4j-admin database dump neo4j --to-path=/backups
docker compose start neo4j
```

Restoring uses `neo4j-admin database load neo4j --from-path=/backups --overwrite-destination` the same way, with Neo4j stopped. Or skip the dump and re-run `prism index`.

### Langfuse

Langfuse keeps its data in its own Postgres, ClickHouse and MinIO volumes. If you need to keep traces and prompts, back up those volumes; prompts can also be pushed again from the code with `prism prompts push`.

## Upgrading

1. Stop anything that is reviewing, and take the backups above.
2. Update the code and the dependencies: `git pull`, then `uv sync` (add the groups you use, such as `--group sast`).
3. Pull newer images if the Compose file changed: `docker compose pull`, then `make up`.
4. Postgres schema changes apply themselves. The stores run pending migrations when they connect and record them in a `schema_migrations` table, under an advisory lock so two processes cannot apply them at once.
5. The Langfuse version is pinned (`LANGFUSE_IMAGE_TAG`, default 4.72.0). To change it, set the variable in `.env` and run `make langfuse-up`. Read Langfuse's release notes first, and keep a backup of its volumes.
6. If the retrieval or embedding model changed, re-run `prism index PATH` so the stored graph and vectors match.
7. For the review worker in containers, rebuild: `docker compose --profile worker up -d --build`.

Check that everything came back with `make ps`, and run a review with `prism review --repo PATH --diff FILE --store memory --mode graph` to check Ollama and the pipeline.
