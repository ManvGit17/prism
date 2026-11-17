# ADR 0004: Best-effort call resolution with confidence bands

Status: accepted.

## Context

Retrieval follows CALLS edges to find callers, callees and tests of a changed function. Python has
no static types to say what `x.save()` calls, so some edges are guesses. A wrong edge puts
unrelated code into the review context. On a large repo, a name match on `get` or `run` is mostly
wrong. Retrieval needs to know how far to trust each edge.

## Decision: Python

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
| `same_package` (Java) | 0.85 |
| `inherited` | 0.8 |
| `star_import` | 0.7 |
| `name_unique` (one candidate) | 0.6 |
| `name_ambiguous` (2 to 10 candidates) | 0.3 |
| `name_common` (a common name or a dunder, any count up to 10) | 0.1 |

The common names are a fixed list in `prism/indexer/resolver.py`: builtin container and string
methods (`append`, `get`, `items`), verbs most codebases reuse (`run`, `save`, `process`), and
Java's (`size`, `equals`, `toString`, `valueOf`, `of`, `getName`). Overloads of one method in one
class count as a single candidate when deciding between `name_unique` and `name_ambiguous`.

Graph queries leave out CALLS and TESTS edges below `PRISM_CALL_CONFIDENCE_THRESHOLD`, default
0.5. So name matches with one candidate are kept, and ambiguous or common-name matches are left
out. A caller can lower the threshold per query. All edges are stored either way.

A TESTS edge goes from a test function to each non-test function or class it calls directly. It
copies the confidence of the CALLS edge. A test is a `test*` function in a test file, or a `test*`
method in a test class. A test file is `test_*.py`, `*_test.py`, `tests.py`, `conftest.py`, or
any file under `tests/` or `test/`. A test class starts with `Test`, subclasses a `*TestCase`, or
ends in `Test`/`Tests` inside a test file.

A call to a class points at the class node, not at `__init__`. In Java, `new T()`, `super(...)`
and `T::new` point at the class node, not at a constructor.

Two more edge kinds come from the same pass. INHERITS goes from a class to each repo class or
interface it extends or implements. OVERRIDES goes from a method to the nearest supertype method
it overrides or implements (Python: same name in a base class; Java: same parameter types, else
same name and parameter count). `overrides`, `overridden_by`, `supertypes` and `subtypes` on the
store follow them, so retrieval can find the interface method a change implements.

## Decision: Java

Qualified names follow the source: `com.acme.shop.Cart.Line`. A method name carries its erased
parameter types, `com.acme.shop.Cart.add(Item,int)`, and a constructor is `<init>(...)`. Always
adding the parameters keeps a method's name stable when an overload is added later; Java forbids
two methods with the same erasure, so names stay unique. Lookup by the plain name or `Cart.add`
returns every overload; `Cart.add(Item,int)` picks one.

A type name is found in this order: the enclosing class and its outer classes and their nested
types (`same_file`), a top-level type in the same file (`same_file`), a single-type import
(`import`), the same package (`same_package`), on-demand imports (`star_import`). A fully
qualified repo name counts as `import`. An import or a `java.lang` name outside the repo means the
call gets no edge.

A call is resolved in this order:

1. **Same class.** An unqualified call or `this.m()` finds `m` on the enclosing class
   (`self_method`), then on its repo supertypes (`inherited`). `super.m()` looks only in the
   supertypes.
2. **Outer classes.** An unqualified call inside a nested class finds `m` on an outer class
   (`same_file`).
3. **Static imports.** `import static a.B.m` (`import`), then `import static a.B.*`
   (`star_import`).
4. **Typed receivers.** If the receiver's type is written down (a parameter, a local, a field of
   the enclosing classes, `var x = new T()`, a cast, `new T().m()`), or the receiver is a class
   name (`Util.run()`, `a.b.Util.run()`), the method is looked up on that type and its
   supertypes, and the edge takes the band that found the type. A repo type without the method
   means an external supertype's method: no edge.
5. **Name match** among Java methods, as for Python.

Overloads are narrowed by argument count, then by the argument types the source states
(literals, casts, `new T()`, declared variables, `null`). As in javac, overloads that fit without
boxing win over those that need it. Unknown argument types fit anything. If narrowing leaves
none, all overloads with the name stay.

Java tests: methods annotated `@Test`, `@ParameterizedTest`, `@RepeatedTest` or `@TestFactory`,
and the public methods of a class annotated `@Test` (TestNG). Test files and classes: anything
under `src/test/`, and names ending in `Test`, `Tests`, `IT` or `TestCase`.

## Limits

- **Dynamic dispatch.** `obj.save()` on a local variable or a parameter is a name match. The
  resolver does not infer types, so a method shared by several classes is ambiguous.
- **Reflection.** `getattr(mod, "f")`, `globals()["f"]` and string-based lookups are not followed.
  An alias such as `f = getattr(mod, "f")` at module level is not a definition, so calls to `f`
  fall back to a name match.
- **Functions passed as values.** `call_with_retry(fetch, ...)` gives an edge to `call_with_retry`
  only. Callbacks, `map(f, ...)` and handler registries are missed.
- **Decorators.** Applying a decorator counts as a call from the scope the definition sits in:
  `@login_required` and `@route("/")` both give the module (or class) a CALLS edge to the
  decorator. A decorator that replaces the function is not modelled; calls go to the original
  definition.
- **Monkey-patching.** Assignments such as `Class.method = other` or `mock.patch(...)` are
  ignored.
- **Star imports.** Followed only into repo modules, and with re-exports at most five steps deep.
  `__all__` is not read, so private names can match.
- **Conditional definitions.** A module-level definition inside an `except` clause
  (`is_fallback`) loses to a repo import of the same name in that file, since the import is what
  normally runs. It is skipped for same-file lookups, for lookups through the module, and as a
  name-match candidate. Without such an import it resolves like any other definition. Other
  conditionals (`if`, `else`) are not modelled: redefinitions get a `#2` suffix and calls go to
  the first one.
- **Imports inside functions** are treated as file-level imports.
- **Module names** come from the path. `src/pkg/mod.py` is also reachable as `pkg.mod` when `src/`
  has no `__init__.py`. A repo file whose import name is a standard-library module name is not used
  for imports of that name. Other `sys.path` changes are not modelled.
- **PR overlays** re-resolve only the changed files. A name-matched edge in an unchanged file keeps
  its old target even if the PR adds a better candidate. Edges from unchanged files into a symbol
  the PR removes are dropped.
- **Incremental base updates** re-resolve the changed files and their dependents (ADR 0006). A
  file whose only link to a changed file is a name match the change creates is not a dependent,
  so its edges stay as they were until the next full index. In Java, every file in the same
  package and every file importing a changed type or package is a dependent.

Java only:

- **Overload selection without types.** Arguments whose type needs inference (method results,
  field accesses on other objects, arithmetic) fit any overload, so a call can link to several.
- **Generics.** Type parameters are erased to their names (`T`), which resolve to nothing. A
  receiver typed `T` falls to a name match even when `T extends Repo`.
- **Lambdas and method references.** Lambda parameters have no declared type, so calls on them
  are name matches. `Foo::bar` links to every overload of `bar`.
- **Chained calls.** `a.b().c()` and `a.field.m()` do not know the receiver type. A dotted
  receiver with a lowercase head that is not a repo class (`data.source.m()`) gets no edge.
- **Inherited fields.** A field declared in a superclass has no known type in the subclass.
- **Reflection.** `Class.forName`, `Method.invoke` and annotation processors are not followed.
- **Lombok and other generated code.** Getters, setters and builders that Lombok adds do not
  exist in the source, so calls to them get no edge or a wrong name match. Generated-source
  folders are skipped.
- **Dependency injection.** A field typed by an interface links to the interface method. The
  implementation a container injects is reached only through `overridden_by`.
- **Inner, local and anonymous classes.** Nested classes are nodes. Local and anonymous classes
  are not: their methods' calls belong to the enclosing method, and `this.m()` inside them is
  resolved against the outer class.
- **Constructors.** `new T()` links to the class, so callers of a constructor are the callers of
  its class.

## Consequences

- Retrieval sees fewer but more reliable edges by default. Recall for dynamic code is lower.
- The bands are fixed estimates, not measured against ground truth.
- Each edge says how it was found, so a reviewer prompt can mark low-confidence context as such.
