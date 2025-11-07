"""Turnstile Solver Manager - Integration with main application"""
import asyncio
import logging
from typing import Optional
from app.core.config import setting

logger = logging.getLogger(__name__)


class TurnstileSolverManager:
    """Manager for Cloudflare clearance solving"""

    def __init__(self):
        self.enabled = setting.grok_config.get("turnstile_enabled", False)
        self.headless = setting.grok_config.get("turnstile_headless", True)
        self.browser_type = setting.grok_config.get("turnstile_browser_type", "chromium")

    async def solve_cloudflare(self, url: str) -> Optional[str]:
        """
        Solve Cloudflare Interstitial and get cf_clearance cookie

        Args:
            url: Target URL

        Returns:
            cf_clearance cookie value or None if failed
        """
        self.enabled = setting.grok_config.get("turnstile_enabled", False)
        self.headless = setting.grok_config.get("turnstile_headless", True)

        if not self.enabled:
            logger.warning("[CF Solver] Disabled in config")
            return None

        logger.info(f"[CF Solver] Starting (headless={self.headless}, url={url})")

        try:
            from camoufox import AsyncCamoufox
            from playwright_captcha import CaptchaType, ClickSolver, FrameworkType
            from playwright_captcha.utils.camoufox_add_init_script.add_init_script import get_addon_path
            import os

            addon_path = get_addon_path()

            async with AsyncCamoufox(
                headless=self.headless,
                geoip=True,
                humanize=True,
                i_know_what_im_doing=True,
                config={'forceScopeAccess': True},
                disable_coop=True,
                main_world_eval=True,
                addons=[os.path.abspath(addon_path)]
            ) as browser:
                logger.debug("[CF Solver] Browser launched")
                context = await browser.new_context()
                page = await context.new_page()

                async with ClickSolver(framework=FrameworkType.CAMOUFOX, page=page) as solver:
                    await page.goto(url, timeout=120000)
                    await asyncio.sleep(5)

                    await solver.solve_captcha(
                        captcha_container=page,
                        captcha_type=CaptchaType.CLOUDFLARE_INTERSTITIAL,
                        expected_content_selector="body"
                    )

                    await asyncio.sleep(5)

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
