import json

import pytest

from prism.models.reviewer import (
    SYSTEM_PROMPT,
    anchor_line,
    hunk_lines,
    parse_reply,
    reply_json,
    review_messages,
    to_raw_comment,
    training_messages,
)

HUNK = """@@ -40,6 +40,7 @@ class Cache:
     def get(self, key):
-        value = self.store[key]
+        value = self.store.get(key)
+        self.hits += 1
         return value

     def put(self, key, value):"""


def test_messages_are_the_same_for_training_and_serving() -> None:
    serve = review_messages(HUNK + "\n\n")
    train = training_messages(HUNK, "Check for None.")
    assert train[:2] == serve
    assert serve[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert serve[1]["content"] == f"```diff\n{HUNK}\n```"
    assert json.loads(train[2]["content"]) == {"comment": "Check for None."}


@pytest.mark.parametrize(
    "text",
    [
        '{"comment": "Check for None."}',
        '  {"comment": "Check for None."}\n',
        '```json\n{"comment": "Check for None."}\n```',
        reply_json("Check for None."),
    ],
)
def test_parse_reply_accepts(text: str) -> None:
    reply = parse_reply(text)
    assert reply is not None and reply.comment == "Check for None."


@pytest.mark.parametrize(
    "text",
    [
        "Check for None.",
        '{"comment": ""}',
        '{"comment": "x", "line": 3}',
        '{"line": 3}',
        'Sure! {"comment": "x"}',
        "[1, 2]",
    ],
)
def test_parse_reply_rejects(text: str) -> None:
    assert parse_reply(text) is None


def test_hunk_lines_numbering() -> None:
    lines = hunk_lines(HUNK)
    assert lines[0] == (" ", 40, "    def get(self, key):")
    assert lines[1] == ("-", 41, "        value = self.store[key]")
    assert lines[2] == ("+", 41, "        value = self.store.get(key)")
    assert lines[3] == ("+", 42, "        self.hits += 1")
    assert lines[4] == (" ", 43, "        return value")


def test_hunk_without_header_starts_at_one() -> None:
    assert [no for _, no, _ in hunk_lines(" a\n+b\n c")] == [1, 2, 3]


def test_anchor_on_named_identifier() -> None:
    assert anchor_line(HUNK, "Is `hits` thread-safe?") == 42
    # Both the removed and the added line mention store; the added line wins.
    assert anchor_line(HUNK, "Why change `store` access?") == 41


def test_anchor_fallbacks() -> None:
    assert anchor_line(HUNK, "Looks fine to me, thanks!") == 42  # last added line
    removal = "@@ -5,3 +5,2 @@\n a\n-b\n c"
    assert anchor_line(removal, "Why remove it?") == 6
    assert anchor_line("@@ -1,1 +7,1 @@\n same", "Hm.") == 7
    assert anchor_line("", "Hm.") == 1


def test_to_raw_comment() -> None:
    raw = to_raw_comment(HUNK, '{"comment": "Count `hits` only on success?"}')
    assert raw is not None and raw.line == 42
    assert to_raw_comment(HUNK, "not json") is None
