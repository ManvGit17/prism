# Learning log

## Phase 0: Scaffolding (2026-11-11)

**What was built.** An empty but working project. `uv` manages Python 3.12 and the dependencies.
Docker Compose runs three services: Postgres with the pgvector extension (for embeddings and
ordinary tables), Neo4j (for the code graph) and Redis (for the job queue later). Ollama runs on
Windows itself, outside Docker, and serves the base reviewer model `qwen2.5-coder:1.5b`. A smoke
test proves each of the four answers. The Pydantic schemas in `prism/schemas.py` define what a
review looks like before any code produces one.

**The idea worth remembering.** There are two comment schemas. `RawComment` is what the model
writes: a line number and a comment. `ReviewComment` is what gets posted, and only the verifier
can make one, because it adds the severity and category. The model is never asked for fields it
was not trained to produce.

**Quiz**

1. Why does the reviewer model emit only `{line, comment}` and not a severity?
2. `RawComment` rejects unknown fields. What failure does that catch?
3. Why does Ollama run on the host instead of in a Compose container on this machine?
4. `make test` and `make unit` differ by one pytest marker. Which tests does CI skip, and why?
5. Neo4j's heap is fixed at 512 MB. What would you expect to see if that were too small for a
   large repository, and which setting would you change first?
