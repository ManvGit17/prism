# BugBench: PRism against PR-Agent

The same 83 pull requests (runs `mid-01` and `pra-01`). A hit is a posted comment, or for PR-Agent a finding's line range, on the defect's file within 3 lines of a defect line. Both used Claude Sonnet for every model answer they needed from Claude, through a handoff.

| | PRism | PR-Agent |
|---|---|---|
| Injected recall | 48/57 (0.84) | 54/57 (0.95) |
| Injected recall, findings of at most 10 lines | 48/57 (0.84) | 54/57 (0.95) |
| Real-bug recall | 6/10 (0.60) | 8/10 (0.80) |
| Clean PRs with any comment | 0/16 | 2/16 |
| Comments per buggy PR | 1.00 | 2.03 |
| Claude requests | 519 | 227 |
| Claude input (MB of prompt text) | 2.88 | 3.77 |

Found by PR-Agent only: aiosqlite-i06, aiosqlite-i11, jsoup-i01, sqlite-utils-r01, sqlite-utils-r02, sqlite-utils-i04, sqlite-utils-i06, sqlite-utils-i11.
Found by PRism only: none.
Missed by both: aiosqlite-r02, jsoup-r02, jsoup-i07, jsoup-i12, sqlite-utils-i03.

## PR-Agent on clean PRs

- **jsoup-c02** `src/test/java/org/jsoup/select/SelectorTest.java:1195-1208` (review): Stale Test Reference : `map` is read from `prevEval.threadMemo.get()` before any selection. `reset()` now calls `threadMemo.remove()`, which drops the ThreadLocal entry but does not clear the map object the test still holds. After the se...
- **jsoup-c02** `src/test/java/org/jsoup/select/SelectorTest.java:1208-1208` (improve): `reset()` now calls `threadMemo.remove()` instead of `clear()`, so the `map` captured before the selects is detached from the evaluator. It still holds the entries from the first select (so this assertion would fail with 1, or pass only ...
- **jsoup-c02** `src/main/java/org/jsoup/select/Collector.java:34-36` (improve): If evaluation throws partway through the collect (e.g. a custom evaluator error), `reset()` is skipped and the memo keeps strong references on the retained thread, which is the leak this PR is meant to prevent. Reset in a `finally` block.
- **sqlite-utils-c04** `sqlite_utils/db.py:1920-1923` (review): WITHOUT ROWID : `rowid` is now always selected from the old table. For a `WITHOUT ROWID` table (which sqlite-utils can still transform when created elsewhere) there is no `rowid` column, so `transform()` fails with `no such column: rowid...
