"""Pytest配置和共享fixtures"""

import pytest


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
