# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Language Setting
- Always respond in Chinese-simplified

# Project Overview
Grok2API 是一个基于 FastAPI 的 Grok API 转换服务，将 Grok 的 Web API 转换为 OpenAI 兼容格式。支持流式对话、图像生成/编辑、视频生成、联网搜索、深度思考等功能。

## 核心技术栈
- **Web框架**: FastAPI + Uvicorn
- **HTTP客户端**: curl_cffi (模拟浏览器指纹)
- **存储层**: 支持文件/MySQL/Redis三种模式
- **MCP集成**: FastMCP (Model Context Protocol)
- **浏览器自动化**: Patchright + Camoufox (用于Cloudflare验证)

# Development Commands

## 运行服务
```bash
# 直接运行
python main.py

# 使用 uvicorn (推荐用于开发)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 使用 uv 运行
uv run python main.py
```

## 测试
```bash
# 运行所有测试
pytest

# 运行特定测试文件
pytest tests/test_api_endpoints.py

# 运行特定测试函数
pytest tests/test_api_endpoints.py::test_chat_completions

# 显示详细输出
pytest -v -s
```

## Docker 部署
```bash
# 构建镜像
docker build -t grok2api .

# 使用 docker-compose
docker-compose up -d

# 查看日志
docker-compose logs -f
```

## 依赖管理
```bash
# 使用 uv 安装依赖
uv sync

# 添加新依赖
uv add <package-name>

# 更新依赖
uv lock --upgrade
```

# Architecture

## 目录结构
```
app/
├── api/              # API路由层
│   ├── v1/          # v1版本API (chat, models, images)
│   └── admin/       # 管理后台API
├── core/            # 核心模块
│   ├── config.py    # 配置管理 (支持热重载)
│   ├── storage.py   # 存储抽象层 (file/mysql/redis)
│   ├── auth.py      # 认证中间件
│   ├── logger.py    # 日志系统
│   └── exception.py # 异常处理
├── models/          # 数据模型
│   ├── openai_schema.py  # OpenAI格式定义
│   └── grok_models.py    # Grok模型映射
├── services/        # 业务逻辑层
│   ├── grok/       # Grok服务核心
│   │   ├── client.py      # API客户端 (请求转换与重试)
│   │   ├── processer.py   # 响应处理器 (流式/非流式)
│   │   ├── token.py       # Token管理器 (负载均衡)
│   │   ├── cache.py       # 图片/视频缓存
│   │   ├── upload.py      # 图片上传
│   │   ├── create.py      # 会话创建
│   │   ├── cf_clearance.py # Cloudflare验证
│   │   ├── statsig.py     # 浏览器指纹生成
│   │   └── browser_config.py # 浏览器配置
│   ├── mcp/        # MCP服务器
│   │   ├── server.py      # FastMCP实例
│   │   └── tools.py       # MCP工具实现
│   └── turnstile/  # Cloudflare Turnstile求解器
└── template/       # 前端模板 (管理后台)
```

## 核心架构设计

### 1. 存储抽象层 (app/core/storage.py)
- **BaseStorage**: 抽象基类定义统一接口
- **FileStorage**: 本地文件存储 (默认)
- **MySQLStorage**: MySQL数据库存储
- **RedisStorage**: Redis缓存存储
- **StorageManager**: 统一管理器，根据环境变量选择存储模式

### 2. Token管理与负载均衡 (app/services/grok/token.py)
- **TokenManager**: 管理 ssoNormal 和 ssoSuper 两类token
- **负载均衡策略**: 基于使用次数和配额的智能选择
- **自动故障转移**: 检测403错误并自动切换token
- **标签过滤**: 支持按标签筛选可用token

### 3. Cloudflare验证机制 (app/services/grok/cf_clearance.py)
- **自动检测**: 识别403响应并触发验证流程
- **浏览器自动化**: 使用Patchright + Camoufox绕过检测
- **队列机制**: 避免并发验证冲突
- **cf_clearance缓存**: 验证成功后自动更新配置

### 4. 请求处理流程 (app/services/grok/client.py)
```
OpenAI请求 → 格式转换 → Token选择 → 图片上传 → 会话创建 →
Grok API调用 → 响应处理 → OpenAI格式输出
```

### 5. MCP集成 (app/services/mcp/)
- **ask_grok工具**: 暴露Grok对话能力给MCP客户端
- **认证**: 基于api_key的JWT验证 (可选)
- **挂载方式**: 通过 `app.mount("", mcp_app)` 集成到主应用

## 关键设计模式

### 重试机制
- 最大重试3次 (MAX_RETRY = 3)
- 403错误触发Cloudflare验证后重试
- Token失效自动切换并重试

### 流式响应处理
- **GrokResponseProcessor**: 统一处理流式/非流式响应
- **标签过滤**: 移除 xaiartifact, xai:tool_usage_card 等内部标签
- **思考过程控制**: 可配置是否显示深度思考内容

### 图片/视频缓存
- **本地缓存**: 绕过Grok的403限制
- **容量管理**: 自动清理超出限制的缓存
- **URL重写**: 将Grok URL替换为本地缓存URL

# Eight Honors and Eight Disgraces
- Be ashamed of guessing interfaces blindly; be proud of checking carefully.
- Be ashamed of vague execution; be proud of seeking confirmation.
- Be ashamed of conjecturing business scenarios; be proud of obtaining human confirmation.
- Be ashamed of creating new interfaces; be proud of reusing existing ones.
- Be ashamed of passing on the first try; be proud of reviewing and verifying afterwards.
- Be ashamed of destroying the architecture; be proud of following specifications.
- Be ashamed of pretending to understand; be proud of being honestly ignorant.
- Be ashamed of modifying blindly; be proud of refactoring carefully.

# Occam's Razor
**Core Idea**: "Entities should not be multiplied beyond necessity"

When solving problems, designing schemes, and writing code, the following principles should be followed:
- Among multiple solutions that can explain the same phenomenon, choose the one with the fewest assumptions and the simplest explanation.
- Avoid introducing unnecessary complexity, dependencies, and abstractions.
- Prioritize simple and straightforward solutions over over-designed complex architectures.
- Do not pre-design complex scalability for needs that may not arise in the future.
- Whenever adding a component, dependency, or abstraction layer, ask "Is this really necessary?"

**Application Scenarios**:
- Technology selection: Prioritize simple and mature technology stacks over complex new technologies.
- Architecture design: Start with the simplest feasible architecture and evolve it according to actual needs.
- Code implementation: Write the most intuitive code first, and refactor and optimize only when necessary.
- Problem diagnosis: Prioritize the simplest and most common causes over rare edge cases.
- Dependency management: Do not introduce third-party libraries if standard libraries can solve the problem.

# Codex Calling Rules
When needing to use the Codex tool to perform tasks, please follow the following rules to ensure efficient execution:

## Basic Configuration Principles
- **Approval Policy**: Use `approval-policy: "on-failure"` to reduce unnecessary waiting for interactions
- **Sandbox Mode**: Select the appropriate sandbox mode according to task requirements
  - Read-only analysis: `sandbox: "read-only"`
  - Needing to modify files: `sandbox: "workspace-write"`
  - Full access: `sandbox: "danger-full-access"` (use with caution)
- **Avoid Overly Conservative Settings**: Do not use `approval-policy: "untrusted"` as it will lead to excessive confirmation steps

## Prompt Writing Specifications
1. **Clarity and Specificity**: Clearly state the task objectives, current status, and expected results
2. **Provide Context**: Include key information such as project type, file paths, and current version numbers
3. **Define Scope**: Clearly specify the specific files and operation types that need to be processed
4. **Avoid Vagueness**: Do not use vague expressions such as "analyze the entire project" or "optimize all code"

## Task Splitting Principles
- For complex tasks, split them into multiple independent simple tasks
- Each Codex call should focus on only one clear task
- Avoid requiring Codex to handle multiple different types of operations in a single call

## Verification and Confirmation
- After Codex execution, use appropriate tools to verify the execution results
- If Codex does not complete the expected task, check if the prompt is clear enough
- If necessary, you can re-call Codex with a more precise prompt
