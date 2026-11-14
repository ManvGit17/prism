# The PRism review worker in a container (ADR 0018). On Windows the worker can also run on the
# host (`prism app worker`, solo pool); this image is for Linux hosts and for Compose.
#
#   docker compose --profile worker up -d --build
#
# Ollama stays on the host: the worker reaches it at PRISM_OLLAMA_URL (host.docker.internal by
# default), so the host's Ollama must listen on an address containers can reach.
# PMD (Java) is not in the image; Java files get Semgrep only unless you add it.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 prism

WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/app/.venv

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --group app --group sast --group mcp

COPY prism ./prism
COPY README.md LICENSE ./
RUN uv sync --frozen --no-dev --group app --group sast --group mcp \
    && mkdir -p /app/.prism-work && chown -R prism /app/.prism-work

USER prism
ENV PRISM_REVIEW_WORK_DIR=/app/.prism-work PRISM_APP_CHECKOUT_DIR=/app/.prism-work/checkouts
CMD ["uv", "run", "--no-sync", "celery", "-A", "prism.github.queue:celery_app", "worker", \
     "--loglevel=INFO", "-Q", "prism-reviews", "--concurrency=1"]
