# Go→Python 移植台账

追踪 [chenyme/grok2api](https://github.com/chenyme/grok2api) (Go) 向上游移植到本仓库 (Python) 的进度。

## 规则

1. **每次移植前**查台账，确认提交未被处理过
2. **移植后**立即更新本条记录
3. 状态：`✅ 已移植` / `⏭️ 跳过`(注明原因) / `📋 待审阅` / `🔄 移植中`
4. 版本列记录移植所在的本仓库版本/提交

---

## 台账

| 日期 | 提交 Hash | 提交描述 | 状态 | Python 版本/提交 | 备注 |
|------|-----------|----------|------|-------------------|------|
| 2026-07-15 | `c450dee` | feat(errors): Go→Python 结构化错误分类移植 | ✅ 已移植 | `c450dee` | Go `failure.go` → Python `errors.py` UpstreamError |
| 2026-07-15 | `0fe097e` | test: add coverage for structured UpstreamError classification | ✅ 已移植 | `0fe097e` | 425 tests ported from Go `failure_test.go` patterns |
| 2026-07-15 | `e376c22` | fix: response_format type-skip — 防止 schema 自身 type 覆盖 json_schema | ✅ 已移植 | current | normalize_response_format() in chat.py |
| 2026-07-15 | `982e27b` | fix: messages 内联 system role 兼容 + error type 透传 | ✅ 已移植 | current | _parse_anthropic_messages + router.py error handler |
| 2026-07-15 | `ca97848` | fix: upstream error 后流标记终止 | ✅ 已移植 | current | StreamAdapter._finished in xai_chat.py |
| 2026-07-15 | `afb169e` | fix: error 后忽略后续流事件 | ✅ 已移植 | current | StreamAdapter._handle_event skip when _finished |
| 2026-07-15 | `178bfd4` | fix: message_delta 补齐 input_tokens | ✅ 已移植 | current | messages.py + console_messages.py usage |
| 2026-07-15 | `56fa0a9` | fix: 账号提权修复 | ⏭️ 跳过 | — | Python 无周额度机制，不存在此问题 |
| 2026-07-15 | `3b8feb2` | fix: Console egress scope | ⏭️ 跳过 | — | Python 代理反馈不区分 Scope，clearance_host 已正确 |
| 2026-07-15 | `2b797b5` | fix: 临时文件清理不删已提交图片 | ⏭️ 跳过 | — | Python 使用 os.replace (rename) 而非 hard link |
| 2026-07-15 | `e376c22` | fix: prevent json_schema's own type field from overwriting response_format type | ✅ 已移植 | (this commit) | Ported `normalize_response_format()` to `chat.py` with type-skip fix; added `response_format` to `ChatCompletionRequest` schema |
| 2026-07-15 | `982e27b` | fix: Messages API 兼容 messages 内联 system role (Claude Code) | ✅ 已移植 | (this commit) | Ported inline system extraction to `_parse_anthropic_messages()` with `_extract_system_text()` helper; error type passthrough in `router.py` |

---

## 待评估（当前批 78 个 Go 新提交）

### 🐛 Bug 修复（建议移植）

| # | 提交 | 范围 | 优先级 | 状态 | 描述 |
|---|------|------|--------|------|------|
| 1 | `56fa0a9` | 账号 | 🔴 高 | ⏭️ 跳过 | Basic/Auto 账号在限速端点不可用时被提升为 Super |
| 2 | `e376c22` | 协议转换 | 🔴 高 | ✅ 已移植 | json_schema 自身 `type` 字段覆盖 response_format 的 `"json_schema"` |
| 3 | `982e27b` | 兼容性 | 🟡 中 | ✅ 已移植 | Messages API 兼容 messages 内联 system role (Claude Code) |
| 4 | `ca97848` | 流处理 | 🟡 中 | ✅ 已移植 | upstream error 后流标记终止 — StreamAdapter._finished |
| 5 | `178bfd4` | 流处理 | 🟡 中 | ✅ 已移植 | message_delta usage 添加 input_tokens |
| 6 | `2b797b5` | 媒体存储 | 🟡 中 | ⏭️ 跳过 | 临时文件清理失败误删已提交图片 |
| 7 | `0363483` | 媒体 URL | 🟡 中 | 📋 待审阅 | 从请求派生 public URL 而非硬编码 127.0.0.1 |
| 8 | `c929ad0` | 响应头 | 🟡 中 | 📋 待审阅 | fhttp 响应头突变：Header.Clone() 防止别名修改 |
| 9 | `792410b` | 响应头 | 🟡 中 | 📋 待审阅 | 确保 fhttp 响应头不被下游突变（Trailer 克隆） |
| 10 | `afb169e` | 流处理 | 🟡 中 | ✅ 已移植 | _handle_event skip when _finished |
| 11 | `3b8feb2` | Console | 🟡 中 | ⏭️ 跳过 | Console egress 反馈使用 ScopeConsole 而非 ScopeWeb |
| 12 | `dce8627` | 设置 | 🟢 低 | 📋 待审阅 | 设置保存 revision=0 报 400 |
| 13 | `7c03e43` | 前端 | 🟢 低 | 📋 待审阅 | 非 HTTPS 复制按钮失效 |
| 14 | `7a26e9d` | 前端 | 🟢 低 | 📋 待审阅 | LAN 部署复制失效 |
| 15 | `1daa6d0` | 账号同步 | 🟡 中 | 📋 待审阅 | Web SSO 账号不同步到 Console |
| 16 | `dd6624c` | 工具调用 | 🟢 低 | 📋 待审阅 | Web 搜索工具兼容性警告 |
| 17 | `9244306` | 全链路 | 🔴 高 | 📋 待审阅 | 分离 Grok Build 与 Web 代理传输（架构级改动） |
| 18 | `ec6e351` | 全链路 | 🟡 中 | 📋 待审阅 | 响应处理兼容性增强（30 文件大改动） |
| 19 | `f30195d` | 审计 | 🟡 中 | 📋 待审阅 | 恢复 Console egress 审计路由 |
| 20 | `4f34707` | 账号 | 🔴 高 | 📋 待审阅 | Web SSO 等级检测、配额重新探测、模型同步修复 |
| 21 | `b5aad0e` | 媒体 | 🟡 中 | 📋 待审阅 | 图片生成可靠性改进和资源泄漏修复 |
| 22 | `99e4e78` | Console | 🟡 中 | 📋 待审阅 | Grok Console 无状态响应支持 |

### ✨ 新功能（评估后移植）

| # | 提交 | 范围 | 优先级 | 状态 | 描述 |
|---|------|------|--------|------|------|
| 23 | `dc9b157` | Build API | 🟡 中 | 📋 待审阅 | Build API 提示缓存（prompt_cache_key 注入+缓存） |
| 24 | `3c30472` | Build API | 🟡 中 | 📋 待审阅 | 注入 prompt_cache_key 到 Build API 请求体 |
| 25 | `9b661ea` | 设置 | 🟢 低 | 📋 待审阅 | 公共 API Base URL 可配置 |
| 26 | `3d5e7de` | 审计 | 🟢 低 | 📋 待审阅 | 完整故障诊断审计 |
| 27 | `5cee3d2` | Console | 🟡 中 | 📋 待审阅 | 新增 Grok Console provider（Python 版已有） |
| 28 | `d626a26` | 配额 | 🟡 中 | 📋 待审阅 | 模型配额管理和路由候选选择增强 |
| 29 | `90c3320` | 同步 | 🟢 低 | 📋 待审阅 | Build+Web 双 provider 账号同步 |
| 30 | `75d3896` | Console | 🟡 中 | 📋 待审阅 | Console Multi-Agent 与媒体管理链路迁移 |
| 31 | `f9e2f91` | Build | 🟢 低 | 📋 待审阅 | Grok Build v0.2.99 升级 |

### 🔧 重构（参考但不紧急）

| # | 提交 | 范围 | 优先级 | 状态 | 描述 |
|---|------|------|--------|------|------|
| 32 | `845bff8` | 媒体 | 🟢 低 | 📋 待审阅 | 媒体任务审计和错误处理增强 |
| 33 | `d439bd7` | 媒体 | 🟢 低 | 📋 待审阅 | 媒体任务和资产管理优化 |
| 34 | `cce2213` | 前端 | 🟢 低 | 📋 待审阅 | 客户端密钥对话框和复制按钮优化 |
| 35 | `01975a6` | Console | 🟢 低 | 📋 待审阅 | 对齐上游 Console 实现并保留媒体管理 |
| 36 | `1b5cddc` | 路由 | 🟢 低 | 📋 待审阅 | 模型路由和 provider 处理增强 |
| 37 | `f524576` | 启动 | 🟢 低 | 📋 待审阅 | 凭证刷新逻辑和启动处理增强 |

### ⏭️ 跳过（Go 特有/CI/文档）

| # | 提交 | 原因 |
|---|------|------|
| — | `a16837c` | Go 重写自身（已到账） |
| — | `2608161` | 超时调整 30→60s |
| — | `b7f9f83`, `ef87d9e`, `19ec781`, `dcaeb3f`, `ec6cddc` | CI/仓库整理/README |
| — | `56fa0a9` | 无周额度机制，账号提升仅基于 rate-limits API 实时数据，不存在周额度误提权问题 |
| — | `3b8feb2` | Python 代理反馈不区分 Scope，clearance 已通过 lease.clearance_host 正确区分域名 |
| — | `2b797b5` | Python 使用 `os.replace` (rename) 而非 hard link，临时文件在 rename 后已不存在 |

---

## 统计

| 类别 | 数量 |
|------|------|
| 已移植 | 7 |
| 待审阅（高优先级） | 2 |
| 待审阅（中优先级） | 9 |
| 待审阅（低优先级） | 20 |
| 跳过 | 8 |
| **总计 (78 提交)** | **45 (非合并) + 33 (合并)** |
