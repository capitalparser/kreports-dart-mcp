FROM ghcr.io/astral-sh/uv:0.11.11 AS uv
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KREPORTS_RUNTIME_MODE=readonly \
    DB_URL=sqlite:////data/kreports.db \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY kreports ./kreports
COPY scripts/kreports-mcp.sh ./scripts/kreports-mcp.sh

RUN uv sync --frozen --no-dev --extra api

RUN mkdir -p /data

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -m kreports.deployment_healthcheck

CMD ["kreports", "serve-http", "--host", "0.0.0.0", "--port", "8765", "--path", "/mcp", "--stateless"]
