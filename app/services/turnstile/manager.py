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
            from app.services.grok.browser_config import (
                BROWSER_USER_AGENT,
                PLAYWRIGHT_CHANNEL,
                PLAYWRIGHT_VIEWPORT,
                get_browser_fingerprint,
                get_playwright_headers
            )

            proxy_url = setting.grok_config.get("proxy_url", "")
            proxy_config = {"server": proxy_url} if proxy_url else None

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=headless,
                    proxy=proxy_config,
                    channel=PLAYWRIGHT_CHANNEL
                )
                logger.debug(f"[CF Solver] Browser launched (proxy={proxy_url or 'None'})")

                # 使用集中配置的浏览器指纹
                context = await browser.new_context(
                    user_agent=BROWSER_USER_AGENT,
                    viewport=PLAYWRIGHT_VIEWPORT,
                    extra_http_headers=get_playwright_headers()
                )
                page = await context.new_page()

                # 保存集中配置的浏览器指纹
                fingerprint_config = get_browser_fingerprint()
                logger.info(f"[CF Solver] 使用固定指纹 - UA: {fingerprint_config['browser_user_agent']}")
                logger.info(f"[CF Solver] Sec-Ch-Ua: {fingerprint_config['browser_sec_ch_ua']}")
                logger.info(f"[CF Solver] Platform: {fingerprint_config['browser_sec_ch_ua_platform']}")
                await setting.save(grok_config=fingerprint_config)

                wait_before = setting.grok_config.get("cf_solver_wait_before", 10)
                wait_after = setting.grok_config.get("cf_solver_wait_after", 0)
                max_attempts = setting.grok_config.get("cf_solver_max_attempts", 5)
                attempt_delay = setting.grok_config.get("cf_solver_attempt_delay", 3)
                click_delay = setting.grok_config.get("cf_solver_click_delay", 10)
                checkbox_delay = setting.grok_config.get("cf_solver_checkbox_delay", 8)
                checkbox_attempts = setting.grok_config.get("cf_solver_checkbox_attempts", 15)

                async with ClickSolver(
                    framework=FrameworkType.PATCHRIGHT,
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

                    # 获取所有 Cloudflare 相关的 cookies
                    cf_clearance = next((c['value'] for c in cookies if c['name'] == 'cf_clearance'), None)
                    cf_cookies = {c['name']: c['value'] for c in cookies if c['name'].startswith('cf_') or c['name'].startswith('__cf')}

                    logger.info(f"[CF Solver] 获取到的 Cloudflare cookies: {list(cf_cookies.keys())}")

                    await browser.close()

                    if cf_clearance:
                        logger.info(f"[CF Solver] Success: cf_clearance={cf_clearance[:20]}...")
                        # 构建完整的 cookie 字符串
                        cookie_string = "; ".join([f"{name}={value}" for name, value in cf_cookies.items()])
                        return cookie_string

                    logger.error("[CF Solver] cf_clearance cookie not found")
                    return None

        except Exception as e:
            logger.error(f"[CF Solver] Failed: {e}", exc_info=True)
            return None


# Global instance
turnstile_manager = TurnstileSolverManager()
