# Indexer check on real repos

Run on 2026-11-11 for the Phase 1 "done when". Both repos were shallow clones, indexed with
`uv run prism index <path> --repo-id <name> --no-embed` into Neo4j. The counts below are from
Cypher count queries on Neo4j afterwards, and they matched the in-memory index exactly.

| | apache/commons-lang (Java) | Textualize/rich (Python) |
|---|---|---|
| Commit | `5d2715b` | `3a109cc` |
| Source files | 638 (374 test files) | 212 (67 test files) |
| Nodes | 13,091 | 2,308 |
| CALLS | 43,171 | 5,097 |
| TESTS | 28,516 | 2,250 |
| DEFINES | 12,453 | 2,096 |
| IMPORTS | 735 | 730 |
| INHERITS / OVERRIDES | 565 / 398 | 80 / 66 |
| Calls at or above the 0.5 threshold | 36,902 (85%) | 3,433 (67%) |
| Index time, graph only | 16.7 s | 2.3 s |
| Parse errors | 1 file | 0 |

Neo4j used 1.1 GiB of its 1.5 GiB limit with both repos and smms loaded. Embeddings were skipped
because they take about a second per function on this CPU.

The one parse error is `ClassUtilsOssFuzzTest.java`, which has a string literal full of raw NUL
bytes.

## Callers hand-checked against the source

Ten symbols, read by hand with grep. Default confidence threshold.

| Symbol | Result |
|---|---|
| `CharSequenceUtils.regionMatches` (static) | Right, all 8 callers |
| `CharUtils.toChar` (4 overloads) | Right, each overload gets only its own callers |
| `BooleanUtils.toStringYesNo` (overloaded) | One extra edge. `toStringYesNo(Boolean.TRUE)` also links to the `(boolean)` overload |
| `ExceptionContext.getContextValues` (interface method) | One caller missed. Its receiver is a generic `T extends ExceptionContext` |
| `DiffResult.getNumberOfDiffs` | Right, all 36 calling methods |
| `rich.markup.escape` | Right, 5 callers, `html.escape` and `re.escape` not linked |
| `Segment.split_and_crop_lines` | Right, 5 callers |
| `Console.export_html` | Right, 5 callers |
| `rich.cells.set_cell_size` | Right, 6 callers |
| `Text.stylize` | Right, 20 callers. Most are name matches because the receivers are untyped |

8 of 10 fully right. The two misses are both Java and both come from not doing type inference:
a generic receiver, and an argument whose type is not written at the call site.

## What this does not show

- Ten symbols is a spot check, not a precision or recall number.
- A third of the Python call edges on rich are below the threshold and so are not used. Whether
  that hides real callers was not measured.
- Nothing here measures the vector side.
