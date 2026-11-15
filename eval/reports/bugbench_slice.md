# BugBench: tinydb (slice-01)

10 pull requests, each a real commit with one planted defect at a known line. A hit is a posted comment on the defect's file within 3 lines of it.

| | |
|---|---|
| Recall | 9/10 (0.90) |
| Near the defect but dropped | 0 |
| Comments per PR | 1.60 |
| Hits by model | claude 7, prism-reviewer:v2 2 |
| First run, p50 / p95 (s) | 13.88 / 36.61 |

Reviewer `prism-reviewer:v2`, gate `onnx`, context `graph`, verifier `handoff`, SAST on, escalation on, minimum confidence 0.60. First-run time covers the local reviewer, SAST and indexing the checkout in memory; Claude's answers come from a handoff and are not timed.

## Cases

| Case | Defect | Line | Hit | By | Posted | Considered |
|---|---|---|---|---|---|---|
| tinydb-01 | wrong boolean operator | `tinydb/queries.py:31` | yes | prism-reviewer:v2 | 1 | 3 |
| tinydb-02 | missing cache key | `tinydb/queries.py:378` | yes | claude | 1 | 4 |
| tinydb-03 | resource leak | `tinydb/middlewares.py:140` | yes | claude | 1 | 1 |
| tinydb-04 | off-by-one | `tinydb/table.py:220` | yes | claude | 1 | 4 |
| tinydb-05 | wrong null check | `tinydb/utils.py:99` | yes | claude | 1 | 4 |
| tinydb-06 | wrong exception handling | `tinydb/queries.py:78` | yes | claude | 1 | 5 |
| tinydb-07 | missing check | `tinydb/table.py:456` | no | - | 1 | 7 |
| tinydb-08 | swapped arguments | `tinydb/table.py:317` | yes | claude | 5 | 7 |
| tinydb-09 | swapped arguments | `tinydb/storages.py:149` | yes | claude | 3 | 4 |
| tinydb-10 | inverted condition | `tinydb/table.py:114` | yes | prism-reviewer:v2 | 1 | 3 |

## Comments near the defects

- **tinydb-01** `tinydb/queries.py:31` (prism-reviewer:v2): I think this is a bug in the `is_sequence` function. It should be `if isinstance(obj, str)`
- **tinydb-02** `tinydb/queries.py:378` (claude): The new comment says flags are included in the hash, but the hash tuple is still ('search', self._path, regex). Two search() queries that differ only by flags hash equal, so Table's query cache can return results cached for the other que...
- **tinydb-03** `tinydb/middlewares.py:140` (claude): The diff deletes `self.storage.close()` from `close()`; the comment above it ("Let the storage clean up too") now has nothing under it. The wrapped storage is never closed, so with JSONStorage the file handle leaks. The existing `test_ca...
- **tinydb-04** `tinydb/table.py:218` (claude): next_id is set to the current maximum ID with no +1. _get_next_id() returns self._next_id as is (then increments), so the first auto-assigned document in the batch gets the ID of the existing max document and overwrites it in table[doc_i...
- **tinydb-05** `tinydb/utils.py:99` (claude): get() now reads with the _missing sentinel but still tests `value is not None`. For an absent key, value is _missing, so the branch runs: move_to_end(key) raises KeyError, and get() with a default crashes instead of returning it. Entries...
- **tinydb-06** `tinydb/queries.py:78` (claude): hash() on a tuple that contains an unhashable element (list, dict, numpy array) raises TypeError, not ValueError. This except never fires, so the fallback to uncacheable (hashval = None) is dead code, and Query.test() with unhashable arg...
- **tinydb-08** `tinydb/table.py:317` (claude): The arguments to document_class are swapped. Document is constructed as Document(value, doc_id), but here it is called with (int(x), raw_docs[str(x)]). dict(int) raises TypeError, so any get(doc_ids=[...]) that matches a document crashes...
- **tinydb-09** `tinydb/storages.py:149` (claude): The arguments to os.rename are reversed. os.rename(src, dst) is called with src=the database file and dst=the temp file holding the new data. This moves the old database over the temp file, so the new data is discarded, and the database ...
- **tinydb-10** `tinydb/table.py:114` (prism-reviewer:v2): I think this should be `if persist_empty`
