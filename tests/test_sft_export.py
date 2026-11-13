from prism.models.reviewer import SYSTEM_PROMPT
from training.sft.export import modelfile


def test_modelfile() -> None:
    text = modelfile("prism-reviewer.Q4_K_M.gguf")
    lines = text.splitlines()
    assert lines[0] == "FROM ./prism-reviewer.Q4_K_M.gguf"
    assert f'SYSTEM """{SYSTEM_PROMPT}"""' in lines
    assert "PARAMETER temperature 0" in lines
    assert 'PARAMETER stop "<|im_end|>"' in lines
    assert "<|im_start|>assistant\n" in text
