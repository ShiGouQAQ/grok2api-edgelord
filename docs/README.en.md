<img alt="Grok2API" src="https://github.com/user-attachments/assets/037a0a6e-7986-41cc-b4af-04df612ee886" />

[![Python](https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.119%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-16a34a)](../LICENSE)
[![GitHub](https://img.shields.io/badge/edgelord-111827?logo=github&logoColor=white)](https://github.com/ShiGouQAQ/grok2api-edgelord)
[![Personal Use](https://img.shields.io/badge/for_personal_use_only-FF4500)](.)
[![AI Vibe](https://img.shields.io/badge/AI_Vibe_Coding-8A2BE2)](.)
[![中文](https://img.shields.io/badge/%E4%B8%AD%E6%96%87-DC2626?logo=bookstack&logoColor=white)](../README.md)

> [!CAUTION]
> **For personal use only.** This is an AI Vibe coding project — most of the code is AI-generated. No guarantees on code quality, security, or stability. Use at your own risk.
>
> Comply with Grok's Terms of Service and your local laws.

**Grok2API edgelord** is a personal trolling fork of [jiujiu532/grok2api](https://github.com/jiujiu532/grok2api) (which is based on [chenyme/grok2api](https://github.com/chenyme/grok2api)). It uses a multi-layer branch architecture to sync upstream features while adding local meme-level enhancements.

<br>

## Branch Architecture

```
upstream/source (chenyme, archived) ──→  extract useful features
                                          ↓
upstream/active (jiujiu532, active) ────→  main (merge + local enhancements)
                                              ↓
                                        custom/my-build (personal deployment)
```

| Layer | Branch | Source | Role | Status |
|:---|:---|:---|:---|:---|
| L1 | `upstream/source` | [chenyme/grok2api](https://github.com/chenyme/grok2api) | Original upstream mirror | Read-only, no new commits |
| L2 | `upstream/active` | [jiujiu532/grok2api](https://github.com/jiujiu532/grok2api) | Active upstream mirror | Read-only, periodic sync |
| L3 | **`main`** | Merge + local enhancements | **Primary dev branch** | Active |
| L5 | `custom/my-build` | Based on main | Personal deployment | On-demand |

**Sync commands:**

```bash
git fetch jiujiu532 && git branch -f upstream/active jiujiu532/main
git checkout main && git merge upstream/active
git checkout custom/my-build && git rebase main
```

> A daily GitHub Actions workflow (`.github/workflows/branch-stacking-sync.yml`) checks for upstream updates at 04:00 UTC and creates an Issue when new commits are detected.

<br>

## Features

- OpenAI-compatible: `/v1/models`, `/v1/chat/completions`, `/v1/responses`, `/v1/images/generations`, `/v1/images/edits`, `/v1/videos`
- Anthropic-compatible: `/v1/messages`
- Streaming and non-streaming chat, reasoning output, function tools passthrough
- Multi-account pool (basic/super/heavy), tiered selection, auto load balancing and quota sync
- Free console.x.ai account support with `*-console` model family
- Text-to-image, image edit, text-to-video, image-to-video with local caching and proxy URLs
- Anti-block built-in: `x-statsig-id` compat fix, WARP + FlareSolverr one-click deploy
- Admin console, Web Chat, Masonry gallery, ChatKit voice page
- CF Clearance monitoring dashboard
- Mihomo proxy group management

<br>

## Fork-Specific Enhancements

| Feature | jiujiu532/grok2api | edgelord |
|:---|:---|:---|
| Base image | `python:3.13-alpine` | `linuxserver/chrome:latest` (Chrome + Xvfb) |
| Init | Custom entrypoint.sh | s6-overlay v3 |
| CF solving | FlareSolverr / manual | **+ Turnstile local solver** (patchright + playwright-captcha) |
| Proxy management | Basic proxy pool | **+ Mihomo integration** (node switch, blacklist, CF fallback) |
| Clearance monitoring | None | **+ SQLite history DB + admin dashboard** (survives restarts) |
| 403 handling | All treated as CHALLENGE | **Smart classification**: IP ban → node rotation / CF challenge → solver / violation → no retry |
| Docker image | `ghcr.io/jiujiu532/grok2api` | `ghcr.io/shigouqaq/grok2api-edgelord` |

## Deployment Notes

This fork uses `linuxserver/chrome:latest` as its base image, which differs from upstream:

| Note | Details |
|:---|:---|
| **Larger image** | ~1.5GB+ (bundles Chrome browser), pull time is longer than upstream |
| **Docker capabilities** | Chrome needs `SYS_PTRACE`. If using the Turnstile solver, add `cap_add: [SYS_PTRACE]` in Compose, or the browser may crash |
| **Upstream image by default** | CI does not auto-build images for this fork. `docker-compose.yml` still points to `ghcr.io/jiujiu532/grok2api:latest`. Build yourself or change `image` to use the edgelord image |
| **Mihomo setup** | Requires a running [Mihomo](https://github.com/MetaCubeX/mihomo) instance with REST API (default `127.0.0.1:9093`). See `mihomo-xai.yaml` for reference config |
| **Turnstile usage** | If not using Turnstile, keep `proxy.clearance.mode = none` and use FlareSolverr/manual mode. The base image change doesn't affect normal operation |

<br>

## Deployment

### Docker Compose (recommended)

```bash
git clone https://github.com/ShiGouQAQ/grok2api-edgelord
cd grok2api-edgelord
cp .env.example .env
docker compose up -d
```

> The default `docker-compose.yml` uses `ghcr.io/jiujiu532/grok2api:latest`. To use a fork-built image, change `image` to `ghcr.io/shigouqaq/grok2api-edgelord:latest` or build from source.

### Plain Docker

```bash
docker run -d \
  --name grok2api \
  -p 8000:8000 \
  -e TZ=Asia/Shanghai \
  -e LOG_LEVEL=INFO \
  -e ACCOUNT_STORAGE=local \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  --restart unless-stopped \
  ghcr.io/jiujiu532/grok2api:latest
```

### From source

Prerequisites: Python 3.13+, [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/ShiGouQAQ/grok2api-edgelord
cd grok2api-edgelord
cp .env.example .env
uv sync
uv run granian --interface asgi --host 0.0.0.0 --port 8000 --workers 1 app.main:app
```

### First-time setup

Open `http://localhost:8000/admin/login`. Default password `grok2api`. Then:

1. Change `app.app_key` (Admin password)
2. Set `app.api_key` (API auth key; leave empty for no auth)
3. Set `app.app_url` (public URL for image/video links)

> Runtime config persists instantly. No container restart needed.

<br>

## Anti-Block Deployment

Requires `NET_ADMIN` + `SYS_MODULE` capabilities (KVM/XEN, not OpenVZ/LXC).

```bash
git clone https://github.com/ShiGouQAQ/grok2api-edgelord
cd grok2api-edgelord
docker compose -f docker-compose.warp.yml up -d
```

Starts: WARP proxy, Privoxy, FlareSolverr, init-config, and grok2api.

<br>

## Upstream Sync

| Mechanism | Description |
|:---|:---|
| **GitHub Actions** | Daily check at 04:00 UTC for new commits in [jiujiu532/grok2api](https://github.com/jiujiu532/grok2api); creates an Issue when updates are found |
| **Manual** | See [upstream-sync-guide.md](../upstream-sync-guide.md) |

> **Format protection**: Never run `ruff format` on upstream code — it produces meaningless diffs and merge conflicts.

<br>

## Environment Variables

| Variable | Description | Default |
|:---|:---|:---|
| `TZ` | Timezone | `Asia/Shanghai` |
| `LOG_LEVEL` | Log level | `INFO` |
| `LOG_FILE_ENABLED` | Write file logs | `true` |
| `SERVER_HOST` | Listen host | `0.0.0.0` |
| `SERVER_PORT` | Listen port | `8000` |
| `SERVER_WORKERS` | Granian workers | `1` |
| `HOST_PORT` | Compose host port mapping | `8000` |
| `DATA_DIR` | Data root | `./data` |
| `LOG_DIR` | Logs dir | `./logs` |
| `ACCOUNT_STORAGE` | Backend: `local` / `redis` / `mysql` / `postgresql` | `local` |
| `ACCOUNT_SYNC_INTERVAL` | Account directory sync interval (s) | `30` |
| `ACCOUNT_SYNC_ACTIVE_INTERVAL` | Active sync interval after change (s) | `3` |
| `ACCOUNT_LOCAL_PATH` | SQLite path | `${DATA_DIR}/accounts.db` |
| `ACCOUNT_REDIS_URL` | Redis DSN | `""` |
| `ACCOUNT_MYSQL_URL` | MySQL DSN | `""` |
| `ACCOUNT_POSTGRESQL_URL` | PostgreSQL DSN | `""` |
| `ACCOUNT_SQL_POOL_SIZE` | SQL pool core size | `5` |
| `ACCOUNT_SQL_MAX_OVERFLOW` | SQL pool max overflow | `10` |
| `ACCOUNT_SQL_POOL_TIMEOUT` | Pool checkout timeout (s) | `30` |
| `ACCOUNT_SQL_POOL_RECYCLE` | Connection recycle time (s) | `1800` |

Runtime config can also be overridden via `GROK_`-prefixed env vars, e.g. `GROK_APP_API_KEY`.

<br>

## Models

> Use `GET /v1/models` for the live list.

### Chat (paid — grok.com)

| Model | mode | tier |
|:---|:---|:---|
| `grok-4.20-0309-non-reasoning` | `fast` | `basic` |
| `grok-4.20-0309` | `auto` | `super` |
| `grok-4.20-0309-reasoning` | `expert` | `super` |
| `grok-4.20-fast` | `fast` | `basic` |
| `grok-4.20-auto` | `auto` | `super` |
| `grok-4.20-expert` | `expert` | `super` |
| `grok-4.20-heavy` | `heavy` | `heavy` |
| `grok-4.3-beta` | computer-use | `super` |
| `grok-4.20-multi-agent-0309` | `heavy` | `heavy` |

### Chat (free — console.x.ai)

| Model | reasoning effort | Notes |
|:---|:---|:---|
| `grok-4.3-console` | medium (default) | basic |
| `grok-4.3-low` | low (fixed) | basic |
| `grok-4.3-medium` | medium (fixed) | basic |
| `grok-4.3-high` | high (fixed) | basic |
| `grok-4.20-multi-agent-console` | medium (default) | basic, multi-agent |
| `grok-4.20-multi-agent-high` | high (fixed, 16 agents) | basic |

### Image / Video

| Model | Mode | tier |
|:---|:---|:---|
| `grok-imagine-image-lite` | `fast` | `basic` |
| `grok-imagine-image` | `auto` | `super` |
| `grok-imagine-image-edit` | `auto` | `super` |
| `grok-imagine-video` | `auto` | `super` |

<br>

## API Reference

| Endpoint | Auth | Description |
|:---|:---|:---|
| `GET /v1/models` | yes | List enabled models |
| `POST /v1/chat/completions` | yes | Unified chat / image / video |
| `POST /v1/responses` | yes | OpenAI Responses API |
| `POST /v1/messages` | yes | Anthropic Messages API |
| `POST /v1/images/generations` | yes | Image generation |
| `POST /v1/images/edits` | yes | Image editing |
| `POST /v1/videos` | yes | Async video job |
| `GET /v1/videos/{video_id}` | yes | Query video job |
| `GET /v1/files/image?id=...` | no | Locally cached image |

<br>

## Changelog

### Beyond v0.2.2 — edgelord local enhancements

**CF Clearance**
- Real-time SQLite aggregation (stats survive restarts)
- Dual-domain manual refresh (grok.com + x.ai)
- Fixed i18n init error on CF Clearance page
- Added `last_check_time` to monitoring API
- Added CF Clearance `get_stats()` SQLite tests

**Console**
- Exact 403 differentiation (content violation vs CF challenge)
- Console proxy rotation & CF refresh fixes
- 429/403 body code parsing for fine-grained rotation

**Proxy**
- Per-domain `_last_check_time` tracking
- Auto-refresh updates `cache_misses` stats

**Engineering**
- CLAUDE.md for AI-assisted development
- Upstream sync guide with format preservation policy
- All dependencies upgraded to latest
- GitHub Actions: Docker build + upstream sync notification

<br>

## Credits

- Active upstream: [jiujiu532/grok2api](https://github.com/jiujiu532/grok2api)
- Original upstream: [chenyme/grok2api](https://github.com/chenyme/grok2api)
- DeepWiki: [chenyme/grok2api](https://deepwiki.com/chenyme/grok2api)
- Project blog: [blog.cheny.me](https://blog.cheny.me/blog/posts/grok2api)
- Community: [Linux.do](https://linux.do)

<br>

## License

MIT
