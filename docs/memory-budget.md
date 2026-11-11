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
