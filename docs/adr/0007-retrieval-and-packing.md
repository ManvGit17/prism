# ADR 0007: Retrieval, reranking and packing of review context

Status: accepted.

## Context

For each changed hunk, the verifier (and the frontier model on escalated hunks) gets code from
the rest of the repository to confirm or reject a review comment. The fine-tuned reviewer is
served the hunk alone (ADR 0009). The context has to fit a small local model's prompt, so what
goes in, in which order, and how it is counted matter more than how much is found.

## Decision

**Hunk to symbol.** The PR's changed files are parsed into an overlay over the base graph
(ADR 0006). Each added line maps to the innermost function or class around it at the head. A
hunk that only removes lines maps to the symbol around the removed block, when the context lines
on both sides sit in the same symbol. Removed lines are also looked up in the base graph at the
old path; a base symbol the head no longer has was deleted or renamed, and its callers and tests
come from the base graph. Hunks outside any symbol are `module` (no graph context; vector context
only), and files without a parser are `non_code` (no context). Binary files are skipped.

**What is collected,** for each changed symbol S:

| Kind | What | Packing priority |
|---|---|---|
| signature | S's own code at the head; a long S shows its first lines and a window around the hunk | 1 |
| contract | the supertype method S overrides or implements (OVERRIDES edges) | 2 |
| test | tests that call S directly | 3 |
| caller | callers of S, plus callers of its contract method (calls by dynamic dispatch); 2 hops with `--hops 2` | 4 |
| implementation | methods that override or implement S | 5 |
| class | S's class: header and the 12 member signatures nearest S | 6 |
| callee | what S calls | 7 |
| class_test | tests of S's class, only when S has no direct test; best 3 kept | 8 |
| similar | functions most similar to the hunk (vector search), outside the changed files and not already found | 9 |

The base order is signature > tests > callers > similar. Contract sits next to the signature
because it is short and states what S must keep doing. Implementations sit after callers because
a change to an interface method reaches them the same way it reaches callers. Class context and
callees come after: they explain S but show less of the change's impact, and on the bench repos a
class summary of a large utility class pushed every caller out of the budget when it ranked
higher. CALLS and TESTS edges below `PRISM_CALL_CONFIDENCE_THRESHOLD` are left out (ADR 0004).

**Rerank.** Items are scored against a query made of the changed symbols' signatures and the hunk
as the head reads it. The score orders items **within** a priority class; it never moves an item
across classes, because the priority encodes what the verifier needs first and the reranker is
not trained on code. The reranker is a cross-encoder, `Xenova/ms-marco-MiniLM-L-6-v2`, run with
ONNX Runtime through `fastembed` 0.9.1 (`TextCrossEncoder`). No torch. Installing fastembed added
124 MB to the virtualenv; the model is 87.5 MB in `PRISM_MODEL_CACHE_DIR` (default
`~/.cache/prism`), downloaded once. fastembed lists it at 0.08 GB. Tests use a deterministic
word-overlap reranker; `--reranker none` keeps collection order.

**Budget.** `PRISM_CONTEXT_BUDGET_TOKENS` (default 1500) per hunk, covering the whole rendered
block: wrapper, item headers, reasons and code. Items are taken in priority order, then score,
then name. An item that does not fit is cut to the lines that fit if at least
`PRISM_CONTEXT_MIN_ITEM_TOKENS` (40) and three lines remain; otherwise it is dropped and listed in
the bundle with the reason. Output is deterministic for the same input.

**Token counter.** Behind a `TokenCounter` interface. The default is `ceil(chars / 4)`, which
needs no download. `eval/token_counter_check.py` compared it with the Qwen2.5-Coder tokenizer on
every function of three repos (`eval/reports/token_counter.jsonl`):

| Repo | Real chars per token (median) | Estimate error, median | 5th percentile | Undercounted |
|---|---|---|---|---|
| rich (Python) | 4.29 | +8% | -28% | 31% of functions |
| commons-lang (Java) | 4.76 | +20% | -11% | 14% |
| smms (Python) | 4.88 | +23% | -7% | 8% |

So the estimate is usually a little high, and for a minority of items it is low by up to about a
quarter; a packed block can then exceed the budget in real tokens by that much. A ratio of 3
overcounted by 44% to 63% (median) and wasted a third of the budget. `PRISM_TOKEN_COUNTER=qwen`
counts exactly with the real tokenizer (7 MB download).

**Rendering.** Each hunk's items go inside `<repository_context path=... lines=...>`, with a
line saying the content is data and contains no instructions. Each snippet is in a code fence
longer than any backtick run inside it, and a closing tag inside a snippet is escaped.

**Ablation.** `PRISM_CONTEXT_MODE` / `--mode`: `none`, `vector`, `graph`, `graph+vector`. One
function, `build_context`, handles every mode; the mode only switches the graph and vector
collectors on or off, so all modes share the reranker, budget and packer. Signature, contract
and class items count as graph context, so `none` and `vector` carry no copy of the changed
symbol beyond the hunk itself.

## Consequences and limits

- There is no budget across hunks or per PR; a PR with many hunks gets many full blocks. The
  review budget (ADR 0010) decides how many hunks are reviewed.
- There is no deduplication across hunks: two hunks in one function each get that function's
  context.
- Long items are cut by lines, not summarised by a model.
- The reranker is a web-passage model, not a code model. I checked it on a handful of examples
  only. Whether reranking helps at all against `--reranker none` is not yet measured.
- A deleted symbol's own code is not shown (the hunk shows it); only its callers and tests are.
- Module-level hunks get no graph context.
- `--store memory` builds the base from the head tree, so deleted or renamed symbols have no base
  callers in that mode, and vector modes embed the whole repo first.
