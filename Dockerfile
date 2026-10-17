# Production image: one service that serves the API and the built React app (same origin).

# Stage 1: build the React app into client/dist.
FROM node:22-alpine AS client
WORKDIR /app
COPY package.json package-lock.json ./
COPY client/package.json client/
RUN npm ci --workspace client
COPY client/ client/
RUN npm run build --workspace client

# Stage 2: the Python API, with the built UI next to it (server/app/main.py serves ../client/dist).
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1
WORKDIR /app/server
# Dependencies first, so this layer is reused when only the code changes.
COPY server/pyproject.toml server/uv.lock server/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY server/ ./
COPY --from=client /app/client/dist /app/client/dist
ENV PATH="/app/server/.venv/bin:$PATH"

# Run as an unprivileged user.
RUN useradd --create-home app
USER app

CMD ["python", "start.py"]
