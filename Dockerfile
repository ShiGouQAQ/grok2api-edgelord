# ── Builder ───────────────────────────────────────────────────────────────────
FROM python:3.13-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

ENV PATH="$UV_PROJECT_ENVIRONMENT/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    build-essential \
    libffi-dev \
    libssl-dev \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /uvx /bin/

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project \
    && find /opt/venv -type d \
         \( -name "__pycache__" -o -name "tests" -o -name "test" -o -name "testing" \) \
         -prune -exec rm -rf {} + \
    && find /opt/venv -type f -name "*.pyc" -delete \
    && rm -rf /root/.cache /tmp/uv-cache

# ── Runtime (Chrome + Xvfb for Turnstile solver) ────────────────────────────
FROM linuxserver/chrome:latest

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    VIRTUAL_ENV=/opt/venv \
    DISPLAY=:99 \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8000 \
    SERVER_WORKERS=1 \
    TITLE="Grok2API"

ENV PATH="$VIRTUAL_ENV/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libffi8 \
    libssl3 \
    libgcc-s1 \
    curl \
    git \
    && apt-get autoclean \
    && rm -rf /var/lib/apt/lists/* /var/tmp/* /tmp/*

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

COPY pyproject.toml config.defaults.toml ./
COPY app ./app
COPY scripts ./scripts
COPY root /

# Fix Python symlink for linuxserver/chrome base image
RUN ln -sf /lsiopy/bin/python3 /usr/local/bin/python3 \
    && ln -sf /lsiopy/bin/python3 /usr/local/bin/python

RUN mkdir -p /app/data /app/logs \
    && chown -R abc:abc /app/data /app/logs \
    && chmod +x /app/scripts/init_storage.sh \
    && chmod +x /etc/s6-overlay/s6-rc.d/svc-grok2api/run \
    && chmod +x /etc/cont-init.d/99-init-storage

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["sh", "-c", "curl -f http://127.0.0.1:${SERVER_PORT}/health || exit 1"]

ENTRYPOINT ["/init"]
