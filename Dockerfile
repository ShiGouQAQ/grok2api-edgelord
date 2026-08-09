ARG NODE_VERSION=22
ARG GO_VERSION=1.26
ARG ALPINE_VERSION=3.23

FROM --platform=$BUILDPLATFORM node:${NODE_VERSION}-alpine AS frontend-builder

WORKDIR /src/frontend
RUN corepack enable

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN --mount=type=cache,id=grok2api-pnpm,target=/pnpm/store \
    pnpm config set store-dir /pnpm/store && \
    pnpm fetch --frozen-lockfile

RUN --mount=type=cache,id=grok2api-pnpm,target=/pnpm/store \
    pnpm config set store-dir /pnpm/store && \
    pnpm install --offline --frozen-lockfile

COPY frontend/index.html frontend/vite.config.ts frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.node.json ./
COPY frontend/public ./public
COPY frontend/src ./src
RUN --mount=type=cache,id=grok2api-tsc,target=/src/frontend/.cache,sharing=locked \
    pnpm build


FROM --platform=$BUILDPLATFORM golang:${GO_VERSION}-alpine AS backend-builder

ARG TARGETOS
ARG TARGETARCH

WORKDIR /src/backend
RUN apk add --no-cache ca-certificates git

COPY backend/go.mod backend/go.sum ./
RUN --mount=type=cache,id=grok2api-go-mod,target=/go/pkg/mod,sharing=locked \
    go mod download

COPY backend/cmd ./cmd
COPY backend/internal ./internal
COPY backend/docs/docs.go ./docs/docs.go
RUN --mount=type=cache,id=grok2api-go-mod,target=/go/pkg/mod,sharing=locked \
    --mount=type=cache,id=grok2api-go-build,target=/root/.cache/go-build,sharing=locked \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -buildvcs=false -trimpath -ldflags="-s -w" -o /out/grok2api ./cmd/grok2api


FROM alpine:${ALPINE_VERSION}

ARG TARGETARCH
ARG TARGETVARIANT

ENV TZ=Asia/Shanghai \
    GROK2API_CONFIG_SOURCE=/run/grok2api/config.yaml \
    GROK2API_QUALITY_GUARD_DIR=/var/lib/grok2api-quality-guard \
    PUID=10001 \
    PGID=10001

# Install s6-overlay (unpinned, latest via GitHub) + runtime deps
RUN apk add --no-cache ca-certificates su-exec tzdata python3 curl xz && \
    S6_VER=$(curl -fsSL https://api.github.com/repos/just-containers/s6-overlay/releases/latest | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p') && \
    case "${TARGETARCH}-${TARGETVARIANT}" in \
      amd64-*)      ARCH=x86_64 ;; \
      arm64-*)      ARCH=aarch64 ;; \
      arm-v7|arm-)  ARCH=arm ;; \
      arm-v6)       ARCH=armhf ;; \
      386-*)        ARCH=i686 ;; \
      riscv64-*)    ARCH=riscv64 ;; \
      s390x-*)      ARCH=s390x ;; \
      *) echo "unsupported: ${TARGETARCH}-${TARGETVARIANT}" >&2; exit 1 ;; \
    esac && \
    curl -fsSL -o /tmp/noarch.tar.xz "https://github.com/just-containers/s6-overlay/releases/download/${S6_VER}/s6-overlay-noarch.tar.xz" && \
    curl -fsSL -o /tmp/arch.tar.xz "https://github.com/just-containers/s6-overlay/releases/download/${S6_VER}/s6-overlay-${ARCH}.tar.xz" && \
    tar -C / -Jxpf /tmp/noarch.tar.xz && \
    tar -C / -Jxpf /tmp/arch.tar.xz && \
    rm -f /tmp/*.tar.xz && \
    apk del xz curl && \
    rm -rf /var/cache/apk/* && \
    addgroup -S -g 10001 grok2api && \
    adduser -S -D -H -u 10001 -G grok2api grok2api && \
    mkdir -p /app/data /run/grok2api /var/lib/grok2api-quality-guard && \
    chown -R grok2api:grok2api /app/data /run/grok2api /var/lib/grok2api-quality-guard && \
    chmod 0700 /var/lib/grok2api-quality-guard

WORKDIR /app

COPY --from=backend-builder --chmod=0755 /out/grok2api /app/grok2api
COPY --from=frontend-builder /src/frontend/dist /app/frontend/dist
COPY VERSION /app/VERSION
COPY --chmod=0755 tools/egress-quality-guard/quality_guard.py /usr/local/bin/grok2api-egress-quality-guard

# s6 service definitions
COPY docker/s6/s6-rc.d/ /etc/s6-overlay/s6-rc.d/
COPY docker/s6/scripts/ /etc/s6-overlay/scripts/
COPY docker/s6/user-bundles.d/ /etc/s6-overlay/user-bundles.d/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8000/healthz >/dev/null || exit 1

ENTRYPOINT ["/init"]
