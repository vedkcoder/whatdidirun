# syntax=docker/dockerfile:1

# ---- 1. Build the React UI ---------------------------------------------------
FROM node:22-bookworm-slim AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# vite.config.ts writes the bundle to ../src/recall/web/dist
RUN npm run build

# ---- 2. Install the Python package -------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first, so code changes don't invalidate this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY --from=web /build/src/recall/web/dist ./src/recall/web/dist
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---- 3. Runtime --------------------------------------------------------------
FROM python:3.12-slim-bookworm
COPY --from=app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    RECALL_DATA_DIR=/data \
    RECALL_WEB_HOST=0.0.0.0 \
    RECALL_WEB_PORT=8765 \
    HOME=/tmp
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4)" || exit 1

ENTRYPOINT ["recall"]
CMD ["web"]
