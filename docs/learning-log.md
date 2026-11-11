# Learning log

## Phase 0: Scaffolding (2026-11-11)

**What was built.** An empty but working project. `uv` manages Python 3.12 and the dependencies.
Docker Compose runs three services: Postgres with the pgvector extension (for embeddings and
ordinary tables), Neo4j (for the code graph) and Redis (for the job queue later). Ollama runs on
Windows itself, outside Docker, and serves the base reviewer model `qwen2.5-coder:3b`. A smoke
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

## Phase 1: Code indexer and knowledge graph (2026-11-11)

**What was built.** PRism can now read a Python or Java repository and turn it into a graph.
tree-sitter parses each file into an AST, a tree of the code's structure. From that tree the
indexer pulls out files, classes and functions as nodes, and connects them with edges: `DEFINES`,
`IMPORTS`, `CALLS`, `TESTS`, `INHERITS` and `OVERRIDES`. The graph is stored in Neo4j, and each
function is also embedded into pgvector for similarity search.

**Why tree-sitter and not regex.** A regex sees text, so it cannot tell a call from a mention in
a comment or a string, and it breaks on nesting. A parser knows which function a line sits inside
and what is actually being called.

**Call resolution is a guess with a score.** Working out which function a call refers to is
exact only when you can see the definition: same file, same class, or an import. Otherwise the
indexer falls back to matching by name, which is often wrong for names like `get` or `run`. So
every `CALLS` edge carries a confidence, and edges below 0.5 are left out of queries by default.
On the two libraries checked, 85% of Java call edges and 67% of Python ones were above the line.
Java scores higher because declared types say what a receiver is.

**Base graph plus overlay.** Neo4j holds only the default branch. A pull request never writes to
it. Its changed files are parsed into an in-memory overlay, and queries answer from the base
graph with those files swapped out. Two open PRs therefore cannot disturb each other.

**The ledger.** Neo4j and Postgres cannot commit together, so each indexing run is a row in a
Postgres table: running, then complete. A review waits while a run for its repo is in progress.

**Why a graph and not only vectors.** A vector search finds code that looks similar. It cannot
answer "who calls this function" or "which tests cover it", because those are relationships, not
similarities. A change to a function is risky because of its callers, and those can look nothing
like it.

**Quiz**

1. A call `items.add(x)` appears in a Java class that also defines its own `add` method. Why
   does the indexer not link them, and what would go wrong if it did?
2. Two PRs change the same file in different ways. Explain how each gets correct "callers of X"
   answers at the same time.
3. An edge is stored with resolution `name_unique` and confidence 0.6. What does that tell you,
   and what change elsewhere in the repo could make it wrong tomorrow?
4. Indexing crashed halfway through writing Neo4j. What does the ledger show, and what happens
   on the next run?
5. The hand-check found two wrong answers out of ten, both in Java. What do they have in common,
   and what would it take to fix them properly?
