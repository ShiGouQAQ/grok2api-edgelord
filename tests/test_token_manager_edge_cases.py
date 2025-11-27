"""Token Manager边缘情况测试"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.grok.token import GrokTokenManager


@pytest.mark.asyncio
async def test_select_token_with_empty_pool():
    """测试token池为空时的行为"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {"ssoNormal": {}, "ssoSuper": {}}

    with pytest.raises(Exception) as exc_info:
        mgr.select_token("grok-2-latest")
    assert "没有可用Token" in str(exc_info.value)


@pytest.mark.asyncio
async def test_select_token_all_expired():
    """测试所有token都过期时的行为"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {
        "ssoNormal": {
            "token1": {"status": "expired", "remainingQueries": 100},
            "token2": {"status": "expired", "remainingQueries": 50}
        },
        "ssoSuper": {}
    }

    with pytest.raises(Exception) as exc_info:
        mgr.select_token("grok-2-latest")
    assert "没有可用Token" in str(exc_info.value)


@pytest.mark.asyncio
async def test_select_token_all_rate_limited():
    """测试所有token都限流时的行为"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {
        "ssoNormal": {
            "token1": {"status": "active", "remainingQueries": 0},
            "token2": {"status": "active", "remainingQueries": 0}
        },
        "ssoSuper": {}
    }

    with pytest.raises(Exception) as exc_info:
        mgr.select_token("grok-2-latest")
    assert "没有可用Token" in str(exc_info.value)


@pytest.mark.asyncio
async def test_select_token_prefers_unused():
    """测试优先选择未使用的token"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {
        "ssoNormal": {
            "used_token": {"status": "active", "remainingQueries": 50},
            "unused_token": {"status": "active", "remainingQueries": -1}
        },
        "ssoSuper": {}
    }

    selected = mgr.select_token("grok-2-latest")
    assert selected == "unused_token"


@pytest.mark.asyncio
async def test_select_token_prefers_higher_remaining():
    """测试选择剩余次数最多的token"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {
        "ssoNormal": {
            "token_low": {"status": "active", "remainingQueries": 10},
            "token_high": {"status": "active", "remainingQueries": 100}
        },
        "ssoSuper": {}
    }

    selected = mgr.select_token("grok-2-latest")
    assert selected == "token_high"


@pytest.mark.asyncio
async def test_select_token_grok4_heavy_requires_super():
    """测试grok-4-heavy只能使用Super Token"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {
        "ssoNormal": {
            "normal_token": {"status": "active", "remainingQueries": 100}
        },
        "ssoSuper": {}
    }

    with pytest.raises(Exception) as exc_info:
        mgr.select_token("grok-4-heavy")
    assert "没有可用Token" in str(exc_info.value)


@pytest.mark.asyncio
async def test_select_token_grok4_heavy_uses_heavy_remaining():
    """测试grok-4-heavy使用heavyremainingQueries字段"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {
        "ssoNormal": {},
        "ssoSuper": {
            "super_token": {"status": "active", "heavyremainingQueries": 50}
        }
    }

    selected = mgr.select_token("grok-4-heavy")
    assert selected == "super_token"


@pytest.mark.asyncio
async def test_select_token_fallback_to_super():
    """测试普通token不可用时回退到Super Token"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {
        "ssoNormal": {
            "normal_expired": {"status": "expired", "remainingQueries": 100}
        },
        "ssoSuper": {
            "super_token": {"status": "active", "remainingQueries": 50}
        }
    }

    selected = mgr.select_token("grok-2-latest")
    assert selected == "super_token"


@pytest.mark.asyncio
async def test_check_limits_with_network_error():
    """测试网络错误时的处理"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {"ssoNormal": {}, "ssoSuper": {}}

    with patch('app.services.grok.token.AsyncSession') as mock_session:
        mock_session.return_value.__aenter__.return_value.post.side_effect = Exception("Network error")

        result = await mgr.check_limits("sso-rw=xxx;sso=yyy", "grok-2-latest")
        assert result is None


@pytest.mark.asyncio
async def test_check_limits_with_500_error():
    """测试服务器500错误"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {"ssoNormal": {"yyy": {"status": "active"}}, "ssoSuper": {}}

    mock_response = MagicMock()
    mock_response.status_code = 500

    with patch('app.services.grok.token.AsyncSession') as mock_session:
        mock_session.return_value.__aenter__.return_value.post.return_value = mock_response
        with patch.object(mgr, 'record_failure', new_callable=AsyncMock) as mock_record:
            result = await mgr.check_limits("sso-rw=xxx;sso=yyy", "grok-2-latest")
            assert result is None
            mock_record.assert_called_once()


@pytest.mark.asyncio
async def test_update_limits_with_nonexistent_token():
    """测试更新不存在的token限制"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {"ssoNormal": {}, "ssoSuper": {}}

    with patch.object(mgr, '_save_data', new_callable=AsyncMock):
        await mgr.update_limits("nonexistent_sso", normal=100, heavy=None)
        assert True


@pytest.mark.asyncio
async def test_concurrent_token_selection():
    """测试并发token选择不会冲突"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {
        "ssoNormal": {
            "token1": {"status": "active", "remainingQueries": 100},
            "token2": {"status": "active", "remainingQueries": 100}
        },
        "ssoSuper": {}
    }

    # 并发选择token
    import asyncio
    results = await asyncio.gather(*[
        asyncio.to_thread(mgr.select_token, "grok-2-latest")
        for _ in range(10)
    ])

    # 所有结果都应该是有效的token
    assert all(r in ["token1", "token2"] for r in results)


@pytest.mark.asyncio
async def test_reset_failure_with_nonexistent_token():
    """测试重置不存在的token失败计数"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {"ssoNormal": {}, "ssoSuper": {}}

    with patch.object(mgr, '_save_data', new_callable=AsyncMock):
        await mgr.reset_failure("sso-rw=xxx;sso=nonexistent")
        assert True


@pytest.mark.asyncio
async def test_reset_failure_clears_count():
    """测试重置失败计数"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {
        "ssoNormal": {
            "test_sso": {
                "status": "active",
                "failedCount": 5,
                "lastFailureTime": 123456,
                "lastFailureReason": "Some error"
            }
        },
        "ssoSuper": {}
    }

    with patch.object(mgr, '_save_data', new_callable=AsyncMock):
        await mgr.reset_failure("sso-rw=xxx;sso=test_sso")
        assert mgr.token_data["ssoNormal"]["test_sso"]["failedCount"] == 0
        assert mgr.token_data["ssoNormal"]["test_sso"]["lastFailureTime"] is None
        assert mgr.token_data["ssoNormal"]["test_sso"]["lastFailureReason"] is None
