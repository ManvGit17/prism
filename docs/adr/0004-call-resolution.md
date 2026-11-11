# ADR 0004: Best-effort call resolution with confidence bands

Date: 2026-11-11. Status: accepted for Python. Java gets its own rules in a later stage.

## Context

Retrieval follows CALLS edges to find callers, callees and tests of a changed function. Python has
no static types to say what `x.save()` calls, so some edges are guesses. A wrong edge puts
unrelated code into the review context. On a large repo, a name match on `get` or `run` is mostly
wrong. Retrieval needs to know how far to trust each edge.

## Decision

The parser records each call site with its name and receiver: none (`f()`), `self`/`cls`,
`super()`, a dotted name (`a.b.f()`), or any other expression. The resolver then tries, in order:

1. **Same file.** A bare name defined in an enclosing function or at module level. Class bodies
   are not enclosing scopes, as in Python. Also `Name.f()` where `Name` is a class or function in
   the same file.
2. **Self.** `self.f()` on the enclosing class, then on its base classes in the repo. `super().f()`
   on the base classes.
3. **Imports.** The file's import bindings expand the head of the name, e.g. `pricing.with_tax`
   with `from . import pricing` becomes `shop.pricing.with_tax`. Re-exports in `__init__.py` are
   followed. If the import points outside the repo (`json`, `flask`), the call gets no edge.
4. **Star imports.** A bare name found in a module the file star-imports.
5. **Name match.** Anything else matches functions (bare calls) or methods (attribute calls) with
   the same short name anywhere in the repo. Builtins get no edge. A name with more than 10
   candidates gets no edge at all.

Each CALLS edge stores `resolution` and a `confidence` from a fixed band:

| Resolution | Confidence |
|---|---|
| `same_file` | 0.95 |
| `self_method` | 0.9 |
| `import` | 0.9 |
| `inherited` | 0.8 |
| `star_import` | 0.7 |
| `name_unique` (one candidate) | 0.6 |
| `name_ambiguous` (2 to 10 candidates) | 0.3 |
| `name_common` (a common name or a dunder, any count up to 10) | 0.1 |

The common names are a fixed list in `prism/indexer/resolver.py`: builtin container and string
methods (`append`, `get`, `items`) and verbs most codebases reuse (`run`, `save`, `process`).

Graph queries leave out CALLS and TESTS edges below `PRISM_CALL_CONFIDENCE_THRESHOLD`, default
0.5. So name matches with one candidate are kept, and ambiguous or common-name matches are left
out. A caller can lower the threshold per query. All edges are stored either way.

A TESTS edge goes from a test function to each non-test function or class it calls directly. It
copies the confidence of the CALLS edge. A test is a `test*` function in a test file, or a `test*`
method in a test class. A test file is `test_*.py`, `*_test.py`, `tests.py`, `conftest.py`, or
any file under `tests/` or `test/`. A test class starts with `Test`, subclasses a `*TestCase`, or
ends in `Test`/`Tests` inside a test file.

A call to a class points at the class node, not at `__init__`.

## Limits

- **Dynamic dispatch.** `obj.save()` on a local variable or a parameter is a name match. The
  resolver does not infer types, so a method shared by several classes is ambiguous.
- **Reflection.** `getattr(mod, "f")`, `globals()["f"]` and string-based lookups are not followed.
  An alias such as `f = getattr(mod, "f")` at module level is not a definition, so calls to `f`
  fall back to a name match.
- **Functions passed as values.** `call_with_retry(fetch, ...)` gives an edge to `call_with_retry`
  only. Callbacks, `map(f, ...)` and handler registries are missed.
- **Decorators.** `@decorator` without parentheses is not a call, so the decorator gets no CALLS
  edge from the decorated function. A decorator that replaces the function is not modelled; calls
  go to the original definition.
- **Monkey-patching.** Assignments such as `Class.method = other` or `mock.patch(...)` are
  ignored.
- **Star imports.** Followed only into repo modules, and with re-exports at most five steps deep.
  `__all__` is not read, so private names can match.
- **Conditional definitions.** A module-level definition shadows an import of the same name, even
  when the definition is the fallback in `except ImportError:`. Redefinitions get a `#2` suffix
  and calls go to the first one.
- **Imports inside functions** are treated as file-level imports.
- **Module names** come from the path. `src/pkg/mod.py` is also reachable as `pkg.mod` when `src/`
  has no `__init__.py`. A repo file whose import name is a standard-library module name is not used
  for imports of that name. Other `sys.path` changes are not modelled.
- **PR overlays** re-resolve only the changed files. A name-matched edge in an unchanged file keeps
  its old target even if the PR adds a better candidate. Edges from unchanged files into a symbol
  the PR removes are dropped.

## Consequences

- Retrieval sees fewer but more reliable edges by default. Recall for dynamic code is lower.
- The bands are fixed guesses, not measured. When code-graph-bench has ground truth for PRism,
  report precision and recall per band and adjust the bands and the threshold from that.
- Each edge says how it was found, so a reviewer prompt can mark low-confidence context as such.
