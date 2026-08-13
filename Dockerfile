FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KREPORTS_RUNTIME_MODE=readonly \
    DB_URL=sqlite:////data/kreports.db

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY kreports ./kreports
COPY scripts/kreports-mcp.sh ./scripts/kreports-mcp.sh

RUN pip install --no-cache-dir ".[api]"

RUN mkdir -p /data

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8765/readyz >/dev/null || exit 1

CMD ["kreports", "serve-http", "--host", "0.0.0.0", "--port", "8765", "--path", "/mcp", "--stateless"]
