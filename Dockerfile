# syntax=docker/dockerfile:1
# ---- web app ---------------------------------------------------------------
FROM node:22.11.0-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- service -----------------------------------------------------------------
FROM python:3.12.7-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git rsync ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ATTRIBUTION.md ./
COPY src ./src
COPY config ./config
COPY data_static ./data_static
COPY tests/fixtures ./tests/fixtures
RUN uv sync --frozen --no-dev --extra embed
COPY --from=web /web/dist ./web/dist
ENV HANSARD_DATA_DIR=/data \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000
CMD ["hansard", "serve", "--host", "0.0.0.0", "--port", "8000", "--behind-proxy"]
