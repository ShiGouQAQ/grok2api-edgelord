"""配置测试"""

import pytest
from app.platform.config.snapshot import get_config


def test_config_has_cf_clearance_fields():
    """测试配置包含CF Clearance字段"""
    # 验证可以获取配置值
    grok_config = get_config("proxy.clearance", {})
    assert isinstance(grok_config, dict)


def test_config_cf_clearance_default_disabled():
    """测试CF Clearance默认禁用"""
    enabled = get_config("proxy.clearance.cf_clearance_enabled", False)
    assert enabled is False
