"""Integration tests for CF Clearance with ProxyDirectory"""

import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestCFClearanceIntegration:
    """Test CF Clearance integration with ProxyDirectory"""

    @pytest.mark.asyncio
    async def test_clearance_refresh_with_mihomo_fallback(self):
        """Test clearance refresh using Mihomo fallback"""
        from app.control.proxy import ProxyDirectory

        directory = ProxyDirectory.__new__(ProxyDirectory)
        directory._stats = {
            "total_checks": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "solver_success": 0,
            "solver_failures": 0,
            "precheck_skips": 0,
        }
        directory._last_check_time = {}
        directory._check_interval = 3600
        directory._lock = asyncio.Lock()
        directory._clearance_mode = MagicMock()
        directory._clearance_mode.__ne__ = lambda self, other: True

        mock_bundle = MagicMock()
        with patch.object(
            directory,
            "_refresh_bundle_with_node_fallback",
            new_callable=AsyncMock,
            return_value=mock_bundle,
        ) as mock_fallback:
            result = await directory._refresh_clearance("https://grok.com")

            assert result is True
            mock_fallback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clearance_refresh_failure(self):
        """Test clearance refresh when returns None"""
        from app.control.proxy import ProxyDirectory

        directory = ProxyDirectory.__new__(ProxyDirectory)
        directory._stats = {
            "total_checks": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "solver_success": 0,
            "solver_failures": 0,
            "precheck_skips": 0,
        }
        directory._last_check_time = {}
        directory._check_interval = 3600
        directory._lock = asyncio.Lock()
        directory._clearance_mode = MagicMock()
        directory._clearance_mode.__ne__ = lambda self, other: True

        with patch.object(
            directory,
            "_refresh_bundle_with_node_fallback",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await directory._refresh_clearance("https://grok.com")

            assert result is False

    @pytest.mark.asyncio
    async def test_clearance_refresh_exception(self):
        """Test clearance refresh when raises exception"""
        from app.control.proxy import ProxyDirectory

        directory = ProxyDirectory.__new__(ProxyDirectory)
        directory._stats = {
            "total_checks": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "solver_success": 0,
            "solver_failures": 0,
            "precheck_skips": 0,
        }
        directory._last_check_time = {}
        directory._check_interval = 3600
        directory._lock = asyncio.Lock()
        directory._clearance_mode = MagicMock()
        directory._clearance_mode.__ne__ = lambda self, other: True

        with patch.object(
            directory,
            "_refresh_bundle_with_node_fallback",
            new_callable=AsyncMock,
            side_effect=Exception("Test error"),
        ):
            result = await directory._refresh_clearance("https://grok.com")

            assert result is False

    @pytest.mark.asyncio
    async def test_ensure_valid_clearance_triggers_refresh(self):
        """Test ensure_valid_clearance triggers refresh when needed"""
        from app.control.proxy import ProxyDirectory

        directory = ProxyDirectory.__new__(ProxyDirectory)
        directory._stats = {
            "total_checks": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "solver_success": 0,
            "solver_failures": 0,
            "precheck_skips": 0,
        }
        directory._last_check_time = {}
        directory._check_interval = 3600
        directory._lock = asyncio.Lock()
        directory._clearance_mode = MagicMock()
        directory._clearance_mode.__ne__ = lambda self, other: True

        mock_bundle = MagicMock()
        with patch.object(
            directory,
            "_refresh_bundle_with_node_fallback",
            new_callable=AsyncMock,
            return_value=mock_bundle,
        ) as mock_fallback:
            result = await directory.ensure_valid_clearance()

            assert result is True
            mock_fallback.assert_awaited_once()
