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

---

## 待评估（当前批 25 个唯一非合并 Go 新提交）

> 注意：仅列尚未评估的提交。已处理（已移植/已跳过）的提交见上方台账。

### 🐛 Bug 修复（建议移植）

| # | 提交 | 范围 | 优先级 | 状态 | 描述 |
|---|------|------|--------|------|------|
| 1 | `c929ad0` | 响应头 | 🟡 中 | 📋 待审阅 | fhttp 响应头突变：Header.Clone() 防止别名修改 |
| 2 | `792410b` | 响应头 | 🟡 中 | 📋 待审阅 | 确保 fhttp 响应头不被下游突变（Trailer 克隆） |
| 3 | `7c03e43` | 前端 | 🟢 低 | 📋 待审阅 | 非 HTTPS 复制按钮失效 |
| 4 | `7a26e9d` | 前端 | 🟢 低 | 📋 待审阅 | LAN 部署复制失效 |
| 5 | `1daa6d0` | 账号同步 | 🟡 中 | 📋 待审阅 | Web SSO 账号不同步到 Console |
| 6 | `dd6624c` | 工具调用 | 🟢 低 | 📋 待审阅 | Web 搜索工具兼容性警告 |
| 7 | `9244306` | 全链路 | 🔴 高 | 📋 待审阅 | 分离 Grok Build 与 Web 代理传输（架构级改动） |
| 8 | `ec6e351` | 全链路 | 🟡 中 | 📋 待审阅 | 响应处理兼容性增强（30 文件大改动） |

### ✨ 新功能（评估后移植）

| # | 提交 | 范围 | 优先级 | 状态 | 描述 |
|---|------|------|--------|------|------|
| 9 | `3d5e7de` | 审计 | 🟢 低 | 📋 待审阅 | 完整故障诊断审计 |
| 10 | `5cee3d2` | Console | 🟡 中 | 📋 待审阅 | 新增 Grok Console provider（Python 版已有） |
| 11 | `d626a26` | 配额 | 🟡 中 | 📋 待审阅 | 模型配额管理和路由候选选择增强 |
| 12 | `90c3320` | 同步 | 🟢 低 | 📋 待审阅 | Build+Web 双 provider 账号同步 |
| 13 | `75d3896` | Console | 🟡 中 | 📋 待审阅 | Console Multi-Agent 与媒体管理链路迁移 |
| 14 | `f9e2f91` | Build | 🟢 低 | 📋 待审阅 | Grok Build v0.2.99 升级 |

### 🔧 重构（参考但不紧急）

| # | 提交 | 范围 | 优先级 | 状态 | 描述 |
|---|------|------|--------|------|------|
| 15 | `845bff8` | 媒体 | 🟢 低 | 📋 待审阅 | 媒体任务审计和错误处理增强 |
| 16 | `d439bd7` | 媒体 | 🟢 低 | 📋 待审阅 | 媒体任务和资产管理优化 |
| 17 | `cce2213` | 前端 | 🟢 低 | 📋 待审阅 | 客户端密钥对话框和复制按钮优化 |
| 18 | `01975a6` | Console | 🟢 低 | 📋 待审阅 | 对齐上游 Console 实现并保留媒体管理 |
| 19 | `1b5cddc` | 路由 | 🟢 低 | 📋 待审阅 | 模型路由和 provider 处理增强 |
| 20 | `f524576` | 启动 | 🟢 低 | 📋 待审阅 | 凭证刷新逻辑和启动处理增强 |

### ⏭️ 跳过（Go 特有/CI/文档）

| # | 提交 | 原因 |
|---|------|------|
| — | `a16837c` | Go 重写自身（已到账，Python 版无此概念） |
| — | `2608161` | 超时调整 30→60s（Python 版超时配置独立） |
| — | `b7f9f83`, `ef87d9e`, `19ec781`, `dcaeb3f`, `ec6cddc` | CI/仓库整理/README（不涉及 Python 逻辑） |

---

## 统计

| 类别 | 数量 |
|------|------|
| 已移植 | 12 |
| 待审阅（高优先级） | 1 |
| 待审阅（中优先级） | 7 |
| 待审阅（低优先级） | 12 |
| 跳过 | 14 |
| **总计（唯一非合并提交）** | **46** |
