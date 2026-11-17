"""Reviewer fine-tuning.

Unsloth patches transformers and peft when it is imported, so it has to come before them. This
package import runs before any module in it, which makes it the one place that guarantees that.
Where Unsloth is not installed (this machine, CI) nothing happens.
"""

import importlib.util

if importlib.util.find_spec("unsloth") is not None:
    import unsloth  # noqa: F401
