# ADR 0005: Function embeddings with nomic-embed-text on the host Ollama, exact search

Date: 2026-11-11. Status: accepted for now; the model choice is not evaluated.

## Context

Phase 2 retrieval finds functions similar to a changed one, to surface duplicated logic or the
same bug elsewhere. Each function needs an embedding in pgvector. The build machine has no
NVIDIA GPU and about 2.5 GB of free memory, and Ollama already runs on the host. PLAN.md
suggested `jinaai/jina-embeddings-v2-base-code` or the current best small code embedder.

## Decision

- **Model:** `nomic-embed-text` served by the host Ollama (`PRISM_EMBEDDING_MODEL`). It is 274 MB
  on disk and 376 MB loaded, has 768 dimensions, and reads 2048 tokens.
- **Why not a code model:** on 2026-11-11 the Ollama library's embedding category listed 14
  models, and none was described as made for code search. The Jina code model is not in the
  Ollama library. Running it would mean a second runtime (PyTorch or ONNX), which this project
  avoids. If a maintained code embedder appears in the Ollama library, switching is a setting
  change plus a full re-index.
- **What is embedded:** each function's signature and body. The docstring is the first
  statement of the body, so it is included. Decorators and the module path are left out. Text is
  cut to `PRISM_EMBEDDING_MAX_CHARS` (6000, roughly 2000 tokens of code), and Ollama truncates
  whatever still exceeds the context (`truncate: true`). Each text gets the `search_document: `
  prefix nomic expects (`PRISM_EMBEDDING_DOCUMENT_PREFIX`; set it to empty for other models).
- **Calls:** `POST /api/embed` with batches of `PRISM_EMBEDDING_BATCH_SIZE` (16). Vectors are
  normalised to unit length.
- **Storage:** table `function_embeddings`, keyed by (repo_id, qualified_name), with path,
  embedder name (`ollama:nomic-embed-text`), a hash of the embedded text, and the vector. The
  column has no fixed dimension, so a model change needs no migration. Search filters on repo and
  embedder name, so vectors from two models are never compared.
- **Exact search, no ANN index.** Search is `ORDER BY embedding <=> query` over one repo's rows.
  A repo has thousands to tens of thousands of functions, which an exact scan handles. An HNSW
  index needs a fixed-dimension column, costs memory in a 512 MB Postgres container, and returns
  approximate results. Add one when a repo passes about 50,000 functions or search becomes
  slow.

## Measured

The first runs on `smms` (325 functions) took 330 s and 397 s, about 1 s per function (ledger runs
10 and 11). `eval/embedding_speed.py` later measured, on the same machine
(`eval/reports/embedding_speed.jsonl`):

- one request through `localhost` took 0.47 s against 0.054 s through `127.0.0.1`: on Windows,
  `localhost` tries IPv6 first and Ollama listens on IPv4 only. The default URL is now
  `127.0.0.1` (changed in stage 2);
- a cold full index embedded all 325 functions in 50.8 s, 156 ms per function, at batch size 16;
- a warm full index, nothing changed, embedded nothing and took 0.48 s, because functions whose
  text hash is unchanged are skipped.

The 1 s per function of the first runs was not reproduced. The `localhost` delay explains only
part of it (21 batches at about 0.4 s); memory pressure at the time is a likely cause but was not
measured. A scratch probe of request shapes showed batch size 1, 16 or 32 and `num_ctx` 2048 all
within about 30% of each other, so batch size is left at 16. Requests now pass `keep_alive: 10m`
so the model stays loaded between batches and runs.

## Not compared

No model was evaluated on code retrieval. Not tried: `qwen3-embedding:0.6b`, `embeddinggemma`,
`bge-m3`, `snowflake-arctic-embed2`, `mxbai-embed-large`, `granite-embedding`, the Jina code
model, and the effect of the task prefix or of embedding the qualified name. Retrieval quality
should be measured in Phase 7 before this choice is treated as settled.

## Consequences

- A first index embeds every function: about 2.6 minutes per 1,000 functions on this CPU at the
  measured rate. Later full and incremental indexes embed only functions whose text (or model)
  changed; vectors of functions that no longer exist are removed.
- Embedding needs the host Ollama to be running. `prism index --no-embed` skips it.
