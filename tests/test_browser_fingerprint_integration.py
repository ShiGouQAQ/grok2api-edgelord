"""Integration tests for browser fingerprint synchronization"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from app.services.turnstile.manager import TurnstileSolverManager
from app.services.grok.statsig import get_dynamic_headers


class TestBrowserFingerprintIntegration:
    """Test browser fingerprint synchronization between Solver and curl_cffi"""

    @pytest.mark.asyncio
    async def test_solver_saves_browser_fingerprint(self):
        """Test Cloudflare Solver saves browser fingerprint to config"""
        mock_playwright = AsyncMock()
        mock_chromium = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_solver = AsyncMock()

        # Mock browser fingerprint data
        mock_page.evaluate.return_value = {
            'userAgent': 'Mozilla/5.0 (X11; Linux x86_64) Chrome/133.0.0.0',
            'platform': 'Linux',
            'userAgentData': {
                'platform': 'Linux',
                'mobile': False,
                'brands': '"Chromium";v="133", "Not(A:Brand";v="99"'
            }
        }
        mock_context.cookies.return_value = [
            {'name': 'cf_clearance', 'value': 'test_clearance'}
        ]
        mock_context.new_page.return_value = mock_page
        mock_browser.new_context.return_value = mock_context
        mock_chromium.launch.return_value = mock_browser
        mock_playwright.chromium = mock_chromium

        saved_config = {}

        async def mock_save(grok_config=None, **kwargs):
            if grok_config:
                saved_config.update(grok_config)

        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': True,
                'turnstile_headless': True
            }.get(key, default)
            mock_setting.save = mock_save

            with patch('patchright.async_api.async_playwright') as mock_pw:
                mock_pw.return_value.__aenter__.return_value = mock_playwright

                with patch('playwright_captcha.ClickSolver') as mock_click_solver:
                    mock_click_solver.return_value.__aenter__.return_value = mock_solver

                    manager = TurnstileSolverManager()
                    result = await manager.solve_cloudflare("https://grok.com")

                    assert result == 'cf_clearance=test_clearance'
                    assert 'browser_user_agent' in saved_config
                    assert 'browser_sec_ch_ua' in saved_config
                    assert 'browser_sec_ch_ua_platform' in saved_config
                    assert 'browser_sec_ch_ua_mobile' in saved_config

    @pytest.mark.asyncio
    async def test_fingerprint_consistency_between_solver_and_requests(self):
        """Test fingerprint consistency between Solver and curl_cffi requests"""
        # Simulate Solver saving fingerprint
        test_fingerprint = {
            'browser_user_agent': 'Mozilla/5.0 (X11; Linux x86_64) Chrome/133.0.0.0',
            'browser_sec_ch_ua': '"Chromium";v="133", "Not(A:Brand";v="99"',
            'browser_sec_ch_ua_platform': '"Linux"',
            'browser_sec_ch_ua_mobile': '?0'
        }

        with patch('app.services.grok.statsig.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'dynamic_statsig': False,
                'x_statsig_id': 'test_id',
                **test_fingerprint
            }.get(key, default)

            headers = get_dynamic_headers()

            # Verify headers match saved fingerprint
            assert headers['User-Agent'] == test_fingerprint['browser_user_agent']
            assert headers['Sec-Ch-Ua'] == test_fingerprint['browser_sec_ch_ua']
            assert headers['Sec-Ch-Ua-Platform'] == test_fingerprint['browser_sec_ch_ua_platform']
            assert headers['Sec-Ch-Ua-Mobile'] == test_fingerprint['browser_sec_ch_ua_mobile']

    def test_no_regression_in_existing_headers(self):
        """Test existing headers are not broken by fingerprint changes"""
        with patch('app.services.grok.statsig.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'dynamic_statsig': False,
                'x_statsig_id': 'test_id'
            }.get(key, default)

            headers = get_dynamic_headers()

            # Verify critical existing headers still present
            assert headers['Origin'] == 'https://grok.com'
            assert headers['Accept'] == '*/*'
            assert headers['Accept-Language'] == 'zh-CN,zh;q=0.9'
            assert headers['Sec-Fetch-Mode'] == 'cors'
            assert headers['Sec-Fetch-Site'] == 'same-origin'
            assert 'x-xai-request-id' in headers
            assert 'Baggage' in headers

    @pytest.mark.asyncio
    async def test_solver_handles_missing_useragentdata(self):
        """Test Solver handles browsers without userAgentData API"""
        mock_playwright = AsyncMock()
        mock_chromium = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_solver = AsyncMock()

        # Mock browser without userAgentData
        mock_page.evaluate.return_value = {
            'userAgent': 'Mozilla/5.0 Test',
            'platform': 'Linux',
            'userAgentData': None
        }
        mock_context.cookies.return_value = [
            {'name': 'cf_clearance', 'value': 'test_clearance'}
        ]
        mock_context.new_page.return_value = mock_page
        mock_browser.new_context.return_value = mock_context
        mock_chromium.launch.return_value = mock_browser
        mock_playwright.chromium = mock_chromium

        saved_config = {}

        async def mock_save(grok_config=None, **kwargs):
            if grok_config:
                saved_config.update(grok_config)

        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': True,
                'turnstile_headless': True
            }.get(key, default)
            mock_setting.save = mock_save

            with patch('patchright.async_api.async_playwright') as mock_pw:
                mock_pw.return_value.__aenter__.return_value = mock_playwright

                with patch('playwright_captcha.ClickSolver') as mock_click_solver:
                    mock_click_solver.return_value.__aenter__.return_value = mock_solver

                    manager = TurnstileSolverManager()
                    result = await manager.solve_cloudflare("https://grok.com")

                    assert result == 'cf_clearance=test_clearance'
                    # 现在使用固定的浏览器指纹配置，而不是从浏览器动态获取
                    assert saved_config['browser_user_agent'] == 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'
                    assert saved_config['browser_sec_ch_ua'] == '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"'
                    assert saved_config['browser_sec_ch_ua_platform'] == '"Windows"'
