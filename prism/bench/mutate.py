"""Generating injected BugBench cases: mutations on a commit's added lines, kept only when a
test catches them.

For each recent commit that touches the repo's source, every added source line is offered to a
set of single-line mutation operators (off-by-one, wrong null check, swapped arguments, missing
`await`, ...). A candidate becomes a case only if the repo's tests pass at the commit and fail
with the mutation, so every injected defect changes behaviour that a test checks. Mutations
that do not parse (Python) or do not compile (Java) are thrown away, since a build error is
not a review finding. One case per commit, and the operators are drawn so the kinds stay
balanced. Commits whose tests pass and that got no case can become clean cases.
"""

from __future__ import annotations

import ast
import os
import random
import re
import subprocess
import sys
import time
import tomllib
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prism.bench.cases import checkout, git

MAX_CHANGED_LINES = 400  # skip commits too large to be a reviewable PR


@dataclass(frozen=True)
class RepoSpec:
    name: str
    url: str
    license: str
    language: str
    source: tuple[str, ...]
    exclude: tuple[str, ...]
    test: tuple[str, ...]
    timeout: int

    def is_source(self, path: str) -> bool:
        return path.startswith(self.source) and not path.startswith(self.exclude or ("\0",))


def load_repos(path: Path) -> dict[str, RepoSpec]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        name: RepoSpec(
            name=name,
            url=r["url"],
            license=r.get("license", ""),
            language=r["language"],
            source=tuple(r["source"]),
            exclude=tuple(r.get("exclude", ())),
            test=tuple(r["test"]),
            timeout=int(r.get("timeout", 300)),
        )
        for name, r in data.items()
    }


# Operators


@dataclass(frozen=True)
class Operator:
    name: str
    kind: str
    pattern: re.Pattern[str]
    repl: str | Callable[[re.Match[str]], str]  # "" with delete=True removes the line
    languages: frozenset[str]
    delete: bool = False
    accept: Callable[[re.Match[str]], bool] | None = None


def _op(
    name: str,
    kind: str,
    pattern: str,
    repl: str | Callable[[re.Match[str]], str],
    langs: str,
    *,
    delete: bool = False,
    accept: Callable[[re.Match[str]], bool] | None = None,
) -> Operator:
    return Operator(name, kind, re.compile(pattern), repl, frozenset(langs.split()), delete, accept)


def _distinct_args(m: re.Match[str]) -> bool:
    return m.group(2) != m.group(3)


# `not` binds looser than comparisons, tighter than `and`/`or`: parenthesize only then.
_LOOSE = re.compile(r"\b(and|or|if|lambda)\b")


_COMPARISON = re.compile(r" (is|in|==|!=|<|>|<=|>=) ")


def _negate_python(m: re.Match[str]) -> str:
    cond = m.group(3).strip()
    if _COMPARISON.search(cond) or cond in ("True", "False", "None", "1", "0"):
        # Comparisons have operators of their own (`not a is b` reads oddly), and negating a
        # literal (`while not True:`) is not a mistake anyone makes.
        return m.group(0)
    cond = f"({cond})" if _LOOSE.search(cond) else cond
    return f"{m.group(1)}{m.group(2)} not {cond}:"


_JAVA_COMPARISON = re.compile(r" (==|!=|<|>|<=|>=|instanceof) ")


def _negate_java(m: re.Match[str]) -> str:
    cond = m.group(1)
    if _JAVA_COMPARISON.search(cond) or cond in ("true", "false"):
        return m.group(0)  # as in Python: `!(i == -1)` and `!(true)` read as planted
    simple = re.fullmatch(r"[\w.]+(\([\w., ]*\))?", cond) is not None
    return f"if (!{cond}) {{" if simple else f"if (!({cond})) {{"


OPERATORS: tuple[Operator, ...] = (
    _op("drop-plus-one", "off-by-one", r" \+ 1\b", "", "python java"),
    _op("drop-minus-one", "off-by-one", r" - 1\b", "", "python java"),
    _op("le-to-lt", "off-by-one", r" <= ", " < ", "python java"),
    _op("lt-to-le", "off-by-one", r" < ", " <= ", "python java"),
    _op("ge-to-gt", "off-by-one", r" >= ", " > ", "python java"),
    _op("gt-to-ge", "off-by-one", r" > ", " >= ", "python java"),
    _op("is-not-none", "wrong null check", r"\bis not None\b", "is None", "python"),
    _op("is-none", "wrong null check", r"\bis None\b", "is not None", "python"),
    _op("ne-null", "wrong null check", r"!= null\b", "== null", "java"),
    _op("eq-null", "wrong null check", r"== null\b", "!= null", "java"),
    _op("and-to-or", "wrong boolean operator", r" and ", " or ", "python"),
    _op("or-to-and", "wrong boolean operator", r" or ", " and ", "python"),
    _op("andand-to-oror", "wrong boolean operator", r" && ", " || ", "java"),
    _op("oror-to-andand", "wrong boolean operator", r" \|\| ", " && ", "java"),
    _op(
        "negate-if",
        "inverted condition",
        r"^(\s*)(if|elif|while) (?!not )(.+):\s*$",
        _negate_python,
        "python",
    ),
    _op(
        "drop-not",
        "inverted condition",
        r"^(\s*)(if|elif|while) not (.+):\s*$",
        r"\1\2 \3:",
        "python",
    ),
    _op(
        "negate-if-java",
        "inverted condition",
        r"\bif \((?!!)(.+)\) \{\s*$",
        _negate_java,
        "java",
    ),
    _op("drop-await", "missing await", r"\bawait ", "", "python"),
    _op(
        "type-to-value",
        "wrong exception handling",
        r"\bexcept TypeError\b",
        "except ValueError",
        "python",
    ),
    _op(
        "value-to-type",
        "wrong exception handling",
        r"\bexcept ValueError\b",
        "except TypeError",
        "python",
    ),
    _op(
        "key-to-index",
        "wrong exception handling",
        r"\bexcept KeyError\b",
        "except IndexError",
        "python",
    ),
    _op(
        "index-to-key",
        "wrong exception handling",
        r"\bexcept IndexError\b",
        "except KeyError",
        "python",
    ),
    _op(
        "swap-args",
        "swapped arguments",
        r"\b(\w+)\(([\w.]+), ([\w.]+)\)",
        r"\1(\3, \2)",
        "python java",
        accept=_distinct_args,
    ),
    _op("true-to-false", "wrong return value", r"\breturn True\b", "return False", "python"),
    _op("false-to-true", "wrong return value", r"\breturn False\b", "return True", "python"),
    _op("true-to-false-java", "wrong return value", r"\breturn true;", "return false;", "java"),
    _op("false-to-true-java", "wrong return value", r"\breturn false;", "return true;", "java"),
    _op(
        "drop-close",
        "resource leak",
        r"^\s*[\w.]+\.close\(\);?\s*$",
        "",
        "python java",
        delete=True,
    ),
)


# Lines whose behaviour depends on the interpreter, not the code under review.
_ENVIRONMENT = re.compile(r"sys\.version_info|TYPE_CHECKING|sys\.platform|os\.name")


def _in_string_or_comment(line: str, pos: int, language: str) -> bool:
    before = line[:pos]
    if before.count('"') % 2 or before.count("'") % 2:
        return True
    stripped = line.lstrip()
    if language == "python":
        return "#" in before or stripped.startswith(('"""', "'''"))
    return "//" in before or stripped.startswith(("*", "/*"))


@dataclass(frozen=True)
class Mutation:
    commit: str
    path: str
    line_no: int
    operator: str
    kind: str
    find: str
    replace: str


def mutations_of_line(line: str, language: str) -> Iterator[tuple[Operator, str]]:
    """(operator, mutated line or "" for a deletion) for each operator that applies."""
    if _ENVIRONMENT.search(line):
        return
    for op in OPERATORS:
        if language not in op.languages:
            continue
        m = op.pattern.search(line)
        if m is None or _in_string_or_comment(line, m.start(), language):
            continue
        if op.accept is not None and not op.accept(m):
            continue
        if op.delete:
            yield op, ""
            continue
        body = op.repl(m) if callable(op.repl) else m.expand(op.repl)
        new = line[: m.start()] + body + line[m.end() :]
        if new != line:
            yield op, new


def anchor(text: str, lines: Sequence[str], idx: int, new: str | None) -> tuple[str, str] | None:
    """A (find, replace) pair that occurs once in `text`: the line, with up to three lines
    before it when the line alone is not unique. `new` None deletes the line."""
    for back in range(4):
        if idx - back < 0:
            return None
        before = lines[idx - back : idx]
        find = "\n".join([*before, lines[idx]]) + "\n"
        if text.count(find) == 1:
            replace = "\n".join(before) + ("\n" if before else "")
            if new is not None:
                replace += new + "\n"
            return find, replace
    return None


def _added_lines(clone: Path, commit: str, spec: RepoSpec) -> dict[str, list[int]]:
    diff = git(clone, "diff", "--no-color", "--unified=0", f"{commit}^", commit)
    out: dict[str, list[int]] = {}
    path, new_no = "", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else ""
        elif raw.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", raw)
            new_no = int(m.group(1)) if m else 0
        elif raw.startswith("+") and path and spec.is_source(path):
            if raw[1:].strip():
                out.setdefault(path, []).append(new_no)
            new_no += 1
        elif raw.startswith(" "):
            new_no += 1
    return out


def _prose_lines(text: str, language: str) -> set[int]:
    """Lines inside a multi-line string (docstrings) or a block comment: never mutated."""
    out: set[int] = set()
    if language == "python":
        import io
        import tokenize

        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.STRING and tok.end[0] > tok.start[0]:
                    out.update(range(tok.start[0], tok.end[0] + 1))
        except (tokenize.TokenError, SyntaxError):
            pass
        return out
    for m in re.finditer(r"/\*.*?\*/", text, re.S):
        first = text.count("\n", 0, m.start()) + 1
        out.update(range(first, first + m.group(0).count("\n") + 1))
    return out


def candidates(clone: Path, commit: str, spec: RepoSpec) -> list[Mutation]:
    out: list[Mutation] = []
    for path, numbers in _added_lines(clone, commit, spec).items():
        try:
            text = git(clone, "show", f"{commit}:{path}")
        except subprocess.CalledProcessError:
            continue
        lines = text.split("\n")
        prose = _prose_lines(text, spec.language)
        for no in numbers:
            if no in prose:
                continue
            line = lines[no - 1] if no - 1 < len(lines) else ""
            for op, new in mutations_of_line(line, spec.language):
                pair = anchor(text, lines, no - 1, None if op.delete else new)
                if pair is None:
                    continue
                mutated = text.replace(pair[0], pair[1], 1)
                if spec.language == "python" and not _parses(mutated):
                    continue
                out.append(Mutation(commit, path, no, op.name, op.kind, *pair))
    return out


def _parses(source: str) -> bool:
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


# Running the tests


@dataclass
class TestRun:
    passed: bool
    seconds: float
    failing: str = ""
    compile_error: bool = False


_FAILING = (
    re.compile(r"^(?:FAILED|ERROR) (\S+::\S+)", re.M),  # pytest (not unittest's summary)
    re.compile(r"^(?:FAIL|ERROR): (\S+ \([^)]*\))", re.M),  # unittest
    re.compile(r"^\[ERROR\] +(\w+\.\w+)[:(]", re.M),  # surefire
    re.compile(r"^\[ERROR\] Tests run: .*? in (\S+)", re.M),
)


def _placeholders(root: Path, spec: RepoSpec) -> dict[str, str]:
    scripts = "Scripts" if os.name == "nt" else "bin"
    return {
        "python": str(root / "envs" / spec.name / scripts / "python"),
        "mvn": os.environ.get("PRISM_BENCH_MVN", "mvn"),
        "m2": os.environ.get("PRISM_BENCH_M2", str(Path.home() / ".m2" / "repository")),
    }


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
    else:
        import contextlib
        import signal

        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    proc.kill()


def run_tests(root: Path, spec: RepoSpec, wt: Path, timeout: float | None = None) -> TestRun:
    subs = _placeholders(root, spec)
    cmd = [part.format(**subs) for part in spec.test]
    env = {**os.environ, "PYTHONPATH": str(wt), "PYTHONDONTWRITEBYTECODE": "1"}
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=wt,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=os.name == "nt" and spec.language == "java",  # mvn is a .cmd on Windows
        start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout or spec.timeout)
    except subprocess.TimeoutExpired:
        # Kill the whole tree: a test runner's children (Maven's forked JVM) hold the pipes
        # open, and killing only the parent would leave them running and this call waiting.
        _kill_tree(proc)
        proc.communicate()
        return TestRun(False, round(time.monotonic() - started, 1), "timeout")
    took = round(time.monotonic() - started, 1)
    if proc.returncode == 0:
        return TestRun(True, took)
    output = stdout + "\n" + stderr
    if "COMPILATION ERROR" in output or "compilation failure" in output.lower():
        return TestRun(False, took, "compile error", compile_error=True)
    for pattern in _FAILING:
        m = pattern.search(output)
        if m:
            return TestRun(False, took, m.group(1))
    return TestRun(False, took, f"exit {proc.returncode}")


# Generating a case set


@dataclass
class Generated:
    injected: list[dict[str, Any]] = field(default_factory=list)
    clean: list[dict[str, Any]] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)


def recent_commits(clone: Path, spec: RepoSpec, window: int) -> list[str]:
    out = git(clone, "log", "--no-merges", "--format=%h", f"-n{window}", "--", *spec.source)
    return [c for c in out.split() if c]


def _changed_lines(clone: Path, commit: str) -> int:
    stat = git(clone, "diff", "--numstat", f"{commit}^", commit)
    total = 0
    for row in stat.splitlines():
        a, d, *_ = row.split("\t")
        total += int(a) + int(d) if a.isdigit() and d.isdigit() else 0
    return total


def generate(
    root: Path,
    spec: RepoSpec,
    *,
    injected: int,
    clean: int,
    window: int = 150,
    tries_per_commit: int = 4,
    seed: int = 7,
    notes: Callable[[str], None] = print,
) -> Generated:
    clone = root / spec.name
    rng = random.Random(seed)
    commits = recent_commits(clone, spec, window)
    rng.shuffle(commits)
    wt = root / "gen" / spec.name
    kinds: Counter[str] = Counter()
    cap = max(2, -(-injected // 5))  # no kind takes more than about a fifth of the set
    out = Generated()
    spare: list[str] = []  # commits whose tests pass and that got no case
    for commit in commits:
        if len(out.injected) >= injected and len(spare) >= clean:
            break
        try:
            if _changed_lines(clone, commit) > MAX_CHANGED_LINES:
                out.skipped["too large"] += 1
                continue
            muts = candidates(clone, commit, spec)
        except subprocess.CalledProcessError:
            out.skipped["git error"] += 1
            continue
        checkout(clone, wt, commit)
        base = run_tests(root, spec, wt)
        if not base.passed:
            out.skipped["tests fail at the commit"] += 1
            notes(f"{spec.name} {commit}: tests fail at the commit ({base.failing})")
            continue
        if len(out.injected) >= injected or not muts:
            spare.append(commit)
            continue
        # Least-used kinds first, then at random, so the kinds stay balanced.
        rng.shuffle(muts)
        muts = [m for m in muts if kinds[m.kind] < cap]
        muts.sort(key=lambda m: kinds[m.kind])
        kept = False
        for mut in muts[:tries_per_commit]:
            target = wt / mut.path
            original = target.read_bytes()
            text = original.decode("utf-8")
            target.write_bytes(text.replace(mut.find, mut.replace, 1).encode("utf-8"))
            try:
                run = run_tests(root, spec, wt, timeout=max(30.0, base.seconds * 3 + 30))
            finally:
                target.write_bytes(original)
            if run.compile_error:
                out.skipped["does not compile"] += 1
                continue
            if run.failing.startswith("exit "):
                out.skipped["fails with no named test"] += 1
                continue
            if run.passed:
                out.skipped["no test fails"] += 1
                continue
            kinds[mut.kind] += 1
            n = len(out.injected) + 1
            out.injected.append(
                {
                    "id": f"{spec.name}-i{n:02d}",
                    "group": "injected",
                    "commit": commit,
                    "path": mut.path,
                    "kind": mut.kind,
                    "operator": mut.operator,
                    "find": mut.find,
                    "replace": mut.replace,
                    "failing": run.failing,
                }
            )
            notes(f"{spec.name} {commit}: {mut.kind} ({mut.operator}) caught by {run.failing}")
            kept = True
            break
        if not kept:
            spare.append(commit)
    for n, commit in enumerate(spare[:clean], 1):
        out.clean.append(
            {"id": f"{spec.name}-c{n:02d}", "group": "clean", "commit": commit, "kind": "clean"}
        )
    return out


def _toml_str(s: str) -> str:
    if "\n" in s:
        if '"""' in s or "\\" in s:
            return "'''" + s + "'''" if "'''" not in s else _basic(s)
        return '"""' + s + '"""'
    return _basic(s)


def _basic(s: str) -> str:
    esc = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{esc}"'


def write_cases(path: Path, spec: RepoSpec, rows: Sequence[dict[str, Any]], header: str) -> None:
    out = [f"# {line}".rstrip() for line in header.splitlines()]
    out += ["", "[repo]", f"name = {_basic(spec.name)}", f"url = {_basic(spec.url)}"]
    out += [f"license = {_basic(spec.license)}", ""]
    order = (
        "id",
        "group",
        "commit",
        "path",
        "kind",
        "operator",
        "lines",
        "fix",
        "find",
        "replace",
        "failing",
        "note",
    )
    for row in rows:
        out.append("[[case]]")
        for key in order:
            if key not in row or row[key] in ("", None, ()):
                continue
            v = row[key]
            if isinstance(v, (list, tuple)):
                out.append(f"{key} = [{', '.join(str(int(x)) for x in v)}]")
            else:
                out.append(f"{key} = {_toml_str(str(v))}")
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")
