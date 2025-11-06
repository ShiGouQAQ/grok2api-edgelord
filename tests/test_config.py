"""配置测试"""

import pytest
from app.core.config import setting


def test_config_has_cf_clearance_fields():
    """测试配置包含CF Clearance字段"""
    # 验证配置字段存在
    assert hasattr(setting, 'grok_config')

    # 验证可以获取配置值
    enabled = setting.grok_config.get("cf_clearance_enabled", None)
    api_url = setting.grok_config.get("turnstile_api_url", None)

    # 这些字段应该存在（即使是默认值）
    assert enabled is not None or enabled == False
    assert api_url is not None or api_url == ""


def test_config_cf_clearance_default_disabled():
    """测试CF Clearance默认禁用"""
    enabled = setting.grok_config.get("cf_clearance_enabled", False)
    assert enabled is False
