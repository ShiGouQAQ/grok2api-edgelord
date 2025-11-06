"""Integration tests for CF Clearance with Cloudflare Solver"""
import pytest
from unittest.mock import Mock, patch, AsyncMock


class TestCFClearanceIntegration:
    """Test CF Clearance integration with Cloudflare Solver"""

    @pytest.mark.asyncio
    async def test_cf_clearance_refresh_with_solver(self):
        """Test CF clearance refresh using Cloudflare Solver"""
        from app.services.grok.cf_clearance import CFClearanceManager

        manager = CFClearanceManager()

        with patch('app.services.grok.cf_clearance.setting') as mock_setting:
            mock_setting.grok_config.get.return_value = True
            mock_setting.save = AsyncMock()

            with patch('app.services.turnstile.manager.turnstile_manager') as mock_solver:
                mock_solver.solve_cloudflare = AsyncMock(return_value='test_cf_clearance_value')

                result = await manager._try_refresh_once()

                assert result is True
                mock_solver.solve_cloudflare.assert_called_once_with(url="https://grok.com")
                mock_setting.save.assert_called_once_with(
                    grok_config={"cf_clearance": "test_cf_clearance_value"}
                )

    @pytest.mark.asyncio
    async def test_cf_clearance_refresh_solver_failure(self):
        """Test CF clearance refresh when solver fails"""
        from app.services.grok.cf_clearance import CFClearanceManager

        manager = CFClearanceManager()

        with patch('app.services.grok.cf_clearance.setting') as mock_setting:
            mock_setting.grok_config.get.return_value = True

            with patch('app.services.turnstile.manager.turnstile_manager') as mock_solver:
                mock_solver.solve_cloudflare = AsyncMock(return_value=None)

                result = await manager._try_refresh_once()

                assert result is False
                mock_solver.solve_cloudflare.assert_called_once()

    @pytest.mark.asyncio
    async def test_cf_clearance_refresh_solver_exception(self):
        """Test CF clearance refresh when solver raises exception"""
        from app.services.grok.cf_clearance import CFClearanceManager

        manager = CFClearanceManager()

        with patch('app.services.grok.cf_clearance.setting') as mock_setting:
            mock_setting.grok_config.get.return_value = True

            with patch('app.services.turnstile.manager.turnstile_manager') as mock_solver:
                mock_solver.solve_cloudflare = AsyncMock(side_effect=Exception("Solver error"))

                result = await manager._try_refresh_once()

                assert result is False

    @pytest.mark.asyncio
    async def test_ensure_valid_clearance_triggers_solver(self):
        """Test ensure_valid_clearance triggers solver when needed"""
        from app.services.grok.cf_clearance import CFClearanceManager

        manager = CFClearanceManager()

        with patch('app.services.grok.cf_clearance.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'cf_clearance_enabled': True,
                'cf_clearance': ''
            }.get(key, default)
            mock_setting.save = AsyncMock()

            with patch('app.services.turnstile.manager.turnstile_manager') as mock_solver:
                mock_solver.solve_cloudflare = AsyncMock(return_value='new_clearance')

                with patch.object(manager, '_check_cf_challenge', new_callable=AsyncMock) as mock_check:
                    mock_check.return_value = True

                    result = await manager.ensure_valid_clearance()

                    assert result is True
                    mock_solver.solve_cloudflare.assert_called()
