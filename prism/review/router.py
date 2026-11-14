"""The router: which hunks the local reviewer takes and which go to the frontier model (ADR 0013).

Up front, from the plan:

* `security`: the hunk's path or added code looks security-sensitive (the planner's check,
  ADR 0010, which includes prompt-injection text). The tuned 3B model found none of the four
  security bugs in the Phase 5 test commit, so it is not asked.
* `large`: the hunk changes at least `min_lines` lines. The reviewer was trained on short hunks
  and writes one comment; a big hunk needs more than that.

After verification, a hunk whose local comment the verifier kept with low confidence, or marked
`escalate` (the comment is wrong but the hunk hides something), goes to the frontier model in a
second round. That part lives in the Claude verifier (`prism.review.handoff`).

An escalated hunk skips the local model: its comment would only be one more thing to verify.
"""

from __future__ import annotations

from prism.review.plan import ReviewPlan

RouteKey = tuple[str, int]  # (path, hunk index)


def route(plan: ReviewPlan, *, enabled: bool, min_lines: int) -> dict[RouteKey, tuple[str, ...]]:
    """Reasons for each hunk sent to the frontier model up front. Empty when routing is off."""
    if not enabled:
        return {}
    routes: dict[RouteKey, tuple[str, ...]] = {}
    for task in plan.tasks:
        reasons: list[str] = []
        if task.security:
            reasons.append("security")
        if sum(1 for ln in task.hunk.lines if ln.kind != " ") >= min_lines:
            reasons.append("large")
        if reasons:
            routes[(task.path, task.index)] = tuple(reasons)
    return routes
