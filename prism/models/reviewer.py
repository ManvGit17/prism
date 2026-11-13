"""The reviewer model's prompt, reply parsing and line placement (ADR 0009).

The fine-tuned reviewer is trained and served with exactly these messages, so training code and
the review pipeline both import them from here. The model writes `{"comment": "..."}` only:
CodeReviewer has no line labels, so the line is chosen afterwards by `anchor_line`.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from prism.models.gate import normalize_hunk
from prism.schemas import RawComment, ReviewerReply

SYSTEM_PROMPT = (
    "You are a senior engineer reviewing one hunk of a pull request. Write the single most "
    "useful comment a human reviewer would leave on it: a bug, a risk, a clearer name, a missing "
    "test or a question about intent. Keep it short and specific. Reply with JSON only, in this "
    'form: {"comment": "<your comment>"}'
)

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```$", re.DOTALL)
# Common English and code words that say nothing about which line a comment is on.
_STOP = frozenset(
    """the and for this that should with have from when what will would could there their about
    which these those than then also just like make need does into only here more some other
    because think change maybe using used return true false none null self this not can why
    are was but you its use get set new var let def int str""".split()
)


def user_message(hunk: str) -> str:
    return f"```diff\n{normalize_hunk(hunk)}\n```"


def review_messages(hunk: str) -> list[dict[str, str]]:
    """The prompt sent to the reviewer for one hunk."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message(hunk)},
    ]


def reply_json(comment: str) -> str:
    """The exact assistant text the model is trained to produce."""
    return json.dumps({"comment": comment}, ensure_ascii=False)


def training_messages(hunk: str, comment: str) -> list[dict[str, str]]:
    return [*review_messages(hunk), {"role": "assistant", "content": reply_json(comment)}]


def parse_reply(text: str) -> ReviewerReply | None:
    """The model's reply as a `ReviewerReply`, or None if it is not the JSON asked for.

    A single surrounding code fence is tolerated; anything else around the JSON is not.
    """
    body = text.strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1).strip()
    try:
        return ReviewerReply.model_validate(json.loads(body))
    except (json.JSONDecodeError, ValidationError, TypeError):
        return None


def identifiers(text: str) -> set[str]:
    return {t.lower() for t in _IDENT.findall(text)} - _STOP


def hunk_lines(hunk: str) -> list[tuple[str, int, str]]:
    """(kind, new-file line, text) for each line of a hunk; kind is '+', '-' or ' '.

    A removed line gets the new-file line it was removed before. Without a header, numbering
    starts at 1.
    """
    out: list[tuple[str, int, str]] = []
    new_line = 1
    for raw in hunk.split("\n"):
        header = _HUNK_HEADER.match(raw)
        if header:
            new_line = int(header.group(1))
            continue
        if raw.startswith("\\"):
            continue
        kind = raw[:1] if raw[:1] in "+-" else " "
        out.append((kind, max(new_line, 1), raw[1:]))
        if kind != "-":
            new_line += 1
    return out


def anchor_line(hunk: str, comment: str) -> int:
    """The new-file line a comment most likely refers to.

    The changed line sharing the most identifiers with the comment wins (added lines first on a
    tie). With no overlap: the last added line, else the first removed line, else the first line.
    """
    lines = hunk_lines(hunk)
    if not lines:
        return 1
    changed = [(kind, no, text) for kind, no, text in lines if kind != " "]
    words = identifiers(comment)
    best: tuple[int, int, int] | None = None  # (overlap, is_added, -position)
    best_line = 0
    for pos, (kind, no, text) in enumerate(changed):
        overlap = len(words & identifiers(text))
        key = (overlap, int(kind == "+"), -pos)
        if overlap and (best is None or key > best):
            best, best_line = key, no
    if best is not None:
        return best_line
    added = [no for kind, no, _ in changed if kind == "+"]
    if added:
        return added[-1]
    if changed:
        return changed[0][1]
    return lines[0][1]


def to_raw_comment(hunk: str, reply_text: str) -> RawComment | None:
    """Parse a model reply and place it on a line. None when the reply is not valid."""
    reply = parse_reply(reply_text)
    if reply is None:
        return None
    return RawComment(line=anchor_line(hunk, reply.comment), comment=reply.comment)
