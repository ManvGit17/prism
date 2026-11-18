# Code graph and retrieval

This page explains how PRism turns a repository into a code graph and a set of embeddings, and
how it uses them to build the context that the verifier reads for each changed hunk. It is for
readers who want to know what "repo-aware" means in practice and which settings control it. The
decisions are in [ADR 0004](../adr/0004-call-resolution.md),
[0005](../adr/0005-embeddings.md), [0006](../adr/0006-index-consistency.md) and
[0007](../adr/0007-retrieval-and-packing.md).

## Indexing

`prism index PATH` indexes a repository: it parses the files, builds the graph, writes it to the
store and embeds the functions. The graph covers Python and Java; other files have no parser and
get no graph context.

```bash
prism index path/to/repo          # graph in Neo4j, embeddings in pgvector
prism index path/to/repo --no-embed
prism index path/to/repo --store memory
```

The stages are:

1. **Discover.** Walk the tree and keep files a parser handles. Hidden folders and the usual
   build and environment folders (`node_modules`, `venv`, `build`, `target`, `generated` and
   similar) are skipped, and so are files over 2 MB.
2. **Parse.** Each file is parsed with tree-sitter (`tree-sitter-python`, `tree-sitter-java`)
   into nodes and call sites.
3. **Build a symbol table and resolve calls.** Each call site is matched to a definition.
4. **Write.** One `FileGraph` per file replaces that file's nodes and edges in the store.
5. **Embed.** Functions are embedded and written to pgvector.

### Nodes and edges

Nodes are files, classes and functions (methods are functions). A node is identified by its
`repo_id` and a qualified name:

- a file is its repo-relative path, such as `shop/cart.py`;
- a class or function is its module plus its nesting, such as `shop.cart.Cart.add`;
- in Java a method name carries its erased parameter types, such as
  `com.acme.shop.Cart.add(Item,int)`, so overloads have distinct names.

Edges:

| Edge | From and to |
|---|---|
| `DEFINES` | a file, class or function to the class or function defined directly inside it |
| `IMPORTS` | a file to what it imports |
| `CALLS` | a function to the function or class it calls |
| `TESTS` | a test function to each non-test function or class it calls directly |
| `INHERITS` | a class to each repository class or interface it extends or implements |
| `OVERRIDES` | a method to the nearest supertype method it overrides or implements |

An edge belongs to the file of its source node, so replacing a file replaces the edges that start
in it. In Neo4j every node has the label `Node` plus `File`, `Class` or `Function`, with a
uniqueness constraint on `(repo_id, qualified_name)`, so all repositories share one database and
every query filters on `repo_id`.

You can query the graph from the command line:

```bash
prism graph callers Cart.add
prism graph callees Cart.add
prism graph tests Cart.add
prism graph neighbours Cart.add
```

### Call resolution and confidence

Python has no static types to say what `x.save()` calls, so some edges are guesses. The resolver
tries a fixed order of strategies, and each `CALLS` edge records how it was found and a
confidence from a fixed band:

| Resolution | Confidence | Meaning |
|---|---|---|
| `same_file` | 0.95 | A name defined in the same file or an enclosing scope |
| `self_method` | 0.9 | `self.f()` on the enclosing class |
| `import` | 0.9 | Resolved through the file's imports |
| `same_package` (Java) | 0.85 | A type in the same package |
| `inherited` | 0.8 | Found on a base class or interface in the repository |
| `star_import` | 0.7 | Found in a module the file star-imports |
| `name_unique` | 0.6 | The only function with that name in the repository |
| `name_ambiguous` | 0.3 | 2 to 10 candidates with that name |
| `name_common` | 0.1 | A common name (`get`, `run`, `save`, `size`) or a dunder |

A name with more than 10 candidates gets no edge. Calls to builtins and to names imported from
outside the repository get no edge. For a `TESTS` edge, the confidence is copied from the `CALLS`
edge it comes from.

Graph queries leave out `CALLS` and `TESTS` edges below `PRISM_CALL_CONFIDENCE_THRESHOLD` (default
0.5). With that default, a unique name match is kept and ambiguous or common-name matches are
left out. All edges are stored, so a caller can lower the threshold for one query.

The bands are fixed estimates, not measured against ground truth. The limits of the resolver
(dynamic dispatch, reflection, functions passed as values, Lombok, dependency injection and
others) are listed in [ADR 0004](../adr/0004-call-resolution.md).

### Embeddings

Each function's signature and body (the docstring is the first statement of the body) is embedded
with `nomic-embed-text` served by the host Ollama (`PRISM_EMBEDDING_MODEL`). Text is cut to
`PRISM_EMBEDDING_MAX_CHARS` (6000), prefixed with `search_document: `, sent in batches of
`PRISM_EMBEDDING_BATCH_SIZE` (16) and normalised to unit length. The model has 768 dimensions.

Vectors live in the Postgres table `function_embeddings`, keyed by repository and qualified name,
with the path, the embedder name, a hash of the embedded text and the vector. Search is an exact
scan, `ORDER BY embedding <=> query`, filtered by repository and embedder name, so vectors from
two models are never compared. I chose an exact scan over an HNSW index because a repository has
thousands to tens of thousands of functions, and an index would add memory use and approximate
results; I would add one past about 50,000 functions.

An index run embeds only functions whose text hash (which includes the model name) changed, and
removes vectors of functions that no longer exist. A first index embeds everything, which takes
the longest part of the run; later runs embed what changed. Embedding needs the host Ollama to be
running and `nomic-embed-text` pulled; `--no-embed` skips it.

### The index ledger

Neo4j and Postgres cannot share a transaction, and Neo4j is updated in place. A ledger in the
Postgres table `index_runs` keeps the two consistent:

- Every run is a row with the repository, commit SHA, kind (`full` or `incremental`), status
  (`running`, `complete` or `failed`) and start, heartbeat and finish times. A run is inserted as
  `running`, writes Neo4j, then pgvector, and is marked `complete`. Any error marks it `failed`.
  Readers use the latest complete run.
- A unique partial index allows one `running` row per repository, so two indexers cannot work on
  one repository at once.
- A run updates its heartbeat between stages and between embedding batches. A `running` row with
  no heartbeat for `PRISM_INDEX_RUN_STALE_AFTER_S` (default 900) is treated as crashed: the next
  run marks it failed and starts. An incremental update after a run that did not complete becomes
  a full index.
- Neo4j is updated in place, so a reader in the middle of an update could see one file's new nodes
  next to another file's old edges. The ledger exposes `raise_if_indexing` for a reader to wait on
  an update in progress; a stale run does not count as in progress.

### Keeping the graph current

The base graph follows the default branch only. When that branch moves, an incremental update
re-parses the changed files, removes the deleted ones and re-resolves the files that depend on
them: files with an edge into a changed file, files whose imports now resolve to a changed module
and, for Java, every file in the same package or importing a changed type.

A pull request never writes to the base. A review parses the changed files at the head commit into
an in-memory overlay, and queries read the base with those files swapped in. Two overlays on one
base do not see each other.

!!! note
    The base follows the default branch head, which can be newer than the pull request's merge
    base. Code that changed on the default branch after the pull request branched shows up in the
    context as if the pull request had it. For short-lived pull requests the difference is small.

## Context retrieval

The local reviewer sees only the hunk. The verifier and Claude see the hunk plus retrieved
context, and the context step builds it for every hunk that has a comment to check or was
escalated (`prism/retrieval/`). You can run it on its own:

```bash
prism context path/to/repo change.diff --mode graph --render
```

### From hunk to symbol

Each added line maps to the innermost function or class around it at the head. A hunk that only
removes lines maps to the symbol around the removed block. Removed lines are also looked up in the
base graph at the old path: a base symbol the head no longer has was deleted or renamed, and its
callers and tests come from the base graph. A hunk outside any symbol is `module` (vector context
only), and a file without a parser is `non_code` (no context).

### What is collected

For each changed symbol, in the order they are packed:

| Kind | What |
|---|---|
| `signature` | The symbol's own code at the head; a long symbol shows its first lines and a window around the hunk |
| `contract` | The supertype method it overrides or implements |
| `test` | Tests that call it directly |
| `caller` | Callers of it, plus callers of its contract method; two hops with `PRISM_CONTEXT_HOPS=2` |
| `implementation` | Methods that override or implement it |
| `class` | Its class header and the 12 member signatures nearest the change |
| `callee` | What it calls |
| `class_test` | Tests of its class, only when it has no direct test; the best 3 are kept |
| `similar` | The functions most similar to the hunk by vector search, outside the changed files and not already found (`PRISM_CONTEXT_VECTOR_K`, default 8) |

The order puts what the verifier needs first: the code itself, what it must keep doing, the tests
that pin it and the callers that depend on it. Class summaries and callees explain the symbol but
show less of the change's impact, and a class summary of a large class ranked higher pushed every
caller out of the budget on the benchmark repositories.

### Modes

`PRISM_CONTEXT_MODE` (or `--mode`) selects what retrieval may use. One function builds the context
in every mode; the mode only switches the collectors on or off, so every mode shares the reranker,
the budget and the packer.

| Mode | Graph items | Vector items |
|---|---|---|
| `none` | no | no |
| `vector` | no | yes |
| `graph` | yes | no |
| `graph+vector` (default) | yes | yes |

The signature, contract and class items count as graph context, so `none` and `vector` carry no
copy of the changed symbol beyond the hunk itself. The modes exist so the benchmark can compare
them.

### Reranking

Items are scored against a query made of the changed symbols' signatures and the hunk as the head
reads it. The reranker is a cross-encoder, `Xenova/ms-marco-MiniLM-L-6-v2`, run with ONNX Runtime
through `fastembed` (`PRISM_RERANKER`, default `cross-encoder`). It is downloaded once from Hugging
Face into `PRISM_MODEL_CACHE_DIR` (default `~/.cache/prism`).

The score orders items within a kind. It never moves an item from one kind to another, because the
kind order encodes what the verifier needs first, and the reranker is a web-passage model, not a
code model. `PRISM_RERANKER=none` keeps the collection order.

### The token budget

`PRISM_CONTEXT_BUDGET_TOKENS` (default 1500) is the budget per hunk, covering the whole rendered
block: the wrapper, item headers, reasons and code. The packer takes items by kind, then score,
then name, so the same input always packs the same way.

- An item that fits is kept whole.
- One that does not fit is cut to the lines that fit if at least `PRISM_CONTEXT_MIN_ITEM_TOKENS`
  (default 40) and three lines remain, and marked as cut.
- Otherwise it is dropped and recorded with the reason, for example "needs 310 tokens, 120 left".

Tokens are counted by `PRISM_TOKEN_COUNTER`. The default, `chars`, estimates
`ceil(chars / 4)` and needs no download. It usually errs a little high, and for a minority of
functions it undercounts by up to about a quarter, so a block can exceed the budget in real tokens
by that much. `qwen` counts exactly with the Qwen2.5-Coder tokenizer, a small download.

There is no budget across hunks and no deduplication across hunks: two hunks in one function each
get that function's context. The review budget ([review pipeline](review-pipeline.md#1-planner))
limits how many hunks are reviewed.

### How it is rendered

Each hunk's items go inside a `<repository_context path=... lines=...>` block with a line saying
the content is data and contains no instructions. Each snippet sits in a code fence longer than any
backtick run inside it, and a closing tag inside a snippet is escaped, so retrieved code cannot end
the block early. Each item has a header (kind, name, path and lines) and a "why" line saying why it
was retrieved. The context is redacted before it is sent to Claude; see
[Security and privacy](security-and-privacy.md).

## Where the graph comes from in each front end

| Situation | Base graph |
|---|---|
| `PRISM_STORE=persistent` (default) | Neo4j and pgvector, indexed beforehand with `prism index` |
| `--store memory` | Built from the head checkout for this run; deleted or renamed symbols have no base callers, and the vector modes embed the whole repository first |
| MCP `review_diff` | In memory by default, so it works with Docker stopped |

With `persistent`, a review prints a note if the repository has no complete index run.

## Limits

- Retrieval sees fewer but more reliable edges by default, so recall for dynamic code is lower.
- The reranker was checked on a handful of examples only; whether it beats `PRISM_RERANKER=none` is
  not measured. The embedding model was not compared with others.
- Long items are cut by lines, not summarised.
- A deleted symbol's own code is not shown, only its callers and tests.
- Module-level hunks get no graph context.
