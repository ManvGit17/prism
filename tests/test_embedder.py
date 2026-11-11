"""OllamaEmbedder request handling, against a fake transport (no Ollama needed)."""

import json

import httpx

from prism.indexer.embedder import OllamaEmbedder, function_text
from prism.indexer.parsers import PythonParser


def test_batches_truncates_prefixes_and_normalises() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"embeddings": [[3.0, 4.0] for _ in body["input"]]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    embedder = OllamaEmbedder(
        model="m", url="http://ollama", batch_size=2, max_chars=5, prefix="doc: ", client=client
    )
    out = embedder.embed(["abcdefgh", "b", "c"])
    assert out == [(0.6, 0.8)] * 3
    assert embedder.dimension == 2
    assert [b["input"] for b in seen] == [["doc: abcde", "doc: b"], ["doc: c"]]
    assert all(b["model"] == "m" and b["truncate"] is True for b in seen)


def test_function_text_is_signature_docstring_and_body() -> None:
    src = b'@deco\ndef f(a, b):\n    """Add."""\n    return a + b\n'
    pf = PythonParser().parse("r", "m.py", src)
    text = function_text(pf.functions[0], src)
    assert text == 'def f(a, b):\n    """Add."""\n    return a + b'
