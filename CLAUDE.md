# CLAUDE.md — Project Rules

## Project Overview

Grok2API is an OpenAI/Anthropic-compatible API gateway that proxies grok.com and console.x.ai capabilities. Python 3.13+, FastAPI, async throughout.

**上游现状：** [chenyme/grok2api](https://github.com/chenyme/grok2api) 已恢复开发并**以 Go 完全重写**[^1]，不再有 Python 代码。本仓库作为 Python 分支持续跟进，采用 **Go→Python 移植**方式同步上游修复与功能。
[^1]: Go 重写提交：`a16837c feat: Refactor the project using Go to support Grok Build & Grok Web.`

## Branch Architecture

```
upstream/active (jiujiu532 Python) ──→  main (直接 merge，同技术栈)
                                                ↑
upstream/source (chenyme Go 重写) ──────────┘  Go→Python 移植
```

| 层级 | 分支 | 来源 | 作用 | 同步方式 | 状态 |
|---:|---|---|---|---|---|
| **L1** | `upstream/active` | [jiujiu532/grok2api](https://github.com/jiujiu532/grok2api) | **主上游** (Python，同栈) | `git merge` 直接合并 | 只读，定期同步 |
| **L2** | `upstream/source` | [chenyme/grok2api](https://github.com/chenyme/grok2api) | 辅助参考 (Go，需移植) | `git log` 分析 → 评估 → 移植 | 只读，移植参考 |
| **L3** | `main` | 合并层 + 本地增强 | **主要开发分支**，接收上游合并 + Go→Python 移植 | — | 活跃 |

### 同步策略

| 上游 | 技术栈 | 同步方式 | 频率 |
|------|--------|----------|------|
| `jiujiu532` **(L1 主上游)** | Python (同栈) | `git merge` 直接合并 | 按需 |
| `chenyme` **(L2 参考)** | Go (需翻译) | `git log` → 评估 → **记录到 `go-port-ledger.md`** → 移植 | 按需 |

**同步命令：**
```bash
# L1: 更新同栈上游 (jiujiu532 — Python)
git fetch jiujiu532 && git branch -f upstream/active jiujiu532/main
git checkout main && git merge upstream/active

# L2: 更新 Go 参考上游 (chenyme)
git fetch source && git branch -f upstream/source source/main
# Go 代码不可 merge，见 go-port-ledger.md 追踪移植进度
```

### Go→Python 移植台账

每次从 chenyme (Go) 移植代码到 `main` 前，**必须先**记录到 `go-port-ledger.md`，标记提交已审阅/已移植/跳过。避免重复分析同一批提交。

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
│   ├── errors/       # `errors.py` — structured UpstreamError classification
│   └── storage/      # Local media cache
└── products/         # API products
    ├── openai/       # OpenAI-compatible endpoints
    ├── anthropic/    # Anthropic-compatible endpoints
    └── web/          # Admin UI and WebUI
```

## Go→Python 移植对照

从 chenyme (Go) 上游移植代码到本仓库 (Python) 的等价映射：

| Go 概念 | Python 等价 |
|---------|-------------|
| `struct` + 方法 | `class` + 方法 |
| `interface` | `ABC` / `Protocol` |
| `error` 返回值 | `Exception` 层级 + try/except |
| `errors.Is()` / `errors.As()` | `isinstance()` / `type()` 判断 |
| `context.Context` | `asyncio` 任务/取消 |
| `goroutine + channel` | `asyncio.Task` / `asyncio.Queue` |
| `sync.Mutex` | `asyncio.Lock` |
| `sync.WaitGroup` | `asyncio.TaskGroup` / `gather()` |
| `http.Handler` | FastAPI route handler |
| `go.mod` 依赖 | `pyproject.toml` 依赖 |
| `json.RawMessage` | `dict` / `orjson` |
| `io.ReadCloser` | 异步迭代器 / `aiohttp.StreamReader` |
| `time.After` / `time.Ticker` | `asyncio.sleep` / 循环 |
| `slog.Logger` | `structlog` / `logging` logger |
| `Option[T]` | `T \| None` |
| `[]T` (slice) | `list[T]` |
| `map[K]V` | `dict[K, V]` |

## Key Conventions

1. **Async everywhere** — All I/O is async (aiohttp, asyncio.TaskGroup)
2. **Config via TOML** — `config.defaults.toml` + user overrides in `data/config.toml`
3. **Account pools** — Three tiers: `basic` (free), `super` (paid), `heavy` (premium)
4. **Proxy modes** — `direct`, `single_proxy`, `proxy_pool`, `mihomo`
5. **Clearance modes** — `none`, `manual`, `turnstile`, `flaresolverr`
6. **Structured UpstreamError** — `app/platform/errors.py`:
   - Flags: `account_scoped`, `permanent_account_denial`, `quota_exhausted`, `free_quota_exhausted`, `model_quota_exhausted`, `credential_rejected`
   - `from_http_response()` — auto-classifies HTTP status + body into flags
   - 3 mappers: `to_feedback_kind()` (account state machine), `to_proxy_feedback_kind()` (proxy health), `to_result_category()` (reverse pipeline)
   - Classification engine: `_classify_upstream_status()` ports Go `failure.go` patterns
   - `to_dict()` always includes `param` (null when unset) per OpenAI spec
   - 510 tests covering classification + new ported Go fixes

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
