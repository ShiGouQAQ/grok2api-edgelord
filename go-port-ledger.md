# Go→Python 移植台账

追踪 [chenyme/grok2api](https://github.com/chenyme/grok2api) (Go) 向上游移植到本仓库 (Python) 的进度。

## 规则

1. **每次移植前**查台账，确认提交未被处理过
2. **移植后**立即更新本条记录
3. 状态：`✅ 已移植` / `⏭️ 跳过`(注明原因) / `📋 待审阅` / `🔄 移植中`
4. 版本列记录移植所在的本仓库版本/提交

**去重要求**：每个提交 hash 在整个台账中只出现一次。已移植/已跳过的提交不应出现在"待评估"中。

> **注意（2026-08-04，审计 L1）**：下方 2026-07-15 两行（`c450dee`/`0fe097e`）的"提交 Hash"列填的是**本仓库 Python 移植提交**（存在于 `main` 历史），**非 Go 上游 hash** —— 这两个 hash 不在 `upstream/source` 历史中，仅为本地移植记录占位，勿按 Go 上游 hash 检索。

---

## 台账

| 日期 | 提交 Hash | 提交描述 | 状态 | Python 版本/提交 | 备注 |
|------|-----------|----------|------|-------------------|------|
| 2026-07-15 | `c450dee`（本地移植提交，非 Go 上游） | feat(errors): Go→Python 结构化错误分类移植 | ✅ 已移植 | `c450dee` | Go `failure.go` → Python `errors.py` UpstreamError |
| 2026-07-15 | `0fe097e`（本地移植提交，非 Go 上游） | test: add coverage for structured UpstreamError classification | ✅ 已移植 | `0fe097e` | 425 tests ported from Go `failure_test.go` patterns |
| 2026-07-15 | `e376c22` | fix: prevent json_schema's own type field from overwriting response_format type | ✅ 已移植 | current | Ported `normalize_response_format()` to `chat.py` with type-skip fix; added `response_format` to `ChatCompletionRequest` schema |
| 2026-07-15 | `982e27b` | fix: Messages API 兼容 messages 内联 system role (Claude Code) + error type 透传 | ✅ 已移植 | current | Ported inline system extraction to `_parse_anthropic_messages()` with `_extract_system_text()` helper; error type passthrough in `router.py` |
| 2026-07-15 | `ca97848` | fix: upstream error 后流标记终止 | ✅ 已移植 | current | StreamAdapter._finished in xai_chat.py |
| 2026-07-15 | `afb169e` | fix: error 后忽略后续流事件 | ✅ 已移植 | current | StreamAdapter._handle_event skip when _finished |
| 2026-07-15 | `178bfd4` | fix: message_delta 补齐 input_tokens | ✅ 已移植 | current | messages.py + console_messages.py usage |
| 2026-07-15 | `56fa0a9` | fix: 账号提权修复 | ⏭️ 跳过 | — | Python 无周额度机制，不存在此问题 |
| 2026-07-15 | `3b8feb2` | fix: Console egress scope | ⏭️ 跳过 | — | Python 代理反馈不区分 Scope，clearance 已通过 lease.clearance_host 正确区分域名 |
| 2026-07-15 | `2b797b5` | fix: 临时文件清理不删已提交图片 | ⏭️ 跳过 | — | Python 使用 `os.replace` (rename) 而非 hard link，临时文件在 rename 后已不存在 |
| 2026-07-15 | `dce8627` | fix: 设置保存 revision=0 报 400 (#592) | ⏭️ 跳过 | — | Python 版 admin `/api/admin/config` 使用 `ConfigPatchRequest` 无 revision 字段，不存在 `required` 校验问题 |
| 2026-07-15 | `f30195d` | fix: 恢复 Console egress 审计路由 | ⏭️ 跳过 | — | Python 版无 `audit.Record` 持久化系统；反馈通过 `AccountPatch`/`ProxyFeedback` 走状态机，无通用审计记录可加 egress 字段 |
| 2026-07-15 | `9b661ea` | feat: 公共 API Base URL 可配置 | ⏭️ 跳过 | — | Python 版 `config.defaults.toml` 中 `app_url` 默认为空串，`_app_url()` 返回空时各调用方已正确处理（回退到上游 URL 或相对路径），无需额外配置 |
| 2026-07-15 | `dc9b157` | Build API 提示缓存（prompt_cache_key 注入+缓存） | ✅ 已移植 | current | `prompt_cache.py` — `resolve_prompt_cache_identity()` 移植为 Python，使用 `hashlib.sha256` |
| 2026-07-15 | `3c30472` | 注入 prompt_cache_key 到 Build API 请求体 | ✅ 已移植 | current | `inject_prompt_cache_key()` + `build_console_payload()` 参数 + `console_responses.py`/`console_chat.py` 集成 |
| 2026-07-15 | `99e4e78` | Grok Console 无状态响应支持 | ⏭️ 跳过 | — | Python 版 Console 请求已硬编码 `store: False`，`previous_response_id` 仅在 schema 中存在但不传递给 handler；错误规范化通过 `UpstreamError.from_http_response()` 已处理 |
| 2026-07-15 | `4f34707` | Web SSO 等级检测、配额重新探测、模型同步修复 | ✅ 已移植 | current | `_refresh_one()` 中新增二级探测：当推断为 basic 但账号为 super/heavy 时尝试 expert/heavy 模式配额确认；`_infer_pool_from_live_windows()` 新增 mode_id=3 校验 |
| 2026-07-15 | `0363483` | 图片 URL 基础地址改为请求派生 + 三层回退链 | ✅ 已移植 | current | `_resolve_public_url()` 三层回退链：显式 base_url > `_app_url()` 配置 > `http://127.0.0.1:8000` 默认；`_resolve_image_output()` 和 `_local_image_url()` 新增可选 `base_url` 参数 |
| 2026-07-15 | `b5aad0e` | 图片生成可靠性改进（失败重试 + 下载超时） | ✅ 已移植 | current | WS 非流式路径加入失败重试循环；`_download_image_bytes()` 加入 `asyncio.timeout(30)` 超时 |
| 2026-07-25 | `68bd35e` | fix: correct Anthropic cached input token accounting | ✅ 已移植 | current | Added `cache_creation_input_tokens`/`cache_read_input_tokens` to all Anthropic Messages usage blocks (message_start, message_delta, non-streaming); `server_tool_use.web_search_requests` in usage |
| 2026-07-25 | `db28846` | fix: close CodeQL overflow and search resource bounds | ✅ 已移植 | current | `MAX_WEB_SEARCH_RESULTS=50` cap on StreamAdapter; `_sanitize_url()`/`_sanitize_title()` for search metadata; drop untrusted capacity arithmetic (Python lists auto-grow) |
| 2026-07-25 | `79f225a` | fix(web): distinguish hosted search tool usage | ✅ 已移植 | current | `_web_search_requests` counter in StreamAdapter; `web_search_requests_count()` method; `server_tool_use.web_search_requests` in message_delta usage |
| 2026-07-25 | `4a18b61` | fix(web): sanitize and bound search metadata | ✅ 已移植 | current | `_sanitize_url()` rejects control chars/excessive length; `_sanitize_title()` strips control chars and caps at 200 runes; applied to both webSearchResults and xSearchResults collection |
| 2026-07-25 | `5ad1636` | fix(messages): bound hosted search stream state | ✅ 已移植 | current | `MAX_WEB_SEARCH_RESULTS=50` cap on StreamAdapter._web_search_results; bounds applied in feed() collection loop |
| 2026-07-25 | `215ccb9` | fix(web): complete Claude hosted search mapping | ✅ 已移植 | current | `_build_search_content_blocks()` emits Anthropic-native `server_tool_use` + `web_search_tool_result` content blocks in non-streaming and streaming paths |
| 2026-07-25 | `edd94df` | fix(messages): secure hosted web search lifecycle | ✅ 已移植 | current | Search blocks emitted after text block close, before message_delta; correct block lifecycle in streaming path |
| 2026-07-25 | `00b5f90` | fix(messages): harden Claude web search mapping | ✅ 已移植 | current | `dedupeWebSearchUrls` via `_web_search_urls_seen` set; skip empty/invalid URLs; fallback to error block when no hits |
| 2026-07-25 | `1b6a0c1` | fix: isolate Anthropic web search from reasoning replay | ✅ 已移植 | current | Python proxy doesn't have reasoning replay mechanism; search blocks use separate block_index, isolated from thinking blocks |
| 2026-07-25 | `fb5932b` | fix: preserve multimodal Responses tool outputs | ⏭️ 跳过 | — | Responses API feature (`response_media_audit.go`, `chat_request.go` multimodal tool output preservation); Python proxy's Anthropic Messages handler doesn't have this codepath |
| 2026-07-25 | `fb1babd` | feat(messages): map Build web_search_call to Anthropic server tool blocks | ⏭️ 跳过（部分已移植） | — | Build API `web_search_call` → Anthropic blocks mapping (`server_web_search.go` 287 lines); Python proxy uses Grok Web API SSE (different data source). Search block emission ported via `215ccb9`; Build-specific parsing N/A |

---

## 待评估（当前批 29 个唯一非合并 Go 新提交）

> 2026-07-20 同步：新增 4 个提交

> 注意：仅列尚未评估的提交。已处理（已移植/已跳过）的提交见上方台账。

### 🐛 Bug 修复（建议移植）

| # | 提交 | 范围 | 优先级 | 状态 | 描述 |
|---|------|------|--------|------|------|
| 1 | `c929ad0` | 响应头 | 🟡 中 | ⏭️ 跳过 | Python 使用 aiohttp，响应头为独立对象，不存在别名突变问题 |
| 2 | `792410b` | 响应头 | 🟡 中 | ⏭️ 跳过 | Python aiohttp 无 fhttp Trailer 克隆需求 |
| 3 | `7c03e43` | 前端 | 🟢 低 | 📋 待审阅 | 非 HTTPS 复制按钮失效 |
| 4 | `7a26e9d` | 前端 | 🟢 低 | 📋 待审阅 | LAN 部署复制失效 |
| 5 | `1daa6d0` | 账号同步 | 🟡 中 | ⏭️ 跳过 | Go 多 provider（Web→Console）SSO 同步，Python 无 Web/Console provider 分离，SSO token 统一管理 |
| 6 | `dd6624c` | 工具调用 | 🟢 低 | 📋 待审阅 | Web 搜索工具兼容性警告 |
| 7 | `9244306` | 全链路 | 🔴 高 | ⏭️ 跳过 | Python 版代理已统一，无 Build/Web 传输分离概念 |
| 8 | `ec6e351` | 全链路 | 🟡 中 | ⏭️ 跳过 | 大部分为 Go 特有（conversation/CLI adapter/web chat），Python 无此模块 |

### ✨ 新功能（评估后移植）

| # | 提交 | 范围 | 优先级 | 状态 | 描述 |
|---|------|------|--------|------|------|
| 9 | `3d5e7de` | 审计 | 🟢 低 | 📋 待审阅 | 完整故障诊断审计 |
| 21 | `2db3f65` | 账号/管理 | 🟡 中 | ⏭️ 跳过 | Go 多 provider batch workflows + admin preferences（24 files, ~1200 行），Python 无多 provider 架构和 Go admin UI |
| 22 | `d2eecc4` | 全链路 | 🟡 中 | ⏭️ 跳过 (2026-08-04) | gateway Multi-Provider 路由 + 运行时并发管理（61 files, ~3600 行, 对话模块拆分为独立文件）；Python 无 Web/Console/Build provider 分离架构，路由通过 `ModelSpec.is_console_chat()`/`is_build()` 静态分派，账号池统一 `AccountDirectory` 管理，无 provider registry/selector 层可移植 |
| 23 | `8004840` | 图片 | 🟡 中 | ✅ 已移植 (wave1-G) | `images.py generate()` 非流式路径：重试失败时记录 `last_credential_error`；最终尝试仍可重试失败 → 503 `upstream_unavailable` 审计日志 + 包装错误抛出（Go `writeFailureAudit` + `ErrNoAvailableAccount` 等价） |
| 10 | `5cee3d2` | Console | 🟡 中 | ⏭️ 跳过 (verify-only) | Console provider 已存在：`console_chat.py` 通过 `spec.is_console_chat()` 路由（chat.py:507-508），headers.py:314 `build_console_headers()`；Go 新增 provider 架构无增量可移植 |
| 11 | `d626a26` | 配额 | 🟡 中 | ⏭️ 跳过 | Go `account_model_quota_blocks` DB 表 + `SelectionUnavailableError` + `CapacityWait` 路由配置 + `signerurl` 验证; Python 配额通过 `QuotaWindow` 内存管理 + `_classify_upstream_status()` 错误分类，无 per-model quota block DB |
| 12 | `90c3320` | 同步 | 🟢 低 | ⏭️ 跳过 | Go `syncAllAccounts` 迭代 `ProviderBuild` + `ProviderWeb` 双 provider; Python 单 provider 模型 (`AccountDirectory.sync()`)，无多 provider 同步 |
| 13 | `75d3896` | Console | 🟡 中 | ⏭️ 跳过 | Go Console Multi-Agent 前端页面 (Gallery/Video Gallery) + 媒体 API; Python Admin UI 独立 (`products/web/admin/`)，前端不移植 |
| 14 | `f9e2f91` | Build | 🟢 低 | ⏭️ 跳过 | Go 前端大版本升级 (Vite 8/React 19/Zod 4/TS 6) + egress scope 简化 + settings page; Python 不涉及前端依赖升级 |

### 🔧 重构（参考但不紧急）

| # | 提交 | 范围 | 优先级 | 状态 | 描述 |
|---|------|------|--------|------|------|
| 15 | `845bff8` | 媒体 | 🟢 低 | 📋 待审阅 | 媒体任务审计和错误处理增强 |
| 16 | `d439bd7` | 媒体 | 🟢 低 | 📋 待审阅 | 媒体任务和资产管理优化 |
| 17 | `cce2213` | 前端 | 🟢 低 | 📋 待审阅 | 客户端密钥对话框和复制按钮优化 |
| 18 | `01975a6` | Console | 🟢 低 | ⏭️ 跳过 | Go 删除 `console.go` 并将 Console 路由到独立 `consoleprovider` adapter; Python Console 通过 `xai_console_chat.py` 统一处理，无 provider 拆分概念 |
| 19 | `1b5cddc` | 路由 | 🟢 低 | ⏭️ 跳过 | Python 路由架构不同，无 Go provider registry 概念 |
| 20 | `f524576` | 启动 | 🟢 低 | ⏭️ 跳过 | Go OAuth 凭证刷新逻辑和 startup 流程（`startup.go` 390 行），Python 无 OAuth 和 Go startup 架构 |

### ⏭️ 跳过（Go 特有/CI/文档）

| # | 提交 | 原因 |
|---|------|------|
| — | `a16837c` | Go 重写自身（已到账，Python 版无此概念） |
| — | `2608161` | 超时调整 30→60s（Python 版超时配置独立） |
| — | `0b95234` | Go 特有 slice init 优化 — 7 files 8 insertions, Python 列表无此问题 |
| — | `b7f9f83`, `ef87d9e`, `19ec781`, `dcaeb3f`, `ec6cddc` | CI/仓库整理/README（不涉及 Python 逻辑） |
| — | `c929ad0` | Python 使用 aiohttp，响应头为独立对象，不存在别名突变问题 |
| — | `792410b` | Python aiohttp 无 fhttp Trailer 克隆需求 |
| — | `9244306` | Python 版代理已统一，无 Build/Web 传输分离概念 |
| — | `ec6e351` | 大部分为 Go 特有（conversation/CLI adapter/web chat），Python 无此模块 |
| — | `505c0b3` | Python PROXY_POOL 用游标轮转，无逐节点冷却机制，单节点故障不全局影响 |
| — | `cc578d5` | Python 配置已通过 get_config() 热重载，aiohttp 超时机制不同 |
| — | `d6d88a5` | Python 无 billing hints 配额恢复系统，配额管理架构不同 |
| — | `c31206d` | Python 无账号轮转/failover 机制 |
| — | `d091294` | Python 流处理架构不同，无 StreamFailureDiagnostic 概念 |
| — | `4c918c8` | Python 无逐节点 enabled/disabled 状态跟踪 |
| — | `73826a0` | Python 反馈系统无 scope 级冷却逻辑 |
| — | `106a7e7` | Python 无 nil pointer panic 问题（aiohttp 响应始终有效） |
| — | `6224ed9` | Python 无 egress 回退配置，代理模型不同 |
| — | `57d3f80` | Python 无 egress 节点 CRUD/操作模块 |
| — | `42e1fe7` | Python 无 egress 批量删除功能 |
| — | `c792e47` | Python 无 egress 操作模块（assignment/subscription/sync） |
| — | `6458bb8` | Python 无 Resin/sticky proxy 概念，代理模型不同 |
| — | `34afed0` | Python 无 egress operations 模块，配置失效/回退管理概念不适用 |
| — | `1b5cddc` | Python 路由架构不同，无 Go provider registry 概念 |

---

## 统计

| 类别 | 数量 |
|------|------|
| 已移植 | 14 |
| 待审阅（高优先级） | 0 |
| 待审阅（中优先级） | 7 |
| 待审阅（低优先级） | 11 |
| 跳过 | 11 |
| **总计（旧批唯一非合并提交）** | ~~43~~ **12**（43 为内部统计之和，见下方修正） |

> ⚠️ **修正（2026-08-04，审计 L3）**：本批（`a16837c..dd6624c`）上游实际**非合并提交为 12 个**（`git rev-list --count --no-merges a16837c..dd6624c` 实测）。原"43"是台账内部各类别计数之和（14 已移植 + 7 中 + 11 低 + 11 跳过），非上游提交真实数量，不应用于上游进度统计。

---

## 待评估（新批：dd6624c..d2a8b4f，v3.0.9）

> 同步于 2026-07-25 | 207 个非合并提交，其中 35 个已在上方台账/待评估/跳过中，新增 172 个

> 注意：仅列 dd6624c 之后且未在上方出现的提交。重复提交（如 `ef16e55`/`106a7e7` 同一 fix 两次出现）仅列一次。
>
> 🔁 修正（2026-08-04，审计 L2）：以下 9 行与上方台账重复（`db28846`、`68bd35e`、`79f225a`、`4a18b61`、`5ad1636`、`215ccb9`、`edd94df`、`00b5f90`、`fb1babd`），状态已改标 🔁 重复（台账已登记），不计入待审阅。

### 🐛 Bug 修复（建议移植）

| # | 提交 | 范围 | 优先级 | 状态 | 描述 |
|---|------|------|--------|------|------|
| 1 | `db28846` | Messages API | 🔴 高 | 🔁 重复（台账已登记） | 修复 CodeQL 溢出：取消不可信容量运算，钳制 deferred-text 残差检查，web_search_call 去重前 cap — 安全修复 |
| 2 | `505c0b3` | 代理池 | 🔴 高 | ⏭️ 跳过 | Python PROXY_POOL 用游标轮转，无逐节点冷却机制，单节点故障不全局影响 |
| 3 | `68bd35e` | Anthropic | 🔴 高 | 🔁 重复（台账已登记） | 修正 Anthropic cached input token 计费 — 影响 token 统计准确性 |
| 4 | `4cf6c50` | 403 处理 | 🟡 中 | ⏭️ 跳过 | Python 已通过 `_classify_upstream_status()` 检查 `blocked-user`/`user is blocked` → `credential_rejected` → EXPIRED；Go 的 `IsDefinitiveAccountBlockBody()` JSON 解析等价于 Python `_extract_error_metadata()` |
| 5 | `ba81e8d` | Gateway | 🟡 中 | ✅ 已移植 | 2026-08-04: chat 403 blocked-user → 标 SSO invalid → `reauthRequired`（保留账号仅提示重认证，不再直接 EXPIRED 移除）；与 `232fe83`/`5700827` 同批移植 |
| 6 | `232fe83` | 账号 | 🟡 中 | ✅ 已移植 | 2026-08-04: blocked-user → mark SSO invalid → `reauthRequired`（保留账号仅提示重认证，不删除）；session identity 概念仍不移植（Python 用 SSO cookie 直接访问） |
| 7 | `cc578d5` | Gateway | 🟡 中 | ⏭️ 跳过 | Python 配置已通过 get_config() 热重载，aiohttp 超时机制不同 |
| 8 | `d6d88a5` | Gateway | 🟡 中 | ⏭️ 跳过 | Python 无 billing hints 配额恢复系统，配额管理架构不同 |
| 9 | `c31206d` | Gateway | 🟡 中 | ⏭️ 跳过 | Python 无账号轮转/failover 机制 |
| 10 | `fa13c08` | Build | 🟡 中 | ⏭️ 跳过 | Python 无 reasoning recovery 机制（Go `responses_reasoning_recovery.go` 专用），rate limit 在 StreamAdapter.feed() 中已正确处理 |
| 11 | `454692c` | Build | 🟡 中 | ⏭️ 跳过 | 同 `fa13c08` |
| 12 | `1b6a0c1` | Build | 🟡 中 | ✅ 已移植 | Python 无 reasoning replay 机制；search blocks 使用独立 block_index，与 thinking blocks 天然隔离 |
| 13 | `153bcaa` | Build | 🟡 中 | ⏭️ 跳过 | Python 无 compaction/replay 系统（Go `responses_reasoning_recovery.go` 专用），encrypted_content 处理不同 |
| 14 | `2e5a30d` | Build | 🟡 中 | ⏭️ 跳过 | Python 无 compaction stream 概念，流处理架构不同 |
| 15 | `d091294` | Gateway | 🟡 中 | ⏭️ 跳过 | Python 流处理架构不同，无 StreamFailureDiagnostic 概念 |
| 16 | `3c4e0ef` | Build | 🟡 中 | ✅ 已移植 | `prompt_cache.py`: 添加 `replay_key` 生成（SHA-256 哈希 `grok2api:build-replay:` 前缀），返回 `(cache_key, replay_key)` 元组；soft identity 不生成 replay_key |
| 17 | `48ec7dc` | Prompt Cache | 🟡 中 | ✅ 已移植 | `prompt_cache.py` v3: 新增 `extract_prompt_cache_seed()` 提取 HTTP headers/body cache seed (Sub2API, Codex, Claude Code), `extract_soft_session()` 软会话锚点, `merge_usage()` usage 合并 |
| 18 | `f65a07e` | Prompt Cache | 🟡 中 | ✅ 已移植 | 合并到 `extract_prompt_cache_seed()`: 扩展 headers 列表 (`session_id`, `conversation_id`, `X-Grok-Conv-Id`, `X-Client-Session-Id`) + body 字段 (`prompt_cache_key`, `session_id`, `sessionId`) |
| 19 | `539a6ae` | 全协议 | 🟡 中 | ✅ 已移植 | `extract_soft_session()` 支持 `instructions`/`system` 顶层字段; `merge_usage()` 非零覆盖语义; prompt_cache.py v3 |
| 20 | `4d48031` | 账号/Token | 🟡 中 | ⏭️ 跳过 | Python 已通过 `68bd35e` 移植 Anthropic cache token accounting; `credential_decrypt_failed` 恢复逻辑在 Python 中不适用（Python 无加密凭证，SSO cookie 直接失效走 EXPIRED） |
| 21 | `e2fe5f2` | FlareSolverr | 🟡 中 | ⏭️ 跳过 | Go 的 egress manager `userAgent` 参数传递 + whitespace 修复；Python `FlareSolverrClearanceProvider._solve()` 已直接使用 profile user_agent，无需额外参数 |
| 22 | `09d6f72` | Settings | 🟡 中 | ⏭️ 跳过 | Go 有独立 settings service（`application/settings/service.go`），Python 用 `config.toml` + `get_config()`，概念不同 |
| 23 | `e89c0de` | Settings | 🟡 中 | ⏭️ 跳过 | 同上，Go settings service 的 runtime concurrency 回填逻辑不适用于 Python config.toml 架构 |
| 24 | `502211e` | Console | 🟡 中 | ⏭️ 跳过 | Go 新增 `teamModelRateLimit` map 追踪 Team 级别限流（需 gateway service 架构），Python Console 429 走本地配额扣减（`record_failure_async()`），架构不同 |
| 25 | `d6cc305` | 账号 | 🟡 中 | ⏭️ 跳过 | Go OAuth refresh token 退役逻辑（`resolvePermanentRefreshFailure()`），Python 不使用 OAuth tokens，用 SSO cookie 直接访问 |
| 26 | `b34cb0f` | Settings | 🟡 中 | ⏭️ 跳过 | Go settings service 的 legacy concurrency 保留，Python 无 settings service |
| 27 | `5700827` | 账号 | 🟡 中 | ✅ 已移植 | 2026-08-04: AuthStatusReauthRequired 核心 — SSO 失效标 reauthRequired（保留账号仅提示重认证，而非 EXPIRED 移除）；access token 仍有效时不标 reauthRequired |
| 28 | `4c918c8` | Egress | 🟡 中 | ⏭️ 跳过 | Python 无逐节点 enabled/disabled 状态跟踪 |
| 29 | `80a0bc0` | Build | 🟡 中 | ⏭️ 跳过 | Python 路由架构不同，无 Go `selector`/`forwardResponse` billing 概念；Build 账号通过 console pool (mode_id=5) 统一调度 |
| 30 | `726c379` | Build | 🟡 中 | ⏭️ 跳过 | Python 无 Build 账号等级检测（`IsBuildSuper`），账号池通过 `Tier` 枚举 (BASIC/SUPER/HEAVY) 管理 |
| 31 | `f15b735` | Console | 🟡 中 | ✅ 已移植 | `headers.py` `_client_hints()`: 新增 `Sec-Ch-Ua-Arch` (x86/arm) + `Sec-Ch-Ua-Bitness` (64) |
| 32 | `b4099ca` | 账号 | 🟡 中 | ⏭️ 跳过 | Go billing 模型（`IsPaid()`/`HasFreeProfileSignal()`），Python 用 pool 推断（`_infer_pool_from_live_windows()`）从配额窗口推断，无 billing 概念 |
| 33 | `90b5e6b` | Build | 🟡 中 | ⏭️ 跳过 | Python 无 reasoning replay insert 机制（Go `reasoningreplay/reasoning_replay.go`），slice capacity 溢出是 Go 特有问题 |
| 34 | `fcaeb67` | Build | 🟡 中 | ⏭️ 跳过 | Python 无 Codex Responses history rebuild 机制（Go `responses_history.go` 专用），input normalization 由 `build_console_payload` 处理 |
| 35 | `6b51808` | Build/Media | 🟡 中 | ⏭️ 跳过 | Go 特有：`BuildAPIFallback` 字段移除 + 媒体上传 ticket 消费事务化，Python 无此概念 |
| 36 | `0779b24` | Build | 🟡 中 | ⏭️ 跳过 | Python 无 `IsBuildSuper`/`build_super_entitlement` 概念，账号池通过 Tier 枚举管理 |
| 37 | `d875056` | Build | 🟡 中 | ⏭️ 跳过 | 同 `0779b24`，Python 无 Build 账号等级分层 |
| 38 | `67925ae` | XAI | 🟡 中 | ⏭️ 跳过 | Go Build provider 的 XAI 回退限制（`CanUseBuildAPIFallback` 检查 billing），Python 无 Build provider 和 XAI 回退机制 |
| 39 | `73826a0` | Egress | 🟡 中 | ⏭️ 跳过 | Python 反馈系统无 scope 级冷却逻辑 |
| 40 | `79f225a` | Web | 🟡 中 | 🔁 重复（台账已登记） | 区分 hosted search tool usage |
| 41 | `4a18b61` | Web | 🟡 中 | 🔁 重复（台账已登记） | 清理和限制 search metadata — 安全加固 |
| 42 | `5ad1636` | Messages | 🟡 中 | 🔁 重复（台账已登记） | 限制 hosted search stream state — 防止状态膨胀 |
| 43 | `215ccb9` | Web | 🟡 中 | 🔁 重复（台账已登记） | 完成 Claude hosted search mapping |
| 44 | `edd94df` | Messages | 🟡 中 | 🔁 重复（台账已登记） | 加固 hosted web search 生命周期 |
| 45 | `00b5f90` | Messages | 🟡 中 | 🔁 重复（台账已登记） | 加固 Claude web search mapping |
| 46 | `106a7e7` | Web | 🟡 中 | ⏭️ 跳过 | Python 无 nil pointer panic 问题（aiohttp 响应始终有效） |
| 47 | `c496550` | Video | 🟡 中 | ✅ 已移植 | 记录 video upstream failures |
| 48 | `ac6562b` | Image | 🟡 中 | ✅ 已移植 | 暴露 upload response diagnostics |
| 49 | `c1b6957` | Video/账号 | 🟡 中 | ⏭️ 跳过 | Python 无 media job 外键约束，视频任务存储在内存中 |
| 50 | `9bf43ad` | Media | 🟡 中 | ⏭️ 跳过 | Python 无 media job 数据库约束，视频任务存储在内存中 |
| 51 | `b710e95` | Auto-Clean | 🟡 中 | ⏭️ 跳过 | Go nil-safe sticky cleanup，Python 无 sticky store 概念 |
| 52 | `ccec13f` | Auto-Clean | 🟡 中 | ⏭️ 跳过 | 同上，Go BatchDelete 的 nil-safe sticky guard，Python 无 sticky store |
| 53 | `fb5932b` | Responses | 🟡 中 | ⏭️ 跳过 | Python 响应媒体审计通过结构化日志实现，无 Go response_media_audit.go 等价模块 |
| 54 | `f9b3eef` | Auto-Clean | 🟡 中 | ⏭️ 跳过 | Go reauth auto_clean 模块执行路径 — auto-clean 暂不移植（简化：默认关闭、单 worker、低价值）；`reauthRequired` 核心语义已随 2026-08-04 批 10 移植（保留账号不删除） |
| 55 | `acb822e` | Auto-Clean | 🟡 中 | ⏭️ 跳过 | 同 `f9b3eef`：auto-clean delete path/anchor 硬化暂不移植（简化跳过）；`reauthRequired` 核心已移植 |
| 56 | `7e6656b` | Codex | 🟡 中 | ✅ 已移植 | `router.py`: 新增 `_build_codex_catalog()` 构建 Codex 兼容模型目录，含 `slug`/`display_name`/`supported_reasoning_levels`/`context_window`/`visibility`；SHA256 ETag 缓存；`?client_version=` 查询参数触发 |
| 57 | `dae50ce` | Build | 🟡 中 | ✅ 已移植 | `xai_console_chat.py`: `_BUILD_EFFORT_NORMALIZE` + `_EFFORT_MAP` 将 "max"/"xhigh" 规范化为 "high"；Build 模型不支持这两个 effort 值 |

### ✨ 新功能（评估后移植）

| # | 提交 | 范围 | 优先级 | 状态 | 描述 |
|---|------|------|--------|------|------|
| 58 | `09388e5` | Build/Gateway | 🟡 中 | ✅ 已移植 | `config.defaults.toml`: 新增 `features.build_403_invalidation_codes` 配置（2026-08-04 修正：原记录 `chat.` 前缀错误，schema 键为 `features.`）; `errors.py`: 新增 `should_invalidate_build_forbidden()` 匹配可配置封禁码 (默认: blocked-user, email-domain-rejected, account-suspended, token-revoked) |
| 59 | `6224ed9` | Egress | 🟡 中 | ⏭️ 跳过 | Python 无 egress 回退配置，代理模型不同 |
| 60 | `57d3f80` | Egress | 🟡 中 | ⏭️ 跳过 | Python 无 egress 节点 CRUD/操作模块 |
| 61 | `42e1fe7` | Egress | 🟡 中 | ⏭️ 跳过 | Python 无 egress 批量删除功能 |
| 62 | `c792e47` | Egress | 🟡 中 | ⏭️ 跳过 | Python 无 egress 操作模块（assignment/subscription/sync） |
| 63 | `46483ab` | FlareSolverr | 🟡 中 | ⏭️ 跳过 | Go 托管 FlareSolverr clearance 含 egress manager 大改 (23 files); Python 已有 `FlareSolverrClearanceProvider` 通过 proxy lease 统一管理; Go 的 clearance state tracking/singleflight 在 Python 中由 proxy directory 实现 |
| 64 | `a801c5c` | Client Keys | 🟡 中 | ⏭️ 跳过 | Go client key RPM/MaxConcurrent *int 类型 + DB schema 迁移; Python 用简单 api_key 字符串列表 (`app.api_key`)，无限流/并发控制概念不同 |
| 65 | `c936ab1` | Media | 🟡 中 | ✅ 已移植 (wave1-G) | 新增 `app/platform/storage/media_audit.py`：`summarize_response_media()` b'image' 预过滤（JSON 解码前）+ 忠实 Go walk（root input/messages、chat+anthropic blocks、tool_result 嵌套、data-URI/base64 字节估算）；`log_response_media_summary()` 仅 InputImages>0 时 DEBUG 记录（只记计数不记载荷）；`is_function_call_output_content_array`/`normalize_function_call_output_input`/`normalize_input_image_part`（auto/low/high、original→high+warning、url→image_url 别名） |
| 66 | `65c85f2` | Auto-Clean | 🟡 中 | ✅ 已移植 | 2026-08-04: opt-in auto-clean 随批 10 评估 — `reauthRequired` 核心已移植（保留账号不删除）；auto-clean 执行 ⏭️ 简化跳过（默认关闭、单 worker、低价值） |
| 67 | `d55eb6e` | Build | 🟡 中 | ⏭️ 跳过 | Python 无 reasoning replay cache 系统（Go `reasoningreplay/` 新模块），replay key 生成已添加到 `prompt_cache.py` 供未来使用 |
| 68 | `3405347` | Cache/Session | 🟡 中 | ⏭️ 跳过 | Python 无 sticky session 概念（Go `sticky/` 模块），账号粘滞通过 AccountDirectory 实现 |
| 69 | `ad96250` | Image | 🟡 中 | ✅ 已移植 | 增强 image editing API: aspect ratio, size, streaming, partial images |
| 70 | `e8a104c` | Image | 🟡 中 | ✅ 已移植 | 支持 partial image generation in streaming requests |
| 71 | `f9e9b45` | Video/Build | 🟡 中 | ⏭️ 跳过 | Python 无 `preferFreeBuild` 路由配置（Go `selector.UpdatePreferFreeBuild`），视频路由由 `xai_video.py` 处理 |
| 72 | `f811ffa` | XAI/Video | 🟡 中 | ⏭️ 跳过 | Python 无 account-aware XAI fallback（Go `adapter.go` XAI probe 逻辑），代理反馈通过 `ProxyFeedback` 模型处理 |
| 73 | `6458bb8` | Proxy | 🟡 中 | ⏭️ 跳过 | Python 无 Resin/sticky proxy 概念，代理模型不同 |
| 74 | `095177d` | Build | 🟡 中 | ✅ 已移植 | `xai_console_chat.py`: Build 模型版本从 `grok-build-0.1` 升级到 `grok-build-0.2.106`；添加到 `_MODELS_WITH_SEARCH_TOOLS` 支持 web_search/x_search |
| 75 | `3019b0c` | Build | 🟡 中 | ✅ 已移植 | 同 `095177d`，Build 0.2.106 协议版本对齐 |
| 76 | `037faab` | 路由 | 🟡 中 | ⏭️ 跳过 | Go `model_routes` 表 `uidx_provider_upstream` UNIQUE → 非唯一索引，允许多 public_id 映射同一 upstream; Python 路由通过 `ModelSpec` 静态注册，无动态路由表 |
| 77 | `eb70ea4` | Web 账号 | 🟡 中 | ⏭️ 跳过 | Go 新模块 `web_account_scripts.go`（Tos/NSFW 设置脚本），Python 无 Web provider 和 web account scripts 概念 |
| 78 | `a60e92e` | Web 账号 | 🟡 中 | ⏭️ 跳过 | Go Web account settings terms versioning，Python 无 Web provider 和 web account settings 模块 |
| 79 | `331ac5b` | Video/Web | 🟡 中 | ⏭️ 跳过 | Go `VideoContentDownloader` 接口 + gateway 视频流代理; Python 视频通过 `xai_video.py` WebSocket 获取，无 HTTP 视频流代理 |
| 80 | `07e03e5` | Codex | 🟡 中 | ✅ 已移植 | `router.py`: 新增 Codex 模型目录 (`?client_version=` 查询参数)，含 `slug`/`display_name`/`context_window`/`visibility`；SHA1 ETag |
| 81 | `34afed0` | Egress | 🟡 中 | ⏭️ 跳过 | Python 无 egress operations 模块，配置失效/回退管理概念不适用 |
| 82 | `83bf4f4` | Import | 🟡 中 | ✅ 已移植 | `tokens.py`: 新增 `strip_bom()` 工具函数去除 UTF-8 BOM; `_TOKEN_TRANS` 已含 `\ufeff` → `""` 映射; JSON/JSONL 解析在 Python 中由 Pydantic 处理 |
| 83 | `af29741` | Build/Quota | 🟡 中 | ⏭️ 跳过 | Go Build provider 批量配额重置，Python 无 Build provider，配额管理通过 `refresh.py` 的 `refresh_scheduled()` 实现 |
| 84 | `603176c` | Build/账号 | 🟡 中 | ⏭️ 跳过 | Python 无 `buildBotFlagCache`/`resultcache.Cache`（Go 特有缓存机制），Build bot 标记检测通过 `xai_usage.py` 的 `is_invalid_credentials_body()` 处理 |
| 85 | `6aa4f3f` | Video | 🟡 中 | ✅ 已移植 | 新增 video input size limit + 错误处理 |
| 86 | `9714712` | 账号 | 🟡 中 | ⏭️ 跳过 | Go 多 provider 批量 token refresh 和 `CleanupAccounts`，Python 无多 provider（Web/Console/Build）和 `CleanupStatus` 概念 |
| 87 | `a5f87e0` | SSO | 🟡 中 | ⏭️ 跳过 | Go 多 provider account identity 同步（`provider_links.go`），Python 无 provider links 和多 provider 架构 |
| 88 | `4a56afb` | SSO/Web | 🟡 中 | ⏭️ 跳过 | Go 多 provider account identity 同步 + web account settings，Python 无此架构 |
| 89 | `05f2e2f` | Console/Web | 🟡 中 | ⏭️ 跳过 | Go Console/Web 多 provider SSO 集成，Python 用统一 SSO token 模型，无 provider 概念 |
| 90 | `fb1babd` | Messages | 🟡 中 | 🔁 重复（台账已登记） | 将 Build web_search_call 映射到 Anthropic server tool blocks |
| 91 | `69869c7` | Image | 🟡 中 | ✅ 已移植 | 增强 remote image handling with validation 和 fetching logic |
| 92 | `5190c7b` | 账号 | 🟡 中 | ⏭️ 跳过 | Go billing profile inference（`IsPaid()`/`HasFreeProfileSignal()`），Python 无 billing 模型，用 pool 推断 |
| 93 | `57e7e4b` | 系统 | 🟢 低 | ✅ 已存在 | Python 已有 `platform/update_check.py`: GitHub Release API 轮询 + 版本对比 + 缓存; 无需额外移植 |
| 94 | `1967db1` | 账号 | 🟢 低 | ⏭️ 跳过 | Go Web provider 的 agreement/association filters，Python 无 Web provider 和过滤器概念 |
| 104 | `5d3023ef` | Auto-Clean | 🟡 中 | ✅ 已移植 | 2026-08-04: 可选 auto-clean for reauthRequired — `reauthRequired` 保留账号语义已移植（不删除、仅提示重认证）；auto-clean 启用路径 ⏭️ 简化跳过（默认关闭、单 worker、低价值） |

### 🔧 重构（参考）

| # | 提交 | 范围 | 优先级 | 状态 | 描述 |
|---|------|------|--------|------|------|
| 95 | `1a7f0ac` | 路由 | 🟢 低 | 📋 待审阅 | 更新 ListRoutingCandidates 含 modelRouteID 参数 |
| 96 | `9c6d78c` | 账号 | 🟢 低 | 📋 待审阅 | 精简 Grok Web agreement 和 association filters |
| 97 | `67133a9` | Cache | 🟢 低 | ✅ 已合并 | 合并到 prompt_cache.py v3 移植中 — 清理冗余兼容代码,简化函数签名 |
| 98 | `4c0e593` | Auto-Clean | 🟢 低 | ⏭️ 跳过 | Go auto_clean 模块逻辑更新 — auto-clean 暂不移植（简化跳过）；`reauthRequired` 核心已移植 |
| 99 | `3c50f58` | Video | 🟢 低 | ✅ 已移植 | 增强 video uploads 和 responses 的错误处理和日志 |
| 100 | `d10d649` | 全局 | 🟢 低 | ⏭️ 跳过 | Go `repository.NormalizePage()` 统一分页 + `VirtualTableBody` 前端组件; Python 分页在 `ListAccountsQuery` 中已统一 (page_size 默认 50, 最大 2000); 前端不移植 |
| 101 | `c050086` | Build | 🟢 低 | ⏭️ 跳过 | Python 无 reasoning replay cache 配置（Go `reasoningreplay.Config`），replay key 已添加到 `prompt_cache.py` |
| 102 | `8bcf824` | Build | 🟢 低 | ⏭️ 跳过 | Python 无 Codex responses handling（Go `responses_history.go`），Build 模型通过 `build_console_payload` 处理 |
| 103 | `a71b540` | Web | 🟢 低 | 📋 待审阅 | 更新 web asset tests 使用 credential-based acquisition |

### ⏭️ 跳过

| # | 提交 | 原因 |
|---|------|------|
| — | `834f9f7`, `a51a5d6`, `11bb5e2`, `cf859b5`, `d74b153`, `1334cb6` | 版本号 bump（v3.0.3 → v3.0.9），不涉及逻辑 |
| — | `9a74db4`, `af3c1ea`, `4ff0071` | README/docs 文档更新 |
| — | `8a8db12`, `a39f7b8`, `a88036b`, `4011a22` | chore: 更新 recommended build version / user agent — 版本常量 + 测试适配 |
| — | `4f7371a` | chore: 更新 web chat.go 解析逻辑 — 版本 bump 附属 |
| — | `8b90ba2` | Go 特有内存优化：ensureReasoningInclude slice 预分配 |
| — | `a4d2111`, `413eb39`, `858509b`, `1e70959`, `8695bb6`, `2750537`, `9c583fd` | Go 特有 perf 优化：并发安全、segmented selection、atomic load、batch write — Python asyncio 无此模式 |
| — | `35f5a3a`, `bbd7e38` | Go 特有：nullable function parameter root normalization（CLI tool declarations） |
| — | `002bcac`, `f915e3c` | Go 特有：proxy pool 测试修复 |
| — | `32e9e1c`, `87abeed`, `0316c22` | Go 特有测试：assertMissing, auto-clean 测试, 百万级上下文压缩恢复 |
| — | `62fc6c8`, `8319d16`, `857adc6`, `a277e72`, `da8e55d`, `03469c0`, `3412e65`, `0b53fc1`, `3b24257`, `d45c713`, `47f1686`, `19c9817`, `b1a80d3`, `98c1bb2`, `2c83cca`, `7810a8e`, `def4ea9`, `3b7f5da`, `f257de3`, `ea29d6d`, `abdba88`, `65fae96`, `b848ba7`, `8f376cf`, `a4478b7`, `bac76aa`, `269a81b` | Go React 前端 UI 组件 — Python admin UI 独立，不移植 |
| — | `e39e5be`, `447d45a` | Go React 前端：quota 显示 + account type labels |
| — | `ba3beaf` | 混合（backend pricing + frontend tooltip）— 定价计算可能需单独评估，但 tooltip 部分跳过 |
| — | `26a6995`, `2f3f08f` | 混合（backend auto_clean + frontend settings）— auto-clean 主功能已列在 features，此处为 polish/fix |
| — | `0789063`, `5177b12`, `dde7242` | 混合（backend + frontend）— video asset management / image deletion / dashboard UI，后端部分可能需单独评估 |
| — | `ef16e55` | 与 `106a7e7` 为同一 fix 两次出现（cherry-pick），仅保留 `106a7e7` |

---

## 统计（更新后）

| 类别 | 旧批 | 新批 | 合计 |
|------|------|------|------|
| 已移植 | 14 | 35 | 49 |
| 已存在 | 0 | 1 | 1 |
| 待审阅（高优先级） | 0 | 0 | 0 |
| 待审阅（中优先级） | 7 | 21 | 28 |
| 待审阅（低优先级） | 11 | 4 | 15 |
| 跳过 | 11 | 132 | 143 |
| **总计（唯一非合并提交）** | **43** | **192** | **235** |

> 上游 `upstream/source` 总非合并提交：357（含已到账的旧批43个 + 新批192个 + 更早期未追踪提交）

> 2026-07-25 移植批 1：19 个 proxy/egress/gateway 相关提交全部跳过。原因：Python 代理架构与 Go 完全不同。
> 2026-07-26 移植批 2：23 个 Build/Reasoning 相关提交。其中 5 个已移植（reasoning effort 规范化、replay key、Build 协议版本对齐），18 个跳过（Go 特有 reasoning replay/compaction/entitlement 系统，Python 架构不同）。
> 2026-07-25 移植批 3：11 个 Anthropic Messages/Web Search 相关提交。移植 9 个（token accounting, search bounds, search metadata sanitization, search block emission, search usage tracking）。跳过 2 个（`fb5932b` Responses API multimodal tool outputs, `fb1babd` Build API web_search_call parsing — Python proxy uses Grok Web API, 不同数据源）。
> 2026-07-25 移植批 4（media/image/video）：15 个媒体相关提交。移植 8 个（video failure logging, upload diagnostics, video input size limit, remote image validation, image editing API enhancements, partial image streaming, video error handling）。跳过 7 个（media job DB constraints, terminal video job deletion, media audit logging, egress tracking — Python 无 media job 持久化层）。
> 2026-07-26 移植批 5（account/auth/SSO/quota/auto-clean）：31 个账号/认证/SSO/配额/自动清理相关提交全部跳过。核心原因：Go 重写引入了 Python 不存在的架构层——多 provider（Web/Console/Build）、OAuth refresh tokens、`reauthRequired` 状态、billing 模型、settings service、sticky sessions、auto_clean 模块。Python 用 SSO token + EXPIRED 状态 + config.toml + `cleanup.py` 等不同机制处理等价场景，无需移植。
> **修正（2026-08-04 移植批 10）**：逆转批 5 中 `reauthRequired` 相关提交的跳过决定 — AuthStatusReauthRequired 状态已移植（SSO 失效保留账号仅提示重认证，而非 EXPIRED 移除）。`ba81e8d`/`232fe83`/`5700827`/`65c85f2`/`5d3023ef` 转已移植。仅 auto-clean 执行模块（`f9b3eef`/`acb822e`/`4c0e593`）仍简化跳过：默认关闭、单 worker、低价值。
> 2026-07-26 移植批 6（prompt cache/console/codex/import/403）：22 个提交。移植 9 个（prompt cache v3 提取+软会话+usage 合并, Client Hints Arch/Bitness, Codex 模型目录, 403 封禁码配置, BOM 去除）。跳过 13 个（FlareSolverr egress 大改、客户端密钥 DB 迁移、路由唯一性、模型配额管理、Build+Web 多 provider 同步、Console Multi-Agent、视频流代理、分页标准化）。已存在 1 个（version check）。

---

## 待评估（新批：d2a8b4f7..8f979d45，v3.0.10 → v3.0.11）

> 同步于 2026-08-04 | 54 个非合并提交，均未在上方出现

### 🔴 高优先级 Bug 修复（建议移植）

| # | 提交 | 范围 | 状态 | 描述 |
|---|------|------|------|------|
| 1 | `8b5c1ed6` | 工具调用 | ✅ 已移植 (wave1-B) | `tool_parser.py`：纯字符串十进制整数规范化（无 float64 数学，词法比较 `9007199254740991`，256 字符上限，`-0`→`0`）；`schema_requires_integer()`（type:"integer"、数组排除 "number"）；深度≤64 有界 `$ref` walker（visited-refs 环守卫，allOf/anyOf/oneOf/prefixItems/items/additionalProperties/properties）；流式路径缓冲 `function_call_arguments` delta，`.done` 归一化后发射修正 delta+done；缓冲炸弹防护 每调用 1MB/全局 4MB 上限 + passthrough 模式 |
| 2 | `e3af4fce` | Responses | ✅ 已移植 (wave1-B) | 同 `8b5c1ed6`，Responses 路径 `responses_input.py`/`responses_response.py`：`rewrite_function_call` 存储 Responses 工具输出同样规范化（`UseNumber` 深度≤64，流式 delta 缓冲+修正发射，functionSchemas 按别名键控） |
| 3 | `d1205d85` | 错误分类 | ✅ 已移植 (wave1-A) | `errors.py _classify_upstream_status()`：permanent-denial 措辞收紧（裸 "permission-denied" 不再计入，精确 "access denied" 双位置匹配）；429 free/model 配额标志解耦（free→free_quota_exhausted、model→model_quota_exhausted，不再 OR 合并）；`"permission"` 从 account-scoped 关键词剔除 |
| 4 | `d00698ac` | Gateway | ✅ 已移植 (wave1-A) | `errors.py` 新增 `safety_rejected` 标志（403 metadata+raw body 匹配 "content violates usage guidelines"/"safety_check_type_"，request-scoped 无账号副作用，短路 account/quota/credential 标志）；`should_invalidate_build_forbidden()` 修复孤儿调用 — 同时要求 `safety_rejected=False` 且 `account_scoped=True`；新增 `app/dataplane/reverse/protocol/rate_limit.py`：`parse_rate_limit_metadata`（RPS/RPM 正则、resets in Xd Xh Xm Xs）+ `rate_limit_from_response`（Retry-After 回填，RPS 2s/RPM 60s 默认） |

### 🟡 中优先级（评估后移植）

| # | 提交 | 范围 | 状态 | 描述 |
|---|------|------|------|------|
| 5 | `b4c7baab` | Build 账号 | ✅ 已移植 (wave2-C) | 增强 Build 账号检测的错误分类 — refresh.py 新增 `_is_quota_exhaustion_error` 桥（credit markers "run out of credits"/"out of credits"/"usage balance exhausted"/"usage limit reached" → quota 标志，401 恒为凭据拒绝），`_refresh_build_billing` 配额体不再 EXPIRED（per-account failed，ForEachObserved 式独立结果）；build_detect.py 检测分类消费 quota/free/model 配额标志 |
| 6 | `bcc6435f` | Build 账号 | ✅ 已移植 (wave2-C) | 改进 Build 账号检测和路由 failover — 仅移植检测面：`build_detect.py`（POST /responses 探测 grok-4.5/"hello,test" 非流式，401→手动刷新一次→重试→二次 401→reauth；网络错误 status==0 → failed 不累加计数 softNetworkCooldown；403 chat-denied 默认模型级 failed）；`/admin/api/batch/build-detect` 端点（sync/async SSE）；配置键 `account.build_detect.max_attempts=999`（拒 0）/`mark_build_chat_denied_as_reauth=false`（**直接**门控 detect 403 reauth，不经过 `should_invalidate_build_forbidden`）。路由 selector 侧（selection session/softNetworkCooldown 5s 冷却）归 Wave-2 F。**修正（batch-14 审计）**：`should_invalidate_build_forbidden()`（errors.py:375）在 app/ 零调用方——`build_chat.py`/`build_detect.py` 均未引用，为保留 API（供客户端/测试使用）；默认码表 blocked-user/email-domain-rejected/account-suspended/token-revoked 与 Go failure.go 一致 |
| 7 | `ef10c4cb` | Build 凭证 | ✅ 已移植 (wave2-C) | 允许手动重试无效 Build 凭证 — `build_refresh.py` `refresh_build_token_manual()`（`:manual-retry` 后缀 singleflight 守卫，绕过一次永久短回路）+ `build_refresh_short_circuited()`（scheduler 两循环对永久标记+access 存活账号跳过 OAuth，Go resolvePermanentRefreshFailure）；重试失败后永久标记回写，成功清除 |
| 8 | `34811392` | Build 配额 | ✅ 已移植 (wave2-C) | 更新 Build free quota 估算 — Python 无 `estimated_free_token_limit`（Build 配额为查询次数制 quota_build=100/2h，非 token 估算）；Go 1M→500K 无可移植面，test_refresh_infer.py 钉住查询次数模型 |
| 9 | `75f4f7a7` | Build 代理 | ✅ 已移植 (wave1-D) | `ProxyScope.BUILD` + `ProxyLease.fresh_tunnel`；`acquire()` proxy_pool+BUILD+非 sticky（`{account}` 占位符）每请求轮换 cursor；`build_session_kwargs` 消费 `fresh_tunnel` → curl `FRESH_CONNECT`+`FORBID_REUSE`（Go `request.Close=true`） |
| 10 | `0893557a` | 代理健康 | ✅ 已移植 (wave1-D) | `EgressNode.failure_count` + 节点健康状态机：`feedback()` SUCCESS 分支修复健康/清零计数，失败分支按 kind 衰减 health → state（HEALTHY/DEGRADED/UNHEALTHY），`healthy_nodes()` 生效；`mark_failure_after_success()` 从基线 1 重新计数；节点未命中时记录 `proxy node failure write failed`（Go `stream_failure_health_write_failed`） |
| 11 | `f1867395` | 代理节点 | ✅ 已移植 (wave1-D) | 验证仅 + 回归测试：Python `CancelledError` 是 BaseException，transport wrapper `except Exception` 已天然不捕获 → 取消请求不产生反馈/不冷却节点/不动 cursor/不进黑名单（test_proxy_health.py `TestCancelNoCooldown` 驱动真实 `post_stream` 验证） |
| 12 | `1edc9fbe` | 模型别名 | ✅ 已移植 | current (wave1-E) | `registry.py` ALIASES 映射 + `resolve_alias()`/别名感知 `resolve()`；`spec.py` 新增 `supports_reasoning`；`xai_build.py` `_normalize_reasoning_effort` 别名路由 + "none"→thinking disabled/其余 adaptive；`xai_console_chat.py` CONSOLE_MODELS/_MODEL_FIXED_EFFORT 扩展 `grok-4.20-0309-reasoning-{low,medium,high}` |
| 13 | `15146556` | 路由 | ✅ 已移植 (wave2-F) | `app/products/_routing_policy.py` `RoutingAttemptPolicy`/`new_routing_attempt_policy`/`routing_attempt_policy`（-1 无限，≤0→3，配置 1..200，0/越界 ValueError）；10 个产品路由循环改 `count()`+`allows()` 驱动，`has_next()` 替换 `attempt < max_retries`；stored Responses 强制 1 次尝试；配置 `routing.max_routing_attempts=200`/`unlimited_routing_attempts=-1`；tests/test_routing_policy.py 16 项；Python 提交 `626595ed` |
| 14 | `72340380` | 路由 | ✅ 已移植 (wave2-F) | 同上：验证上限放宽至 200（`MAX_ROUTING_ATTEMPTS`），移除硬上限 10；Python 提交 `626595ed` |
| 15 | `2aaac4d0` | Clearance | ⏭️ 跳过 (verify-only) | 已满足：`ManualClearanceProvider.build_bundle()`（providers/manual.py:11-25）从不校验 `cf_cookies` 非空 — mode==MANUAL 时无条件构建 bundle（仅模式不匹配返回 None）；无 challenge cookies 的 clearance 在 Python 架构性成立 |

### 🟢 低优先级（参考）

| # | 提交 | 范围 | 状态 | 描述 |
|---|------|------|------|------|
| 16 | `35f4f115` | Web 媒体 | 📋 待审阅 | 文件名清理和安全日志 — 上传路径安全加固 |
| 17 | `f4f439a4` | 账号管理 | 📋 待审阅 | 批量更新账号并发 — Go admin 批处理，Python admin batch 参考 |

### ⏭️ 跳过（Go 特有/UI/egress/版本/测试）

| # | 提交 | 原因 |
|---|------|------|
| — | `89b47825`, `fc65a8d2`, `6011cf87`, `630f6d0f`, `981f4cfe`, `3d7159a3`, `ebbea88c`, `f030909c`, `3d6a699a`, `830c3b07`, `0de70e0f` | egress 节点管理/探测 — Python 无 egress 节点 CRUD/操作模块（同旧批结论） |
| — | `2f030c09`, `80d099b2`, `e87525a6`, `b97dac94` | Go React 前端 UI — Python admin UI 独立，不移植 |
| — | `c3182ac6`, `37268f0b`, `e4552ee9`, `347f5b75` | Go linked-account 管理/清理 — Python 无多 provider linked accounts 概念 |
| — | `f2676e8a`, `022ffc1c` | Go client key routing scopes — Python 用简单 api_key 列表，无限流/scope 控制 |
| — | `2a336686` | Go BatchUpdate provider 校验 — Python 无多 provider 架构 |
| — | `d21cc395`, `ed0b5da0`, `25cf4699`, `f4ac412f`, `cd6369d0` | Go OAuth 凭证刷新/DB 凭证导出 — Python 用 SSO cookie，无 OAuth tokens 和加密凭证 DB |
| — | `db66caba` | response billing 指标 — Python 无 billing 模型 |
| — | `6ec604c1`, `f6c74e9e`, `2a1d6cfe`, `413bebee` | web 媒体上传/fsync/诊断 — Python 媒体路径不同（无 media job 持久化层） |
| — | `326d9aed`, `c3c07553`, `1325476b` | Go 特有测试 |
| — | `aabf8f9f`, `c27f0545` | 版本号 bump（v3.0.10/v3.0.11），不涉及逻辑 |

---

## 统计（更新后）

| 类别 | 旧批 | 新批 | 合计 |
|------|------|------|------|
| 已移植 | 49 | 0 | 49 |
| 已存在 | 1 | 0 | 1 |
| 待审阅（高优先级） | 0 | 4 | 4 |
| 待审阅（中优先级） | 28 | 11 | 39 |
| 待审阅（低优先级） | 15 | 2 | 17 |
| 跳过 | 143 | 37 | 180 |
| **总计（唯一非合并提交）** | **235** | **54** | **289** |

> 上游 `upstream/source` 总非合并提交：**678**（`git rev-list --count --no-merges upstream/source` 实测，2026-08-04 修正；原"411"为旧统计口径）。自 `a16837c` 起的非合并提交：**282**（`git rev-list --count --no-merges a16837c..upstream/source` 实测）。

> 2026-08-04 移植批 7（v3.0.10→v3.0.11）：54 个提交。待审阅 17 个（4 高：整数工具参数规范化 ×2、HTTP 失败分类增强 ×2；11 中：Build 账号检测/配额/代理轮换、代理健康、路由尝试上限、clearance 无 cookie；2 低：媒体文件名清理、批量并发）。跳过 37 个（egress 管理/探测 11、Go UI 4、linked-account 4、client keys 2、Go 凭证/OAuth 5、billing 1、媒体 4、Go 测试 3、版本号 2）。

> 2026-08-04 本地修复批 8（配置键不匹配，非移植）：审计全库 get_config() 键读取 vs config.defaults.toml schema，修复 4 处：
> - **M1 根因**（SSO→Build mint 403）：`sso_build.py` PKCE-CS/Device Flow 直接读 `proxy.clearance.cf_clearance`/`proxy.cf_clearance`（schema 无此键）→ 恒空 → 不播种 cf_clearance → accounts.x.ai 预校验 403。修复：新增 `_resolve_cf_clearance_value()` 复用 `resolve_clearance_config().cf_clearance`。
> - **M2**：`errors.py` `should_invalidate_build_forbidden` 读 `chat.build_403_invalidation_codes` → 改 `features.build_403_invalidation_codes`。
> - **M3**：`config.py` `resolve_clearance_config` 的 `cf_clearance` 字段从 `cf_cookies` 派生（`extract_cookie_value`），legacy 平键兜底。
> - **M6**：`config.defaults.toml` 的 `image_format`/`imagine_public_image_proxy`/`video_format` 从 `[build]` 移回 `[features]`（与 jiujiu532 上游一致）。
> - M4/M5（`storage.data_dir`、`browser.custom_fingerprint.enabled`）审计后 KEEP。
> 测试：全套 1179 passed。TDD：每处修复先 RED 后 GREEN（tests/test_clearance_config.py、test_build_errors.py、test_config.py、test_sso_build.py 新增 16 测试）。

---

## 2026-08-04 移植批 9（Go 8f979d4 SSO→Build + QuotaBilling 对齐，非独立提交移植）

上游对照：`backend/internal/infra/provider/web/sso_build.go`（@8f979d4）为 **Device Flow-only**（SSO 预检 → device/code → verify(consent) → approve(done) → poll token），**全仓库零 PKCE-CS**。`cli/definition.go` `grok_build → Quota: QuotaBilling`（api.x.ai/billing + Bearer），`accountsync/service.go` 按 QuotaKind 分派探测。本仓库此前 PKCE-CS 为注册机逆向移植（GrokRegisterAgent/cpa_grpcweb，公开源不可查），生产从未成功。

| 提交 | 状态 | 说明 |
|---|---|---|
| `a16837c9` (v3.0.0) | 已审阅 | Go 重写引入 sso_build.go（Device Flow-only），无 PKCE-CS |
| `8f979d45` (sso_build.go 现状) | **已移植** | convert_sso_to_build 改为 **Device Flow 首选**（PermissionError=SSO 无效直接传播对齐 ErrUnauthorized）；PKCE-CS 降级为可选 fallback（SSOCredentialRejected 硬失效传播） |
| `cli/definition.go` `QuotaBilling` | **已移植** | build 账号探测从 grok.com/rest/rate-limits（错：OAuth token 当 sso cookie→401→误标 EXPIRED）改路由 `api.x.ai/billing/usage` + Bearer（`fetch_build_billing`），`_refresh_one` build 分支 + `_POOL_CONFIG["build"]` 6h 定时 |
| `accountsync` QuotaKind 分派 | **已移植** | 转换 success 计数 = 验证通过才 success（非空 token + JWT exp + billing smoke 200）；冒烟 credential_rejected → 标源 SSO rejected（对齐 markSSOCredentialRejected） |

本地增强保留：PKCE-CS 作为可选 fallback（注册机路径，仅临时性失败触发）；`SSOCredentialRejected` 异常类型；`fetch_build_billing` 共享 helper（tokens.py build_refresh_billing 同款逻辑提取）。
测试：全套 1207 passed（新增 test_build_convert.py 5 + test_build_refresh_routing.py 5 + sso_build/xai_billing/admin_payload 增量）。

---

## 2026-08-04 移植批 10（AuthStatusReauthRequired 逆转：保留账号仅提示重认证）

逆转批 5 的跳过决定。此前判定"Python 无 `reauthRequired` 概念（直接 EXPIRED 失效）"，现移植 Go 的 AuthStatusReauthRequired 语义：**SSO/凭证失效标 reauthRequired，保留账号仅提示重认证，不再直接 EXPIRED 移除**。上游对照：`backend/internal/...` `markSSOCredentialRejected → MarkReauthRequired` 谱系（`sso.go`/`accounts.go` 状态机）。

| 提交 | 状态 | 说明 |
|---|---|---|
| `ba81e8d4` (invalidate SSO on chat 403 blocked-user) | **已移植** | chat 403 `blocked-user` body → 标 SSO invalid → reauthRequired（保留账号仅提示重认证） |
| `232fe832` (mark SSO invalid on blocked-user) | **已移植** | `blocked-user`/`session blocked` → mark SSO invalid → reauthRequired |
| `57008276` (do not mark reauthRequired when access token valid) | **已移植** | AuthStatusReauthRequired 核心 — access token 仍有效时不标 reauthRequired；SSO 失效保留账号不删除 |
| `65c85f24` (opt-in auto-clean for reauthRequired) | **已移植（简化）** | opt-in 开关基础设施随批评估；`reauthRequired` 语义已移植，auto-clean 执行 ⏭️ 简化跳过 |
| `5d3023ef` (optional auto-clean for reauthRequired) | **已移植（简化）** | 同上 — 可选 auto-clean 暂不移植 |

**简化（`ponytail:` 级决策）**：
- **auto-clean 暂不移植**（`f9b3eef`/`acb822e`/`4c0e593` 仍跳过）：默认关闭、单 worker、低价值 — 保留账号不删除语义本身已覆盖主要风险（不再误删账号），自动物理清理在单 worker 部署下收益可忽略。
- **保留账号不删除语义**：reauthRequired 账号保留在池中，仅标记待重认证；沿用 `credential_rejected` 反馈链，但不再立即升级 EXPIRED 移除。

测试：全套 passed（新增 reauthRequired 状态机/保留账号测试）。


---

## 2026-08-04 移植批 11（8f979d45..de0dcbe3：Build 0.2.119 协议加固 + Egress Quality Guard）

上游同步点：`upstream/source` = `de0dcbe3`（PR #843 合并），上次分析点 `8f979d45`。12 个新提交（PR #837 egress quality guard ×11 + PR #843 3721babd ×1）。A3 审计同时发现 **4 个 batch-2 漏网提交**（dd6624c..d2a8b4f7 范围内 ledger 未覆盖），一并登记。

### PR #843: Build 0.2.119 协议加固（`3721babd`）— ✅ 已移植

| 提交 | 状态 | 说明 |
|---|---|---|
| `3721babd` (harden Grok Build 0.2.119 protocol compatibility) | **已移植** | 见下方移植详情 |

**移植详情**（本批核心）：
- `RecommendedBuildClientVersion` 0.2.111 → **0.2.119**：`config.defaults.toml [build] client_version`、`headers.py build_build_headers()` 默认值（改读 config `build.client_version`/`build.token_auth`/`build.user_agent`，不再硬编码）、`oauth_device.py CLIENT_VERSION`
- `normalizeBuildReasoningEffortPayload` **模型感知 xhigh**：`xai_build.py _normalize_reasoning_effort(effort, model)` 新增 `_XHIGH_SUPPORTED_MODELS = {"grok-4.20-multi-agent-0309"}`（对应 Go `modeldomain.SupportsReasoningEffort`）；max 恒→high，xhigh 仅支持模型保留，未知模型防御性 high
- **client_metadata 剥离**：Python 架构性已处理（payload 重建 + pydantic `extra="ignore"`，`client_metadata` 永不过界）→ 无需移植，测试锁定
- **store:false + include reasoning.encrypted_content 默认**：`build_build_responses_payload` 已硬编码 → 已有，测试锁定
- **SSE BOM 剥离 + doom_loop_check 过滤**：`_classify_build_line` `.strip()` 已剥 BOM（U+FEFF isspace=True），`BuildStreamAdapter.feed` 丢弃未知事件 → 架构性已处理
- **清理死代码**：`xai_console_chat.py` 重复的 `_BUILD_EFFORT_NORMALIZE`（`_EFFORT_MAP` 已覆盖）；`xai_build.py` 旧 `_BUILD_EFFORT_NORMALIZE`
- 测试：`tests/test_build_normalize.py` 新增表驱动（effort 9 例 + store/include + client_metadata），`test_build_headers.py` 断言更新 0.2.119

**未移植（Go 特有）**：`normalizeResponseStream` toolCompatibility nil-safe 分支、`patchReasoningTextTypes`、`normalizeResponseFormat` 迁移 —— Python Build 路径 payload 由参数重建（非 raw body 透传），无等价场景；`responses_response.py normalize_response_stream` 是 no-op 桩未被调用，保持现状。

### PR #837: Egress Quality Guard（`137589c3`..`69e5fdd8` 8 功能提交 + 2 merge）— ⏭️ 跳过（核心阻塞）

| 提交 | 状态 | 说明 |
|---|---|---|
| `137589c3` (feat: add egress recovery and quality guard) | ⏭️ 跳过 | 被动检测依赖 audit 持久化，Python 无（见下方） |
| `fe762c58` (fix: match quality guard to panel TPS) | ⏭️ 跳过 | 同上 |
| `fb31e5ca` (fix(quality-guard): quarantine immediately on passive hard TPS) | ⏭️ 跳过 | 同上 |
| `08af9a85` (feat: manage quality guard egress nodes) | ⏭️ 跳过 | 需 egress node CRUD 持久化，Python 节点为 config 派生临时对象 |
| `982e9ba7` (feat: harden quality guard proxy rotation) | ⏭️ 跳过 | session_rotator 需 1024Proxy + Mihomo exit-IP 校验，Python MihomoClient 无此能力 |
| `334dbe0f` (fix: recover quality probes without bound accounts) | ⏭️ 跳过 | 依赖 selector 账号绑定模型 |
| `4fecb950` (feat: show account counts in quality guard nodes) | ⏭️ 跳过 | 前端页面 |
| `69e5fdd8` (feat: integrate egress quality guard with managed identity and Compose) | ⏭️ 跳过 | bootstrap + clientkey 身份系统，Python 无 client-key 模型 |
| `ecfd2333` / `1d62978a` (merge) | — | merge 提交 |

**阻塞根因**（wave-1 A2 agent 评估）：
1. **被动检测数据源不存在**：Go `GET /api/internal/v1/quality-guard/request-audits` 依赖 audit 子系统按请求记录 `outputTokens/firstTokenMs/egressNodeId`。Python 零 audit 持久化（`grep -ril audit app/` 空；ledger `f30195d` 已记录此缺口）——被动模式不可移植，需先建 telemetry 子系统（独立项目）。
2. **无 egress node CRUD/隔离原语**：Go 节点是 DB 持久行（id/enabled/exitIp/账号绑定）；Python `EgressNode`（`app/control/proxy/models.py:66`）为 config 派生的内存临时对象（`node_id="pool-N"`），无 enabled 标志、无持久化、无账号绑定。隔离动作（disable node）无处可落；`EgressNodeState`/`healthy_nodes()` 机制存在但从未被写入（死字段，可作未来 hook）。
3. **无 forced-node 探测能力**：Go `ProbeEgressQuality` 强制指定节点走网关测 TPS；Python reverse pipeline（`ProxyDirectory.acquire()`）无节点 pinning。
4. **mihomo 模式节点不可映射**：节点在 Mihomo 内部，grok2api 仅切换 group；sidecar 的数字 node_id 无对应物。

**可落地部分（后续可选路线图，不在本批）**：
- sidecar `tools/egress-quality-guard/quality_guard.py`（1126 行，**已是 Python**，stdlib-only）可直接采用——但它消费 Go 的 6 个 internal API，Python 端需先实现 bootstrap + internal router + admin status/config
- 池模式隔离最小集：让 `ProxyDirectory.feedback()` 在 NODE_BANNED 时写 `EgressNode.state=UNHEALTHY`（复用现成 `healthy_nodes()` 过滤）
- 建议顺序：建 request-audit telemetry → internal API → sidecar 接入 → 池模式隔离 → passive 模式

### A3 审计：batch-2 漏网提交（dd6624c..d2a8b4f7，ledger 原判全覆盖实漏 4 个）

| 提交 | 状态 | 说明 |
|---|---|---|
| `a234e3b4` (fix(web,session): prefer block signals before identity and Statsig retry) | ⏭️ 跳过（评估） | 封号 body 优先于 Statsig invalidation 重试。Python 侧 `x-statsig-id` 处理在 `headers.py _statsig_id`（无 Statsig 重试逻辑），web sessionidentity 是 Go 特有模块；Python 无等价重试吞首包路径 → 无风险场景 |
| `af2415f1` (feat: introduce webVisibleStreamPhase to manage client-visible output and suppress late reasoning) | ⏭️ 跳过（评估） | web chat.go 流相位管理。Python web chat 流经 `xai_chat.py StreamAdapter` 已按事件类型过滤（`_finished`/`_handle_event`），晚期 reasoning 事件天然被丢弃 → 架构性等价 |
| `894bf6b5` (feat: updated egress binding UI) | ⏭️ 跳过 | 前端 UI |
| `b4836edd` (feat: add partial_images and stream fields to API documentation) | ⏭️ 跳过 | 文档 |

---

## 2026-08-04 移植批 12（台账高/中优先级全量迁移，8f979d45..de0dcbe3 批 + 旧批残留）

> 依据任务："检查 port-ledger，进行所有的高、中等级的台账完全迁移"。本批将所有 🔴 高 / 🟡 中 且状态为 📋 待审阅 的条目全部处理完毕（已移植或已决策跳过），**此后台账高/中优先级待审阅清零**（剩余待审阅均为 🟢 低，out of scope）。

### 批 3 高优先级（4/4 已移植）

| 提交 | 状态 | 落点 |
|---|---|---|
| `8b5c1ed6` (流式整数工具参数规范化) | ✅ 已移植 | `tool_parser.py`：string 十进制整数规范化（maxExactJSONIntegerText）、`schema_requires_integer`、深度≤64 有界 $ref walker、1MB/4MB 缓冲上限 |
| `e3af4fce` (Responses 路径整数规范化) | ✅ 已移植 | 同 `8b5c1ed6`，responses 路径共用 tool_parser 规范化 |
| `d1205d85` (HTTP 上游失败分类增强) | ✅ 已移植 | `errors.py`：429 free/model 配额解耦、永久拒绝措辞收紧（bare permission-denied 不再计）、`body` 关键字参数 |
| `d00698ac` (Build safety/quota 失败分类) | ✅ 已移植 | `errors.py` 新增 `safety_rejected` 标志（403 metadata+raw body，短路 account/quota/credential 标志）；新增 `app/dataplane/reverse/protocol/rate_limit.py`（RateLimitMetadata + parse + Retry-After 回填，RPS 2s/RPM 60s） |

### 批 3 中优先级（11/11 已处理：6 移植 + 3 verify-only + 2 架构跳过）

| 提交 | 状态 | 说明 |
|---|---|---|
| `b4c7baab` (Build 账号检测错误分类) | ✅ 已移植 | `refresh.py` `_is_quota_exhaustion_error()` 桥：credit markers → quota 标志；`_refresh_build_billing` 配额体 per-account failed（不 EXPIRED 不 abort） |
| `bcc6435f` (Build 检测 + 路由 failover) | ✅ 已移植 | 新 `app/control/account/build_detect.py` + `POST /admin/api/batch/build-detect`：grok-4.5 "hello,test" 探测、401→刷新→二次 401→reauth、网络错误不累加计数；配置 `account.build_detect.max_attempts=999` / `mark_build_chat_denied_as_reauth=false`；路由侧 softNetworkCooldown 归批 2-F |
| `ef10c4cb` (Build 凭证手动重试) | ✅ 已移植 | `build_refresh.py` `refresh_build_token_manual()`（`:manual-retry` singleflight）+ `build_refresh_short_circuited()`（scheduler 两循环跳过永久标记账号 OAuth） |
| `34811392` (Build free quota 估算) | ⏭️ 跳过 | Python 无 `estimated_free_token_limit`（查询次数制 quota_build=100/2h），1M 值仅为 max_output_tokens 协议上限，无关 |
| `75f4f7a7` (每请求轮换 Build 隧道) | ✅ 已移植 | `ProxyLease.fresh_tunnel`（BUILD scope + PROXY_POOL + 非 sticky → 每请求 fresh connect），`_pick_proxy_url(rotate=True)` |
| `0893557a` (MarkFailureAfterSuccess) | ✅ 已移植 | `feedback()` SUCCESS 分支（failure_count→0 + health 回升）；failure 分支 health 衰减因子表；`mark_failure_after_success` 基线=1 |
| `f1867395` (取消不冷却节点) | ✅ 已移植 (verify-only) | Python `except Exception` 不捕获 `CancelledError`（BaseException）→ 架构性等价，测试锁定 |
| `1edc9fbe` (模型别名 reasoning effort) | ✅ 已移植 | `registry.py`/`spec.py` 别名→canonical 解析；effort none→thinking 禁用；SupportsReasoning |
| `15146556` (无限路由尝试) | ✅ 已移植 | 新 `app/products/_routing_policy.py` `RoutingAttemptPolicy`（allows/has_next，-1→unlimited，≤0→3）；10 产品循环 `for attempt in count()` 转换 |
| `72340380` (移除 maxAttempts 上限 10) | ✅ 已移植 | 同上，Python 原无 10 硬上限，随 policy 对齐（验证 config 允许 -1、拒 0/>200） |
| `2aaac4d0` (无 challenge cookie clearance) | ✅ 已移植 (verify-only) | `ManualClearanceProvider.build_bundle()` 不校验 cf_cookies，无条件构建 → 架构性等价，测试锁定 |

### 旧批残留中优先级（已处理）

| 提交 | 状态 | 说明 |
|---|---|---|
| `8004840` (image generation 增强) | ✅ 已移植 (wave1-G) | `images.py generate()`：last_credential_error + 503 upstream_unavailable 包装 |
| `c936ab1` (media audit logging) | ✅ 已移植 (wave1-G) | 新 `app/platform/storage/media_audit.py`（b'image' 预过滤、InputImages>0 DEBUG、normalizers） |
| `5cee3d2` (Grok Console provider) | ⏭️ 跳过 (verify-only) | Console provider 已存在（`spec.is_console_chat()` 路由 + `build_console_headers()`），无增量 |
| `d2eecc4` (gateway Multi-Provider) | ⏭️ 跳过 (2026-08-04) | Python 无 Web/Console/Build provider 分离架构，路由静态分派（见上方行 80） |

### 测试

全套 `uv run pytest tests/ -q --timeout=30` → **1467 passed, 1 skipped**（基线 1432+1，+35 本批新增，无回归）。新增测试：`test_routing_policy.py` (16)、`test_build_detect.py` (15)、`test_tool_parser_normalize.py`、`test_proxy_health.py` (13)、`test_rate_limit_parser.py` (15)、`test_media_audit.py`、`test_model_alias.py` + 各既有文件增量。SSO→Build 测试全部 mock `get_proxy_runtime`（沿用 `_no_real_mint_network` autouse，无真实网络）。

---

## 2026-08-05 移植批 13（PR #853：DPoP 协议 + 真实 /v1/usage 配额 + 24h 恢复探测）

上游：chenyme/grok2api **PR #853**（head `grok_console_260805` = f1d51254 + 377710f4，OPEN 未合并），同步 x.ai 服务端 DPoP 强制（issue #852 的适配成果，修复 2026-08-05 生产 console 403 `unauthorized:dpop-required` 故障）。

| 文件 | 状态 | 说明 |
|---|---|---|
| `console/dpop.go` (401 行) | ✅ 已移植 | → `app/dataplane/reverse/protocol/dpop.py`（455 行）：DPoPSessionManager（LRU 4096 + singleflight + 20s 偏斜）、`get_or_fetch`、`sign_dpop_proof`（ES256 jti/htm/htu/iat/ath）、`do_dpop_request`（2 次 401 重建 + x-cluster 条件）、`dpop_session_cache_key`/`dpop_htu`/`parse_dpop_access_token`；`DPoPError`/`DPoPTokenEndpointError`（带 `invalidate_clearance` 标志）；`tests/test_dpop.py` 36 测试 |
| `console/headers.go` | ✅ 已移植 | `build_console_headers` 增可选 `access_token`/`dpop_proof` 参数（两者齐 → `Authorization: DPoP` + `DPoP:` header，否则保持 Bearer anonymous）；增 `Cache-Control: no-cache` + `Pragma: no-cache`；`x-cluster` 本就无条件存在（Python console 恒走 /v1/responses，与 Go 条件化等价） |
| `console/quota.go` (167 行) | ✅ 已移植 | → `app/dataplane/reverse/protocol/xai_console_usage.py`（482 行）：`fetch_console_usage`（DPoP GET /v1/usage，30s 超时）、`parse_console_usage_payload`（chat/image/video 三窗口必齐 + 校验）、chat 24h 预测恢复窗口、image/video 仅展示；`tests/test_xai_console_usage.py` 15 测试 |
| `application/quotarecovery/service.go` | ✅ 已移植 | → `app/control/account/quota_recovery.py`（255 行）：`probe_console_quota`（成功→+24h 固定，失败→有界指数退避 1s-1min，429 不杀凭据）、`recover_due_console_quotas` leader 扫描（Ack 健康账号）；ClaimToken 内存版（单 leader fcntl.flock 下与 Go Redis 等价，`ponytail:` 注释标注多 worker 升级路径）；`console-quota-recovery` 60s leader 任务注册于 main.py |
| `runtime/memory/quota_queue.go` + `redis/store.go` | ✅ 已移植 | ClaimToken 语义：`schedule_quota_recovery` 已 claimed 时返回 False（并发刷新不得清除权威结果）、`cancel_quota_recovery` claimed 时 no-op、claim 在 finally 释放；内存 dict 实现（Redis Lua 仅多 worker 需要） |
| `application/account/service.go` (518 行重构) | ✅ 已移植（合并） | 关键语义并入既有架构：`_refresh_one` 增 `grok_console` → `_refresh_console_usage()` 分支（三窗口写入 + 本地模拟 fallback）；`models.py QuotaWindow` 增 `usage_percent`/`predicted` 字段；`quota_defaults.py` 增 `console_usage_windows()` 映射器；单 leader 恢复调度 = Go 刷新队列等价物；`_apply_fallback` 本地模拟保留为 fallback |
| frontend `account-quota.tsx` + i18n | ⏭️ 跳过 | Python Admin UI 独立，不移植前端 |

**DPoP 故障背景**（2026-08-05 生产）：console `/v1/responses` + `/v1/chat/completions` 均 403 `{"code":"unauthorized:dpop-required"}`；错误链 403 body 含 `unauthorized` → `credential_rejected=True` → `mark_account_reauth_required` 误标账号 REAUTH_REQUIRED（用户手动恢复 3 个）。上游两端（jiujiu532 L1 / chenyme L2）此前均零 DPoP 代码。本批移植 PR #853 为根治方案。

**移植目标（Python 侧）**：
- 新模块（待定）：`app/dataplane/reverse/protocol/dpop.py`（DPoP 会话管理 + proof 签名）——参照 `rate_limit.py` 风格
- `headers.py build_console_headers` 改造：DPoP Authorization + DPoP header + no-cache
- console 出站统一 DPoP 请求层（do_dpop_request 等价物）
- console 配额刷新：/v1/usage DPoP 接入（替代/增强本地 20/60min 模拟）
- 恢复调度：24h 预测探测 + ClaimToken 保护
- errors.py：DPoP 相关 403 不误标 credential_rejected（防御）

**测试基线**：全套 1478 passed（DPoP 移植前）。SSO→Build mint 测试全部 mock `get_proxy_runtime`。

---

## 2026-08-05 移植批 14（Go↔Python 差异审查修复：3 Critical + 6 Important + 架构决策项）

> 审查方法：3 个分层审查 Agent（dataplane/control/products）+ 2 个专项 Go↔Python 逐行对照 Agent（godiff-1/godiff-2），全部 Go 语义经 `git show` 双向验证。发现 3 Critical + 19 Important + 24 Minor 偏移，本批修复 Critical 3 + Important 10（两个完整清单见 `app/platform/errors.py` 注释与 `/tmp/opencode/godiff-*.md`）。

| 类别 | 偏移 | 提交 | 说明 |
|---|---|---|---|
| 🔴 C1 | G1-C1 model 免费额度 flags | `0fe9f89e` | 403/429/402 model 文案同步置 free_quota_exhausted + quota_exhausted=free or paid（Go failure.go:130-131/231-233/117）→ 403 model-text 从 FORBIDDEN 修正为 RATE_LIMITED |
| 🔴 C2 | G2-C1 流式 tool-argument 整数规范化 | `e799e4c6` | `StreamFunctionArgumentsBuffer` + BuildStreamAdapter `schemas` 参数：缓冲 delta → done 规范化 → 重发纠正 delta+done；超限 passthrough；native 事件禁用 sieve 防双发；非流式 `parse_tool_calls(schemas=)` 一行接线。Go 8b5c1ed6 rewriteStreamData |
| 🔴 C3 | G4-C1/I1/I2/M1 previous_response_id 链 | 随 `28e7f079`/`e799e4c6` 入库 | router 透传 → console/build 单尝试策略（new_routing_attempt_policy(1)）→ build/grok payload 转发；console 无状态重放 log WARNING；无 storage 层（架构缺口，仅 port 单尝试侧效应） |
| 🟡 I | G1-1..G1-4 节点健康机 | `28e7f079` | 0.7 单因子（替代本地 factor 表）；401/429/Build-403/Build-400/pool-节点 跳过；3xx→SUCCESS；_NODE_SUCCESS_STEP 0.10；499 guard；mark_failure_after_success 接入 console stream seam。Go 75f4f7a7 FeedbackForScope |
| 🟡 I | G6-I1/I2/M1/M2 console DPoP chat | `7a826f32` | is_definitive_block 谓词复用；browser_headers 每次交换解析（Callable）；缓存 key 带节点 crc32；x-cluster 仅 /responses。Go f1d51254 dpop.go |
| 🟡 I | G3-I1 routing 200 哨兵 | `5554c198` | 删 _SHIPPED_DEFAULT_ATTEMPTS=200 → key-presence 回退；config.defaults.toml 删 max_routing_attempts=200（保留 legacy 默认）。Go 15146556 |
| 🟡 I | G5-1/G5-2 恢复退避 + reset_at | `8c1f2eb0` | BACKOFF 1s/1min → 30s/30min（Go 默认）；reset_at=None 窗口跳过（Go IS NOT NULL）。Go 377710f4 |
| 🟡 I | G3-1/G3-2 build-detect 状态写入 | `d02c025d` | quota→24h 恢复窗口（predicted）；permanent→5min COOLING；detect 403 credential 无条件 REAUTH（网关门保留）。Go b4c7baab |
| 🟡 I | G1-I1/I2/M2 账号封锁 scope + 精确码匹配 | `2864c1f1` | "user is blocked"/"user blocked" → account_scoped（不放 credential）；should_invalidate 默认码表补 permission-denied + 精确 code 集合匹配替代子串 + 移除 account_scoped 前置门槛；token 信号集收窄为复合短语。Go d1205d85 failure.go |
| 🟡 I | G2-1/G2-2/G2-3 媒体真实 body + 归一化接线 + 图片 502 | `671fc76f` | 真实 payload 审计（generate/_generate_lite/create_video）；function_call_output/image 归一化接入 responses_input 请求路径（import 复用）；非凭据错误（429/403/5xx）最终尝试原样 raise 保留原 status，401 凭据类保留 503 wrap。Go c936ab1/80048405 |

**架构决策项（未实施，待评估）**：G4-1 等级重探测方向（Go 用 weekly-credit/auto-mode 成功作为升级到 Super 的正向信号，Python 仅 super/heavy 降级确认）；G4-4 事件驱动 vs 固定间隔；G5-3 Redis→内存 ClaimToken 已声明等价；G2-3 重试语义（Go 上游失败立即 502 不重试）；quota_build 无数据层列（random 策略下 build 路由排除部分）。

**后续修复（batch-14 补充，2026-08-05）**：
- **C1 竞态** ✅ `8c549c5f`：30s console-quota-reset × 60s console-quota-recovery 同字段——reset WHERE 排除 `source=REAL(1)` 的 predicted 窗口（recovery 独占）；DEFAULT/ESTIMATED/legacy 无 source 照常重置。三后端对齐（local.py SQLite / sql.py PG+MySQL / redis.py）。TDD 5 测试（tests/test_console_quota_reset.py）。
- **Build seam twin** ✅ `06aae1de`：build_chat.py 流读取失败块接线 `mark_failure_after_success`（TRANSPORT_ERROR/502 全新基线，Go 0893557a），与 console_chat seam 对齐；client cancel（CancelledError BaseException）不触发。TDD 2 测试。
- **should_invalidate_build_forbidden 记录修正**：经审计 app/ 零调用方（build_chat.py/build_detect.py 均未引用），为保留 API（供客户端/测试）；detect 403 reauth 实际由 `mark_build_chat_denied_as_reauth` 配置**直接**门控（见行 335 修正）。

**测试基线**：全套 **1646 passed / 1 skipped**（12 个修复 commit 后实跑，含 C1 竞态 5 测试 + Build seam 2 测试）。已修复：Critical 3 + Important 10 + C1 竞态 + Build seam twin。待评估架构决策项见上。

---

## 待评估（新批：PR #853 合并 + v3.1.1，725ecf08..fee63588）

> 同步于 2026-08-05 | chenyme 正式发布 **v3.1.1**（tag），PR #853（grok_console_260805）**已合并**进 main（此前批 13/14 基于未合并的 PR head f1d51254+377710f4 移植）。本批为合并后的增量提交（6 个非合并提交）。

### 🔴 高优先级

| # | 提交 | 范围 | 状态 | 描述 |
|---|------|------|------|------|
| 1 | `a05e06a2` | Console 媒体 | 🔄 审计中 | console provider 集成增强 + 媒体路由配额处理：`media.go`（727 行新文件）、`quota.go`+8、`adapter.go`（QuotaMode 增 image/video 分支 + ImageAssetStore）、`catalog.go`+57、`definition.go`（Console 增 CapabilityImage/ImageEdit/Video + MediaSurface）、`read_closer.go`（46 行新）；egress/inference/model HTTP handler 各 +16/+42。**Console 媒体能力是 Python 侧架构缺口，需评估** |

### 🟡 中优先级

| # | 提交 | 范围 | 状态 | 描述 |
|---|------|------|------|------|
| 2 | `cd7d3cd3` | 模型路由 | 🔄 审计中 | 加固分组路由查询与选择安全：`model/service.go`+4、`domain/model/model.go`+17、`relational/model_repository.go`+96（分组路由查询）。Python 模型为静态注册表无关系型 model repo，大概率 ⏭️ 跳过，待确认是否含行为修复 |
| 3 | `1b58823a` | 凭证导入 | 🔄 审计中 | 支持裸 JSON 数组导入凭证文件（PR #854）：`import_json.go`+30、console/web `import.go`、`account/service.go`+13。Python 导入侧需确认是否已支持 `["token1","token2"]` 形式 |
| 4 | `2a320e69` | 测试 | 🔄 审计中 | console provider adapter 初始化补参——仅测试文件（import_limit_test.go / web_console_sync_test.go 各 1 行），纯测试适配 |

### ⏭️ 跳过候选

| # | 提交 | 原因 |
|---|------|------|
| 5 | `75fa94f8` | fix(test): Windows 跳过 POSIX 权限断言 — 纯测试 |
| 6 | `95d6471a` | chore: bump v3.1.1 — 版本号 |
| 7 | `a4405eda` | refactor: 澄清 console/build adapter 导入限制测试 — 纯测试 |

**DPoP 生产故障（用户报告）**：`Console DPoP setup failed: Console DPoP token response invalid` 502 —— `dpop.py:353` 判定 token 端点 2xx 但 body 无有效 access_token/token_type（`_post_dpop_token` 空/非 JSON body → `{}`）。疑点：Python `_post_dpop_token` 用**新 acquire 的 lease** 发传输，但 cookie headers 来自 `_dpop_manager_lease`（旧 lease）→ egress 节点/clearance 可能不匹配 → CF 200 挑战页；Go 用同一 lease + `DoDeferredForbidden` + chromium client hints。根因审计 Agent 进行中（bg_cd058ed7）。

### 审计结论（2026-08-05，3 个并行审计 Agent，只读）

| # | 提交 | 最终判定 | 结论 |
|---|------|---------|------|
| 1 | `a05e06a2` | **部分移植** | 全量 = 新功能（console.x.ai 媒体：GenerateImage/EditImage/GenerateVideo/DownloadVideo + DPoP、可信主机白名单下载、ScopeConsoleAsset 免 clearance、quota mode console_image/video 参与路由恢复）~600-800 行；**模型名冲突**：Go 复用 grok-imagine-image 但 Python 已将该名路由到 grok.com，需新命名（如 `grok-imagine-image-console`）或路由表改造——设计决策，另批。**本批立即可移植切片**：(a) ASSET scope 403 豁免（`ProxyDirectory.feedback()` ~5 行：公网媒体主机 403 = 对象 URL 过期，非节点故障）；(b) 下载加固 ~40 行（SSRF 主机白名单 + 重定向校验，`_download_image_bytes`/`download_asset`） |
| 2 | `cd7d3cd3` | **⏭️ 跳过** | 纯关系型 DB ListGroups SQL 硬化（model_repository.go）+ Go models-page.tsx UI；Python 静态 MODELS 注册表（app/control/model/registry.py），无 model_routes 表、无 admin models 端点（`@router.*model` grep 0 命中），无行为 fix 可搬 |
| 3 | `1b58823a` | **部分移植（前端 ~10 行）** | `app/statics/admin/account.html` `doImportFile`：JSON 分支（L1950-1952）现拒绝数组 → 接受裸数组（string→token；object→.token ?? .sso_token），数组时显示 pool 选择器（现对 .json 隐藏，L2002-2004），默认 basic；TXT 分支（L1966）`raw.trimStart().startsWith('[')` → JSON.parse 展平（修 JSON 数组被静默当纯文本导入）。后端 tokens.py add_tokens/save_tokens 已兼容 string+object（L297），无需改 |
| 4 | `2a320e69` | **⏭️ 跳过** | 测试-only（2 测试文件各 1 行构造函数补参） |
| 5 | `75fa94f8` | ⏭️ 跳过 | 纯测试（Windows 权限断言跳过） |
| 6 | `95d6471a` | ⏭️ 跳过 | 版本号 bump v3.1.1 |
| 7 | `a4405eda` | ⏭️ 跳过 | 纯测试 refactor |

**DPoP 根因定案**：`_post_dpop_token`（xai_console_chat.py:422-459）自 acquire 新 lease 发传输，headers（Cookie: cf_clearance）来自全局 `_dpop_manager_lease`（并发 last-writer-wins）→ 传输节点 ≠ cookie 节点 → cf_clearance IP 绑定不匹配 → CF 200 挑战页（浏览器指纹请求）→ body 非 JSON → `{}` → DPoPError。Go 从未有此问题（f1d51254 起 `fetchDPoPSession(ctx, ssoToken, lease)` 同 lease：`lease.DoDeferredForbidden` + `applyBrowserHeaders`）。**修复 = 让 token 交换复用产生 headers 的那个 lease**（Fix A：`get_or_fetch → _fetch → _post_json_fn` 全程穿 lease；`_post_dpop_token(url, headers, json_body, lease)` 用传入 lease 建 session，删全局 `_dpop_manager_lease`；`xai_console_usage.py` 本已自洽不破坏）。**不会新增 CF 求解**：求解只发生在 `proxy.acquire()` 的 bundle 有效性检查（按 proxy_url+host 缓存，同节点全账号共享），token 交换只带已有 cookie；修复反而消除双 acquire 选到冷节点引发的多余求解。377710f4/a05e06a2 均无此病的修复——同 lease 不变量在原始 f1d51254 就有，Python e41c5d96 移植时丢掉了。

**DPoP 修复已实施（2026-08-05）**：`app/dataplane/reverse/protocol/dpop.py`（`PostJsonFn` 4 参 + `resolve_browser_headers(lease)` + `get_or_fetch/…/ _post_json_fn` 全程穿 lease + `do_dpop_request(lease=…)`）；`xai_console_chat.py`（`_post_dpop_token` 删 `proxy.acquire` 改用传入 lease；`_get_dpop_manager(token)` 删全局 `_dpop_manager_lease`，`browser_headers=lambda lease: build_console_headers(token, lease=lease)`；`stream_console_chat`/`_post_console_with_dpop` 调用传 lease）；`xai_console_usage.py`（`post_json` 4 参兼容）。回归测试 `tests/test_console_dpop.py` 新增 `test_token_exchange_reuses_chat_lease_no_second_acquire`（RED 证明 `assert 2 == 1`，GREEN 后 acquire 仅 1 次）；另修复 `tests/test_console_clearance_origin.py` fixture lambda 旧签名（`lambda token: fake`）。全量：**1654 passed / 1 skipped**（基线 1646 + 新增 8）。
