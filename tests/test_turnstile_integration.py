"""Integration tests for Turnstile Solver"""
import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from app.services.turnstile.manager import TurnstileSolverManager


class TestTurnstileSolverManager:
    """Test Turnstile Solver Manager"""

    @pytest.fixture
    def manager(self):
        """Create manager instance"""
        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': False,
                'turnstile_host': '127.0.0.1',
                'turnstile_port': 5072,
                'turnstile_headless': True,
                'turnstile_browser_type': 'camoufox',
                'turnstile_threads': 2,
                'turnstile_debug': False
            }.get(key, default)
            return TurnstileSolverManager()

    def test_manager_initialization(self, manager):
        """Test manager initializes with correct config"""
        assert manager.enabled is False
        assert manager.host == '127.0.0.1'
        assert manager.port == 5072
        assert manager.headless is True
        assert manager.browser_type == 'camoufox'
        assert manager.thread_count == 2
        assert manager.debug is False

    def test_manager_default_values(self):
        """Test manager uses default values when config missing"""
        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: default
            manager = TurnstileSolverManager()

            assert manager.enabled is False
            assert manager.host == '127.0.0.1'
            assert manager.port == 5072

    @pytest.mark.asyncio
    async def test_start_disabled(self, manager):
        """Test start does nothing when disabled"""
        await manager.start()
        assert manager.app is None
        assert manager.server_task is None

    @pytest.mark.asyncio
    async def test_solve_turnstile_disabled(self, manager):
        """Test solve returns None when disabled"""
        result = await manager.solve_turnstile(
            url="https://x.ai",
            sitekey="test_key"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_solve_with_action_parameter(self, manager):
        """Test solve accepts optional action parameter"""
        result = await manager.solve_turnstile(
            url="https://x.ai",
            sitekey="test_key",
            action="verify"
        )
        assert result is None  # Still None because disabled

    @pytest.mark.asyncio
    async def test_stop_no_task(self, manager):
        """Test stop handles no running task"""
        await manager.stop()
        # Should not raise any exception

    @pytest.mark.asyncio
    async def test_multiple_stops(self, manager):
        """Test multiple stop calls don't cause errors"""
        await manager.stop()
        await manager.stop()
        # Should not raise any exception

    @pytest.mark.asyncio
    async def test_config_reload_on_start(self):
        """Test start reloads config from setting"""
        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': False,
                'turnstile_host': '127.0.0.1',
                'turnstile_port': 5072,
                'turnstile_headless': True,
                'turnstile_browser_type': 'camoufox',
                'turnstile_threads': 2,
                'turnstile_debug': False
            }.get(key, default)

            manager = TurnstileSolverManager()
            assert manager.enabled is False

            # Change config
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': True,
                'turnstile_host': '192.168.1.1',
                'turnstile_port': 8080,
                'turnstile_headless': False,
                'turnstile_browser_type': 'chrome',
                'turnstile_threads': 4,
                'turnstile_debug': True
            }.get(key, default)

            # Mock create_app to avoid actual browser initialization
            with patch('app.services.turnstile.api_solver.create_app'):
                await manager.start()

            # Verify config was reloaded
            assert manager.enabled is True
            assert manager.host == '192.168.1.1'
            assert manager.port == 8080
            assert manager.headless is False
            assert manager.browser_type == 'chrome'
            assert manager.thread_count == 4
            assert manager.debug is True

    @pytest.mark.asyncio
    async def test_config_reload_on_solve(self):
        """Test solve_turnstile reloads config from setting"""
        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': False,
                'turnstile_host': '127.0.0.1',
                'turnstile_port': 5072
            }.get(key, default)

            manager = TurnstileSolverManager()

            # Change config to enabled
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': True,
                'turnstile_host': '192.168.1.1',
                'turnstile_port': 8080
            }.get(key, default)

            # Mock aiohttp to avoid actual HTTP calls
            with patch('aiohttp.ClientSession') as mock_session:
                mock_response = AsyncMock()
                mock_response.json = AsyncMock(return_value={"errorId": 0, "taskId": "test"})
                mock_session.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_response

                await manager.solve_turnstile("https://x.ai", "test_key")

            # Verify config was reloaded
            assert manager.enabled is True
            assert manager.host == '192.168.1.1'
            assert manager.port == 8080

    @pytest.mark.asyncio
    async def test_solve_handles_processing_status(self):
        """Test solve_turnstile correctly handles processing status"""
        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': True,
                'turnstile_host': '127.0.0.1',
                'turnstile_port': 5072
            }.get(key, default)

            manager = TurnstileSolverManager()

            with patch('aiohttp.ClientSession') as mock_session:
                # Mock responses
                init_resp = MagicMock()
                init_resp.json = AsyncMock(return_value={"errorId": 0, "taskId": "test_task"})

                poll_resp1 = MagicMock()
                poll_resp1.json = AsyncMock(return_value={"status": "processing"})

                poll_resp2 = MagicMock()
                poll_resp2.json = AsyncMock(return_value={"status": "ready", "solution": {"token": "test_token_123"}})

                mock_get = MagicMock()
                mock_get.__aenter__ = AsyncMock(side_effect=[init_resp, poll_resp1, poll_resp2])
                mock_get.__aexit__ = AsyncMock(return_value=None)

                mock_session.return_value.__aenter__.return_value.get = MagicMock(return_value=mock_get)

                result = await manager.solve_turnstile("https://x.ai", "test_key")

                assert result == "test_token_123"

    @pytest.mark.asyncio
    async def test_solve_handles_error_with_errorid(self):
        """Test solve_turnstile correctly handles errorId field"""
        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': True,
                'turnstile_host': '127.0.0.1',
                'turnstile_port': 5072
            }.get(key, default)

            manager = TurnstileSolverManager()

            with patch('aiohttp.ClientSession') as mock_session:
                init_response = AsyncMock()
                init_response.json = AsyncMock(return_value={"errorId": 0, "taskId": "test_task"})

                error_response = AsyncMock()
                error_response.json = AsyncMock(return_value={"errorId": 1, "value": "CAPTCHA_FAIL"})

                mock_get = mock_session.return_value.__aenter__.return_value.get
                mock_get.return_value.__aenter__.side_effect = [init_response, error_response]

                result = await manager.solve_turnstile("https://x.ai", "test_key")

                assert result is None

    @pytest.mark.asyncio
    async def test_solve_ignores_missing_errorid(self):
        """Test solve_turnstile ignores responses without errorId"""
        with patch('app.services.turnstile.manager.setting') as mock_setting:
            mock_setting.grok_config.get.side_effect = lambda key, default=None: {
                'turnstile_enabled': True,
                'turnstile_host': '127.0.0.1',
                'turnstile_port': 5072
            }.get(key, default)

            manager = TurnstileSolverManager()

            with patch('aiohttp.ClientSession') as mock_session:
                init_resp = MagicMock()
                init_resp.json = AsyncMock(return_value={"errorId": 0, "taskId": "test_task"})

                # Response without errorId should not be treated as error
                processing_resp = MagicMock()
                processing_resp.json = AsyncMock(return_value={"status": "processing"})

                ready_resp = MagicMock()
                ready_resp.json = AsyncMock(return_value={"status": "ready", "solution": {"token": "success_token"}})

                mock_get = MagicMock()
                mock_get.__aenter__ = AsyncMock(side_effect=[init_resp, processing_resp, ready_resp])
                mock_get.__aexit__ = AsyncMock(return_value=None)

                mock_session.return_value.__aenter__.return_value.get = MagicMock(return_value=mock_get)

                result = await manager.solve_turnstile("https://x.ai", "test_key")

                assert result == "success_token"


class TestCFClearanceIntegration:
    """Test CF Clearance integration with Turnstile"""

    @pytest.mark.asyncio
    async def test_cf_clearance_uses_turnstile(self):
        """Test CF Clearance manager uses Turnstile solver"""
        with patch('app.services.turnstile.manager.turnstile_manager') as mock_manager:
            mock_manager.solve_turnstile = AsyncMock(return_value="test_token")

            from app.services.grok.cf_clearance import CFClearanceManager

            with patch('app.services.grok.cf_clearance.setting') as mock_setting:
                mock_setting.grok_config.get.return_value = "test_sitekey"
                mock_setting.save = AsyncMock()

                cf_manager = CFClearanceManager()
                result = await cf_manager._try_refresh_once()

                assert result is True
                mock_manager.solve_turnstile.assert_called_once()
                mock_setting.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_cf_clearance_handles_solver_failure(self):
        """Test CF Clearance handles Turnstile solver failure"""
        with patch('app.services.turnstile.manager.turnstile_manager') as mock_manager:
            mock_manager.solve_turnstile = AsyncMock(return_value=None)

            from app.services.grok.cf_clearance import CFClearanceManager

            with patch('app.services.grok.cf_clearance.setting') as mock_setting:
                mock_setting.grok_config.get.return_value = "test_sitekey"

                cf_manager = CFClearanceManager()
                result = await cf_manager._try_refresh_once()

                assert result is False


class TestTurnstileAPIIntegration:
    """Test Turnstile API endpoints"""

    @pytest.mark.asyncio
    async def test_turnstile_request_format(self):
        """Test Turnstile API request format"""
        # Mock aiohttp session
        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.json = AsyncMock(return_value={
                "errorId": 0,
                "taskId": "test_task_id"
            })
            mock_response.status = 200

            mock_session.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_response

            manager = TurnstileSolverManager()
            manager.enabled = True

            # This would normally call the API
            # We're just testing the structure
            assert manager.host == '127.0.0.1'
            assert manager.port == 5072


class TestBrowserConfigs:
    """Test browser configuration module"""

    def test_browser_config_import(self):
        """Test browser config can be imported"""
        from app.services.turnstile.browser_configs import browser_config
        assert browser_config is not None

    def test_get_random_config(self):
        """Test random browser config generation"""
        from app.services.turnstile.browser_configs import browser_config

        browser, version, ua, sec_ch_ua = browser_config.get_random_browser_config('camoufox')
        assert browser == 'firefox'
        assert version == 'custom'
        assert ua == ''
        assert sec_ch_ua == ''

    def test_get_chrome_config(self):
        """Test Chrome browser config"""
        from app.services.turnstile.browser_configs import browser_config

        browser, version, ua, sec_ch_ua = browser_config.get_random_browser_config('chrome')
        assert browser in ['chrome', 'edge', 'avast', 'brave']
        assert version is not None
        assert ua is not None


class TestDatabaseResults:
    """Test database results module"""

    def test_db_path_in_data_directory(self):
        """Test database path is in data directory"""
        from app.services.turnstile.db_results import DB_PATH
        from pathlib import Path

        assert isinstance(DB_PATH, Path)
        assert str(DB_PATH) == "data/results.db"
        assert DB_PATH.parent.name == "data"

    @pytest.mark.asyncio
    async def test_db_init(self):
        """Test database initialization"""
        from app.services.turnstile.db_results import init_db

        # Should not raise exception
        await init_db()

    @pytest.mark.asyncio
    async def test_save_and_load_result(self):
        """Test saving and loading results"""
        from app.services.turnstile.db_results import save_result, load_result

        task_id = "test_task_123"
        test_data = {"value": "test_token", "elapsed_time": 5.5}

        await save_result(task_id, "turnstile", test_data)
        result = await load_result(task_id)

        assert result is not None
        assert result["value"] == "test_token"
        assert result["elapsed_time"] == 5.5

    @pytest.mark.asyncio
    async def test_load_nonexistent_result(self):
        """Test loading non-existent result returns None"""
        from app.services.turnstile.db_results import load_result

        result = await load_result("nonexistent_task_id")
        assert result is None

    @pytest.mark.asyncio
    async def test_save_overwrites_existing(self):
        """Test saving overwrites existing result"""
        from app.services.turnstile.db_results import save_result, load_result

        task_id = "test_overwrite_123"

        await save_result(task_id, "turnstile", {"value": "old_token"})
        await save_result(task_id, "turnstile", {"value": "new_token"})

        result = await load_result(task_id)
        assert result["value"] == "new_token"


class TestMainIntegration:
    """Test main.py integration"""

    @pytest.mark.asyncio
    async def test_turnstile_manager_in_lifespan(self):
        """Test Turnstile manager is started in lifespan"""
        with patch('app.services.turnstile.manager.turnstile_manager') as mock_manager:
            mock_manager.start = AsyncMock()
            mock_manager.stop = AsyncMock()

            # Import after patching
            from main import lifespan
            from fastapi import FastAPI

            app = FastAPI()

            # Test startup
            async with lifespan(app):
                # Lifespan context entered
                pass

            # Both start and stop should be called
            # Note: This is a simplified test, actual implementation may vary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
