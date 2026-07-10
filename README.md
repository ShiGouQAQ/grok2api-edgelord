<div align="center">

<img alt="Grok2API" src="https://github.com/user-attachments/assets/037a0a6e-7986-41cc-b4af-04df612ee886" />

<h1>Grok Web 能力的 OpenAI 兼容网关</h1>
<h3>edgelord — 巨魔版 · 个人自用 · AI Vibe 编程</h3>

<p>
将 grok.com 与 console.x.ai 的聊天、图像、视频能力，<br>
以 <strong>OpenAI / Anthropic 兼容 API</strong> 统一对外提供。
</p>

<p>
<a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white"></a>
<a href="https://fastapi.tiangolo.com/"><img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.119%2B-009688?logo=fastapi&logoColor=white"></a>
<a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-16a34a"></a>
<a href="https://github.com/ShiGouQAQ/grok2api-edgelord"><img alt="GitHub" src="https://img.shields.io/badge/edgelord-111827?logo=github&logoColor=white"></a>
<a href="#"><img alt="Personal Use" src="https://img.shields.io/badge/for_personal_use_only-FF4500"></a>
<a href="#"><img alt="AI Vibe" src="https://img.shields.io/badge/AI_Vibe_Coding-8A2BE2"></a>
</p>

<p>
<a href="#分支架构">分支架构</a> ·
<a href="#核心特性">核心特性</a> ·
<a href="#本分支增强">本分支增强</a> ·
<a href="#部署指南">部署指南</a> ·
<a href="#上游同步">上游同步</a> ·
<a href="#模型列表">模型列表</a> ·
<a href="#账号配置">账号配置</a> ·
<a href="#api-端点">API 端点</a> ·
<a href="#常见问题">常见问题</a>
</p>

</div>

> [!CAUTION]
> **仅供个人使用。** 本项目为 AI Vibe 编程产物，大部分代码由大模型生成，作者对代码质量、安全性、稳定性不作任何保证。请自行评估风险。
>
> 使用 grok.com 和 console.x.ai 的能力时请遵守其服务条款及当地法律法规。

本仓库是 [jiujiu532/grok2api](https://github.com/jiujiu532/grok2api)（基于 [chenyme/grok2api](https://github.com/chenyme/grok2api)）的**个人巨魔版（Fork）**，采用多层分支架构同步上游功能并在其上叠加本地增强。欢迎 PR 和 Fork，二开请保留原作者与前端标识。

---

## 分支架构

本仓库采用多层上游同步架构，实现与多个上游仓库的持续集成：

```
上游源 (chenyme 停更) ──→  提取有价值功能
                              ↓
活跃上游 (jiujiu532) ──────→  main (合并层 + 本地增强)
```

| 层级 | 分支 | 来源 | 作用 | 状态 |
|:---|:---|:---|:---|:---|
| L1 | `upstream/source` | [chenyme/grok2api](https://github.com/chenyme/grok2api) | 原上游镜像，基础架构来源 | 只读，无新提交 |
| L2 | `upstream/active` | [jiujiu532/grok2api](https://github.com/jiujiu532/grok2api) | 活跃上游镜像，持续同步功能更新 | 只读，定期同步 |
| L3 | **`main`** | 合并层 + 本地增强 | **主要开发分支**，接收上游合并并在其上叠加修复/优化 | 活跃 |

**同步方式：**

```bash
# 1. 获取上游最新代码
git fetch jiujiu532 && git branch -f upstream/active jiujiu532/main

# 2. 合并到 main
git checkout main && git merge upstream/active

# 3. 合并完成
```

> 上游同步工作流（`.github/workflows/branch-stacking-sync.yml`）每日 04:00 UTC 自动检查上游更新，检测到新提交时创建 GitHub Issue 通知。

---

## 核心特性

| 能力 | 说明 |
| :-- | :-- |
| OpenAI 兼容 | `/v1/chat/completions`、`/v1/responses`、`/v1/images/generations`、`/v1/videos` |
| Anthropic 兼容 | `/v1/messages`（Claude SDK 直接对接） |
| 多账号池 | basic / super / heavy 三级池，自动负载均衡与配额同步 |
| 免费账号 | 支持 `console.x.ai` SSO Token，`*-console` 模型零成本使用 |
| 媒体生成 | 文生图、图像编辑、文生视频、图生视频，本地缓存与代理链接 |
| 防封内置 | `x-statsig-id` 兼容修复，WARP + FlareSolverr 一键部署 |
| 管理后台 | Admin 配置、账号管理、Web Chat、Masonry 画廊、ChatKit 语音 |
| CF Clearance 监控 | Cloudflare 求解器状态、历史记录、一键刷新 |
| Mihomo 代理管理 | 代理节点状态、切换、黑名单管理 |

---

## 本分支增强 vs 上游

| 方面 | jiujiu532/grok2api | edgelord |
|:---|:---|:---|
| 基础镜像 | `python:3.13-alpine` | `linuxserver/chrome:latest`（含 Chrome + Xvfb） |
| 初始化 | 自定义 entrypoint.sh | s6-overlay v3 标准框架 |
| CF 求解 | FlareSolverr / 手动 | **新增 Turnstile 本地求解** |
| 代理管理 | 基础代理池 | **新增 Mihomo 集成**（节点切换、黑名单、CF 回退） |
| Clearance 监控 | 无 | **新增 SQLite 历史数据库 + 管理面板**（重启不丢） |
| 403 处理 | 全部走 CHALLENGE | **智能分类**：IP 封禁走节点轮换 / CF 挑战走求解 / 违规不重试 |
| 镜像 | `ghcr.io/jiujiu532/grok2api` | `ghcr.io/shigouqaq/grok2api-edgelord` |

## 部署注意事项

本分支使用 `linuxserver/chrome:latest` 基础镜像，与上游有以下差异：

| 注意事项 | 说明 |
|:---|:---|
| **镜像体积较大** | 内置 Chrome 浏览器，镜像约 1.5GB+，拉取时间比上游长 |
| **Docker 权限** | Chrome 启动需要 `SYS_PTRACE` 权限。如果使用 Turnstile 求解器，Compose 中建议添加 `cap_add: [SYS_PTRACE]`，否则浏览器可能崩溃 |
| **使用上游镜像** | 本仓库未在 CI 中自动构建镜像，`docker-compose.yml` 默认仍指向 `ghcr.io/jiujiu532/grok2api:latest`。如需使用本分支镜像需自行构建或修改 `image` 字段 |
| **Mihomo 集成** | 需要额外部署 [Mihomo](https://github.com/MetaCubeX/mihomo) 并暴露 REST API（默认 `127.0.0.1:9093`）。参考 `mihomo-xai.yaml` 配置 |
| **Turnstile 求解** | 不使用 Turnstile 求解器时，`proxy.clearance.mode` 保持默认 `none`，可用 FlareSolverr 或手动模式，镜像差异不影响 |

---

## 部署指南

本项目提供两种部署方式：

| 方式 | 说明 | 适用场景 |
| :-- | :-- | :-- |
| **标准版** | 仅 grok2api，直连 Grok | IP 干净、无 Cloudflare 拦截 |
| **防封版** | grok2api + WARP + Privoxy + FlareSolverr | IP 被 Cloudflare 拦截、需要稳定访问 |

> [!TIP]
> 当前版本已内置 403 兼容修复，标准版可直接验证。仍遇 403 时再切防封版。

---

### 标准版部署

**Docker Compose（推荐）：**

```bash
git clone https://github.com/ShiGouQAQ/grok2api-edgelord
cd grok2api-edgelord
cp .env.example .env
docker compose up -d
```

> `docker-compose.yml` 默认使用 `ghcr.io/jiujiu532/grok2api:latest` 镜像。
> 如需使用本分支构建的镜像，修改 `image` 为 `ghcr.io/shigouqaq/grok2api-edgelord:latest` 或自行构建。

查看日志：

```bash
docker compose logs -f grok2api
```

**Docker 单容器：**

```bash
docker run -d --name grok2api \
  -p 8000:8000 \
  -e TZ=Asia/Shanghai \
  -e LOG_LEVEL=INFO \
  -e ACCOUNT_STORAGE=local \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  --restart unless-stopped \
  ghcr.io/jiujiu532/grok2api:latest
```

Windows PowerShell：

```powershell
docker run -d `
  --name grok2api `
  -p 8000:8000 `
  -e TZ=Asia/Shanghai `
  -e LOG_LEVEL=INFO `
  -e ACCOUNT_STORAGE=local `
  -v ${PWD}/data:/app/data `
  -v ${PWD}/logs:/app/logs `
  --restart unless-stopped `
  ghcr.io/jiujiu532/grok2api:latest
```

---

### 防封版部署

> **前置要求**：服务器需支持 `NET_ADMIN` + `SYS_MODULE` 权限（KVM/XEN 虚拟化均支持，OpenVZ/LXC 不支持）。

```bash
git clone https://github.com/ShiGouQAQ/grok2api-edgelord
cd grok2api-edgelord
docker compose -f docker-compose.warp.yml up -d
```

防封版自动启动以下服务：

| 服务 | 说明 |
| :-- | :-- |
| `warp-proxy` | Cloudflare WARP 出口代理，提供干净 IP |
| `privoxy` | HTTP 代理，将流量转发到 WARP |
| `flaresolverr` | 自动解 Cloudflare 挑战，获取 cf_clearance |
| `init-config` | 初始化容器，自动写入代理配置 |
| `grok2api` | 主服务 |

启动后代理配置已自动完成，进入 Admin 后台添加账号即可使用。

---

<details>
<summary><strong>升级 / 回滚 / 卸载 / 迁移</strong></summary>

### 升级

无论标准版还是防封版，升级时只需更新 `grok2api` 主镜像，防封组件不需要更新。

**标准版升级：**

```bash
docker pull ghcr.io/jiujiu532/grok2api:latest
docker compose up -d --no-deps grok2api
```

**防封版升级（只更新主服务，不动 WARP/FlareSolverr）：**

```bash
docker pull ghcr.io/jiujiu532/grok2api:latest
docker compose -f docker-compose.warp.yml up -d --no-deps grok2api
```

> `--no-deps` 确保只重启 grok2api，WARP/Privoxy/FlareSolverr 继续运行不中断。
>
> `./data/` 中的配置（`config.toml`）和数据库（`accounts.db`）挂载在 volume 中，升级不会覆盖。

### 回滚

```bash
docker pull ghcr.io/jiujiu532/grok2api:<tag>
docker compose up -d --no-deps grok2api
```

### 卸载

```bash
docker compose down
# 如需删除数据（不可恢复）：
rm -rf ./data ./logs
```

### 从标准版迁移到防封版

数据完全保留，无需重新配置：

```bash
docker compose down
docker compose -f docker-compose.warp.yml up -d
```

</details>

---

### 本地源码部署

前置：Python 3.13+、[uv](https://docs.astral.sh/uv/getting-started/installation/)

```bash
git clone https://github.com/ShiGouQAQ/grok2api-edgelord
cd grok2api-edgelord
cp .env.example .env && uv sync
uv run granian --interface asgi --host 0.0.0.0 --port 8000 --workers 1 app.main:app
```

---

### 首次启动

访问 `http://localhost:8000/admin/login`，默认密码 `grok2api`，进入后设置：

1. `app.app_key` — Admin 密码
2. `app.api_key` — API 鉴权密钥（留空不鉴权）
3. `app.app_url` — 公网地址（图片/视频链接需要）

> 配置保存即时生效，无需重启。

---

## 上游同步

本仓库使用 `branch-stacking-maintenance` 工作流管理多层上游同步：

| 机制 | 说明 |
| :-- | :-- |
| **GitHub Actions** | 每日 04:00 UTC 自动检测 [jiujiu532/grok2api](https://github.com/jiujiu532/grok2api) 的新提交，检测到时创建 Issue 通知 |
| **手动同步** | 见 [upstream-sync-guide.md](upstream-sync-guide.md) |

**格式保护策略：**

> [!IMPORTANT]
> **禁止对上游代码运行 `ruff format`**。上游代码有自己的格式风格，自动格式化会导致大量无意义 diff 和合并冲突。
> 详情见 [upstream-sync-guide.md](upstream-sync-guide.md)。

---

## 模型列表

### Chat（grok.com）

basic 表示 free 账号，super 和 heavy 为付费。

| 模型名 | mode | 账号等级 | 备注 |
| :-- | :-- | :-- | :-- |
| `grok-4.20-fast` / `grok-4.3-fast` | fast | basic（优先高等级） |
| `grok-4.20-auto` | auto | super |
| `grok-4.20-expert` | expert | super |
| `grok-4.20-heavy` | heavy | heavy |
| `grok-4.3-beta` | grok-420-computer-use-sa | super |
| `grok-4.20-multi-agent-0309` | heavy | heavy |
| `grok-4.20-0309-non-reasoning` | fast | basic |
| `grok-4.20-0309` | auto | super |
| `grok-4.20-0309-reasoning` | expert | super |
| `grok-4.20-0309-non-reasoning-super` | fast | super |
| `grok-4.20-0309-super` | auto | super |
| `grok-4.20-0309-reasoning-super` | expert | super |
| `grok-4.20-0309-non-reasoning-heavy` | fast | heavy |
| `grok-4.20-0309-heavy` | auto | heavy |
| `grok-4.20-0309-reasoning-heavy` | expert | heavy |

### Chat（console.x.ai）

通过 SSO Token 免费访问，不消耗付费额度。所有免费模型使用 **basic** 等级账号。

| 模型名 | reasoning effort | 账号等级 |
| :-- | :-- | :-- |
| `grok-4.3-console` | 用户传入（默认 medium） | basic |
| `grok-4.3-low` | low（固定） | basic |
| `grok-4.3-medium` | medium（固定） | basic |
| `grok-4.3-high` | high（固定） | basic |
| `grok-4.20-0309-console` | 默认 | basic |
| `grok-4.20-0309-reasoning-console` | 固定 reasoning | basic |
| `grok-4.20-0309-non-reasoning-console` | 无 reasoning | basic |
| `grok-4.20-multi-agent-console` | 用户传入（默认 medium） | basic |
| `grok-4.20-multi-agent-low` | low（固定）→ 4 agents | basic |
| `grok-4.20-multi-agent-medium` | medium（固定）→ 4 agents | basic |
| `grok-4.20-multi-agent-high` | high（固定）→ 16 agents | basic |
| `grok-4.20-multi-agent-xhigh` | xhigh（固定）→ 16 agents | basic |
| `grok-build-console` | 默认 | basic |

**Console 配额**：20 次 / 60 分钟窗口，采用延迟恢复轮换策略（消耗至剩余 12 次时启动计时器，评分机制自动轮换到其他账号）。后台每 30 秒巡检并自动重置过期配额。

### Image / Video（grok.com）

| 模型名 | 能力 | 账号等级 |
| :-- | :-- | :-- |
| `grok-imagine-image-lite` | 文生图 | basic |
| `grok-imagine-image` / `image-pro` | 文生图 | super |
| `grok-imagine-image-edit` | 图像编辑 | super |
| `grok-imagine-video` | 文生视频 | super |

---

## 账号配置

| 类型 | 等级 | 适用模型 |
| :-- | :-- | :-- |
| 付费账号（x.ai 官方） | super / heavy | `grok-4.20-*`、`grok-4.3-beta`、`grok-4.3-fast` |
| 免费账号（console.x.ai SSO） | basic | 所有 `*-console` / `*-low` / `*-medium` / `*-high` / `*-xhigh` |

**免费账号获取方式：**

1. 浏览器 F12 打开开发者工具
2. 访问 `https://console.x.ai/`
3. Network 面板找任意请求，Cookie 中复制 `sso` 值
4. Admin 后台 → 账号管理 → 添加账号，粘贴 token

> SSO Token 属于敏感凭证，请勿写入代码或提交到版本库。

---

## API 端点

### 公开 API

| 端点 | 说明 |
| :-- | :-- |
| `GET /v1/models` | 列出可用模型 |
| `POST /v1/chat/completions` | 聊天 / 图像 / 视频统一入口 |
| `POST /v1/responses` | OpenAI Responses API |
| `POST /v1/messages` | Anthropic Messages API |
| `POST /v1/images/generations` | 图像生成 |
| `POST /v1/images/edits` | 图像编辑 |
| `POST /v1/videos` | 异步视频任务 |
| `GET /v1/videos/{id}` / `{id}/content` | 查询 / 下载视频 |

### Admin API（需 Bearer Token）

| 端点 | 说明 |
| :-- | :-- |
| `GET /admin/api/config` | 获取配置 |
| `POST /admin/api/config` | 更新配置 |
| `GET /admin/api/tokens` | 账号列表 |
| `POST /admin/api/tokens/add` | 添加账号 |
| `POST /admin/api/batch/refresh` | 批量刷新账号 |
| `GET /admin/api/mihomo/status` | Mihomo 代理节点状态 |
| `POST /admin/api/mihomo/switch` | 切换代理节点 |
| `POST /admin/api/mihomo/blacklist/clear` | 清空节点黑名单 |
| `GET /admin/api/cf-clearance/stats` | CF Clearance 统计 |
| `POST /admin/api/cf-clearance/refresh` | 刷新 CF Clearance |
| `GET /admin/api/cache/list` | 缓存列表 |

---

## 环境变量

| 变量名 | 说明 | 默认值 |
| :-- | :-- | :-- |
| `TZ` | 时区 | `Asia/Shanghai` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `LOG_FILE_ENABLED` | 写入本地文件日志 | `true` |
| `SERVER_HOST` | 监听地址 | `0.0.0.0` |
| `SERVER_PORT` | 监听端口 | `8000` |
| `SERVER_WORKERS` | Granian worker 数量 | `1` |
| `HOST_PORT` | Compose 宿主机映射端口 | `8000` |
| `DATA_DIR` | 本地数据根目录 | `./data` |
| `LOG_DIR` | 本地日志目录 | `./logs` |
| `ACCOUNT_STORAGE` | 存储后端：`local` / `redis` / `mysql` / `postgresql` | `local` |
| `ACCOUNT_SYNC_INTERVAL` | 增量同步间隔（秒） | `30` |
| `ACCOUNT_SYNC_ACTIVE_INTERVAL` | 活跃同步间隔（秒） | `3` |
| `ACCOUNT_LOCAL_PATH` | SQLite 路径 | `${DATA_DIR}/accounts.db` |
| `ACCOUNT_REDIS_URL` | Redis DSN | `""` |
| `ACCOUNT_MYSQL_URL` | MySQL DSN | `""` |
| `ACCOUNT_POSTGRESQL_URL` | PostgreSQL DSN | `""` |
| `ACCOUNT_SQL_POOL_SIZE` | 连接池核心连接数 | `5` |
| `ACCOUNT_SQL_MAX_OVERFLOW` | 连接池最大溢出 | `10` |
| `ACCOUNT_SQL_POOL_TIMEOUT` | 等待空闲连接超时（秒） | `30` |
| `ACCOUNT_SQL_POOL_RECYCLE` | 连接最大复用时间（秒） | `1800` |

运行时配置支持 `GROK_` 前缀覆盖，如 `GROK_APP_API_KEY` 覆盖 `app.api_key`。

---

## 调用示例

```bash
# 付费账号对话
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"grok-4.20-auto","stream":true,"messages":[{"role":"user","content":"你好"}]}'

# 免费账号对话
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"grok-4.3-console","stream":true,"messages":[{"role":"user","content":"你好"}]}'
```

---

## 常见问题

| 问题 | 解决方案 |
| :-- | :-- |
| Admin 打不开 | 确认端口映射和防火墙：`docker compose ps` |
| 图片/视频链接 403 | 设置 `app.app_url` 为公网地址（含 `https://`） |
| Cloudflare 拦截 | 更换代理，或者切换防封版部署，再或者手动配置 `proxy.clearance.mode` |
| 多 Worker 冲突 | 无冲突，调度器通过文件锁选举 leader |

---

## 更新日志

### Beyond v0.2.2 — edgelord 本地增强

**CF Clearance 增强**
- CF Clearance 统计改为从 SQLite 实时聚合，修复重启后 stats 归零
- 手动刷新时跳过缓存，确保同时刷新 grok.com 和 x.ai 两个域名
- 监控 API 补充 `last_check_time` 字段
- CF Clearance 页面修复 i18n 初始化回调错误
- 新增 CF Clearance get_stats() SQLite 聚合测试

**Console 稳定性**
- 区分 Console 403 内容违规与 CF 挑战，解析 429/403 body code 并传入 reason
- 修复 Console 代理轮换与 CF 刷新问题
- Console 403 账号封禁与 CF 挑战区分

**代理层**
- `_last_check_time` 改为按域名维护字典
- 自动刷新更新 `cache_misses` 统计

**工程基础**
- 添加 CLAUDE.md 项目规则文档，便于 AI 协作开发
- 添加上游代码同步与格式规范文档（`upstream-sync-guide.md`）
- 升级全部依赖到最新版本
- 上游同步 GitHub Actions 工作流

### v0.2.2 (jiujiu532)

- SQLite WAL 模式在 NFS/特殊 Docker 挂载时静默 fallback，修复 issue #31
- 新增已删除账号定时物理清理
- 账号列表加载性能优化

### v0.2.1 (jiujiu532)

- Console 429 误判缓和 — 滑动窗口 + 自动恢复
- Console 429 EXPIRED 判定改用独立计数器，阈值放宽到 3 次
- 优化账号选号机制

### v0.1.8 (jiujiu532)

- 配额机制深度审查修复 10 个 bug
- Console 429 直接清零配额的 bug 修复
- Console 配额巡检优化 — 直接 SQL 批量重置
- 批量导入账号性能优化 — executemany + 配额缓存

### v0.1.7 (jiujiu532)

**新功能**
- Console 原生工具调用支持（OpenAI 兼容的 `tools` / `tool_choice` 参数）
- 客户端 function tools（如 bash、read）可稳定产出 `tool_calls`
- Grok 内置工具（web_search、x_search 等 19 个）保持内部语义，不泄露为客户端 tool_calls
- 新增 738 行回归测试覆盖核心逻辑

**优化**
- Console 配额参数调整：恢复周期 30 分钟，轮换阈值 20
- SSE 心跳保活，防止思考期间连接超时
- TXT 导入异步化，大批量导入不再阻塞
- 依赖安全升级

**修复**
- 修复 NSFW 初始化时生日已锁定的 429 报错
- 修复批量刷新结果未区分异常与临时失败的问题

---

## 致谢

- 上游活跃： [jiujiu532/grok2api](https://github.com/jiujiu532/grok2api)
- 上游源： [chenyme/grok2api](https://github.com/chenyme/grok2api)
- DeepWiki：[chenyme/grok2api](https://deepwiki.com/chenyme/grok2api)
- 项目文档：[blog.cheny.me](https://blog.cheny.me/blog/posts/grok2api)
- 社区：[Linux.do](https://linux.do)

---

<div align="center">

**MIT License**

</div>
