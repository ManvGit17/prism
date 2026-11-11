from pathlib import Path

import pytest
from typer.testing import CliRunner

from prism.cli import app
from prism.config import get_settings

runner = CliRunner()


def test_index_prints_stats(sample_repo: Path) -> None:
    result = runner.invoke(
        app, ["index", str(sample_repo), "--repo-id", "shop", "--store", "memory"]
    )
    assert result.exit_code == 0, result.output
    assert "repo: shop" in result.output
    assert "files: 9 (2 test files)" in result.output
    assert "functions: 29 (14 methods, 3 tests)" in result.output
    # 27 calls plus the bare `@cached` decorator on slugify.
    assert "CALLS 28" in result.output
    assert "calls at or above threshold 0.5: 23 of 28" in result.output
    assert "store: memory" in result.output


def test_index_falls_back_to_memory_when_stores_are_down(
    sample_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRISM_STORE", "persistent")
    monkeypatch.setenv("PRISM_NEO4J_URI", "bolt://127.0.0.1:1")
    get_settings.cache_clear()
    result = runner.invoke(app, ["index", str(sample_repo), "--repo-id", "shop"])
    assert result.exit_code == 0, result.output
    assert "persistent store unavailable" in result.output
    assert "store: memory" in result.output


def test_graph_without_path_needs_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRISM_NEO4J_URI", "bolt://127.0.0.1:1")
    get_settings.cache_clear()
    result = runner.invoke(app, ["graph", "callers", "anything"])
    assert result.exit_code == 3
    assert "--path" in result.output


def test_graph_neighbours(sample_repo: Path) -> None:
    result = runner.invoke(
        app,
        [
            "graph",
            "neighbours",
            "shop.pricing.round_price",
            "--hops",
            "2",
            "--path",
            str(sample_repo),
        ],
    )
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].startswith("shop.pricing.round_price  shop/pricing.py:10")
    assert any(line.split()[:3] == ["1", "function", "shop.pricing.with_tax"] for line in lines)
    assert any(line.split()[:3] == ["2", "function", "shop.cart.Cart.checkout"] for line in lines)


def test_graph_callers_threshold(sample_repo: Path) -> None:
    default = runner.invoke(app, ["graph", "callers", "Worker.run", "--path", str(sample_repo)])
    assert default.exit_code == 0, default.output
    assert "shop.jobs.dispatch" not in default.output
    lowered = runner.invoke(
        app,
        ["graph", "callers", "Worker.run", "--path", str(sample_repo), "--min-confidence", "0.1"],
    )
    assert lowered.exit_code == 0, lowered.output
    assert "name_common" in lowered.output
    assert "shop.jobs.dispatch" in lowered.output


def test_graph_tests_and_callees(sample_repo: Path) -> None:
    tests = runner.invoke(app, ["graph", "tests", "make_cart", "--path", str(sample_repo)])
    assert "tests.test_cart.test_make_cart" in tests.output
    callees = runner.invoke(app, ["graph", "callees", "Cart.checkout", "--path", str(sample_repo)])
    assert "shop.pricing.with_tax" in callees.output


def test_graph_symbol_errors(sample_repo: Path) -> None:
    ambiguous = runner.invoke(app, ["graph", "neighbours", "total", "--path", str(sample_repo)])
    assert ambiguous.exit_code == 2
    missing = runner.invoke(app, ["graph", "neighbours", "nope", "--path", str(sample_repo)])
    assert missing.exit_code == 1


def test_graph_help_says_it_indexes_in_memory() -> None:
    result = runner.invoke(app, ["graph", "--help"])
    assert result.exit_code == 0
    assert "in memory" in result.output


DIFFS = Path(__file__).parent / "fixtures" / "diffs"


def _context(sample_repo: Path, *args: str) -> str:
    result = runner.invoke(
        app,
        [
            "context",
            str(sample_repo),
            str(DIFFS / "plain.diff"),
            "--store",
            "memory",
            "--reranker",
            "overlap",
            *args,
        ],
    )
    assert result.exit_code == 0, result.output
    return result.stdout


def test_context_text(sample_repo: Path) -> None:
    out = _context(sample_repo, "--mode", "graph")
    assert "mode graph" in out
    assert "shop/util.py @@ -20,2 +20,2 @@" in out
    assert "symbols shop.util._clean" in out
    assert "signature" in out and "caller" in out
    assert "shop.util.slugify" in out
    assert "/1500" in out


def test_context_json_and_budget(sample_repo: Path) -> None:
    from prism.schemas import ContextBundle

    out = _context(sample_repo, "--mode", "graph", "--json", "--budget", "100")
    bundle = ContextBundle.model_validate_json(out)
    hunk = bundle.hunks[0]
    assert hunk.budget == 100
    assert hunk.tokens_used <= 100


def test_context_render(sample_repo: Path) -> None:
    out = _context(sample_repo, "--mode", "graph", "--render")
    assert out.startswith('<repository_context path="shop/util.py"')


@pytest.mark.parametrize("mode", ["none", "vector", "graph", "graph+vector"])
def test_context_modes(sample_repo: Path, mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import prism.indexer.embedder as embedder

    monkeypatch.setattr(embedder, "OllamaEmbedder", lambda: embedder.FakeEmbedder(64))
    from prism.schemas import ContextBundle

    bundle = ContextBundle.model_validate_json(_context(sample_repo, "--mode", mode, "--json"))
    sources = {i.source for h in bundle.hunks for i in h.items}
    assert bundle.mode.value == mode
    assert (
        sources
        == {
            "none": set(),
            "vector": {"vector"},
            "graph": {"graph"},
            "graph+vector": {"graph", "vector"},
        }[mode]
    )


def test_context_rejects_an_unknown_mode(sample_repo: Path) -> None:
    result = runner.invoke(
        app,
        [
            "context",
            str(sample_repo),
            str(DIFFS / "plain.diff"),
            "--store",
            "memory",
            "--mode",
            "x",
        ],
    )
    assert result.exit_code != 0
