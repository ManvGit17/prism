"""Runtime settings, read from the environment and from `.env`.

Defaults are for a process on the host talking to the Compose services through
their published ports. Inside a container, set the hosts to the service names
and `PRISM_OLLAMA_URL` to `http://host.docker.internal:11434`.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PRISM_", env_file=".env", extra="ignore")

    # 127.0.0.1, not localhost: Compose publishes on 127.0.0.1 only, and on Windows
    # "localhost" tries ::1 first, which cost 2 s per Neo4j and up to 130 s per Postgres connect.
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "prism"
    postgres_password: SecretStr = SecretStr("prism")
    postgres_db: str = "prism"

    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("prism-dev-password")

    redis_url: str = "redis://127.0.0.1:6379/0"

    # Ollama runs on the host, not in Compose.
    ollama_url: str = "http://127.0.0.1:11434"
    # The fine-tuned reviewer (ADR 0009). It is built locally from the Kaggle GGUF; the untuned
    # `qwen2.5-coder:3b` scores the same with the judge and works with a plain `ollama pull`.
    reviewer_model: str = "prism-reviewer:v2"

    # Graph queries leave out CALLS and TESTS edges below this confidence (ADR 0004).
    call_confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)

    # "persistent" is Neo4j + pgvector + the Postgres ledger; "memory" keeps nothing.
    store: Literal["memory", "persistent"] = "persistent"
    # A running index run with no heartbeat for this long is treated as crashed (ADR 0006).
    index_run_stale_after_s: int = Field(default=900, ge=10)

    # Code embeddings come from the host Ollama (ADR 0005).
    embedding_model: str = "nomic-embed-text"
    embedding_batch_size: int = Field(default=16, ge=1)
    # nomic-embed-text reads 2048 tokens; about 6000 characters of code fits.
    embedding_max_chars: int = Field(default=6000, ge=100)
    # nomic-embed-text expects a task prefix; set to "" for models that do not.
    embedding_document_prefix: str = "search_document: "

    # Retrieved context for the verifier and the frontier path (ADR 0007).
    context_mode: Literal["none", "vector", "graph", "graph+vector"] = "graph+vector"
    context_budget_tokens: int = Field(default=1500, ge=50, description="Per hunk.")
    context_hops: int = Field(default=1, ge=1, le=2)
    context_vector_k: int = Field(default=8, ge=0)
    context_snippet_max_lines: int = Field(default=40, ge=3)
    # A truncated item smaller than this is dropped instead.
    context_min_item_tokens: int = Field(default=40, ge=1)
    reranker: Literal["cross-encoder", "none"] = "cross-encoder"
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    token_counter: Literal["chars", "qwen"] = "chars"
    token_chars_per_token: float = Field(default=4.0, gt=0)
    # Downloaded models (reranker, tokenizer) live here.
    model_cache_dir: str = "~/.cache/prism"

    # The gate decides which hunks go to the reviewer (ADR 0008). "pass-all" lets every hunk through
    # until a trained model is in the directory below.
    gate: Literal["pass-all", "onnx"] = "pass-all"
    gate_model_dir: str = "models/gate"

    # Review pipeline (Phase 5). Model calls in flight at once; more only queue in Ollama. Start
    # value from the Phase 4 throughput runs with OLLAMA_NUM_PARALLEL=4.
    model_concurrency: int = Field(default=4, ge=1)
    reviewer_timeout_s: float = Field(default=180.0, gt=0)
    # A reply that is not valid JSON is retried this many times, then dropped.
    reviewer_retries: int = Field(default=2, ge=0)
    # At most this many hunks of a PR go to a model; the rest are listed as not reviewed.
    review_max_hunks: int = Field(default=40, ge=1)
    # Hunks not started by then are listed as not reviewed.
    review_time_limit_s: float = Field(default=600.0, gt=0)
    review_max_comments: int = Field(default=20, ge=1)
    # Cached replies and verifier batches for `prism review` (ADR 0011).
    review_work_dir: str = ".prism-work"
    # "handoff": a Claude model writes verdicts into the work directory. "rules": no model check.
    verifier: Literal["handoff", "rules"] = "handoff"
    # A comment the verifier keeps below this confidence is dropped.
    verifier_min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password.get_secret_value()}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
