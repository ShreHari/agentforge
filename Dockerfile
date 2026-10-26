# syntax=docker/dockerfile:1
# One image for all of AgentForge. The same image starts as the api, the worker or the
# sweeper; only the start command changes (see docker-compose.yml, and k8s/ later).
#
# Build:  docker build -t agentforge .
# Run:    docker compose up --build

# ---- Stage 1: build. Has uv, and installs the packages into /app/.venv ----
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_LOCKED=1 \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Packages first, our code second. Docker reuses a layer if its inputs haven't changed, so
# this slow step only re-runs when pyproject.toml or uv.lock change, not on every code edit.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-install-project

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync

# ---- Stage 2: run. Only Python, the packages and our code: no uv, no build cache ----
FROM python:3.12-slim

# Never run as root: if someone broke in through the app, they'd get a powerless user.
RUN useradd --system --create-home --uid 10001 app
WORKDIR /app
COPY --from=build --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app

# Fail the BUILD, not the deploy, if a package is missing.
RUN python -c "import api.main, worker.main, worker.sweeper"

EXPOSE 8000
# Exec form (a JSON list), so uvicorn itself receives Kubernetes' SIGTERM and shuts down
# gracefully. The shell form ("uvicorn ...") would put a shell in between that doesn't
# pass the signal on.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
