# BugBench: aiosqlite, jsoup, sqlite-utils, tinydb (claude-only-01)

Each pull request is a real commit. Injected cases carry one planted defect at a known line; real-bug cases are commits that introduced a bug a later commit fixed; clean cases are left as they are. A hit is a posted comment on the defect's file within 3 lines of it.

| Set | Recall | Near but dropped | Comments per PR | Hits by model |
|---|---|---|---|---|
| Injected | 49/57 (0.86) | 2 | 1.04 | claude 49 |
| Real bugs | 6/10 (0.60) | 2 | 0.70 | claude 5, ruff 1 |

Clean PRs: 0 of 16 got at least one comment (false-positive rate 0.00), 0.00 comments per PR. A comment on a clean PR may still be a true issue in the original code; this counts it as a false positive.

| Repo (injected) | Recall | Near but dropped | Comments per PR | Hits by model |
|---|---|---|---|---|
| aiosqlite | 8/10 (0.80) | 0 | 0.90 | claude 8 |
| jsoup | 12/14 (0.86) | 2 | 1.00 | claude 12 |
| sqlite-utils | 15/18 (0.83) | 0 | 1.06 | claude 15 |
| tinydb | 14/15 (0.93) | 0 | 1.13 | claude 14 |

First run p50 / p95: 6.19 s / 34.66 s.

Reviewer `prism-reviewer:v2`, gate `onnx`, context `graph`, verifier `handoff`, SAST on, escalation on, minimum confidence 0.60. First-run time covers the local reviewer, SAST and indexing the checkout in memory; Claude's answers come from a handoff and are not timed.

## Cases

| Case | Set | Defect | Where | Hit | By | Posted | Considered |
|---|---|---|---|---|---|---|---|
| aiosqlite-r01 | real | late-binding closure | `aiosqlite/core.py:101,102,103,105,109,110,111,113` | yes | ruff | 2 | 5 |
| aiosqlite-r02 | real | wrong conversion | `aiosqlite/core.py:273` | no | - | 0 | 0 |
| aiosqlite-i01 | injected | missing await | `aiosqlite/core.py:287` | yes | claude | 1 | 1 |
| aiosqlite-i02 | injected | missing await | `aiosqlite/core.py:218` | yes | claude | 1 | 1 |
| aiosqlite-i03 | injected | missing await | `aiosqlite/core.py:215` | yes | claude | 1 | 1 |
| aiosqlite-i04 | injected | wrong null check | `aiosqlite/cursor.py:23` | yes | claude | 1 | 1 |
| aiosqlite-i05 | injected | inverted condition | `aiosqlite/core.py:65` | yes | claude | 1 | 2 |
| aiosqlite-i06 | injected | swapped arguments | `aiosqlite/core.py:101` | no | - | 1 | 1 |
| aiosqlite-i08 | injected | swapped arguments | `aiosqlite/core.py:340` | yes | claude | 1 | 1 |
| aiosqlite-i09 | injected | wrong null check | `aiosqlite/core.py:163` | yes | claude | 1 | 1 |
| aiosqlite-i10 | injected | inverted condition | `aiosqlite/core.py:362` | yes | claude | 1 | 1 |
| aiosqlite-i11 | injected | swapped arguments | `aiosqlite/core.py:380` | no | - | 0 | 3 |
| aiosqlite-c01 | clean | clean | - | - | - | 0 | 0 |
| aiosqlite-c02 | clean | clean | - | - | - | 0 | 0 |
| aiosqlite-c03 | clean | clean | - | - | - | 0 | 0 |
| aiosqlite-c04 | clean | clean | - | - | - | 0 | 0 |
| jsoup-r01 | real | null dereference | `src/main/java/org/jsoup/helper/W3CDom.java:357,358,362` | yes | claude | 1 | 2 |
| jsoup-r02 | real | wrong escaping | `src/main/java/org/jsoup/nodes/DocumentType.java:98,99` | dropped | - | 0 | 6 |
| jsoup-i01 | injected | wrong boolean operator | `src/main/java/org/jsoup/nodes/Element.java:1601` | yes | claude | 1 | 2 |
| jsoup-i02 | injected | wrong return value | `src/main/java/org/jsoup/nodes/FormElement.java:87` | yes | claude | 1 | 2 |
| jsoup-i03 | injected | inverted condition | `src/main/java/org/jsoup/nodes/DocumentType.java:103` | yes | claude | 1 | 1 |
| jsoup-i04 | injected | wrong null check | `src/main/java/org/jsoup/parser/XmlTreeBuilder.java:61` | yes | claude | 1 | 8 |
| jsoup-i05 | injected | swapped arguments | `src/main/java/org/jsoup/parser/CharacterReader.java:523` | yes | claude | 1 | 4 |
| jsoup-i07 | injected | off-by-one | `src/main/java/org/jsoup/parser/Tokeniser.java:369` | dropped | - | 0 | 6 |
| jsoup-i08 | injected | wrong null check | `src/main/java/org/jsoup/nodes/Element.java:2105` | yes | claude | 1 | 4 |
| jsoup-i09 | injected | wrong null check | `src/main/java/org/jsoup/nodes/Node.java:559` | yes | claude | 1 | 5 |
| jsoup-i10 | injected | wrong boolean operator | `src/main/java/org/jsoup/helper/DataUtil.java:307` | yes | claude | 1 | 2 |
| jsoup-i11 | injected | wrong boolean operator | `src/main/java/org/jsoup/parser/HtmlTreeBuilder.java:539` | yes | claude | 2 | 4 |
| jsoup-i12 | injected | off-by-one | `src/main/java/org/jsoup/parser/TreeBuilder.java:181` | dropped | - | 1 | 8 |
| jsoup-i13 | injected | wrong return value | `src/main/java/org/jsoup/parser/CharacterReader.java:618` | yes | claude | 1 | 2 |
| jsoup-i14 | injected | inverted condition | `src/main/java/org/jsoup/nodes/Element.java:2006` | yes | claude | 1 | 5 |
| jsoup-i15 | injected | swapped arguments | `src/main/java/org/jsoup/internal/StringUtil.java:357` | yes | claude | 1 | 3 |
| jsoup-c01 | clean | clean | - | - | - | 0 | 0 |
| jsoup-c02 | clean | clean | - | - | - | 0 | 3 |
| jsoup-c03 | clean | clean | - | - | - | 0 | 0 |
| jsoup-c04 | clean | clean | - | - | - | 0 | 2 |
| sqlite-utils-r01 | real | missing case | `sqlite_utils/utils.py:16,17` | yes | claude | 1 | 2 |
| sqlite-utils-r02 | real | wrong check | `sqlite_utils/db.py:964,965` | dropped | - | 0 | 1 |
| sqlite-utils-r03 | real | unquoted SQL identifier | `sqlite_utils/db.py:294` | yes | claude | 1 | 3 |
| sqlite-utils-s01 | injected | SQL injection | `sqlite_utils/db.py:2920` | yes | claude | 1 | 1 |
| sqlite-utils-s02 | injected | SQL injection | `sqlite_utils/cli.py:3002` | yes | claude | 3 | 3 |
| sqlite-utils-s03 | injected | SQL injection | `sqlite_utils/db.py:3129` | yes | claude | 2 | 3 |
| sqlite-utils-i01 | injected | wrong null check | `sqlite_utils/cli.py:2032` | yes | claude | 1 | 2 |
| sqlite-utils-i02 | injected | swapped arguments | `sqlite_utils/db.py:615` | yes | claude | 1 | 2 |
| sqlite-utils-i03 | injected | resource leak | `sqlite_utils/utils.py:383` | no | - | 0 | 0 |
| sqlite-utils-i04 | injected | inverted condition | `sqlite_utils/cli.py:2487` | no | - | 1 | 1 |
| sqlite-utils-i05 | injected | wrong boolean operator | `sqlite_utils/db.py:3184` | yes | claude | 1 | 1 |
| sqlite-utils-i06 | injected | wrong exception handling | `sqlite_utils/db.py:4562` | yes | claude | 1 | 1 |
| sqlite-utils-i07 | injected | inverted condition | `sqlite_utils/cli.py:1028` | yes | claude | 1 | 2 |
| sqlite-utils-i08 | injected | wrong boolean operator | `sqlite_utils/db.py:3868` | yes | claude | 1 | 1 |
| sqlite-utils-i09 | injected | swapped arguments | `sqlite_utils/db.py:1364` | yes | claude | 1 | 2 |
| sqlite-utils-i10 | injected | wrong null check | `sqlite_utils/db.py:708` | yes | claude | 1 | 3 |
| sqlite-utils-i11 | injected | swapped arguments | `sqlite_utils/utils.py:480` | no | - | 0 | 0 |
| sqlite-utils-i12 | injected | wrong boolean operator | `sqlite_utils/cli.py:1206` | yes | claude | 1 | 1 |
| sqlite-utils-i13 | injected | inverted condition | `sqlite_utils/db.py:880` | yes | claude | 1 | 1 |
| sqlite-utils-i14 | injected | off-by-one | `sqlite_utils/db.py:4243` | yes | claude | 1 | 1 |
| sqlite-utils-i15 | injected | off-by-one | `sqlite_utils/cli.py:3428` | yes | claude | 1 | 1 |
| sqlite-utils-c01 | clean | clean | - | - | - | 0 | 0 |
| sqlite-utils-c02 | clean | clean | - | - | - | 0 | 0 |
| sqlite-utils-c03 | clean | clean | - | - | - | 0 | 0 |
| sqlite-utils-c04 | clean | clean | - | - | - | 0 | 1 |
| tinydb-r01 | real | data loss | `tinydb/table.py:114` | no | - | 0 | 0 |
| tinydb-r02 | real | missing check | `tinydb/table.py:369` | yes | claude | 1 | 1 |
| tinydb-r03 | real | missing check | `tinydb/storages.py:21` | yes | claude | 1 | 1 |
| tinydb-i01 | injected | swapped arguments | `tinydb/queries.py:31` | yes | claude | 1 | 1 |
| tinydb-i02 | injected | inverted condition | `tinydb/storages.py:27` | yes | claude | 1 | 1 |
| tinydb-i03 | injected | inverted condition | `tinydb/table.py:193` | yes | claude | 1 | 1 |
| tinydb-i04 | injected | inverted condition | `tinydb/table.py:250` | yes | claude | 1 | 1 |
| tinydb-i05 | injected | wrong boolean operator | `tinydb/queries.py:128` | yes | claude | 2 | 4 |
| tinydb-i06 | injected | wrong boolean operator | `tinydb/table.py:113` | yes | claude | 1 | 1 |
| tinydb-i07 | injected | wrong null check | `tinydb/table.py:465` | yes | claude | 1 | 2 |
| tinydb-i08 | injected | wrong null check | `tinydb/table.py:497` | yes | claude | 1 | 1 |
| tinydb-i09 | injected | wrong null check | `tinydb/table.py:229` | yes | claude | 1 | 1 |
| tinydb-i10 | injected | wrong return value | `tinydb/queries.py:437` | yes | claude | 2 | 3 |
| tinydb-i11 | injected | off-by-one | `tinydb/table.py:559` | no | - | 0 | 0 |
| tinydb-i12 | injected | off-by-one | `tinydb/table.py:220` | yes | claude | 1 | 1 |
| tinydb-i13 | injected | swapped arguments | `tinydb/utils.py:97` | yes | claude | 1 | 1 |
| tinydb-i14 | injected | wrong exception handling | `tinydb/queries.py:78` | yes | claude | 1 | 1 |
| tinydb-i15 | injected | swapped arguments | `tinydb/table.py:149` | yes | claude | 2 | 2 |
| tinydb-c01 | clean | clean | - | - | - | 0 | 0 |
| tinydb-c02 | clean | clean | - | - | - | 0 | 0 |
| tinydb-c03 | clean | clean | - | - | - | 0 | 0 |
| tinydb-c04 | clean | clean | - | - | - | 0 | 0 |

## Comments near the defects

- **aiosqlite-r01** `aiosqlite/core.py:102` (ruff): Function definition does not bind loop variable `future` (ruff B023)
- **aiosqlite-r01** `aiosqlite/core.py:105` (claude): The nested set_result/set_exception close over the loop variable `future` and run later on the event loop thread. By then the worker loop may have fetched the next item and rebound `future`, so the result or exception can be set on the w...
- **aiosqlite-i01** `aiosqlite/core.py:287` (claude): `self._execute(...)` is not awaited, so the coroutine is never run: the progress handler is never installed and Python emits 'coroutine was never awaited'.
- **aiosqlite-i02** `aiosqlite/core.py:218` (claude): `self._execute(...)` is not awaited, so create_function never actually runs on the connection thread; the function is not registered and the coroutine is never awaited.
- **aiosqlite-i03** `aiosqlite/core.py:215` (claude): In the 3.8+ branch `self._execute(...)` is not awaited (the else branch does await), so the function is never registered on newer Pythons and a never-awaited coroutine warning is raised.
- **aiosqlite-i04** `aiosqlite/cursor.py:24` (claude): The condition is inverted: `if row is not None: raise StopAsyncIteration` stops iteration on the first real row and, at end of data, returns None instead of raising. `async for` over a cursor is broken (yields nothing, or None forever).
- **aiosqlite-i05** `aiosqlite/core.py:65` (claude): The condition is inverted: `if not future:` skips setting the result for every real future (futures are truthy), so awaiting callers hang forever, and when future is None it would call None.get_loop() and raise AttributeError. It should ...
- **aiosqlite-i08** `aiosqlite/core.py:340` (claude): Arguments to _execute are swapped: _execute takes the function first, then its args. This calls authorizer_callback(self._conn.set_authorizer) on the worker thread instead of conn.set_authorizer(callback), so the authorizer is never set ...
- **aiosqlite-i09** `aiosqlite/core.py:163` (claude): The guard is inverted: when the connection is open it returns immediately without closing anything, and when it is already closed (_connection is None) it goes on to use `self._conn`, which raises. close() now never closes a live connect...
- **aiosqlite-i10** `aiosqlite/core.py:362` (claude): The empty-queue branch is inverted. An empty queue while the dump task is still running is the normal case (the consumer outpaces the dumper thread), yet `if not task.done()` logs 'completed unexpectedly' and breaks. The iterator then st...
- **jsoup-r01** `src/main/java/org/jsoup/helper/W3CDom.java:358` (claude): The namespace-aware check runs in the constructor, but callers set the flag afterwards (`W3CBuilder builder = new W3CBuilder(out); builder.namespaceAware = namespaceAware;`). So here `namespaceAware` is always the field default `true`, a...
- **jsoup-i01** `src/main/java/org/jsoup/nodes/Element.java:1599` (claude): Regression risk in text(): the old tail() added a separating space after every non-inline (block) element that is followed by a TextNode or inline element, so <div>One</div>Two gave "One Two". needsTrailingTextSeparator now only returns ...
- **jsoup-i02** `src/main/java/org/jsoup/nodes/FormElement.java:87` (claude): isDescendant() ends with `return true`, so it returns true for every element, including controls that are not descendants. FormElement.empty() would then drop all associated controls, including ones attached from outside the form via add...
- **jsoup-i03** `src/main/java/org/jsoup/nodes/DocumentType.java:103` (claude): The condition is inverted: `if (!has(SystemId))` runs the block only when there is no system identifier. A doctype with a SYSTEM id now loses it, and one without one gets an empty `""` appended. The new test and any existing round-trip w...
- **jsoup-i04** `src/main/java/org/jsoup/parser/XmlTreeBuilder.java:61` (claude): restore() has its condition inverted. When a previous value existed it removes the prefix instead of restoring it, and when the prefix was previously unbound it does put(prefix, null). Overridden bindings are therefore lost after the sco...
- **jsoup-i05** `src/main/java/org/jsoup/parser/CharacterReader.java:523` (claude): The arguments to consumeRange are swapped (`pos, start`) in the early-return branch, so the tag name is consumed with start > end. Every tag name that ends at a delimiter before the buffer end will hit this, which will throw or return ga...
- **jsoup-i08** `src/main/java/org/jsoup/nodes/Element.java:2105` (claude): Condition is inverted. When attributes is non-null the method returns null, so every cloned element loses its attributes; when attributes is null it dereferences null and throws NPE. This breaks clone()/shallowClone() generally.
- **jsoup-i09** `src/main/java/org/jsoup/nodes/Node.java:559` (claude): Loop condition `ancestor == null` is inverted. It only runs when parentNode is null (then NPE on ancestor.parentNode after the first body, or no iteration at all), so ancestors are never actually checked and cycles via ancestors are not ...
- **jsoup-i10** `src/main/java/org/jsoup/helper/DataUtil.java:307` (claude): Logic is inverted relative to the intent. For stream parsing (reuseDocIfFullyRead=false) it still falls back to input.baseReadFully(), so the input is still closed when the sniff fully read it, which is the bug being fixed. For a regular...
- **jsoup-i11** `src/main/java/org/jsoup/parser/HtmlTreeBuilder.java:539` (claude): onStackAboveContext uses `||`, so it returns true whenever the element is found (even when it is the fragment context element) and also true when it is not found at all (null != contextElement) in a fragment parse. It is only false when ...
- **jsoup-i13** `src/main/java/org/jsoup/parser/CharacterReader.java:618` (claude): When the scan loop finds no match, the method sets lastIcIndex = -1 but then returns true. containsIgnoreCase now reports a match for any sequence that is absent. The cached path just above returns false when lastIcIndex == -1, so repeat...
- **jsoup-i14** `src/main/java/org/jsoup/nodes/Element.java:2006` (claude): The condition is inverted compared with the original. Before, the block ran when childNodes.isEmpty() and the else branch appended '>'. Now the block (xml self-close or "></tag>") runs for elements that have children and are not void, an...
- **jsoup-i15** `src/main/java/org/jsoup/internal/StringUtil.java:357` (claude): String.substring arguments are reversed: substring(colon, 0) has begin > end and always throws StringIndexOutOfBoundsException whenever the value contains a colon after index 0. hasHttpScheme is called from resolve(String,String) and Saf...
- **sqlite-utils-r01** `sqlite_utils/utils.py:16` (claude): Skipping None values means a column whose values are all None (e.g. a single record {'a': None}) never gets an entry in all_column_types, so it is missing from the returned dict. Previously it got NoneType and fell through to str. Caller...
- **sqlite-utils-r03** `sqlite_utils/db.py:294` (claude): Table and column names are interpolated unquoted into DDL that is written straight into sqlite_master with writable_schema on. A name containing spaces, keywords, quotes, or SQL fragments produces a broken or attacker-shaped schema, whic...
- **sqlite-utils-s01** `sqlite_utils/db.py:2920` (claude): Table name is interpolated into the SQL with an f-string. A table name containing a single quote breaks the query, and a crafted name allows SQL injection. A bound parameter is already the pattern used elsewhere.
- **sqlite-utils-s02** `sqlite_utils/cli.py:3000` (claude): Splitting on any `:` breaks Windows absolute paths. `C:\ext\mod_spatialite.dll`, or the result of `find_spatialite()` on Windows, is parsed as path `C` with entrypoint `\ext\mod_spatialite.dll`, whereas it previously loaded correctly via...
- **sqlite-utils-s02** `sqlite_utils/cli.py:3002` (claude): `path` and `entrypoint` are interpolated straight into a SQL string. A path containing a single quote (e.g. `/home/o'brien/ext.so`) makes the statement invalid, and a crafted value such as `x', 'y') ; ...` alters the statement that is ex...
- **sqlite-utils-s03** `sqlite_utils/db.py:3129` (claude): The table and column names are formatted into the SQL with an f-string and single quotes. A name containing `'` breaks the query and a malicious name can inject SQL; `add_geometry_column` in the same patch correctly uses `?` placeholders...
- **sqlite-utils-i01** `sqlite_utils/cli.py:2032` (claude): Condition is inverted: `if tracker is None` runs transform(types=tracker.types) exactly when there is no tracker, so it raises AttributeError (None has no .types) for every JSON input or with --no-detect-types, and CSV/TSV files never ge...
- **sqlite-utils-i02** `sqlite_utils/db.py:615` (claude): zip arguments are swapped: dict(zip(row, keys)) uses the row values as keys and the column names as values, so every query() result is wrong (e.g. {"Cleo": "name"}). The original was zip(keys, row).
- **sqlite-utils-i05** `sqlite_utils/db.py:3184` (claude): The condition `not list_mode or not all_columns` is true for every dict-mode call, because `not list_mode` is True whenever list_mode is False. So every normal dict insert, with any columns, now returns `INSERT INTO t DEFAULT VALUES` and...
- **sqlite-utils-i06** `sqlite_utils/db.py:4562` (claude): The lookup of the record's key values catches IndexError, but the failure mode here is a missing key. `first_record_dict[resolve_casing(c, first_record_dict)]` raises KeyError when the record lacks a key column. This happens when pk is n...
- **sqlite-utils-i07** `sqlite_utils/cli.py:1028` (claude): The condition is inverted. The comment says type detection is now the default unless --no-detect-types is passed, but `if no_detect_types:` creates the TypeTracker only when the user asks for no detection. Default CSV/TSV imports therefo...
- **sqlite-utils-i08** `sqlite_utils/db.py:3868` (claude): `or` should be `and`. With `or`, any non-None value in a column that is not in `extracts` enters the branch and `extracts[key]` raises KeyError. None values in extracted columns also still pass through lookup(), which defeats the purpose...
- **sqlite-utils-i09** `sqlite_utils/db.py:1364` (claude): The arguments to typing.cast are reversed. cast(type, value) returns value, so cast(fk.other_column, str) returns the `str` class itself. quote_identifier() then receives a class instead of the column name, so CREATE TABLE with a foreign...
- **sqlite-utils-i10** `sqlite_utils/db.py:708` (claude): The condition looks inverted. A plain INSERT/UPDATE/CREATE has cursor.description None, so `cursor.description is not None` is false and the implicit transaction is never committed. Only row-returning statements pass, and committing thos...
- **sqlite-utils-i12** `sqlite_utils/cli.py:1203` (claude): Operator precedence bug: `A and B or C` parses as `(tracker is not None and not table_existed_before_insert) or db.table(table).exists()`. For any pre-existing table the `exists()` arm is true, so the transform still runs and the TEXT-zi...
- **sqlite-utils-i13** `sqlite_utils/db.py:880` (claude): The cleanup condition is inverted. `released` is set to True after the savepoint is successfully released (and the old code rolled back on `not released`). With `released and in_transaction`, the success path inside an outer transaction ...
- **sqlite-utils-i14** `sqlite_utils/db.py:4243` (claude): The pluralisation condition `len(missing_pk_cols) >= 1` is always true for a non-empty list, so the message always says "columns". The new test expects the singular "Invalid primary key column ['not_a_column']" for one missing column, so...
- **sqlite-utils-i15** `sqlite_utils/cli.py:3428` (claude): The legacy sqlite-migrate branch raises when `len(distinct) >= 1`, i.e. even for a single --stop-before value, which is the case it claims to support. The following line `distinct[0] if distinct else None` is then reachable only when not...
- **tinydb-r02** `tinydb/table.py:369` (claude): __len__ reads the raw storage and indexes it by table name. If the storage is empty (read() returns None) this raises TypeError, and for a table that has not been written yet it raises KeyError, whereas the previous _read_table() handled...
- **tinydb-r03** `tinydb/storages.py:21` (claude): os.path.dirname of a bare filename (e.g. 'db.json') is '', so os.path.exists('') is False and os.makedirs('') raises FileNotFoundError. create_dirs=True therefore breaks for files in the current directory. Also exists/makedirs is racy; u...
- **tinydb-i01** `tinydb/queries.py:31` (claude): isinstance arguments are swapped: isinstance(Sequence, obj) asks whether the Sequence class is an instance of obj, which raises TypeError for any non-type obj. Every any()/all() query with a list condition now crashes. Also restricting t...
- **tinydb-i02** `tinydb/storages.py:27` (claude): The condition is inverted. makedirs now runs when base_dir is empty (makedirs('') raises FileNotFoundError) or when the directory already exists (raises FileExistsError), and is skipped when the directory is actually missing, so create_d...
- **tinydb-i03** `tinydb/table.py:193` (claude): The condition is inverted: the existing-ID check (document.doc_id) now runs for documents that are NOT of document_class, i.e. plain dicts, which have no doc_id and raise AttributeError. insert_multiple of ordinary dicts breaks, and real...
- **tinydb-i04** `tinydb/table.py:250` (claude): The cacheability check is inverted: results are cached only when is_cacheable() returns False, so non-cacheable queries (mutable callables) get cached and stale results are served, while cacheable ones never are. The new test_lambda_quer...
- **tinydb-i05** `tinydb/queries.py:128` (claude): __or__ combines the operands with 'and' instead of 'or', so a | b behaves as a & b. Copy-paste error that breaks all OR queries.
- **tinydb-i06** `tinydb/table.py:113` (claude): The condition is wrong. With `persist_empty or not self._read_table()`, passing persist_empty=True still runs table.clear() on a table that already has documents, which is exactly the data wipe the comment and the new test say must not h...
- **tinydb-i07** `tinydb/table.py:465` (claude): The check is inverted. It raises ValueError when a Document with a doc_id is passed without cond, which is the case this change adds support for, so upsert(Document(..., doc_id=1)) always fails. It never raises for a plain dict with no c...
- **tinydb-i08** `tinydb/table.py:497` (claude): The guard is inverted. `if doc_ids is None:` then calls `list(doc_ids)`, which raises TypeError: 'NoneType' is not iterable. Every remove(cond) call now crashes before reaching the cond branch, and the doc_ids branch below it is never re...
- **tinydb-i09** `tinydb/table.py:229` (claude): The condition is inverted. `if cached_results is None: return cached_results[:]` slices None, so every uncached search raises TypeError. When there is a cache hit the code falls through and recomputes the search, so the cache is never us...
- **tinydb-i10** `tinydb/queries.py:437` (claude): The inner test always returns False, including when every key matched, so no document ever matches a fragment query. The final `return False` should be `return True`; the added test_fragment tests cannot pass.
- **tinydb-i12** `tinydb/table.py:223` (claude): Off-by-one: when _next_id is None this seeds _next_id with max(existing ids) (or 1 for an empty table) and then _get_next_id() returns self._next_id as-is and increments afterwards. For a non-empty table the next auto id equals the curre...
- **tinydb-i13** `tinydb/utils.py:97` (claude): Arguments to dict.get are swapped: self.cache.get(_missing, key) looks up the sentinel object as the key and returns `key` as the default. get() therefore never finds a cached entry, returns the key itself as a 'value' (move_to_end is th...
- **tinydb-i14** `tinydb/queries.py:78` (claude): hash() of an unhashable object raises TypeError, not ValueError, so this except never fires and the intended fallback to an uncacheable query does not happen. The new test with an Unhashable argument (is_cacheable() False) would fail wit...
- **tinydb-i15** `tinydb/table.py:149` (claude): isinstance arguments are reversed: isinstance(Document, document) tests the class Document against the instance, and raises TypeError ('arg 2 must be a type') for every insert of a dict. This breaks all inserts and the new doc_id feature.
