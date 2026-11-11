# ADR 0002: Ollama on the host, three Compose services, ops profile

Date: 2026-11-11. Status: accepted.

## Context

The build machine has 15.6 GB of RAM and no NVIDIA GPU. WSL, and so Docker, is capped at 6 GB in
`.wslconfig`. Ollama is already installed on Windows.

## Decision

- Ollama runs on the host. Containers reach it at `host.docker.internal:11434`; the address is the
  setting `PRISM_OLLAMA_URL`.
- Compose runs Postgres with pgvector, Neo4j Community and Redis, each with a memory limit. Neo4j's
  heap is fixed at 512 MB and its page cache at 256 MB.
- Prometheus and Grafana are in the `ops` profile and stay off until Phase 5.
- Ports are published on `127.0.0.1` only.

## Consequences

- The model's memory is outside Docker's 6 GB.
- Development passwords in `.env.example` are defaults for a local machine, not secrets. A
  self-hosted install must change them (Phase 8 guide).
- Neo4j's page cache is small; revisit if indexing a mid-size repo in Phase 1 is slow.
