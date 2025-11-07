# ============================================
# Stage 1: Build dependencies with uv
# ============================================
FROM debian:bookworm-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    curl && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Create virtual environment and install dependencies using copy mode for portability
ENV UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev --no-install-project

# ============================================
# Stage 2: Runtime
# ============================================
FROM linuxserver/chrome:latest

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git && \
    apt-get autoclean && \
    rm -rf \
    /var/lib/apt/lists/* \
    /var/tmp/* \
    /tmp/*

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Install uv and Python
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY app/ ./app/
COPY main.py .
COPY data/setting.toml ./data/

# Copy root filesystem (includes autostart)
COPY /root /

# Create necessary directories with proper permissions for abc user
RUN mkdir -p /app/logs /app/data/temp/image /app/data/temp/video && \
    echo '{"ssoNormal": {}, "ssoSuper": {}}' > /app/data/token.json && \
    chown -R abc:abc /app /config/.cache /app/logs

# Set environment variables
ENV TITLE="Grok2API" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

# Expose ports
EXPOSE 8000 3001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1
