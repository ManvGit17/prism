"""Pack ranked items into a token budget.

Order: kind priority first (signature, contract, test, caller, implementation,
class, callee, class_test, similar; see `ContextKind`), then rerank score, then
qualified name, so the same input always packs the same way. An item is
counted as it will be rendered. One that does not fit is cut to the lines that
do fit if at least `min_tokens` and three lines remain; otherwise it is
dropped and recorded with the reason.
"""

from collections.abc import Sequence

from prism.retrieval.render import render_item
from prism.retrieval.tokens import TokenCounter
from prism.schemas import ContextItem, DroppedItem

_MIN_LINES = 3


def ordered(items: Sequence[ContextItem]) -> list[ContextItem]:
    return sorted(items, key=lambda i: (i.kind.priority, -i.score, i.qualified_name))


def _cost(item: ContextItem, counter: TokenCounter) -> int:
    return counter.count(render_item(item, 99)) + 1  # +1 for the blank line between items


def _truncate(
    item: ContextItem, room: int, counter: TokenCounter, min_tokens: int
) -> ContextItem | None:
    lines = item.snippet.splitlines()
    lo, hi, best = _MIN_LINES, len(lines) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        cut = item.model_copy(
            update={
                "snippet": "\n".join([*lines[:mid], f"... ({len(lines) - mid} lines cut)"]),
                "truncated": True,
                "end_line": item.start_line + mid - 1,
            }
        )
        cost = _cost(cut, counter)
        if cost <= room:
            best = cut.model_copy(update={"tokens": cost})
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None or best.tokens < min_tokens:
        return None
    return best


def pack(
    items: Sequence[ContextItem], budget: int, counter: TokenCounter, min_tokens: int
) -> tuple[list[ContextItem], list[DroppedItem], int]:
    """Return the kept items in order, the dropped ones, and the tokens used."""
    kept: list[ContextItem] = []
    dropped: list[DroppedItem] = []
    used = 0
    for item in ordered(items):
        cost = _cost(item, counter)
        room = budget - used
        if cost <= room:
            kept.append(item.model_copy(update={"tokens": cost}))
            used += cost
            continue
        cut = _truncate(item, room, counter, min_tokens)
        if cut is not None:
            kept.append(cut)
            used += cut.tokens
            continue
        dropped.append(
            DroppedItem(
                kind=item.kind,
                qualified_name=item.qualified_name,
                tokens=cost,
                reason=f"needs {cost} tokens, {room} left",
            )
        )
    return kept, dropped, used
