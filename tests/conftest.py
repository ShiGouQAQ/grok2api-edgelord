"""Pytest配置和共享fixtures"""

import pytest
import asyncio
from typing import Generator


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """创建事件循环"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def reset_singletons():
    """每个测试后重置单例状态"""
    yield
    # 测试后清理 - 使用懒导入避免循环导入问题
    try:
        import app.control.proxy

        app.control.proxy._directory = None
    except (ImportError, AttributeError):
        pass  # 忽略导入错误，避免影响测试
