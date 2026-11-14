from pathlib import Path

import pytest

from prism.models.reviewer import SYSTEM_PROMPT
from training.sft.export import export_gguf, modelfile


def test_modelfile() -> None:
    text = modelfile("prism-reviewer.Q4_K_M.gguf")
    lines = text.splitlines()
    assert lines[0] == "FROM ./prism-reviewer.Q4_K_M.gguf"
    assert f'SYSTEM """{SYSTEM_PROMPT}"""' in lines
    assert "PARAMETER temperature 0" in lines
    assert 'PARAMETER stop "<|im_end|>"' in lines
    assert "<|im_start|>assistant\n" in text


class FakeUnsloth:
    """Writes what Unsloth 2026.11 writes: merged weights in the folder, GGUF in `<folder>_gguf`."""

    def save_pretrained_gguf(
        self, folder: str, tokenizer: object, quantization_method: str
    ) -> None:
        merged = Path(folder)
        merged.mkdir(parents=True)
        (merged / "model-00001-of-00002.safetensors").write_bytes(b"weights")
        gguf_dir = Path(f"{folder}_gguf")
        gguf_dir.mkdir()
        (gguf_dir / "Qwen2.5-Coder-3B-Instruct.Q4_K_M.gguf").write_bytes(b"q4")
        (gguf_dir / "Modelfile").write_text("unsloth's own", encoding="utf-8")


def test_export_gguf_moves_the_gguf_and_drops_scratch(tmp_path: Path) -> None:
    out = tmp_path / "gguf"
    gguf = export_gguf(FakeUnsloth(), None, out)
    assert gguf == out / "Qwen2.5-Coder-3B-Instruct.Q4_K_M.gguf"
    assert sorted(p.name for p in out.iterdir()) == ["Modelfile", gguf.name]
    assert (out / "Modelfile").read_text(encoding="utf-8") == modelfile(gguf.name)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gguf"]


def test_export_gguf_scratch_elsewhere(tmp_path: Path) -> None:
    out, scratch = tmp_path / "working" / "gguf", tmp_path / "temp" / "export"
    gguf = export_gguf(FakeUnsloth(), None, out, scratch_root=scratch)
    assert gguf.parent == out
    assert sorted(p.name for p in (tmp_path / "working").iterdir()) == ["gguf"]
    assert list(scratch.iterdir()) == []


def test_export_gguf_fails_loudly(tmp_path: Path) -> None:
    class NoOutput:
        def save_pretrained_gguf(
            self, folder: str, tokenizer: object, quantization_method: str
        ) -> None:
            Path(folder).mkdir()

    with pytest.raises(FileNotFoundError):
        export_gguf(NoOutput(), None, tmp_path / "gguf")
