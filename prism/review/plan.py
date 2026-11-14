"""The planner: which files and hunks of a PR go to a model, in what order (ADR 0010).

Each file gets a risk score from three signals: security-sensitive paths or code, the size of
the change and, when the repo has history, churn. The gate scores every hunk. Hunks the gate
passes are ranked by file risk plus gate score and the first `max_hunks` are reviewed; the rest
are listed in the summary as not reviewed. A hunk that touches security-sensitive code skips the
gate: the gate was trained on review comments in general, and missing one of these costs more
than one extra model call.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from prism.models.gate import Gate
from prism.retrieval.diff import FileDiff, FileStatus, Hunk

# Path parts that mark security-sensitive code.
_SENSITIVE_PATH = re.compile(
    r"auth|login|logout|passw|secret|token|credential|crypt|cipher|session|permission|"
    r"acl|admin|oauth|jwt|saml|sso|security|sanitiz|upload|payment|billing|\.env|"
    r"settings\.py|config/|middleware",
    re.IGNORECASE,
)
# Added code that calls for a closer look, Python and Java.
_SENSITIVE_CODE = re.compile(
    r"\beval\(|\bexec\(|subprocess|os\.system|os\.popen|shell\s*=\s*True|pickle\.loads?|"
    r"yaml\.load\(|marshal\.loads|verify\s*=\s*False|hashlib\.(md5|sha1)\b|\bmd5\b|"
    r"random\.(random|randint)\(.*(token|secret|password)|password|passwd|secret|api_?key|"
    r"\.execute\(\s*f[\"']|\.execute\([^)]*%|\.execute\([^)]*\+|Runtime\.getRuntime\(\)\.exec|"
    r"ProcessBuilder|createStatement\(|executeQuery\([^)]*\+|ObjectInputStream|"
    r"MessageDigest\.getInstance\(\"(MD5|SHA-?1)\"|setAccessible\(true\)|innerHTML|"
    r"render_template_string|mark_safe|\|\s*safe\b|DEBUG\s*=\s*True|ALLOWED_HOSTS",
    re.IGNORECASE,
)
_SKIP_SUFFIXES = (".lock", ".min.js", ".map", ".svg", ".png", ".jpg", ".ico", ".pdf")
_SKIP_NAMES = ("package-lock.json", "uv.lock", "poetry.lock", "yarn.lock", "pnpm-lock.yaml")


@dataclass(frozen=True)
class HunkTask:
    """One hunk on its way to the reviewer."""

    path: str
    index: int  # position of the hunk in its file
    hunk: Hunk
    gate_score: float
    security: bool
    priority: float

    @property
    def text(self) -> str:
        return self.hunk.text()


@dataclass(frozen=True)
class NotReviewed:
    path: str
    hunk_header: str
    reason: str  # "budget", "time" or "model_failed"


@dataclass
class FilePlan:
    path: str
    status: FileStatus
    risk: float
    reasons: tuple[str, ...]
    security: bool
    hunks_total: int
    tasks: list[HunkTask] = field(default_factory=list)
    skipped_by_gate: int = 0
    over_budget: list[NotReviewed] = field(default_factory=list)


@dataclass
class ReviewPlan:
    files: list[FilePlan]
    skipped_files: list[tuple[str, str]]  # (path, reason)

    @property
    def tasks(self) -> list[HunkTask]:
        return [t for f in self.files for t in f.tasks]

    @property
    def not_reviewed(self) -> list[NotReviewed]:
        return [n for f in self.files for n in f.over_budget]


def skip_reason(file: FileDiff) -> str | None:
    """Why a file is not reviewed at all, or None."""
    if file.is_binary:
        return "binary"
    if file.status is FileStatus.DELETED:
        return "deleted"
    name = file.path.rsplit("/", 1)[-1]
    if name in _SKIP_NAMES or file.path.endswith(_SKIP_SUFFIXES):
        return "generated"
    if not file.hunks:
        return "no_hunks"
    return None


def hunk_is_sensitive(hunk: Hunk) -> bool:
    from prism.review.injection import INJECTION

    added = "\n".join(ln.text for ln in hunk.lines if ln.kind == "+")
    return bool(_SENSITIVE_CODE.search(added) or INJECTION.search(added))


def file_risk(
    file: FileDiff, churn: int = 0, *, size_cap: int = 100, churn_cap: int = 20
) -> tuple[float, tuple[str, ...], bool]:
    """(risk in [0, 1], reasons, security-sensitive) for one file."""
    reasons: list[str] = []
    path_hit = bool(_SENSITIVE_PATH.search(file.path))
    code_hit = any(hunk_is_sensitive(h) for h in file.hunks)
    if path_hit:
        reasons.append("security-sensitive path")
    if code_hit:
        reasons.append("security-sensitive code")
    security = path_hit or code_hit
    changed = sum(1 for h in file.hunks for ln in h.lines if ln.kind != " ")
    if changed >= size_cap // 2:
        reasons.append(f"{changed} changed lines")
    if churn >= churn_cap // 2:
        reasons.append(f"changed in {churn} recent commits")
    risk = (
        0.5 * float(security)
        + 0.3 * min(1.0, changed / size_cap)
        + 0.2 * min(1.0, churn / churn_cap)
    )
    return round(risk, 4), tuple(reasons), security


def git_churn(repo: Path, paths: Sequence[str], commits: int = 200) -> dict[str, int]:
    """How many of the last `commits` commits touched each path. Empty when not a git repo."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", f"-{commits}", "--format=", "--name-only"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    counts = Counter(line.strip() for line in out.splitlines() if line.strip())
    return {p: counts.get(p, 0) for p in paths}


def plan_review(
    files: Sequence[FileDiff],
    gate: Gate,
    *,
    max_hunks: int,
    churn: Mapping[str, int] | None = None,
) -> ReviewPlan:
    churn = churn or {}
    plans: list[FilePlan] = []
    skipped: list[tuple[str, str]] = []
    for file in files:
        reason = skip_reason(file)
        if reason:
            skipped.append((file.path, reason))
            continue
        risk, reasons, security = file_risk(file, churn.get(file.path, 0))
        plans.append(
            FilePlan(
                path=file.path,
                status=file.status,
                risk=risk,
                reasons=reasons,
                security=security,
                hunks_total=len(file.hunks),
            )
        )

    by_path = {p.path: p for p in plans}
    candidates: list[HunkTask] = []
    for file in files:
        plan = by_path.get(file.path)
        if plan is None:
            continue
        texts = [h.text() for h in file.hunks]
        scores = gate.score(texts)
        passes = gate.needs_review(texts)
        path_hit = bool(_SENSITIVE_PATH.search(file.path))
        for i, (hunk, score, ok) in enumerate(zip(file.hunks, scores, passes, strict=True)):
            sensitive = path_hit or hunk_is_sensitive(hunk)
            if not ok and not sensitive:
                plan.skipped_by_gate += 1
                continue
            candidates.append(
                HunkTask(
                    path=file.path,
                    index=i,
                    hunk=hunk,
                    gate_score=score,
                    security=sensitive,
                    priority=round(plan.risk + score, 4),
                )
            )

    # Highest priority first; ties keep diff order.
    ranked = sorted(enumerate(candidates), key=lambda p: (-p[1].priority, p[0]))
    for rank, (_, task) in enumerate(ranked):
        plan = by_path[task.path]
        if rank < max_hunks:
            plan.tasks.append(task)
        else:
            plan.over_budget.append(NotReviewed(task.path, task.hunk.header, "budget"))
    for plan in plans:
        plan.tasks.sort(key=lambda t: t.index)
    return ReviewPlan(files=plans, skipped_files=skipped)
