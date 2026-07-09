# 上游代码同步与格式规范

## 概述

本文档记录上游代码同步流程和格式规范，确保后续修改不会因格式问题导致同步阻塞。

## 上游仓库信息

- **上游仓库**: https://github.com/jiujiu532/grok2api
- **上游分支**: main
- **本地分支架构**:
  - `upstream/active` - 活跃上游镜像（定期同步）
  - `main` - 合并层（基于停更上游 + 本地功能）
  - `custom/my-build` - 个人定制层（实际部署）

## 同步流程

### 1. 更新上游代码

```bash
# 更新活跃上游
git fetch jiujiu532 && git branch -f upstream/active jiujiu532/main
```

### 2. 合并上游到 main

```bash
git checkout main && git merge upstream/active
```

### 3. 解决冲突（如有）

```bash
# 查看冲突文件
git status

# 解决冲突后
git add . && git commit -m "merge: 同步上游代码"
```

## 格式规范

### 代码风格

上游代码使用 **ruff** 作为格式化工具，但未配置强制规则。主要风格：

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

- [ ] `git fetch jiujiu532` 成功
- [ ] `git merge upstream/active` 无冲突或冲突已解决
- [ ] `uv run ruff check .` 无错误
- [ ] `uv run pytest tests/ -v` 全部通过
- [ ] 本地功能测试通过

## 注意事项

1. **不要修改上游代码风格**: 保持上游原有的格式，只在本地功能代码中应用新格式
2. **合并后检查格式**: 每次合并上游后运行 `ruff check` 确保格式一致
3. **提交前检查**: 本地修改提交前运行格式检查，避免格式问题阻塞同步

## 相关文件

- `pyproject.toml` - 项目配置和依赖
- `.ruff_cache/` - ruff 缓存目录（自动生成）
- `app/` - 主要应用代码
- `tests/` - 测试代码
