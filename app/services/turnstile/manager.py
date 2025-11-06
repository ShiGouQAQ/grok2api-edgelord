"""Turnstile Solver Manager - Integration with main application"""
import asyncio
import logging
from typing import Optional
from app.core.config import setting

logger = logging.getLogger(__name__)


class TurnstileSolverManager:
    """Manager for Turnstile Solver service lifecycle"""

    def __init__(self):
        self.app = None
        self.server_task: Optional[asyncio.Task] = None
        self.enabled = setting.grok_config.get("turnstile_enabled", False)
        self.host = setting.grok_config.get("turnstile_host", "127.0.0.1")
        self.port = setting.grok_config.get("turnstile_port", 5072)
        self.headless = setting.grok_config.get("turnstile_headless", True)
        self.browser_type = setting.grok_config.get("turnstile_browser_type", "camoufox")
        self.thread_count = setting.grok_config.get("turnstile_threads", 2)
        self.debug = setting.grok_config.get("turnstile_debug", False)

    async def start(self):
        """Start Turnstile Solver server"""
        # Reload config to get latest values
        self.enabled = setting.grok_config.get("turnstile_enabled", False)
        self.host = setting.grok_config.get("turnstile_host", "127.0.0.1")
        self.port = setting.grok_config.get("turnstile_port", 5072)
        self.headless = setting.grok_config.get("turnstile_headless", True)
        self.browser_type = setting.grok_config.get("turnstile_browser_type", "camoufox")
        self.thread_count = setting.grok_config.get("turnstile_threads", 2)
        self.debug = setting.grok_config.get("turnstile_debug", False)
        
        if not self.enabled:
            logger.info("Turnstile Solver is disabled in configuration")
            return

        try:
            from .api_solver import create_app

            logger.info(f"Starting Turnstile Solver on {self.host}:{self.port}")
            logger.info(f"Browser: {self.browser_type}, Threads: {self.thread_count}, Headless: {self.headless}")

            self.app = create_app(
                headless=self.headless,
                useragent=None,
                debug=self.debug,
                browser_type=self.browser_type,
                thread=self.thread_count,
                proxy_support=False,
                use_random_config=True,
                browser_name=None,
                browser_version=None
            )

            # Run Quart app in background
            self.server_task = asyncio.create_task(
                self._run_server()
            )

            logger.info("Turnstile Solver started successfully")

        except Exception as e:
            logger.error(f"Failed to start Turnstile Solver: {e}")
            raise

    async def _run_server(self):
        """Run the Quart server"""
        try:
            await self.app.run_task(
                host=self.host,
                port=self.port
            )
        except Exception as e:
            logger.error(f"Turnstile Solver server error: {e}")

    async def stop(self):
        """Stop Turnstile Solver server"""
        if self.server_task:
            logger.info("Stopping Turnstile Solver...")
            self.server_task.cancel()
            try:
                await self.server_task
            except asyncio.CancelledError:
                pass
            logger.info("Turnstile Solver stopped")

    async def solve_turnstile(self, url: str, sitekey: str, action: Optional[str] = None) -> Optional[str]:
        """
        Solve Turnstile challenge

        Args:
            url: Target URL
            sitekey: Turnstile sitekey
            action: Optional action parameter

        Returns:
            Turnstile token or None if failed
        """
        # Reload config to get latest values
        self.enabled = setting.grok_config.get("turnstile_enabled", False)
        self.host = setting.grok_config.get("turnstile_host", "127.0.0.1")
        self.port = setting.grok_config.get("turnstile_port", 5072)
        
        if not self.enabled:
            logger.warning("Turnstile Solver is disabled, cannot solve challenge")
            return None

        import aiohttp

        try:
            # Request solving
            async with aiohttp.ClientSession() as session:
                params = {"url": url, "sitekey": sitekey}
                if action:
                    params["action"] = action

                async with session.get(
                    f"http://{self.host}:{self.port}/turnstile",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    result = await resp.json()

                    error_id = result.get("errorId")
                    if error_id is not None and error_id != 0:
                        logger.error(f"Turnstile solve request failed: {result}")
                        return None

                    task_id = result.get("taskId")
                    if not task_id:
                        logger.error("No task ID returned from Turnstile Solver")
                        return None

                # Poll for result
                max_attempts = 60  # 60 seconds timeout
                for _ in range(max_attempts):
                    await asyncio.sleep(1)

                    async with session.get(
                        f"http://{self.host}:{self.port}/result",
                        params={"id": task_id},
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        result = await resp.json()

                        if result.get("status") == "ready":
                            token = result.get("solution", {}).get("token")
                            if token:
                                logger.info(f"Turnstile solved successfully: {token[:20]}...")
                                return token
                        
                        error_id = result.get("errorId")
                        if error_id is not None and error_id != 0:
                            error_msg = result.get("errorDescription", result.get("value", "Unknown error"))
                            logger.error(f"Turnstile solve failed: {error_msg}")
                            return None

                logger.error("Turnstile solve timeout")
                return None

        except Exception as e:
            logger.error(f"Error solving Turnstile: {e}")
            return None


# Global instance
turnstile_manager = TurnstileSolverManager()
