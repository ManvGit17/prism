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

## Phase 2: Retrieval and the context packer (2026-11-12)

**What was built.** Given a checkout and a diff, `prism context` works out which function each
hunk changes and collects what a reviewer would want to see next to it. From the graph: the
function's signature, the tests that cover it, its callers and callees, and the interface method
it implements. From the vector store: functions that look similar. All of it is ranked, trimmed to
a token budget and printed with token counts.

**Hunk to symbol.** A diff only has line numbers. The per-PR overlay from Phase 1 turns a line
into "this is inside `Text.truncate`". A deleted function no longer exists at the PR head, so it
is looked up in the base graph instead, which is how its callers can still be found.

**Why there is a budget.** A model has a limited context window and a small local model is slow,
so not everything can go in. The packer fills the budget in priority order: signature, contract,
tests, callers, then the rest, with similar code last. Whatever does not fit is recorded as
dropped, with the reason, so nothing disappears silently.

**What the reranker does and does not do.** A cross-encoder reads the hunk and one candidate
together and scores how related they are. That is more accurate than comparing two embeddings but
slower, so it only runs on the shortlist. Here it only orders items inside a priority class. It
never lets a similar function jump ahead of a test, because the model was trained on web text, not
code, and has not been shown to deserve that.

**Counting tokens.** The default counter assumes 4 characters per token. Measured against the
real Qwen tokenizer it is usually a little high, but it undercounts on up to a third of functions
in one repo, so a block can run over the budget by about a quarter. An exact counter is available
as a setting.

**Who gets the context.** Not the fine-tuned reviewer. It was trained on a hunk alone, so it is
served a hunk alone. The context goes to the verifier and to the frontier model.

**Quiz**

1. A hunk deletes a whole function. Where do its callers come from, and why not from the overlay?
2. On one test the packer dropped 6 of the callers of a long function. What used up the budget,
   and name two ways to change that.
3. Why does the reranker only reorder items within a priority class?
4. In `graph+vector` mode on one diff, every "similar" item was dropped. Does that mean vector
   search is useless? What experiment would answer it?
5. The token counter can undercount. What is the risk when the block is sent to a model with a
   small context window, and which setting removes it?
