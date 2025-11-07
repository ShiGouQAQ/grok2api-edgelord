"""Turnstile Solver Manager - Integration with main application"""
import asyncio
import logging
from typing import Optional
from app.core.config import setting

logger = logging.getLogger(__name__)


class TurnstileSolverManager:
    """Manager for Cloudflare clearance solving"""

    def __init__(self):
        pass

    async def solve_cloudflare(self, url: str) -> Optional[str]:
        """
        Solve Cloudflare Interstitial and get cf_clearance cookie

        Args:
            url: Target URL

        Returns:
            cf_clearance cookie value or None if failed
        """
        enabled = setting.grok_config.get("turnstile_enabled", False)
        headless = setting.grok_config.get("turnstile_headless", True)

        if not enabled:
            logger.warning("[CF Solver] Disabled in config")
            return None

        logger.info(f"[CF Solver] Starting (headless={headless}, url={url})")

        try:
            from camoufox import AsyncCamoufox
            from playwright_captcha import CaptchaType, ClickSolver, FrameworkType
            from playwright_captcha.utils.camoufox_add_init_script.add_init_script import get_addon_path
            import os

            addon_path = get_addon_path()

            proxy_url = setting.grok_config.get("proxy_url", "")
            proxy_config = {"server": proxy_url} if proxy_url else None

            async with AsyncCamoufox(
                headless=headless,
                geoip=True,
                humanize=True,
                i_know_what_im_doing=True,
                config={'forceScopeAccess': True},
                disable_coop=True,
                main_world_eval=True,
                addons=[os.path.abspath(addon_path)],
                proxy=proxy_config
            ) as browser:
                logger.debug(f"[CF Solver] Browser launched (proxy={proxy_url or 'None'})")
                context = await browser.new_context()
                page = await context.new_page()

                wait_before = setting.grok_config.get("cf_solver_wait_before", 10)
                wait_after = setting.grok_config.get("cf_solver_wait_after", 0)
                max_attempts = setting.grok_config.get("cf_solver_max_attempts", 5)
                attempt_delay = setting.grok_config.get("cf_solver_attempt_delay", 3)
                click_delay = setting.grok_config.get("cf_solver_click_delay", 10)
                checkbox_delay = setting.grok_config.get("cf_solver_checkbox_delay", 8)
                checkbox_attempts = setting.grok_config.get("cf_solver_checkbox_attempts", 15)

                async with ClickSolver(
                    framework=FrameworkType.CAMOUFOX,
                    page=page,
                    max_attempts=max_attempts,
                    attempt_delay=attempt_delay
                ) as solver:
                    await page.goto(url, timeout=120000)
                    logger.debug(f"[CF Solver] Waiting {wait_before}s before solving...")
                    await asyncio.sleep(wait_before)

                    await solver.solve_captcha(
                        captcha_container=page,
                        captcha_type=CaptchaType.CLOUDFLARE_INTERSTITIAL,
                        expected_content_selector='div[class*="@container/mainview"]',
                        solve_click_delay=click_delay,
                        wait_checkbox_delay=checkbox_delay,
                        wait_checkbox_attempts=checkbox_attempts
                    )

                    logger.debug(f"[CF Solver] Waiting {wait_after}s after solving...")
                    await asyncio.sleep(wait_after)

                    cookies = await context.cookies()
                    cf_clearance = next((c['value'] for c in cookies if c['name'] == 'cf_clearance'), None)

                    if cf_clearance:
                        logger.info(f"[CF Solver] Success: cf_clearance={cf_clearance[:20]}...")
                        return cf_clearance

                    logger.error("[CF Solver] cf_clearance cookie not found")
                    return None

        except Exception as e:
            logger.error(f"[CF Solver] Failed: {e}", exc_info=True)
            return None


# Global instance
turnstile_manager = TurnstileSolverManager()
