# CLAUDE.md — Project Rules

## Project Overview

Grok2API is an OpenAI/Anthropic-compatible API gateway that proxies grok.com and console.x.ai capabilities. Python 3.13+, FastAPI, async throughout.

## Branch Architecture

```
upstream/source (chenyme 停更)  ──┐
                                  ↓ 提取有价值功能
upstream/active (jiujiu532 活跃) ──→  main (合并层)
                                        ↓ 个人定制
                                  custom/my-build (部署层)
```

| 层级 | 分支 | 作用 | 状态 |
|---:|---|---|---|
| L1 | `upstream/source` | 停更上游镜像 (chenyme) | 只读，无新提交 |
| L2 | `upstream/active` | 活跃上游镜像 (jiujiu532) | 只读，定期同步 |
| L3 | `main` | 合并层，基于停更上游 + 本地功能 | **主要开发分支** |
| L5 | `custom/my-build` | 个人定制层，实际部署 | 按需创建 |

**同步命令：**
```bash
# 更新活跃上游
git fetch jiujiu532 && git branch -f upstream/active jiujiu532/main

# 合并上游到 main
git checkout main && git merge upstream/active

# 级联到 custom 分支（如有）
git checkout custom/my-build && git rebase main
```

## Architecture

```
app/
├── control/          # Business logic (account, proxy, model)
│   ├── account/      # Account pool management (basic/super/heavy)
│   ├── proxy/        # Proxy lifecycle (egress nodes, clearance bundles)
│   └── model/        # Model registry and specs
├── dataplane/        # Data operations
│   ├── reverse/      # Reverse proxy protocol (XAI chat, image, video)
│   ├── proxy/        # Proxy adapters (headers, Mihomo client)
│   └── account/      # Account sync and table
├── platform/         # Infrastructure
│   ├── config/       # Configuration (TOML, snapshot, browser)
│   ├── auth/         # Authentication middleware
│   └── storage/      # Local media cache
└── products/         # API products
    ├── openai/       # OpenAI-compatible endpoints
    ├── anthropic/    # Anthropic-compatible endpoints
    └── web/          # Admin UI and WebUI
```

## Key Conventions

1. **Async everywhere** — All I/O is async (aiohttp, asyncio.TaskGroup)
2. **Config via TOML** — `config.defaults.toml` + user overrides in `data/config.toml`
3. **Account pools** — Three tiers: `basic` (free), `super` (paid), `heavy` (premium)
4. **Proxy modes** — `direct`, `single_proxy`, `proxy_pool`, `mihomo`
5. **Clearance modes** — `none`, `manual`, `turnstile`, `flaresolverr`

## Admin API Routes

All admin endpoints are under `/admin/api` with `verify_admin_key` guard.

| Module | Prefix | Key Endpoints |
|--------|--------|---------------|
| `__init__.py` | `/` | `/verify`, `/config`, `/status`, `/mihomo/*` |
| `tokens.py` | `/tokens` | CRUD, `/add`, `/edit`, `/disabled`, `/pool` |
| `batch.py` | `/batch` | `/refresh`, `/nsfw`, `/cache-clear`, `/{id}/stream` |
| `clearance.py` | `/cf-clearance` | `/status`, `/stats`, `/history`, `/refresh` |
| `cache.py` | `/cache` | `/list`, `/clear`, `/item/delete` |
| `assets.py` | `/assets` | `/delete-item`, `/clear-token` |

## Mihomo Integration

When `proxy.egress.mode = mihomo`:
- `MihomoClient` manages proxy group node switching
- Blacklist mechanism auto-excludes failed nodes
- `switch_and_blacklist_current()` for CF challenge fallback

**429 vs 403 轮换规则：**
- 429 = 账号配额耗尽，**不触发代理轮换**，只清零账号配额
- 403 = 代理 IP 被封/CF 挑战，触发代理轮换或 clearance 重新求解

## CF Clearance

Cloudflare clearance lifecycle:
1. `ProxyDirectory.acquire()` checks bundle validity
2. Invalid bundles trigger provider refresh (Manual/Turnstile/FlareSolverr)
3. Mihomo fallback: 3 retries with node switching on failure
4. Events recorded to `data/cf_clearance.db`

## Console Quota Background Tasks

Leader-only background tasks (started in `app/main.py` lifespan):

| Task | Interval | Function |
|------|----------|----------|
| `console-quota-reset` | 30s | `reset_expired_console_windows()` — 重置过期/卡死的 console 配额窗口 |
| `console-expired-recovery` | 10min | `recover_console_expired_accounts()` — 自动恢复 429 EXPIRED 账号 |

Console 配额参数：`BASIC_CONSOLE_LIMIT=20`, `BASIC_CONSOLE_WINDOW_SECONDS=3600`。
轮换策略：`remaining <= 12` 时启动恢复计时器（`app/control/account/refresh.py`）。
429 处理：12小时滑动窗口，3次标 EXPIRED，1小时后自动恢复。

## i18n

Translation files in `app/statics/i18n/{lang}.json`. Supported: zh, en, ja, de, fr, es.

Key sections: `header`, `account`, `config`, `cache`, `cfClearance`, `webui`

## Red Lines

- Never commit secrets (SSO tokens, API keys)
- Never suppress type errors with `as any` or `@ts-ignore`
- Never run `git push --force` on main branch
- Always use `get_config()` for runtime config, not direct imports

## Build & Test

```bash
# Install
uv sync

# Run
uv run granian --interface asgi --host 0.0.0.0 --port 8000 --workers 1 app.main:app

# Test
uv run pytest tests/ -v
```

## Docs

- `README.md` — User-facing documentation (Chinese)
- `docs/README.en.md` — English version
- No separate architecture docs (keep it simple)
