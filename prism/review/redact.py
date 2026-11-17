"""Remove secrets from PR text before it leaves the machine (safety; ADR 0013).

Everything sent to Claude (verifier and escalation requests, by file handoff or API) goes through
`redact` first. The local reviewer on Ollama is not an external call and sees the hunk as it is.

Three passes, in order:

1. Known token formats (AWS, GitHub, Slack, Stripe, Google, OpenAI/Anthropic-style keys, JWTs,
   private key blocks, credentials in URLs), replaced whole.
2. A quoted value assigned to a secret-sounding name (`password = "..."`, `"api_key": "..."`).
3. detect-secrets as a second net. Its entropy plugins fire on ordinary identifiers, so an
   entropy hit counts only for a value of 24 characters or more.

The replacement says what was there, `[REDACTED:github-token]`, so the verifier can still
comment on a hard-coded secret without seeing it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TOKENS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private-key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
        ),
    ),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "github-token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    ),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("stripe-key", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("api-key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}\b")),
    ("hf-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
]
_URL_CREDS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^\s:/@]+):(?P<pw>[^\s@/]+)@")
_KEYWORD = re.compile(
    # The name must end in the keyword (DB_PASSWORD, access_token), so TOKENIZER_FILE and
    # QWEN_TOKENIZER_REPO do not match.
    r"""(?P<name>[A-Za-z0-9_.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|"""
    r"""access[_-]?key|private[_-]?key|client[_-]?secret|auth)s?["']?\s*"""
    r"""(?:=|:|:=|=>)\s*)(?P<q>["'])(?P<value>[^"'\n]{6,})(?P=q)""",
    re.IGNORECASE,
)
# Values that are plainly not secrets: placeholders, env lookups, format strings.
_PLACEHOLDER = re.compile(
    r"^(?:\$\{?|<|\{|%|os\.environ|getenv|changeme|example|placeholder|xxx+|\*+|your[_-])",
    re.IGNORECASE,
)
_ENTROPY_TYPES = {"Base64 High Entropy String", "Hex High Entropy String"}
_MIN_ENTROPY_LEN = 24
_MIN_ENTROPY_RUN = 16


@dataclass
class Redaction:
    text: str
    found: list[str] = field(default_factory=list)  # kinds, in order


def _is_noise(kind: str, value: str, line: str) -> bool:
    if len(value) < 8 or value not in line or value.startswith("[REDACTED"):
        return True
    if kind in _ENTROPY_TYPES:
        # Long identifiers (sast_comment_min_severity) pass the entropy test; real tokens
        # almost always mix letters and digits.
        # Words joined by separators (max_transaction_retry_time=5, ms-marco-MiniLM-L-6-v2) do
        # too; a token has a long unbroken run.
        has_digit = any(c.isdigit() for c in value)
        has_alpha = any(c.isalpha() for c in value)
        longest_run = max((len(r) for r in re.findall(r"[A-Za-z0-9]+", value)), default=0)
        return (
            len(value) < _MIN_ENTROPY_LEN
            or not (has_digit and has_alpha)
            or longest_run < _MIN_ENTROPY_RUN
        )
    return False


def _detect_secrets(lines: list[str]) -> list[list[tuple[str, str]]]:
    """(kind, value) pairs detect-secrets reports for each line, filtered for noise."""
    from detect_secrets.core.scan import scan_line
    from detect_secrets.settings import default_settings

    out: list[list[tuple[str, str]]] = []
    with default_settings():
        for line in lines:
            hits = [(s.type, s.secret_value or "") for s in scan_line(line)]
            out.append(
                [
                    (kind.lower().replace(" ", "-"), value)
                    for kind, value in hits
                    if not _is_noise(kind, value, line)
                ]
            )
    return out


def redact(text: str, *, use_detect_secrets: bool = True) -> Redaction:
    found: list[str] = []

    def token(kind: str) -> str:
        found.append(kind)
        return f"[REDACTED:{kind}]"

    for kind, pattern in _TOKENS:
        text = pattern.sub(lambda m, k=kind: token(k), text)  # type: ignore[misc]
    text = _URL_CREDS.sub(lambda m: f"{m['scheme']}{m['user']}:{token('url-password')}@", text)

    def keyword(m: re.Match[str]) -> str:
        value = m["value"]
        if value.startswith("[REDACTED") or _PLACEHOLDER.match(value):
            return m[0]
        return f"{m['name']}{m['q']}{token('secret-value')}{m['q']}"

    text = _KEYWORD.sub(keyword, text)
    if use_detect_secrets:
        lines = text.split("\n")
        for i, hits in enumerate(_detect_secrets(lines)):
            for kind, value in hits:
                lines[i] = lines[i].replace(value, token(kind))
        text = "\n".join(lines)
    return Redaction(text, found)
