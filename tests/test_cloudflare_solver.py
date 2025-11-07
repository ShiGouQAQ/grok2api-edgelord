"""Tests for Cloudflare Solver using playwright-captcha"""
import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from app.services.turnstile.manager import TurnstileSolverManager


class TestCloudflareSolver:
    """Test Cloudflare Solver Manager"""

    @pytest.fixture
    def manager(self):
        """Create manager instance"""
        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': True,
                'turnstile_headless': True,
                'turnstile_browser_type': 'chromium'
            }.get(key, default)
            return TurnstileSolverManager()

    def test_manager_initialization(self, manager):
        """Test manager initializes with correct config"""
        assert manager.enabled is True
        assert manager.headless is True
        assert manager.browser_type == 'chromium'

    def test_manager_default_values(self):
        """Test manager uses default values when config missing"""
        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: default
            manager = TurnstileSolverManager()

            assert manager.enabled is False
            assert manager.headless is True

    @pytest.mark.asyncio
    async def test_solve_cloudflare_disabled(self, manager):
        """Test solve_cloudflare returns None when disabled"""
        manager.enabled = False
        result = await manager.solve_cloudflare("https://grok.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_solve_cloudflare_success(self, manager):
        """Test solve_cloudflare returns cf_clearance on success"""
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_solver = AsyncMock()

        mock_context.cookies.return_value = [
            {'name': 'cf_clearance', 'value': 'test_clearance_value'},
            {'name': 'other_cookie', 'value': 'other_value'}
        ]
        mock_context.new_page.return_value = mock_page
        mock_browser.new_context.return_value = mock_context

        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': True,
                'turnstile_headless': True
            }.get(key, default)

            with patch('camoufox.AsyncCamoufox') as mock_camoufox:
                mock_camoufox.return_value.__aenter__.return_value = mock_browser

                with patch('playwright_captcha.ClickSolver') as mock_click_solver:
                    mock_click_solver.return_value.__aenter__.return_value = mock_solver

                    result = await manager.solve_cloudflare("https://grok.com")

                    assert result == 'test_clearance_value'
                    mock_page.goto.assert_called_once_with("https://grok.com", timeout=120000)
                    mock_solver.solve_captcha.assert_called_once()

    @pytest.mark.asyncio
    async def test_solve_cloudflare_no_cookie(self, manager):
        """Test solve_cloudflare returns None when cf_clearance not found"""
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_solver = AsyncMock()

        mock_context.cookies.return_value = [
            {'name': 'other_cookie', 'value': 'other_value'}
        ]
        mock_context.new_page.return_value = mock_page
        mock_browser.new_context.return_value = mock_context

        with patch('camoufox.AsyncCamoufox') as mock_camoufox:
            mock_camoufox.return_value.__aenter__.return_value = mock_browser

            with patch('playwright_captcha.ClickSolver') as mock_click_solver:
                mock_click_solver.return_value.__aenter__.return_value = mock_solver

                result = await manager.solve_cloudflare("https://grok.com")

                assert result is None

    @pytest.mark.asyncio
    async def test_solve_cloudflare_exception(self, manager):
        """Test solve_cloudflare handles exceptions"""
        with patch('camoufox.AsyncCamoufox') as mock_camoufox:
            mock_camoufox.side_effect = Exception("Test error")

            result = await manager.solve_cloudflare("https://grok.com")

            assert result is None
