# Go→Python 移植台账

追踪 [chenyme/grok2api](https://github.com/chenyme/grok2api) (Go) 向上游移植到本仓库 (Python) 的进度。

## 规则

1. **每次移植前**查台账，确认提交未被处理过
2. **移植后**立即更新本条记录
3. 状态：`✅ 已移植` / `⏭️ 跳过`(注明原因) / `📋 待审阅` / `🔄 移植中`
4. 版本列记录移植所在的本仓库版本/提交

**去重要求**：每个提交 hash 在整个台账中只出现一次。已移植/已跳过的提交不应出现在"待评估"中。

---

## 台账

| 日期 | 提交 Hash | 提交描述 | 状态 | Python 版本/提交 | 备注 |
|------|-----------|----------|------|-------------------|------|
| 2026-07-15 | `c450dee` | feat(errors): Go→Python 结构化错误分类移植 | ✅ 已移植 | `c450dee` | Go `failure.go` → Python `errors.py` UpstreamError |
| 2026-07-15 | `0fe097e` | test: add coverage for structured UpstreamError classification | ✅ 已移植 | `0fe097e` | 425 tests ported from Go `failure_test.go` patterns |
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
| 22 | `d2eecc4` | 全链路 | 🟡 中 | 📋 待审阅 | gateway Multi-Provider 路由 + 运行时并发管理 — 61 files, ~3600 行, 对话模块拆分为独立文件 |
| 23 | `8004840` | 图片 | 🟡 中 | 📋 待审阅 | image generation 增强 + error handling 改进 |
| 10 | `5cee3d2` | Console | 🟡 中 | 📋 待审阅 | 新增 Grok Console provider（Python 版已有） |
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
| **总计（旧批唯一非合并提交）** | **43** |

---

## 待评估（新批：dd6624c..d2a8b4f，v3.0.9）

> 同步于 2026-07-25 | 207 个非合并提交，其中 35 个已在上方台账/待评估/跳过中，新增 172 个

> 注意：仅列 dd6624c 之后且未在上方出现的提交。重复提交（如 `ef16e55`/`106a7e7` 同一 fix 两次出现）仅列一次。

### 🐛 Bug 修复（建议移植）

| # | 提交 | 范围 | 优先级 | 状态 | 描述 |
|---|------|------|--------|------|------|
| 1 | `db28846` | Messages API | 🔴 高 | 📋 待审阅 | 修复 CodeQL 溢出：取消不可信容量运算，钳制 deferred-text 残差检查，web_search_call 去重前 cap — 安全修复 |
| 2 | `505c0b3` | 代理池 | 🔴 高 | ⏭️ 跳过 | Python PROXY_POOL 用游标轮转，无逐节点冷却机制，单节点故障不全局影响 |
| 3 | `68bd35e` | Anthropic | 🔴 高 | 📋 待审阅 | 修正 Anthropic cached input token 计费 — 影响 token 统计准确性 |
| 4 | `4cf6c50` | 403 处理 | 🟡 中 | ⏭️ 跳过 | Python 已通过 `_classify_upstream_status()` 检查 `blocked-user`/`user is blocked` → `credential_rejected` → EXPIRED；Go 的 `IsDefinitiveAccountBlockBody()` JSON 解析等价于 Python `_extract_error_metadata()` |
| 5 | `ba81e8d` | Gateway | 🟡 中 | ⏭️ 跳过 | Python 已在 `errors.py` 将 `blocked-user` 标为 `credential_rejected=True` → `FeedbackKind.UNAUTHORIZED` → EXPIRED；Go 的 reauthRequired 状态在 Python 中不存在（直接走 EXPIRED） |
| 6 | `232fe83` | 账号 | 🟡 中 | ⏭️ 跳过 | Python 已处理 `blocked-user`/`session not found`/`session-expired` → `credential_rejected`；session identity 概念（Go `sessionidentity/session.go`）在 Python 中不存在，Python 用 SSO cookie 直接访问 |
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
| 27 | `5700827` | 账号 | 🟡 中 | ⏭️ 跳过 | Go 的 `reauthRequired` 状态 + access token 有效期检查，Python 用 EXPIRED 状态直接失效，无 reauthRequired 概念 |
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
| 40 | `79f225a` | Web | 🟡 中 | 📋 待审阅 | 区分 hosted search tool usage |
| 41 | `4a18b61` | Web | 🟡 中 | 📋 待审阅 | 清理和限制 search metadata — 安全加固 |
| 42 | `5ad1636` | Messages | 🟡 中 | 📋 待审阅 | 限制 hosted search stream state — 防止状态膨胀 |
| 43 | `215ccb9` | Web | 🟡 中 | 📋 待审阅 | 完成 Claude hosted search mapping |
| 44 | `edd94df` | Messages | 🟡 中 | 📋 待审阅 | 加固 hosted web search 生命周期 |
| 45 | `00b5f90` | Messages | 🟡 中 | 📋 待审阅 | 加固 Claude web search mapping |
| 46 | `106a7e7` | Web | 🟡 中 | ⏭️ 跳过 | Python 无 nil pointer panic 问题（aiohttp 响应始终有效） |
| 47 | `c496550` | Video | 🟡 中 | ✅ 已移植 | 记录 video upstream failures |
| 48 | `ac6562b` | Image | 🟡 中 | ✅ 已移植 | 暴露 upload response diagnostics |
| 49 | `c1b6957` | Video/账号 | 🟡 中 | ⏭️ 跳过 | Python 无 media job 外键约束，视频任务存储在内存中 |
| 50 | `9bf43ad` | Media | 🟡 中 | ⏭️ 跳过 | Python 无 media job 数据库约束，视频任务存储在内存中 |
| 51 | `b710e95` | Auto-Clean | 🟡 中 | ⏭️ 跳过 | Go nil-safe sticky cleanup，Python 无 sticky store 概念 |
| 52 | `ccec13f` | Auto-Clean | 🟡 中 | ⏭️ 跳过 | 同上，Go BatchDelete 的 nil-safe sticky guard，Python 无 sticky store |
| 53 | `fb5932b` | Responses | 🟡 中 | ⏭️ 跳过 | Python 响应媒体审计通过结构化日志实现，无 Go response_media_audit.go 等价模块 |
| 54 | `f9b3eef` | Auto-Clean | 🟡 中 | ⏭️ 跳过 | Go reauth auto_clean 模块，Python 无 `reauthRequired` 状态和 auto_clean 概念（Python 用 EXPIRED + `cleanup.py` 物理删除） |
| 55 | `acb822e` | Auto-Clean | 🟡 中 | ⏭️ 跳过 | 同上，Go auto_clean 的 delete path 和 anchor 硬化，Python 无此模块 |
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
| 65 | `c936ab1` | Media | 🟡 中 | 📋 待审阅 | 增强 media audit logging 和 text/image 内容摘要 |
| 66 | `65c85f2` | Auto-Clean | 🟡 中 | ⏭️ 跳过 | Go 新模块 `auto_clean.go`，Python 无 `reauthRequired` 状态，Python 用 `cleanup.py` 物理删除已删除账号（非 reauth），架构不同 |
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
| 90 | `fb1babd` | Messages | 🟡 中 | 📋 待审阅 | 将 Build web_search_call 映射到 Anthropic server tool blocks |
| 91 | `69869c7` | Image | 🟡 中 | ✅ 已移植 | 增强 remote image handling with validation 和 fetching logic |
| 92 | `5190c7b` | 账号 | 🟡 中 | ⏭️ 跳过 | Go billing profile inference（`IsPaid()`/`HasFreeProfileSignal()`），Python 无 billing 模型，用 pool 推断 |
| 93 | `57e7e4b` | 系统 | 🟢 低 | ✅ 已存在 | Python 已有 `platform/update_check.py`: GitHub Release API 轮询 + 版本对比 + 缓存; 无需额外移植 |
| 94 | `1967db1` | 账号 | 🟢 低 | ⏭️ 跳过 | Go Web provider 的 agreement/association filters，Python 无 Web provider 和过滤器概念 |

### 🔧 重构（参考）

| # | 提交 | 范围 | 优先级 | 状态 | 描述 |
|---|------|------|--------|------|------|
| 95 | `1a7f0ac` | 路由 | 🟢 低 | 📋 待审阅 | 更新 ListRoutingCandidates 含 modelRouteID 参数 |
| 96 | `9c6d78c` | 账号 | 🟢 低 | 📋 待审阅 | 精简 Grok Web agreement 和 association filters |
| 97 | `67133a9` | Cache | 🟢 低 | ✅ 已合并 | 合并到 prompt_cache.py v3 移植中 — 清理冗余兼容代码,简化函数签名 |
| 98 | `4c0e593` | Auto-Clean | 🟢 低 | ⏭️ 跳过 | Go auto_clean 模块逻辑更新，Python 无 reauthRequired 和 auto_clean 概念 |
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
| 已移植 | 14 | 30 | 44 |
| 已存在 | 0 | 1 | 1 |
| 待审阅（高优先级） | 0 | 0 | 0 |
| 待审阅（中优先级） | 7 | 21 | 28 |
| 待审阅（低优先级） | 11 | 4 | 15 |
| 跳过 | 11 | 136 | 147 |
| **总计（唯一非合并提交）** | **43** | **192** | **235** |

> 上游 `upstream/source` 总非合并提交：357（含已到账的旧批43个 + 新批192个 + 更早期未追踪提交）

> 2026-07-25 移植批 1：19 个 proxy/egress/gateway 相关提交全部跳过。原因：Python 代理架构与 Go 完全不同。
> 2026-07-26 移植批 2：23 个 Build/Reasoning 相关提交。其中 5 个已移植（reasoning effort 规范化、replay key、Build 协议版本对齐），18 个跳过（Go 特有 reasoning replay/compaction/entitlement 系统，Python 架构不同）。
> 2026-07-25 移植批 3：11 个 Anthropic Messages/Web Search 相关提交。移植 9 个（token accounting, search bounds, search metadata sanitization, search block emission, search usage tracking）。跳过 2 个（`fb5932b` Responses API multimodal tool outputs, `fb1babd` Build API web_search_call parsing — Python proxy uses Grok Web API, 不同数据源）。
> 2026-07-25 移植批 4（media/image/video）：15 个媒体相关提交。移植 8 个（video failure logging, upload diagnostics, video input size limit, remote image validation, image editing API enhancements, partial image streaming, video error handling）。跳过 7 个（media job DB constraints, terminal video job deletion, media audit logging, egress tracking — Python 无 media job 持久化层）。
> 2026-07-26 移植批 5（account/auth/SSO/quota/auto-clean）：31 个账号/认证/SSO/配额/自动清理相关提交全部跳过。核心原因：Go 重写引入了 Python 不存在的架构层——多 provider（Web/Console/Build）、OAuth refresh tokens、`reauthRequired` 状态、billing 模型、settings service、sticky sessions、auto_clean 模块。Python 用 SSO token + EXPIRED 状态 + config.toml + `cleanup.py` 等不同机制处理等价场景，无需移植。
> 2026-07-26 移植批 6（prompt cache/console/codex/import/403）：22 个提交。移植 9 个（prompt cache v3 提取+软会话+usage 合并, Client Hints Arch/Bitness, Codex 模型目录, 403 封禁码配置, BOM 去除）。跳过 13 个（FlareSolverr egress 大改、客户端密钥 DB 迁移、路由唯一性、模型配额管理、Build+Web 多 provider 同步、Console Multi-Agent、视频流代理、分页标准化）。已存在 1 个（version check）。

---

## 待评估（新批：d2a8b4f7..8f979d45，v3.0.10 → v3.0.11）

> 同步于 2026-08-04 | 54 个非合并提交，均未在上方出现

### 🔴 高优先级 Bug 修复（建议移植）

| # | 提交 | 范围 | 状态 | 描述 |
|---|------|------|------|------|
| 1 | `8b5c1ed6` | 工具调用 | 📋 待审阅 | 安全规范化流式整数工具参数 — Python 流式 tool call 处理相关，防 float/int 解析异常 |
| 2 | `e3af4fce` | Responses | 📋 待审阅 | 同 `8b5c1ed6`，Responses API 路径的整数工具参数规范化 |
| 3 | `d1205d85` | 错误分类 | 📋 待审阅 | 增强 HTTP 上游失败分类 — 对应 Python `_classify_upstream_status()`，可能需新增分类规则 |
| 4 | `d00698ac` | Gateway | 📋 待审阅 | 分类 Build safety 和 quota 失败 — Python 已有 `model_quota_exhausted`/`free_quota_exhausted` 分类，核对新增规则 |

### 🟡 中优先级（评估后移植）

| # | 提交 | 范围 | 状态 | 描述 |
|---|------|------|------|------|
| 5 | `b4c7baab` | Build 账号 | 📋 待审阅 | 增强 Build 账号检测的错误分类 — Python 二级探测 `_refresh_one()` 已有等价逻辑，核对 |
| 6 | `bcc6435f` | Build 账号 | 📋 待审阅 | 改进 Build 账号检测和路由 failover |
| 7 | `ef10c4cb` | Build 凭证 | 📋 待审阅 | 允许手动重试无效 Build 凭证 — Python SSO→Build 导入路径相关 |
| 8 | `34811392` | Build 配额 | 📋 待审阅 | 更新 Build free quota 估算 — Python console 配额窗口 (BASIC_CONSOLE_LIMIT) 相关 |
| 9 | `75f4f7a7` | Build 代理 | 📋 待审阅 | 每请求轮换 Build proxy-pool 隧道 — Python `proxy_pool` 模式相关 |
| 10 | `0893557a` | 代理健康 | 📋 待审阅 | MarkFailureAfterSuccess + 健康更新逻辑 — Python 代理反馈状态机相关 |
| 11 | `f1867395` | 代理节点 | 📋 待审阅 | 取消请求不冷却代理节点 — Python mihomo 黑名单机制相关 |
| 12 | `1edc9fbe` | 模型别名 | 📋 待审阅 | 模型别名支持 reasoning effort — Python 模型注册表相关 |
| 13 | `15146556` | 路由 | 📋 待审阅 | 无限路由尝试功能 — Python 路由重试逻辑相关 |
| 14 | `72340380` | 路由 | 📋 待审阅 | 移除 maxAttempts 硬上限 10 — 与 `15146556` 配套 |
| 15 | `2aaac4d0` | Clearance | 📋 待审阅 | 允许无 challenge cookies 的 clearance — Python CF clearance 生命周期相关 |

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
| 已移植 | 44 | 0 | 44 |
| 已存在 | 1 | 0 | 1 |
| 待审阅（高优先级） | 0 | 4 | 4 |
| 待审阅（中优先级） | 28 | 11 | 39 |
| 待审阅（低优先级） | 15 | 2 | 17 |
| 跳过 | 147 | 37 | 184 |
| **总计（唯一非合并提交）** | **235** | **54** | **289** |

> 上游 `upstream/source` 总非合并提交：411（含已追踪 289 个 + 更早期未追踪提交）

> 2026-08-04 移植批 7（v3.0.10→v3.0.11）：54 个提交。待审阅 17 个（4 高：整数工具参数规范化 ×2、HTTP 失败分类增强 ×2；11 中：Build 账号检测/配额/代理轮换、代理健康、路由尝试上限、clearance 无 cookie；2 低：媒体文件名清理、批量并发）。跳过 37 个（egress 管理/探测 11、Go UI 4、linked-account 4、client keys 2、Go 凭证/OAuth 5、billing 1、媒体 4、Go 测试 3、版本号 2）。

> 2026-08-04 本地修复批 8（配置键不匹配，非移植）：审计全库 get_config() 键读取 vs config.defaults.toml schema，修复 4 处：
> - **M1 根因**（SSO→Build mint 403）：`sso_build.py` PKCE-CS/Device Flow 直接读 `proxy.clearance.cf_clearance`/`proxy.cf_clearance`（schema 无此键）→ 恒空 → 不播种 cf_clearance → accounts.x.ai 预校验 403。修复：新增 `_resolve_cf_clearance_value()` 复用 `resolve_clearance_config().cf_clearance`。
> - **M2**：`errors.py` `should_invalidate_build_forbidden` 读 `chat.build_403_invalidation_codes` → 改 `features.build_403_invalidation_codes`。
> - **M3**：`config.py` `resolve_clearance_config` 的 `cf_clearance` 字段从 `cf_cookies` 派生（`extract_cookie_value`），legacy 平键兜底。
> - **M6**：`config.defaults.toml` 的 `image_format`/`imagine_public_image_proxy`/`video_format` 从 `[build]` 移回 `[features]`（与 jiujiu532 上游一致）。
> - M4/M5（`storage.data_dir`、`browser.custom_fingerprint.enabled`）审计后 KEEP。
> 测试：全套 1179 passed。TDD：每处修复先 RED 后 GREEN（tests/test_clearance_config.py、test_build_errors.py、test_config.py、test_sso_build.py 新增 16 测试）。
