# CLAUDE.md — Project Rules

## Project Overview

Grok2API is an OpenAI/Anthropic-compatible API gateway that proxies grok.com and console.x.ai capabilities. Python 3.13+, FastAPI, async throughout.

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

## CF Clearance

Cloudflare clearance lifecycle:
1. `ProxyDirectory.acquire()` checks bundle validity
2. Invalid bundles trigger provider refresh (Manual/Turnstile/FlareSolverr)
3. Mihomo fallback: 3 retries with node switching on failure
4. Events recorded to `data/cf_clearance.db`

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
