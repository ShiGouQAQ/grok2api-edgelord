# 上游代码同步与格式规范

## ⚠️ 上游现状

[chenyme/grok2api](https://github.com/chenyme/grok2api) 已恢复开发并**以 Go 完全重写**（提交 `a16837c`），不再有 Python 代码。

本仓库作为 Python 分支持续跟进，采用 **Go→Python 移植** 方式同步上游修复与功能。

---

## 概述

本文档记录两类上游的同步流程：

| 层级 | 上游 | 远程名 | 本地分支 | 技术栈 | 同步方式 |
|:---|------|--------|----------|--------|----------|
| **L1** | [jiujiu532/grok2api](https://github.com/jiujiu532/grok2api) | `jiujiu532` | `upstream/active` | Python (同栈) | `git merge` **直接合并** |
| **L2** | [chenyme/grok2api](https://github.com/chenyme/grok2api) | `source` | `upstream/source` | Go (需翻译) | **分析 → 记录 → 移植** |

---

## 方式一：合并 Python 上游 (jiujiu532)

### 1. 更新上游代码

```bash
git fetch jiujiu532 && git branch -f upstream/active jiujiu532/main
```

### 2. 合并到 main

```bash
git checkout main && git merge upstream/active
```

### 3. 解决冲突（如有）

```bash
git status          # 查看冲突文件
# 解决冲突后
git add . && git commit -m "merge: 同步上游代码"
```

### 4. 验证

```bash
uv run pytest tests/ -v
```

---

## 方式二：Go→Python 移植 (chenyme)

由于 chenyme 上游已转为 Go，无法直接 merge。移植流程如下：

### 1. 更新上游镜像

```bash
git fetch source && git branch -f upstream/source source/main
```

### 2. 分析新提交

```bash
# 查看自上次同步后的新提交
git log --oneline --no-merges upstream/source

# 按类别分组
git log --oneline --no-merges upstream/source | grep -E "^(fix|feat|refactor)"
```

### 3. 评估移植优先级

| 优先级 | 类别 | 处理方式 |
|--------|------|----------|
| 🔴 高 | Bug 修复（影响运行） | 优先移植 |
| 🟡 中 | 新功能（非破坏性） | 评估必要性后移植 |
| 🟢 低 | 重构、文档、CI 变更 | 按需移植 |

### 4. 移植操作

```bash
# 在 main 上工作
git checkout main

# 查看 Go 源文件参考
git show upstream/source:backend/internal/path/to/file.go

# 编辑对应 Python 文件后提交
git add -A && git commit -m "port(chenyme): 说明移植了什么"
```

### 5. 更新移植台账

每次 Go→Python 移植后，**必须**更新 `go-port-ledger.md`：

```bash
# 编辑台账，添加新移植的提交记录
vim go-port-ledger.md
```

台账示例条目：

```markdown
| 2026-07-15 | abc1234 | feat: ... | ✅ 已移植 | v2.1.0 |
| 2026-07-14 | def5678 | fix: ... | ⏭️ 跳过 - Go 特有 | — |
```

移植前永远先查台账：`grep "<提交hash>" go-port-ledger.md` 确认未被处理过。

### 6. Go→Python 移植对照

| Go 概念 | Python 等价 |
|---------|-------------|
| `struct` + 方法 | 类 (`class`) + 方法 |
| `interface` | 抽象基类 (`ABC`) / Protocol |
| `error` 返回值 | `Exception` 层级 |
| `context.Context` | `asyncio` 任务/取消 |
| `goroutine + channel` | `asyncio.Task` / `asyncio.Queue` |
| `sync.Mutex` | `asyncio.Lock` |
| `http.Handler` | `FastAPI` route handler |
| `go.mod` 模块 | `pyproject.toml` 依赖 |
| `json.RawMessage` | `dict` / `orjson` |
| `io.ReadCloser` | 异步迭代器 / `aiohttp` stream |

---

## 格式规范

### 代码风格

上游 (jiujiu532) 使用 **ruff** 作为格式化工具，未配置强制规则。主要风格：

1. **缩进**: 4 空格
2. **行宽**: 无严格限制（建议 120 字符）
3. **引号**: 双引号 `"` 为主
4. **导入排序**: 标准库 → 第三方 → 本地，使用 `isort` 风格
5. **类型注解**: Python 3.13+ 语法，使用 `dict[str, str]` 而非 `Dict[str, str]`

### 文件结构

```python
"""模块文档字符串"""

# 标准库导入
from typing import Any, AsyncGenerator

# 第三方库导入
import orjson
from fastapi import APIRouter

# 本地导入
from app.platform.errors import UpstreamError
from app.platform.logging.logger import logger
```

### 命名规范

- **模块**: 小写 + 下划线 (`xai_console_chat.py`)
- **类**: 大驼峰 (`ConsoleChat`)
- **函数/变量**: 小写 + 下划线 (`stream_console_chat`)
- **常量**: 大写 + 下划线 (`CONSOLE_MODELS`)

## 格式化检查

### 本地检查

```bash
# 安装 ruff
uv sync --group dev

# 检查格式
uv run ruff check .

# 自动修复
uv run ruff check --fix .
```

### CI/CD 检查

上游未配置强制格式检查，但建议本地提交前运行：

```bash
uv run ruff check . && uv run ruff format --check .
```

## 处理上游代码的 Ruff 警告

**原则：忽略上游代码的 ruff 警告，不要修复它们。**

上游代码可能有以下类型的警告：
- `F401` — 未使用的导入
- `F841` — 未使用的变量
- `E712` — 比较风格问题

**正确做法：**
1. 运行 `ruff check .` 查看警告
2. 只修复**本地新增代码**的警告
3. 忽略**上游代码**的警告
4. 如果警告太多，可以用 `# noqa` 注释（但不推荐）

**错误做法：**
- ❌ 运行 `ruff check --fix .` 自动修复所有警告
- ❌ 手动修复上游代码的导入或变量
- ❌ 运行 `ruff format` 格式化整个项目

**示例：**
```bash
# 查看警告（只看不修）
uv run ruff check .

# 只修复本地代码的警告（指定文件）
uv run ruff check --fix app/my_local_file.py
```

## 常见格式问题

### 1. 导入顺序

**错误**:
```python
from app.platform.errors import UpstreamError
import orjson
from typing import Any
```

**正确**:
```python
from typing import Any

import orjson

from app.platform.errors import UpstreamError
```

### 2. 类型注解

**错误**:
```python
from typing import Dict, List

def get_models() -> Dict[str, str]:
    ...
```

**正确**:
```python
def get_models() -> dict[str, str]:
    ...
```

### 3. 字符串引号

**错误**:
```python
name = 'grok-4.3'
```

**正确**:
```python
name = "grok-4.3"
```

## 同步检查清单

### jiujiu532 (Python merge)
- [ ] `git fetch jiujiu532` 成功
- [ ] `git merge upstream/active` 无冲突或冲突已解决
- [ ] `uv run pytest tests/ -v` 全部通过
- [ ] ❌ **不要运行** `ruff format` 或 `ruff check --fix .`

### chenyme (Go→Python port)
- [ ] `git fetch source && git branch -f upstream/source source/main` 成功
- [ ] `git log --oneline --no-merges upstream/source` 查看新提交
- [ ] 关键修复已评估移植必要性
- [ ] 移植代码编译/测试通过

## 注意事项

1. **不要修改上游代码风格**: 保持上游原有的格式，只在本地功能代码中应用新格式
2. **不要运行 ruff format**: 这会格式化整个项目，包括上游代码
3. **不要运行 ruff check --fix .**: 这会修复所有文件的警告，包括上游代码
4. **只检查本地代码**: 运行 `ruff check` 只看不修，或只修复指定的本地文件
5. **Go 移植不是简单翻译**: 需要理解 Go 逻辑后在 Python 异步架构中等效实现
6. **提交前检查**: 本地修改提交前运行格式检查，避免格式问题阻塞同步

## 相关文件

- `pyproject.toml` — 项目配置和依赖
- `.ruff_cache/` — ruff 缓存目录（自动生成）
- `app/` — 主要应用代码
- `tests/` — 测试代码
