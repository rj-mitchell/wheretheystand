# syntax=docker/dockerfile:1
# ---- web app ---------------------------------------------------------------
FROM node:22.23.4-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- service -----------------------------------------------------------------
FROM python:3.12.15-slim
# the base image's own packages are brought up to date too, so security fixes released since it was built are in
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends git rsync ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.11.32
# the app runs as a user of its own, not as root: it can write its data and backups, and nothing else
RUN groupadd --system --gid 10001 hansard \
 && useradd --system --uid 10001 --gid hansard --home-dir /home/hansard --create-home hansard \
 && mkdir -p /data /backups && chown hansard:hansard /data /backups
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ATTRIBUTION.md ./
COPY src ./src
COPY config ./config
COPY data_static ./data_static
COPY tests/fixtures ./tests/fixtures
RUN uv sync --frozen --no-dev --extra embed --compile-bytecode
COPY --from=web /web/dist ./web/dist
ENV HANSARD_DATA_DIR=/data \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
USER hansard
EXPOSE 8000
CMD ["hansard", "serve", "--host", "0.0.0.0", "--port", "8000", "--behind-proxy"]
