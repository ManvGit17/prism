# BugBench: aiosqlite, jsoup, sqlite-utils, tinydb (mid-01)

Each pull request is a real commit. Injected cases carry one planted defect at a known line; real-bug cases are commits that introduced a bug a later commit fixed; clean cases are left as they are. A hit is a posted comment on the defect's file within 3 lines of it.

| Set | Recall | Near but dropped | Comments per PR | Hits by model |
|---|---|---|---|---|
| Injected | 48/57 (0.84) | 3 | 1.05 | claude 41, prism-reviewer:v2 7 |
| Real bugs | 6/10 (0.60) | 3 | 0.70 | claude 5, prism-reviewer:v2 1 |

Clean PRs: 0 of 16 got at least one comment (false-positive rate 0.00), 0.00 comments per PR. A comment on a clean PR may still be a true issue in the original code; this counts it as a false positive.

| Repo (injected) | Recall | Near but dropped | Comments per PR | Hits by model |
|---|---|---|---|---|
| aiosqlite | 8/10 (0.80) | 0 | 1.10 | claude 7, prism-reviewer:v2 1 |
| jsoup | 11/14 (0.79) | 2 | 0.93 | claude 10, prism-reviewer:v2 1 |
| sqlite-utils | 14/18 (0.78) | 1 | 1.00 | claude 12, prism-reviewer:v2 2 |
| tinydb | 15/15 (1.00) | 0 | 1.20 | claude 12, prism-reviewer:v2 3 |

First run p50 / p95: 19.95 s / 90.28 s.

Reviewer `prism-reviewer:v2`, gate `onnx`, context `graph`, verifier `handoff`, SAST on, escalation on, minimum confidence 0.60. First-run time covers the local reviewer, SAST and indexing the checkout in memory; Claude's answers come from a handoff and are not timed.

## Cases

| Case | Set | Defect | Where | Hit | By | Posted | Considered |
|---|---|---|---|---|---|---|---|
| aiosqlite-r01 | real | late-binding closure | `aiosqlite/core.py:101,102,103,105,109,110,111,113` | yes | claude | 1 | 6 |
| aiosqlite-r02 | real | wrong conversion | `aiosqlite/core.py:273` | no | - | 0 | 2 |
| aiosqlite-i01 | injected | missing await | `aiosqlite/core.py:287` | yes | claude | 1 | 3 |
| aiosqlite-i02 | injected | missing await | `aiosqlite/core.py:218` | yes | claude | 1 | 3 |
| aiosqlite-i03 | injected | missing await | `aiosqlite/core.py:215` | yes | claude | 1 | 2 |
| aiosqlite-i04 | injected | wrong null check | `aiosqlite/cursor.py:23` | yes | claude | 1 | 1 |
| aiosqlite-i05 | injected | inverted condition | `aiosqlite/core.py:65` | yes | claude | 2 | 10 |
| aiosqlite-i06 | injected | swapped arguments | `aiosqlite/core.py:101` | no | - | 1 | 2 |
| aiosqlite-i08 | injected | swapped arguments | `aiosqlite/core.py:340` | yes | claude | 1 | 5 |
| aiosqlite-i09 | injected | wrong null check | `aiosqlite/core.py:163` | yes | prism-reviewer:v2 | 1 | 2 |
| aiosqlite-i10 | injected | inverted condition | `aiosqlite/core.py:362` | yes | claude | 1 | 5 |
| aiosqlite-i11 | injected | swapped arguments | `aiosqlite/core.py:380` | no | - | 1 | 6 |
| aiosqlite-c01 | clean | clean | - | - | - | 0 | 1 |
| aiosqlite-c02 | clean | clean | - | - | - | 0 | 2 |
| aiosqlite-c03 | clean | clean | - | - | - | 0 | 2 |
| aiosqlite-c04 | clean | clean | - | - | - | 0 | 1 |
| jsoup-r01 | real | null dereference | `src/main/java/org/jsoup/helper/W3CDom.java:357,358,362` | yes | claude | 1 | 17 |
| jsoup-r02 | real | wrong escaping | `src/main/java/org/jsoup/nodes/DocumentType.java:98,99` | dropped | - | 0 | 18 |
| jsoup-i01 | injected | wrong boolean operator | `src/main/java/org/jsoup/nodes/Element.java:1601` | dropped | - | 0 | 14 |
| jsoup-i02 | injected | wrong return value | `src/main/java/org/jsoup/nodes/FormElement.java:87` | yes | claude | 1 | 4 |
| jsoup-i03 | injected | inverted condition | `src/main/java/org/jsoup/nodes/DocumentType.java:103` | yes | claude | 1 | 4 |
| jsoup-i04 | injected | wrong null check | `src/main/java/org/jsoup/parser/XmlTreeBuilder.java:61` | yes | claude | 1 | 19 |
| jsoup-i05 | injected | swapped arguments | `src/main/java/org/jsoup/parser/CharacterReader.java:523` | yes | prism-reviewer:v2 | 1 | 6 |
| jsoup-i07 | injected | off-by-one | `src/main/java/org/jsoup/parser/Tokeniser.java:369` | no | - | 0 | 12 |
| jsoup-i08 | injected | wrong null check | `src/main/java/org/jsoup/nodes/Element.java:2105` | yes | claude | 1 | 8 |
| jsoup-i09 | injected | wrong null check | `src/main/java/org/jsoup/nodes/Node.java:559` | yes | claude | 1 | 9 |
| jsoup-i10 | injected | wrong boolean operator | `src/main/java/org/jsoup/helper/DataUtil.java:307` | yes | claude | 1 | 9 |
| jsoup-i11 | injected | wrong boolean operator | `src/main/java/org/jsoup/parser/HtmlTreeBuilder.java:539` | yes | claude | 1 | 7 |
| jsoup-i12 | injected | off-by-one | `src/main/java/org/jsoup/parser/TreeBuilder.java:181` | dropped | - | 1 | 24 |
| jsoup-i13 | injected | wrong return value | `src/main/java/org/jsoup/parser/CharacterReader.java:618` | yes | claude | 1 | 6 |
| jsoup-i14 | injected | inverted condition | `src/main/java/org/jsoup/nodes/Element.java:2006` | yes | claude | 1 | 13 |
| jsoup-i15 | injected | swapped arguments | `src/main/java/org/jsoup/internal/StringUtil.java:357` | yes | claude | 2 | 12 |
| jsoup-c01 | clean | clean | - | - | - | 0 | 2 |
| jsoup-c02 | clean | clean | - | - | - | 0 | 6 |
| jsoup-c03 | clean | clean | - | - | - | 0 | 6 |
| jsoup-c04 | clean | clean | - | - | - | 0 | 4 |
| sqlite-utils-r01 | real | missing case | `sqlite_utils/utils.py:16,17` | dropped | - | 0 | 3 |
| sqlite-utils-r02 | real | wrong check | `sqlite_utils/db.py:964,965` | dropped | - | 0 | 4 |
| sqlite-utils-r03 | real | unquoted SQL identifier | `sqlite_utils/db.py:294` | yes | claude | 1 | 6 |
| sqlite-utils-s01 | injected | SQL injection | `sqlite_utils/db.py:2920` | yes | claude | 1 | 9 |
| sqlite-utils-s02 | injected | SQL injection | `sqlite_utils/cli.py:3002` | yes | claude | 3 | 10 |
| sqlite-utils-s03 | injected | SQL injection | `sqlite_utils/db.py:3129` | yes | claude | 2 | 11 |
| sqlite-utils-i01 | injected | wrong null check | `sqlite_utils/cli.py:2032` | yes | claude | 1 | 21 |
| sqlite-utils-i02 | injected | swapped arguments | `sqlite_utils/db.py:615` | yes | claude | 1 | 5 |
| sqlite-utils-i03 | injected | resource leak | `sqlite_utils/utils.py:383` | no | - | 0 | 1 |
| sqlite-utils-i04 | injected | inverted condition | `sqlite_utils/cli.py:2487` | no | - | 1 | 5 |
| sqlite-utils-i05 | injected | wrong boolean operator | `sqlite_utils/db.py:3184` | yes | claude | 1 | 4 |
| sqlite-utils-i06 | injected | wrong exception handling | `sqlite_utils/db.py:4562` | dropped | - | 0 | 4 |
| sqlite-utils-i07 | injected | inverted condition | `sqlite_utils/cli.py:1028` | yes | prism-reviewer:v2 | 1 | 9 |
| sqlite-utils-i08 | injected | wrong boolean operator | `sqlite_utils/db.py:3868` | yes | prism-reviewer:v2 | 1 | 9 |
| sqlite-utils-i09 | injected | swapped arguments | `sqlite_utils/db.py:1364` | yes | claude | 1 | 7 |
| sqlite-utils-i10 | injected | wrong null check | `sqlite_utils/db.py:708` | yes | claude | 1 | 16 |
| sqlite-utils-i11 | injected | swapped arguments | `sqlite_utils/utils.py:480` | no | - | 0 | 40 |
| sqlite-utils-i12 | injected | wrong boolean operator | `sqlite_utils/cli.py:1206` | yes | claude | 1 | 6 |
| sqlite-utils-i13 | injected | inverted condition | `sqlite_utils/db.py:880` | yes | claude | 1 | 6 |
| sqlite-utils-i14 | injected | off-by-one | `sqlite_utils/db.py:4243` | yes | claude | 1 | 4 |
| sqlite-utils-i15 | injected | off-by-one | `sqlite_utils/cli.py:3428` | yes | claude | 1 | 3 |
| sqlite-utils-c01 | clean | clean | - | - | - | 0 | 0 |
| sqlite-utils-c02 | clean | clean | - | - | - | 0 | 2 |
| sqlite-utils-c03 | clean | clean | - | - | - | 0 | 1 |
| sqlite-utils-c04 | clean | clean | - | - | - | 0 | 15 |
| tinydb-r01 | real | data loss | `tinydb/table.py:114` | yes | claude | 1 | 4 |
| tinydb-r02 | real | missing check | `tinydb/table.py:369` | yes | prism-reviewer:v2 | 1 | 1 |
| tinydb-r03 | real | missing check | `tinydb/storages.py:21` | yes | claude | 2 | 5 |
| tinydb-i01 | injected | swapped arguments | `tinydb/queries.py:31` | yes | prism-reviewer:v2 | 1 | 3 |
| tinydb-i02 | injected | inverted condition | `tinydb/storages.py:27` | yes | prism-reviewer:v2 | 1 | 1 |
| tinydb-i03 | injected | inverted condition | `tinydb/table.py:193` | yes | claude | 1 | 4 |
| tinydb-i04 | injected | inverted condition | `tinydb/table.py:250` | yes | claude | 1 | 5 |
| tinydb-i05 | injected | wrong boolean operator | `tinydb/queries.py:128` | yes | claude | 1 | 14 |
| tinydb-i06 | injected | wrong boolean operator | `tinydb/table.py:113` | yes | claude | 1 | 3 |
| tinydb-i07 | injected | wrong null check | `tinydb/table.py:465` | yes | claude | 1 | 3 |
| tinydb-i08 | injected | wrong null check | `tinydb/table.py:497` | yes | claude | 1 | 2 |
| tinydb-i09 | injected | wrong null check | `tinydb/table.py:229` | yes | claude | 1 | 11 |
| tinydb-i10 | injected | wrong return value | `tinydb/queries.py:437` | yes | claude | 2 | 7 |
| tinydb-i11 | injected | off-by-one | `tinydb/table.py:559` | yes | prism-reviewer:v2 | 1 | 3 |
| tinydb-i12 | injected | off-by-one | `tinydb/table.py:220` | yes | claude | 1 | 4 |
| tinydb-i13 | injected | swapped arguments | `tinydb/utils.py:97` | yes | claude | 1 | 4 |
| tinydb-i14 | injected | wrong exception handling | `tinydb/queries.py:78` | yes | claude | 1 | 5 |
| tinydb-i15 | injected | swapped arguments | `tinydb/table.py:149` | yes | claude | 3 | 6 |
| tinydb-c01 | clean | clean | - | - | - | 0 | 2 |
| tinydb-c02 | clean | clean | - | - | - | 0 | 1 |
| tinydb-c03 | clean | clean | - | - | - | 0 | 3 |
| tinydb-c04 | clean | clean | - | - | - | 0 | 3 |

## Comments near the defects

- **aiosqlite-r01** `aiosqlite/core.py:102` (claude): set_result/set_exception are closures defined inside the worker loop that read `future` from the enclosing scope. They run later on the event-loop thread via call_soon_threadsafe, by which time the worker thread may already have rebound ...
- **aiosqlite-i01** `aiosqlite/core.py:287` (claude): `self._execute(...)` is a coroutine but is not awaited. The coroutine is never run, so the handler is never queued or installed and Python emits a 'coroutine was never awaited' warning. Errors from set_progress_handler are also lost. The...
- **aiosqlite-i02** `aiosqlite/core.py:218` (claude): `self._execute(...)` is not awaited (the old code had `await`). create_function now returns without registering the function, so SQL that uses it fails with 'no such function' and the coroutine is never run.
- **aiosqlite-i03** `aiosqlite/core.py:215` (claude): In the Python 3.8+ branch `self._execute(...)` is not awaited, while the else branch awaits. On the common path the function is never registered and the coroutine is never run.
- **aiosqlite-i04** `aiosqlite/cursor.py:23` (claude): The condition is inverted compared with the original code in core.py (`if row is None: raise StopAsyncIteration`). With `is not None`, `async for` raises StopAsyncIteration on the first real row, so no rows are ever yielded, and when the...
- **aiosqlite-i05** `aiosqlite/core.py:65` (claude): The condition is inverted. The result is only delivered when `future` is falsy, in which case `future.get_loop()` would raise on None. For real futures the result is never set, so every awaiting call hangs. The except path at line 74 use...
- **aiosqlite-i05** `aiosqlite/core.py:66` (prism-reviewer:v2): I think this is a bug. If `future` is not set then we should call `set_result` on the loop thread.
- **aiosqlite-i08** `aiosqlite/core.py:340` (claude): Arguments to `_execute` are swapped. `_execute(fn, *args)` expects the callable first, so this tries to call `authorizer_callback(self._conn.set_authorizer)`. It never installs the authorizer. With `None` it fails when building the parti...
- **aiosqlite-i09** `aiosqlite/core.py:163` (prism-reviewer:v2): I think this should be `if self._connection is None:`
- **aiosqlite-i10** `aiosqlite/core.py:362` (claude): The check is inverted. An empty queue while the worker task is still running is the normal case (the consumer is faster than the dumper), but this branch logs 'completed unexpectedly' and breaks, truncating the dump. When the task is act...
- **jsoup-r01** `src/main/java/org/jsoup/helper/W3CDom.java:357` (claude): NPE: `contextElement` comes from `doc.getUserData(ContextProperty)`, which is only set when converting from an Element context. For a normal `fromJsoup(Document)` conversion it is null, so `contextElement.ownerDocument()` throws in the c...
- **jsoup-i02** `src/main/java/org/jsoup/nodes/FormElement.java:87` (claude): isDescendant always returns true: after the loop finds no matching ancestor it falls through to `return true`. empty() therefore removes every linked control, including controls outside the form attached via addElement, which the new tes...
- **jsoup-i03** `src/main/java/org/jsoup/nodes/DocumentType.java:103` (claude): The condition is inverted. A system id is now written only when it is absent (emitting an empty "") and is dropped when present, so doctypes with a system id lose it on output.
- **jsoup-i04** `src/main/java/org/jsoup/parser/XmlTreeBuilder.java:61` (claude): The restore logic is inverted. When there was a previous value (non-null) it removes the prefix, and when the prefix was previously unbound (null) it calls put(prefix, null). Scope exit therefore corrupts the namespace bindings.
- **jsoup-i05** `src/main/java/org/jsoup/parser/CharacterReader.java:523` (prism-reviewer:v2): This is a bug. `consumeRange` should be called with the same arguments as before.
- **jsoup-i08** `src/main/java/org/jsoup/nodes/Element.java:2105` (claude): The null check is inverted. When attributes exist, cloneAttributes() returns null, so every cloned element loses its attributes. When attributes is null, it dereferences null and throws an NPE.
- **jsoup-i09** `src/main/java/org/jsoup/nodes/Node.java:559` (claude): The loop condition is inverted (`ancestor == null`). For a node with a parent the loop body never runs, so the cycle check is a no-op. For a root node (parentNode null) it enters the body and then dereferences `ancestor.parentNode`, thro...
- **jsoup-i10** `src/main/java/org/jsoup/helper/DataUtil.java:307` (claude): `reuseDocIfFullyRead || input.baseReadFully()` bypasses the fully-read check on the regular path (flag true). The sniff parse is capped at firstReadBufferSize, so for a document larger than that buffer the truncated doc is kept and retur...
- **jsoup-i11** `src/main/java/org/jsoup/parser/HtmlTreeBuilder.java:539` (claude): `el != null || el != contextElement` is wrong: it is true whenever el is non-null, including when el is the context element, and true when el is null (if contextElement is non-null). The intended check is that an element exists and is no...
- **jsoup-i13** `src/main/java/org/jsoup/parser/CharacterReader.java:618` (claude): The not-found path returns true. containsIgnoreCase now always reports a match, even when lastIcIndex is set to -1. The tokeniser uses this to decide whether </title> or </style> exists, so an unterminated RCDATA element is treated as te...
- **jsoup-i14** `src/main/java/org/jsoup/nodes/Element.java:2006` (claude): The condition is inverted. Elements with children enter the empty/void serialization branch and get `></tag>` or ` />` emitted right after the start tag, while empty elements fall into the else branch with a bare '>'. This breaks the out...
- **jsoup-i15** `src/main/java/org/jsoup/internal/StringUtil.java:357` (claude): `value.substring(colon, 0)` has its arguments reversed (begin > end), so it throws StringIndexOutOfBoundsException for any value with a scheme. It is reached from resolve() for every non-http relative URL such as tel: or mailto:, so thos...
- **sqlite-utils-r03** `sqlite_utils/db.py:294` (claude): Table, column and other_table/other_column names are formatted straight into the CREATE TABLE text without quoting or escaping, and that text is then written directly into sqlite_master with writable_schema=1. A name containing a space, ...
- **sqlite-utils-s01** `sqlite_utils/db.py:2920` (claude): The table name is interpolated into the SQL with an f-string. A table name containing a single quote makes the statement invalid (or alters it), so transform() fails for such tables, and it is an injection pattern. Pass the name as a bou...
- **sqlite-utils-s02** `sqlite_utils/cli.py:3001` (claude): Splitting on ':' breaks Windows paths (`C:\ext\mod_spatialite.dll` becomes path 'C' and entrypoint '\ext\...'), and find_spatialite() can return such a path, so --load-extension now fails on Windows for paths that previously worked. It a...
- **sqlite-utils-s02** `sqlite_utils/cli.py:3002` (claude): The extension path and entry point from the command line are formatted into SQL with an f-string. A single quote in either value breaks the statement or lets extra SQL be injected; loading native code from a crafted string should not go ...
- **sqlite-utils-s03** `sqlite_utils/db.py:3129` (claude): Table and column names are interpolated into the SQL with an f-string. A name with a single quote breaks the call or injects SQL. The sibling add_geometry_column already uses bound parameters, so this one should too.
- **sqlite-utils-i01** `sqlite_utils/cli.py:2032` (claude): The condition is inverted. With `if tracker is None:` the body calls tracker.types, which raises AttributeError for every non-CSV/TSV input (and for CSVs with --no-detect-types), while for CSV/TSV input (tracker set) the transform is ski...
- **sqlite-utils-i02** `sqlite_utils/db.py:615` (claude): zip arguments are swapped: `dict(zip(row, keys))` builds a dict keyed by the row values with the column names as values. Every query() result is wrong (and rows with unhashable or duplicate values break or collapse). execute_returning_di...
- **sqlite-utils-i05** `sqlite_utils/db.py:3184` (claude): `not list_mode or not all_columns` is true for every dict-mode insert (list_mode defaults to False), so ordinary inserts now take the DEFAULT VALUES path and return before any column or value is used. Every normal insert/upsert writes em...
- **sqlite-utils-i07** `sqlite_utils/cli.py:1027` (prism-reviewer:v2): I think this should be `if not detect_types:`
- **sqlite-utils-i08** `sqlite_utils/db.py:3868` (prism-reviewer:v2): I think this should be `if key in extracts and record_values[i] is not None:`
- **sqlite-utils-i09** `sqlite_utils/db.py:1364` (claude): typing.cast arguments are reversed: `cast(fk.other_column, str)` is cast(typ, val), so at runtime it returns the second argument, the `str` class, and quote_identifier receives the type `str` instead of the column name. Any table created...
- **sqlite-utils-i10** `sqlite_utils/db.py:708` (claude): The condition looks inverted. cursor.description is None for INSERT/UPDATE/DELETE/CREATE, so `cursor.description is not None` is true only for row-returning statements; plain writes that opened an implicit transaction are never committed...
- **sqlite-utils-i12** `sqlite_utils/cli.py:1206` (claude): Operator precedence defeats the fix: the condition parses as `(tracker is not None and not table_existed_before_insert) or db.table(table).exists()`. After the insert the table always exists, so the transform runs for pre-existing tables...
- **sqlite-utils-i13** `sqlite_utils/db.py:880` (claude): The cleanup guard is inverted. `released` is True once the savepoint has been released successfully, so this now runs ROLLBACK TO / RELEASE after every successful query (which fails or discards the work, because the savepoint no longer e...
- **sqlite-utils-i14** `sqlite_utils/db.py:4243` (claude): Pluralisation uses `>= 1`, so the message always says 'columns' even for a single missing column. The existing test test_insert_all_pk_not_in_records_raises expects the singular 'Invalid primary key column [...]' for one bad column, so i...
- **sqlite-utils-i15** `sqlite_utils/cli.py:3428` (claude): `len(distinct) >= 1` raises for the common single --stop-before value, so every legacy sqlite-migrate set that matches one value is rejected even though the comment says a single string is supported. The check should only reject more tha...
- **tinydb-r01** `tinydb/table.py:114` (claude): When persist_empty is True the constructor calls table.clear() unconditionally, so re-opening an existing table with persist_empty=True (or creating a Table object over stored data) wipes all of its documents. persist_empty should only w...
- **tinydb-r02** `tinydb/table.py:369` (prism-reviewer:v2): I think this should be `len(self._read_table())`
- **tinydb-r03** `tinydb/storages.py:21` (claude): For a bare filename (JSONStorage('db.json', create_dirs=True)) os.path.dirname returns '', os.path.exists('') is False, and os.makedirs('') raises FileNotFoundError. The feature breaks the most common call form. There is also a check-the...
- **tinydb-i01** `tinydb/queries.py:31` (prism-reviewer:v2): I think this is a bug in the `isinstance` check. It should be `isinstance(obj, Sequence)`.
- **tinydb-i02** `tinydb/storages.py:27` (prism-reviewer:v2): This is a bit confusing. I think this should be `if base_dir and not os.path.exists(base_dir):`
- **tinydb-i03** `tinydb/table.py:193` (claude): The condition is inverted. Branch body reads document.doc_id and keeps the caller's ID, which only makes sense for document_class instances. With `not isinstance(...)`, plain Mapping inputs raise AttributeError on `.doc_id`, while real D...
- **tinydb-i04** `tinydb/table.py:250` (claude): Inverted check: `if not is_cacheable():` stores results in the query cache only for queries that are NOT cacheable (those with no stable hash, where hash(cond) can raise or collide) and never caches the cacheable ones. It defeats the cac...
- **tinydb-i05** `tinydb/queries.py:128` (claude): __or__ builds its test with `and` instead of `or`, so `q1 | q2` behaves exactly like `q1 & q2` while hashing as an 'or' query. Results are wrong, and cached results for the 'or' hash would be the 'and' result.
- **tinydb-i06** `tinydb/table.py:113` (claude): `persist_empty or not self._read_table()` still clears the table whenever persist_empty is True, which is the data-loss the comment says is being fixed. Additionally, with persist_empty False it now writes to storage whenever the table i...
- **tinydb-i07** `tinydb/table.py:465` (claude): Condition is wrong: it raises when a doc_id IS provided and cond is None, which is exactly the supported upsert(Document(..., doc_id=1)) case (test_upsert_by_id fails). When neither is given (plain dict, no query) it does not raise, and ...
- **tinydb-i08** `tinydb/table.py:497` (claude): Inverted check: the block runs when `doc_ids is None` and immediately does `list(doc_ids)`, raising TypeError for every remove(cond) call, while remove(doc_ids=[...]) skips the block. It should run when doc_ids is not None.
- **tinydb-i09** `tinydb/table.py:229` (claude): The None check is inverted. On a cache miss (`cached_results is None`) the code evaluates `None[:]` and raises TypeError, so a first search always crashes; on a cache hit it falls through and recomputes. The cache lookup also no longer r...
- **tinydb-i10** `tinydb/queries.py:437` (claude): fragment's test() returns False when every key matched (the final return), so Query.fragment can never match any document. It should return True after the loop; the added tests (test_fragment, test_fragment_with_path) would fail on their...
- **tinydb-i11** `tinydb/table.py:559` (prism-reviewer:v2): I think this is a bug. The `self._next_id` should be set to the value of `max_id + 1`, not `next_id`. I don't know why you changed it.
- **tinydb-i12** `tinydb/table.py:223` (claude): `next_id = max(...)` is the highest ID already in use, but it is stored in `self._next_id` without adding 1. `_get_next_id()` returns `self._next_id` as-is when it is set, so the new document gets the existing max ID and `table[doc_id] =...
- **tinydb-i13** `tinydb/utils.py:97` (claude): The arguments to `self.cache.get` are swapped: `get(_missing, key)` looks up the key `_missing` and uses `key` as the default. The lookup always misses, so `value` is the caller's key and never `_missing`. `get()` then calls `move_to_end...
- **tinydb-i14** `tinydb/queries.py:78` (claude): `hash()` on an unhashable value (a list or numpy array inside the tuple) raises `TypeError`, not `ValueError`, so this `except` never catches the case the comment describes and the constructor still crashes. (NumPy only raises ValueError...
- **tinydb-i15** `tinydb/table.py:149` (claude): The `isinstance` arguments are reversed. `isinstance(Document, document)` passes the class as the object and an instance as the type, so it raises `TypeError: isinstance() arg 2 must be a type` for every plain-dict insert. Every `insert(...
