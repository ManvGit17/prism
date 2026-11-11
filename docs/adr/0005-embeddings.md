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

Indexing `smms` (28 files, 325 functions) into Neo4j and pgvector took about 330 s, and almost all
of that was embedding on the CPU (about 1 s per function). Parsing and resolution of the same
repo take about 2.5 s. This is one run on one machine, not a benchmark.

## Not compared

No model was evaluated on code retrieval. Not tried: `qwen3-embedding:0.6b`, `embeddinggemma`,
`bge-m3`, `snowflake-arctic-embed2`, `mxbai-embed-large`, `granite-embedding`, the Jina code
model, and the effect of the task prefix or of embedding the qualified name. Retrieval quality
should be measured in Phase 7 before this choice is treated as settled.

## Consequences

- A full index re-embeds every function. For a mid-size repo this takes tens of minutes on this
  CPU. Incremental updates embed only the changed files. The stored text hash would let a full
  index skip unchanged functions; that is not built yet.
- Embedding needs the host Ollama to be running. `prism index --no-embed` skips it.
