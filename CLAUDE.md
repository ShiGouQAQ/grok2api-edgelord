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
   - **读取配置键前先核对 schema**：`config.defaults.toml` 是权威键集合。曾经踩坑：读 `proxy.clearance.cf_clearance`/`proxy.cf_clearance`（schema 只有 `proxy.clearance.cf_cookies`）导致 cf_clearance 恒空 → SSO→Build mint 403（2026-08-04 修复）。
   - **clearance 派生统一走 `resolve_clearance_config()`**（`app/control/proxy/config.py`）：`cf_clearance` 从 `cf_cookies` 提取，禁止直接 `get_config().get_str("proxy.clearance.cf_clearance", ...)`。
   - **media 格式键在 `[features]`**：`features.image_format`/`features.video_format`/`features.imagine_public_image_proxy`（2026-08-04 从 `[build]` 迁回，与 jiujiu532 上游一致）。
   - **curl_cffi 响应读 body 必须匹配 stream 模式**：`stream=True` 的响应用 `await resp.acontent()`/`aiter_*()`；非 stream 用同步 `resp.text`/`resp.content`。混用必抛 `AssertionError: stream mode is not enabled`，且常被 `except Exception` 吞成空 body → 上层误判（DPoP 502 根因之一，2026-08-05 修复）。mock 响应时同步模拟此行为，否则回归测试测不出。
   - **Build billing 端点是 `cli-chat-proxy.grok.com/v1/billing`**，不是 `api.x.ai/billing/usage`（后者恒 404，2026-08-05 浏览器实测修复）；响应值是 `{"val": int}` 对象（`config.monthlyLimit.val` 等），`parse_billing` 已兼容裸 int。
3. **Account pools** — Three tiers: `basic` (free), `super` (paid), `heavy` (premium)
4. **Proxy modes** — `direct`, `single_proxy`, `proxy_pool`, `mihomo`
5. **Clearance modes** — `none`, `manual`, `turnstile`, `flaresolverr`
6. **Structured UpstreamError** — `app/platform/errors.py`:
   - Flags: `account_scoped`, `permanent_account_denial`, `quota_exhausted`, `free_quota_exhausted`, `model_quota_exhausted`, `credential_rejected`
   - `from_http_response()` — auto-classifies HTTP status + body into flags
   - 2 mappers: `to_feedback_kind()` (account state machine), `to_proxy_feedback_kind()` (proxy health)
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
- `NODE_BANNED`（403 + CF "Attention Required" 标记）= 当前出口 IP 被 CF 封禁，`ProxyDirectory.feedback()` **失效该 bundle**（bundle 由被封 IP 铸出）+ 池光标前进 + mihomo `switch_and_blacklist_current()`（`app/control/proxy/__init__.py`）

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
| `reauth-stuck-recovery` | 1h | `recover_stuck_reauth_accounts()` — REAUTH_REQUIRED 连续失败 ≥3 次标 EXPIRED（`app/control/account/recovery.py`） |

Console 配额参数：`BASIC_CONSOLE_LIMIT=20`, `BASIC_CONSOLE_WINDOW_SECONDS=3600`。
轮换策略：`remaining <= 12` 时启动恢复计时器（`app/control/account/refresh.py`）。
429 处理：12小时滑动窗口，3次标 EXPIRED，1小时后自动恢复。

REAUTH 恢复参数：`account.recovery.reauth_stuck_threshold=3`（连续 reauth 失败次数阈值）、`account.recovery.reauth_stuck_interval_sec=3600`（巡检间隔）。每次 `_expire_invalid_credentials` 命中 REAUTH 时 `bump_reauth_fail_count()` 递增计数；达到阈值后 leader 任务标 EXPIRED（`state_reason=reauth_stuck`），由人工恢复或刷新成功后自动恢复。

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

# Test（--timeout 防止测试卡死无输出；pytest-timeout 已入 dev 依赖）
uv run pytest tests/ -q --timeout=30
```

**测试挂起警示（2026-08-04 踩坑）：** SSO→Build mint 路径（`app/control/account/sso_build.py` 的 `_acquire_mint_lease` → `get_proxy_runtime` → `proxy.acquire`）在 `clearance_mode=turnstile` 时会触发**真实 Cloudflare Turnstile 网络求解**，测试里永不返回 → pytest 间歇挂起（失败集随机，删 event_loop 不根治）。写任何调用 `convert_sso_to_build`/`_mint_via_*` 的测试必须 mock `get_proxy_runtime`——参考 `tests/test_sso_build.py` 的 autouse fixture `_no_real_mint_network`（专测 `_resolve_mint_profile`/`_resolve_cf_clearance_value` 的测试自带 `get_proxy_runtime` patch，会覆盖 autouse）。**禁止**添加 session-scoped `event_loop` fixture（pytest-asyncio 1.4.0 已废弃，曾致共享 loop 挂起）。

## Docs

- `README.md` — User-facing documentation (Chinese)
- `docs/README.en.md` — English version
- No separate architecture docs (keep it simple)
