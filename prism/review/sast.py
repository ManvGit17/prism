"""Static analysis on the changed files, as evidence for the verifier (ADR 0012).

Bandit, Semgrep, Ruff and PMD run as subprocesses on the PR head checkout, on the changed files
only. A tool that is not installed, or fails, is skipped and the summary says so; a review never
fails because of SAST. Findings are kept only where they fall inside a reviewed hunk's new
lines. They are attached to that hunk's verifier requests, and the stronger ones on added lines
become comments of their own, which the verifier checks like any other.

The tools come from the optional `sast` dependency group (`uv sync --group sast`); PMD needs Java
and a PMD install pointed at by PRISM_PMD_HOME.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Literal, Protocol

log = logging.getLogger(__name__)

Level = Literal["high", "medium", "low"]
LEVEL_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

PY = (".py",)
JAVA = (".java",)


@dataclass(frozen=True)
class Finding:
    tool: str
    rule: str
    path: str  # repo-relative, forward slashes
    line: int
    severity: Level
    message: str

    def describe(self) -> str:
        return f"{self.tool} {self.rule} ({self.severity}) line {self.line}: {self.message}"


@dataclass
class SastReport:
    findings: list[Finding] = field(default_factory=list)
    ran: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (tool, reason)

    def summary(self) -> str:
        parts = []
        for tool in self.ran:
            n = sum(1 for f in self.findings if f.tool == tool)
            parts.append(f"{tool} {n}")
        found = ", ".join(parts) if parts else "no tool ran"
        text = f"SAST findings in the changed files: {found}"
        if self.skipped:
            text += "; not run: " + ", ".join(f"{t} ({why})" for t, why in self.skipped)
        return text + "."


class SastTool(Protocol):
    name: str
    suffixes: tuple[str, ...]

    def unavailable(self) -> str | None:
        """Why the tool cannot run here, or None."""
        ...

    def run(self, root: Path, paths: Sequence[str], timeout_s: float) -> list[Finding]: ...


def find_exe(name: str) -> str | None:
    """An executable on PATH or next to this Python (the project venv's Scripts or bin)."""
    found = shutil.which(name)
    if found:
        return found
    here = Path(sys.executable).parent
    for candidate in (here / name, here / f"{name}.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


def rel(root: Path, path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(root.resolve())
        except ValueError:
            return PurePosixPath(p.as_posix()).as_posix()
    return p.as_posix().removeprefix("./")


def _run(args: list[str], root: Path, timeout_s: float) -> str:
    proc = subprocess.run(
        args, cwd=root, capture_output=True, text=True, timeout=timeout_s, encoding="utf-8"
    )
    # Bandit, Ruff and Semgrep exit 1 when they find something; that is not a failure.
    if proc.returncode not in (0, 1) and not proc.stdout.strip():
        raise RuntimeError(f"exit {proc.returncode}: {proc.stderr.strip()[:300]}")
    return proc.stdout


class Bandit:
    name = "bandit"
    suffixes: tuple[str, ...] = PY

    def unavailable(self) -> str | None:
        return None if find_exe("bandit") else "not installed (uv sync --group sast)"

    def run(self, root: Path, paths: Sequence[str], timeout_s: float) -> list[Finding]:
        out = _run([find_exe("bandit") or "bandit", "-f", "json", "-q", *paths], root, timeout_s)
        data = json.loads(out)
        return [
            Finding(
                tool=self.name,
                rule=r["test_id"],
                path=rel(root, r["filename"]),
                line=int(r["line_number"]),
                severity=r["issue_severity"].lower(),
                message=r["issue_text"],
            )
            for r in data.get("results", [])
            if r.get("issue_confidence") != "LOW"
        ]


class Semgrep:
    name = "semgrep"
    suffixes: tuple[str, ...] = (*PY, *JAVA)
    _LEVEL: ClassVar[dict[str, Level]] = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}

    def __init__(self, config: str) -> None:
        self.config = config

    def unavailable(self) -> str | None:
        return None if find_exe("semgrep") else "not installed (uv sync --group sast)"

    def run(self, root: Path, paths: Sequence[str], timeout_s: float) -> list[Finding]:
        args = [find_exe("semgrep") or "semgrep", "scan", "--config", self.config]
        args += ["--metrics=off", "--json", "--quiet", *paths]
        data = json.loads(_run(args, root, timeout_s))
        return [
            Finding(
                tool=self.name,
                rule=r["check_id"].rsplit(".", 1)[-1],
                path=rel(root, r["path"]),
                line=int(r["start"]["line"]),
                severity=self._LEVEL.get(r["extra"].get("severity", ""), "low"),
                message=" ".join(r["extra"].get("message", "").split())[:300],
            )
            for r in data.get("results", [])
        ]


class Ruff:
    """Only rules that point at bugs; style is the reviewer's business, not evidence."""

    name = "ruff"
    suffixes: tuple[str, ...] = PY
    SELECT = "F,B,E9,PLE"
    _MEDIUM = ("F821", "F811", "F823", "E9", "PLE", "B006", "B008", "B023", "B904")

    def unavailable(self) -> str | None:
        return None if find_exe("ruff") else "not installed"

    def run(self, root: Path, paths: Sequence[str], timeout_s: float) -> list[Finding]:
        args = [find_exe("ruff") or "ruff", "check", "--select", self.SELECT, "--isolated"]
        args += ["--output-format", "json", "--exit-zero", *paths]
        data: list[dict[str, Any]] = json.loads(_run(args, root, timeout_s) or "[]")
        out = []
        for r in data:
            code = r.get("code") or "E999"
            out.append(
                Finding(
                    tool=self.name,
                    rule=code,
                    path=rel(root, r["filename"]),
                    line=int(r["location"]["row"]),
                    severity="medium" if code.startswith(self._MEDIUM) else "low",
                    message=r["message"],
                )
            )
        return out


class Pmd:
    name = "pmd"
    suffixes: tuple[str, ...] = JAVA
    RULESET = "rulesets/java/quickstart.xml"

    def __init__(self, home: str) -> None:
        self.home = Path(home).expanduser() if home else None

    def _exe(self) -> Path | None:
        if self.home is None:
            return None
        for name in ("pmd.bat", "pmd"):
            exe = self.home / "bin" / name
            if exe.is_file():
                return exe
        return None

    def unavailable(self) -> str | None:
        if self._exe() is None:
            return "PRISM_PMD_HOME not set or has no bin/pmd"
        if not shutil.which("java"):
            return "java not found"
        return None

    def run(self, root: Path, paths: Sequence[str], timeout_s: float) -> list[Finding]:
        args = [str(self._exe()), "check", "-R", self.RULESET, "-f", "json", "--no-progress"]
        args += ["--no-cache", "-d", ",".join(paths)]
        proc = subprocess.run(
            args, cwd=root, capture_output=True, text=True, timeout=timeout_s, encoding="utf-8"
        )
        if proc.returncode not in (0, 4) and not proc.stdout.strip():
            raise RuntimeError(f"exit {proc.returncode}: {proc.stderr.strip()[:300]}")
        data = json.loads(proc.stdout or "{}")
        out = []
        for f in data.get("files", []):
            for v in f.get("violations", []):
                prio = int(v.get("priority", 3))
                level: Level = "high" if prio <= 2 else "medium" if prio == 3 else "low"
                out.append(
                    Finding(
                        tool=self.name,
                        rule=v.get("rule", "?"),
                        path=rel(root, f["filename"]),
                        line=int(v["beginline"]),
                        severity=level,
                        message=" ".join(v.get("description", "").split()),
                    )
                )
        return out


def default_tools(names: Sequence[str], semgrep_config: str, pmd_home: str) -> list[SastTool]:
    made: dict[str, SastTool] = {
        "bandit": Bandit(),
        "semgrep": Semgrep(semgrep_config),
        "ruff": Ruff(),
        "pmd": Pmd(pmd_home),
    }
    tools = [made[n] for n in names if n in made]
    # The injection scan is not optional: it costs nothing and guards the model prompts.
    from prism.review.injection import InjectionScan

    return [*tools, InjectionScan()]


def run_sast(
    root: Path,
    paths: Sequence[str],
    tools: Sequence[SastTool],
    *,
    timeout_s: float = 300.0,
) -> SastReport:
    """Run each tool on the changed files it understands. Never raises."""
    report = SastReport()
    for tool in tools:
        mine = [p for p in paths if p.endswith(tool.suffixes) and (root / p).is_file()]
        if not mine:
            continue
        why = tool.unavailable()
        if why:
            report.skipped.append((tool.name, why))
            continue
        try:
            report.findings.extend(tool.run(root, mine, timeout_s))
            report.ran.append(tool.name)
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError) as exc:
            log.warning("%s failed: %s", tool.name, exc)
            report.skipped.append((tool.name, f"failed: {type(exc).__name__}"))
    return report


def at_least(level: str, minimum: str) -> bool:
    return LEVEL_RANK.get(level, 0) >= LEVEL_RANK.get(minimum, 1)
