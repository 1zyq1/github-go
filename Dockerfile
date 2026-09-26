# syntax=docker/dockerfile:1.7
# Multi-stage build: the Vue app is compiled by Node, the backend is installed
# from the locked uv environment, and the runtime image carries no toolchains.

FROM node:24-alpine AS web
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
WORKDIR /build/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.13-slim AS backend
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /build
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY app/ ./app/

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    GITHUB_ACCELERATOR_STATIC_DIR=/app/frontend/dist
WORKDIR /app
RUN groupadd --system --gid 1001 accelerator \
    && useradd --system --uid 1001 --gid accelerator --home-dir /app accelerator
COPY --from=backend --chown=accelerator:accelerator /build/.venv /app/.venv
COPY --from=backend --chown=accelerator:accelerator /build/app /app/app
COPY --from=web --chown=accelerator:accelerator /build/frontend/dist /app/frontend/dist
# Created before USER so a named volume mounted here inherits this ownership.
RUN mkdir -p /app/.cache/archives \
    && chown --recursive accelerator:accelerator /app/.cache
USER accelerator
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
