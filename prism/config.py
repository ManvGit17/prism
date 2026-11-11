"""Runtime settings, read from the environment and from `.env`.

Defaults are for a process on the host talking to the Compose services through
their published ports. Inside a container, set the hosts to the service names
and `PRISM_OLLAMA_URL` to `http://host.docker.internal:11434`.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PRISM_", env_file=".env", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "prism"
    postgres_password: SecretStr = SecretStr("prism")
    postgres_db: str = "prism"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("prism-dev-password")

    redis_url: str = "redis://localhost:6379/0"

    # Ollama runs on the host, not in Compose.
    ollama_url: str = "http://localhost:11434"
    reviewer_model: str = "qwen2.5-coder:1.5b"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password.get_secret_value()}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
