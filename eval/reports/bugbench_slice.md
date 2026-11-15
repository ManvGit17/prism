# BugBench: tinydb (slice-02)

10 pull requests, each a real commit with one planted defect at a known line. A hit is a posted comment on the defect's file within 3 lines of it.

| | |
|---|---|
| Recall | 10/10 (1.00) |
| Near the defect but dropped | 0 |
| Comments per PR | 1.70 |
| Hits by model | claude 9, prism-reviewer:v2 1 |
| First run, p50 / p95 (s) | 13.88 / 36.61 |

Reviewer `prism-reviewer:v2`, gate `onnx`, context `graph`, verifier `handoff`, SAST on, escalation on, minimum confidence 0.60. First-run time covers the local reviewer, SAST and indexing the checkout in memory; Claude's answers come from a handoff and are not timed.

## Cases

| Case | Defect | Line | Hit | By | Posted | Considered |
|---|---|---|---|---|---|---|
| tinydb-01 | wrong boolean operator | `tinydb/queries.py:31` | yes | claude | 1 | 4 |
| tinydb-02 | missing cache key | `tinydb/queries.py:378` | yes | claude | 1 | 4 |
| tinydb-03 | resource leak | `tinydb/middlewares.py:140` | yes | claude | 1 | 1 |
| tinydb-04 | off-by-one | `tinydb/table.py:220` | yes | claude | 1 | 4 |
| tinydb-05 | wrong null check | `tinydb/utils.py:99` | yes | claude | 1 | 4 |
| tinydb-06 | wrong exception handling | `tinydb/queries.py:78` | yes | claude | 1 | 5 |
| tinydb-07 | missing check | `tinydb/table.py:456` | yes | prism-reviewer:v2 | 1 | 7 |
| tinydb-08 | swapped arguments | `tinydb/table.py:317` | yes | claude | 5 | 8 |
| tinydb-09 | swapped arguments | `tinydb/storages.py:149` | yes | claude | 3 | 4 |
| tinydb-10 | inverted condition | `tinydb/table.py:114` | yes | claude | 2 | 5 |

## Comments near the defects

- **tinydb-01** `tinydb/queries.py:31` (claude): The `or` makes this predicate wrong in both directions. `str` is a registered `Sequence`, so strings still return True and the #638 fix does nothing. Non-sequence, non-string values such as ints, None or dicts now also return True, so `a...
- **tinydb-02** `tinydb/queries.py:378` (claude): The new comment says flags are included in the hash, but the hash tuple is still ('search', self._path, regex). Two searches that differ only in `flags` (e.g. re.I) share a query-cache entry, so the second returns the first one's cached ...
- **tinydb-03** `tinydb/middlewares.py:140` (claude): close() no longer calls self.storage.close(): the diff removes that line and replaces it with `self._closed = True; self.cache = None`, yet the comment above it still says 'Let the storage clean up too'. The underlying JSONStorage file h...
- **tinydb-04** `tinydb/table.py:218` (claude): `_next_id` is seeded with the current maximum ID, not maximum + 1. `_get_next_id()` hands out `_next_id` as the next ID and then increments it, so the first auto-assigned document in a batch gets an ID that already exists in `table` and ...
- **tinydb-05** `tinydb/utils.py:99` (claude): `get()` now looks up with the `_missing` sentinel default but still tests `value is not None`. For an absent key `value` is `_missing`, so the branch runs: `move_to_end(key)` raises KeyError and `get()` no longer returns the default. Thi...
- **tinydb-06** `tinydb/queries.py:78` (claude): `hash()` raises TypeError for unhashable contents (lists, dicts, numpy arrays), not ValueError. The handler never fires, so the crash is only moved to construction time and the 'fall back to uncacheable' behaviour does not happen. Catch ...
- **tinydb-07** `tinydb/table.py:456` (prism-reviewer:v2): I think this should be `doc_id in table` instead of `doc_id in requested_ids`. Otherwise it's possible to update a document that doesn't exist.
- **tinydb-08** `tinydb/table.py:317` (claude): The Document constructor arguments are swapped. The `doc_id` branch uses `document_class(raw_doc, doc_id)`, but here it is `document_class(int(x), raw_docs[str(x)])`, which puts the int in the value slot and the dict in the doc_id slot. ...
- **tinydb-09** `tinydb/storages.py:149` (claude): The `os.rename` arguments are reversed: it moves the original database file onto the temp path instead of moving the new file over the database. The freshly written data is overwritten by the old content and `file_name` no longer exists,...
- **tinydb-10** `tinydb/table.py:114` (claude): The condition is inverted and destructive. With the default `persist_empty=False`, every `Table` construction runs `table.clear()` and writes it back, wiping all existing documents whenever a table is opened (for example after reopening ...
