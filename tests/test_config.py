"""配置测试"""

import pytest
from app.core.config import setting


def test_config_has_cf_clearance_fields():
    """测试配置包含CF Clearance字段"""
    # 验证配置字段存在
    assert hasattr(setting, 'grok_config')

    # 验证可以获取配置值
    turnstile_enabled = setting.grok_config.get("turnstile_enabled", None)
    turnstile_host = setting.grok_config.get("turnstile_host", None)

    # 这些字段应该存在（即使是默认值）
    assert turnstile_enabled is not None or turnstile_enabled == False
    assert turnstile_host is not None or turnstile_host == ""


def test_config_cf_clearance_default_disabled():
    """测试Turnstile Solver默认禁用"""
    enabled = setting.grok_config.get("turnstile_enabled", False)
    assert enabled is False
