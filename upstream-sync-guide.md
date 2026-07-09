# 上游代码同步与格式规范

## ⚠️ 重要警告

**禁止对上游代码运行 `ruff format` 或任何自动格式化工具！**

上游代码有自己的格式风格，自动格式化会导致：
1. 大量无意义的 diff，污染 git 历史
2. 每次合并上游时产生格式冲突
3. 无法区分是格式变更还是功能变更

**原则：保持上游代码原样，只在本地新增的代码中应用自己的风格。**

---

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

## 处理上游代码的 Ruff 警告

**原则：忽略上游代码的 ruff 警告，不要修复它们。**

上游代码可能有以下类型的警告：
- `F401` - 未使用的导入
- `F841` - 未使用的变量
- `E712` - 比较风格问题

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

- [ ] `git fetch jiujiu532` 成功
- [ ] `git merge upstream/active` 无冲突或冲突已解决
- [ ] `uv run pytest tests/ -v` 全部通过
- [ ] 本地功能测试通过
- [ ] ❌ **不要运行** `ruff format` 或 `ruff check --fix .`

## 注意事项

1. **不要修改上游代码风格**: 保持上游原有的格式，只在本地功能代码中应用新格式
2. **不要运行 ruff format**: 这会格式化整个项目，包括上游代码
3. **不要运行 ruff check --fix .**: 这会修复所有文件的警告，包括上游代码
4. **只检查本地代码**: 运行 `ruff check` 只看不修，或只修复指定的本地文件
5. **提交前检查**: 本地修改提交前运行格式检查，避免格式问题阻塞同步

## 相关文件

- `pyproject.toml` - 项目配置和依赖
- `.ruff_cache/` - ruff 缓存目录（自动生成）
- `app/` - 主要应用代码
- `tests/` - 测试代码
