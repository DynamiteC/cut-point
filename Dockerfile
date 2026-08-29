# Root image for the CutPoint control plane: the API (api.main:app) and the
# retention watcher (services.watcher.main:app). Both import the same packages
# (agent, api, ingest, report, services) and both need `uv` on PATH because the
# agent spawns mcp-clickhouse over stdio via `uv run --with mcp-clickhouse`.
#
# One image, two entrypoints. APP_MODULE selects which FastAPI app to serve, so
# deploy/deploy_all.sh builds this once and deploys it as two Cloud Run services.
FROM python:3.12-slim

# uv is a hard runtime dependency here, not just a build tool: agent/cutpoint_agent/mcp.py
# launches `uv run --with mcp-clickhouse mcp-clickhouse` as a subprocess at request time.
COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local

WORKDIR /app

# Install dependencies first, from the lockfile, so this layer caches across code
# changes. --no-install-project installs only third-party deps, not our source.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Now the source. Layout is preserved (agent/, api/, ingest/, report/, services/)
# so REPO_ROOT = Path(__file__).parents[N] resolves to /app in every module.
COPY agent ./agent
COPY api ./api
COPY ingest ./ingest
COPY report ./report
COPY services ./services
COPY sql ./sql
COPY data/ground_truth.json ./data/ground_truth.json

# Install the project itself into the same environment.
RUN uv sync --frozen --no-dev

# Pre-warm mcp-clickhouse into uv's cache so the first analysis request does not
# pay a PyPI download inside the request handler (and does not fail if egress is
# briefly unavailable at cold start). --help exits 0 without needing credentials.
RUN uv run --with mcp-clickhouse mcp-clickhouse --help >/dev/null 2>&1 || true

# APP_MODULE is overridden per service in deploy_all.sh; the API is the default.
ENV APP_MODULE=api.main:app
EXPOSE 8080
CMD ["sh", "-c", "uvicorn ${APP_MODULE} --host 0.0.0.0 --port ${PORT:-8080}"]
