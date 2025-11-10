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
            from patchright.async_api import async_playwright
            from playwright_captcha import CaptchaType, ClickSolver, FrameworkType

            proxy_url = setting.grok_config.get("proxy_url", "")
            proxy_config = {"server": proxy_url} if proxy_url else None

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=headless,
                    proxy=proxy_config,
                    channel="chrome"
                )
                logger.debug(f"[CF Solver] Browser launched (proxy={proxy_url or 'None'})")
                
                context = await browser.new_context()
                page = await context.new_page()

                # 获取浏览器实际的User-Agent和Sec-Ch-Ua信息
                browser_info = await page.evaluate("""() => ({
                    userAgent: navigator.userAgent,
                    platform: navigator.platform,
                    userAgentData: navigator.userAgentData ? {
                        platform: navigator.userAgentData.platform,
                        mobile: navigator.userAgentData.mobile,
                        brands: navigator.userAgentData.brands.map(b => `"${b.brand}";v="${b.version}"`).join(", ")
                    } : null
                })""")
                
                logger.info(f"[CF Solver] Browser User-Agent: {browser_info['userAgent']}")
                if browser_info.get('userAgentData'):
                    logger.info(f"[CF Solver] Sec-Ch-Ua: {browser_info['userAgentData']['brands']}")
                    logger.info(f"[CF Solver] Sec-Ch-Ua-Platform: {browser_info['userAgentData']['platform']}")
                
                # 保存浏览器指纹信息
                user_agent_data = browser_info.get('userAgentData')
                await setting.save(grok_config={
                    "browser_user_agent": browser_info['userAgent'],
                    "browser_sec_ch_ua": user_agent_data['brands'] if user_agent_data else None,
                    "browser_sec_ch_ua_platform": f'"{user_agent_data["platform"]}"' if user_agent_data else None,
                    "browser_sec_ch_ua_mobile": "?1" if user_agent_data and user_agent_data.get('mobile') else "?0"
                })

                wait_before = setting.grok_config.get("cf_solver_wait_before", 10)
                wait_after = setting.grok_config.get("cf_solver_wait_after", 0)
                max_attempts = setting.grok_config.get("cf_solver_max_attempts", 5)
                attempt_delay = setting.grok_config.get("cf_solver_attempt_delay", 3)
                click_delay = setting.grok_config.get("cf_solver_click_delay", 10)
                checkbox_delay = setting.grok_config.get("cf_solver_checkbox_delay", 8)
                checkbox_attempts = setting.grok_config.get("cf_solver_checkbox_attempts", 15)

                async with ClickSolver(
                    framework=FrameworkType.PLAYWRIGHT,
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

                    await browser.close()

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
