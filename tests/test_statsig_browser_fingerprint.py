"""Tests for statsig browser fingerprint synchronization"""
import pytest
from unittest.mock import patch, MagicMock
from app.services.grok.statsig import get_dynamic_headers


class TestStatsigBrowserFingerprint:
    """Test browser fingerprint synchronization in statsig"""

    def test_get_dynamic_headers_with_browser_fingerprint(self):
        """Test headers use browser fingerprint from config"""
        with patch('app.services.grok.statsig.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'dynamic_statsig': False,
                'x_statsig_id': 'test_statsig_id',
                'browser_user_agent': 'Mozilla/5.0 (X11; Linux x86_64) Chrome/133.0.0.0',
                'browser_sec_ch_ua': '"Chromium";v="133", "Not(A:Brand";v="99"',
                'browser_sec_ch_ua_mobile': '?0',
                'browser_sec_ch_ua_platform': '"Linux"'
            }.get(key, default)

            headers = get_dynamic_headers()

            assert headers['User-Agent'] == 'Mozilla/5.0 (X11; Linux x86_64) Chrome/133.0.0.0'
            assert headers['Sec-Ch-Ua'] == '"Chromium";v="133", "Not(A:Brand";v="99"'
            assert headers['Sec-Ch-Ua-Mobile'] == '?0'
            assert headers['Sec-Ch-Ua-Platform'] == '"Linux"'

    def test_get_dynamic_headers_fallback_to_defaults(self):
        """Test headers fallback to defaults when browser fingerprint not available"""
        with patch('app.services.grok.statsig.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'dynamic_statsig': False,
                'x_statsig_id': 'test_statsig_id'
            }.get(key, default)

            headers = get_dynamic_headers()

            assert 'Mozilla/5.0' in headers['User-Agent']
            assert 'Chrome/133.0.0.0' in headers['User-Agent']
            assert 'Sec-Ch-Ua' in headers
            assert headers['Sec-Ch-Ua-Mobile'] == '?0'
            assert headers['Sec-Ch-Ua-Platform'] == '"macOS"'

    def test_get_dynamic_headers_with_dynamic_statsig(self):
        """Test headers with dynamic statsig generation"""
        with patch('app.services.grok.statsig.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'dynamic_statsig': True,
                'browser_user_agent': 'Mozilla/5.0 Test'
            }.get(key, default)

            headers = get_dynamic_headers()

            assert 'x-statsig-id' in headers
            assert headers['User-Agent'] == 'Mozilla/5.0 Test'

    def test_get_dynamic_headers_content_type_for_upload(self):
        """Test Content-Type changes for upload endpoint"""
        with patch('app.services.grok.statsig.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'dynamic_statsig': False,
                'x_statsig_id': 'test_id'
            }.get(key, default)

            headers = get_dynamic_headers("/rest/app-chat/upload-file")

            assert headers['Content-Type'] == 'text/plain;charset=UTF-8'

    def test_get_dynamic_headers_required_fields(self):
        """Test all required headers are present"""
        with patch('app.services.grok.statsig.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'dynamic_statsig': False,
                'x_statsig_id': 'test_id'
            }.get(key, default)

            headers = get_dynamic_headers()

            required_headers = [
                'Accept', 'Accept-Language', 'Accept-Encoding', 'Content-Type',
                'Connection', 'Origin', 'Priority', 'User-Agent', 'Sec-Ch-Ua',
                'Sec-Ch-Ua-Mobile', 'Sec-Ch-Ua-Platform', 'Sec-Fetch-Dest',
                'Sec-Fetch-Mode', 'Sec-Fetch-Site', 'Baggage', 'x-statsig-id',
                'x-xai-request-id'
            ]

            for header in required_headers:
                assert header in headers, f"Missing required header: {header}"
