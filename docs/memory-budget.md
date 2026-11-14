# Memory budget

Measured 2026-11-11 on the build machine (Windows 11, 15.6 GB RAM, Docker capped at 6 GB through
`.wslconfig`) with `docker stats --no-stream` and `ollama ps`, right after `make up` and one run
of `make test`. The services were idle and held no project data, so these are floors, not working
figures. Re-measure after Phase 1 indexes a real repo.

| Service | Memory used | Limit set in Compose |
|---|---|---|
| Postgres + pgvector | 48 MiB | 512 MiB |
| Neo4j Community (heap 512 MB, page cache 256 MB) | 889 MiB | 1500 MiB |
| Redis | 4 MiB | 256 MiB |
| **Compose total** | **about 0.95 GiB** | 2.3 GiB |
| Ollama on the host, `qwen2.5-coder:3b` (the default), CPU, 4096-token context | 2.2 GB | none |
| Ollama on the host, `qwen2.5-coder:1.5b`, CPU, 4096-token context | 1.2 GB | none |

Only one of the two models is loaded at a time.

Not yet measured: the Celery worker, the API, and Prometheus and Grafana (the `ops` profile).

## Langfuse profile (Phase 5)

Measured 2026-11-14 with `docker stats --no-stream` right after three traced reviews, with only
the `langfuse` profile running (`docker compose --profile langfuse up -d --wait`). Langfuse
4.72.0, ClickHouse 26.3.

| Service | Memory used | Limit set in Compose |
|---|---|---|
| langfuse-web (Node heap capped at 1 GB) | 724 MiB | 1536 MiB |
| langfuse-worker | 363 MiB | 768 MiB |
| langfuse-clickhouse | 650 MiB | 1500 MiB |
| langfuse-postgres | 96 MiB | 256 MiB |
| langfuse-minio | 68 MiB | 256 MiB |
| langfuse-redis | 12 MiB | 128 MiB |
| **Profile total** | **about 1.9 GiB** | 4.3 GiB |

With the default stack above that is about 2.9 GiB in use of Docker's 6 GB. The limits add up to
more than 6 GB, so starting everything at once relies on the services staying well below their
limits; start the profile only when looking at traces. Under a 1 GB limit `langfuse-web` ran
out of JavaScript heap while starting, which is why its limit is 1.5 GB with
`--max-old-space-size=1024`.
