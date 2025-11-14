"""Tests for Cloudflare Solver using patchright"""
import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from app.services.turnstile.manager import TurnstileSolverManager


class TestCloudflareSolver:
    """Test Cloudflare Solver Manager"""

    @pytest.mark.asyncio
    async def test_solve_cloudflare_disabled(self):
        """Test solve_cloudflare returns None when disabled"""
        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.return_value = False

            manager = TurnstileSolverManager()
            result = await manager.solve_cloudflare("https://grok.com")
            assert result is None

    @pytest.mark.asyncio
    async def test_solve_cloudflare_success(self):
        """Test solve_cloudflare returns cf_clearance on success"""
        mock_playwright = AsyncMock()
        mock_chromium = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_solver = AsyncMock()

        mock_page.evaluate.return_value = {
            'userAgent': 'Mozilla/5.0 Test User Agent',
            'platform': 'Linux',
            'userAgentData': {
                'platform': 'Linux',
                'mobile': False,
                'brands': '"Chromium";v="133", "Not(A:Brand";v="99"'
            }
        }
        mock_context.cookies.return_value = [
            {'name': 'cf_clearance', 'value': 'test_clearance_value'},
            {'name': 'other_cookie', 'value': 'other_value'}
        ]
        mock_context.new_page.return_value = mock_page
        mock_browser.new_context.return_value = mock_context
        mock_chromium.launch.return_value = mock_browser
        mock_playwright.chromium = mock_chromium

        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': True,
                'turnstile_headless': True
            }.get(key, default)
            mock_setting.save = AsyncMock()

            with patch('patchright.async_api.async_playwright') as mock_pw:
                mock_pw.return_value.__aenter__.return_value = mock_playwright

                with patch('playwright_captcha.ClickSolver') as mock_click_solver:
                    mock_click_solver.return_value.__aenter__.return_value = mock_solver

                    manager = TurnstileSolverManager()
                    result = await manager.solve_cloudflare("https://grok.com")

                    assert result == 'cf_clearance=test_clearance_value'
                    mock_page.goto.assert_called_once_with("https://grok.com", timeout=120000)
                    mock_solver.solve_captcha.assert_called_once()

    @pytest.mark.asyncio
    async def test_solve_cloudflare_no_cookie(self):
        """Test solve_cloudflare returns None when cf_clearance not found"""
        mock_playwright = AsyncMock()
        mock_chromium = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_solver = AsyncMock()

        mock_page.evaluate.return_value = {
            'userAgent': 'Mozilla/5.0 Test User Agent',
            'platform': 'Linux',
            'userAgentData': {
                'platform': 'Linux',
                'mobile': False,
                'brands': '"Chromium";v="133", "Not(A:Brand";v="99"'
            }
        }
        mock_context.cookies.return_value = [
            {'name': 'other_cookie', 'value': 'other_value'}
        ]
        mock_context.new_page.return_value = mock_page
        mock_browser.new_context.return_value = mock_context
        mock_chromium.launch.return_value = mock_browser
        mock_playwright.chromium = mock_chromium

        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': True,
                'turnstile_headless': True
            }.get(key, default)
            mock_setting.save = AsyncMock()

            with patch('patchright.async_api.async_playwright') as mock_pw:
                mock_pw.return_value.__aenter__.return_value = mock_playwright

                with patch('playwright_captcha.ClickSolver') as mock_click_solver:
                    mock_click_solver.return_value.__aenter__.return_value = mock_solver

                    manager = TurnstileSolverManager()
                    result = await manager.solve_cloudflare("https://grok.com")

                    assert result is None

    @pytest.mark.asyncio
    async def test_solve_cloudflare_exception(self):
        """Test solve_cloudflare handles exceptions"""
        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': True
            }.get(key, default)

            with patch('patchright.async_api.async_playwright') as mock_pw:
                mock_pw.side_effect = Exception("Test error")

                manager = TurnstileSolverManager()
                result = await manager.solve_cloudflare("https://grok.com")

                assert result is None
