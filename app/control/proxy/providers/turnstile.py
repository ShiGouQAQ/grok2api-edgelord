"""Turnstile-backed local clearance provider.

Uses patchright (Playwright fork) + playwright-captcha to solve
Cloudflare Interstitial challenges locally via browser automation.
"""

import asyncio
from urllib.parse import urlparse

from app.platform.config.snapshot import get_config
from app.platform.logging.logger import logger
from app.platform.config.browser import (
    BROWSER_SEC_CH_UA,
    BROWSER_SEC_CH_UA_MOBILE,
    BROWSER_SEC_CH_UA_PLATFORM,
    BROWSER_USER_AGENT,
    PLAYWRIGHT_CHANNEL,
    PLAYWRIGHT_VIEWPORT,
)

from ..models import ClearanceBundle, ClearanceBundleState, ClearanceMode


def _extract_cf_cookies(cookies: list[dict]) -> tuple[str, str, str]:
    """Extract CF-related cookies and build cookie string.

    Returns:
        Tuple of (cookie_string, cf_clearance_value, cf_clearance_domain)
    """
    cf_clearance_domain = ""
    cf_cookies = {}
    for c in cookies:
        name = c.get("name", "")
        if name.startswith("cf_") or name.startswith("__cf"):
            cf_cookies[name] = c.get("value", "")
            if name == "cf_clearance":
                cf_clearance_domain = c.get("domain", "")
    cf_clearance = cf_cookies.get("cf_clearance", "")
    cookie_string = "; ".join(f"{name}={value}" for name, value in cf_cookies.items())
    return cookie_string, cf_clearance, cf_clearance_domain


# Domain-specific CSS selectors for verifying page content after CF challenge.
# Key: hostname, Value: CSS selector expected on the target page.
_CONTENT_SELECTORS: dict[str, str] = {
    "grok.com": 'div[class*="@container/mainview"]',
    "console.x.ai": "body > div.isolate.flex.h-full.w-full.flex-col",
}


def _expected_content_selector(target_url: str) -> str | None:
    """Return the expected content selector for the given target URL."""
    host = (urlparse(target_url).hostname or "").lower()
    return _CONTENT_SELECTORS.get(host)


class TurnstileClearanceProvider:
    """Refresh CF clearance bundles via local browser automation.

    Uses patchright + playwright-captcha to solve Cloudflare Interstitial
    challenges, which may have better success rates than FlareSolverr for
    certain CF configurations.
    """

    # Class-level browser instance for reuse (optional optimization)
    _browser_instance = None
    _browser_lock = asyncio.Lock()

    async def refresh_bundle(
        self,
        *,
        affinity_key: str,
        proxy_url: str,
        target_url: str = "https://grok.com",
    ) -> ClearanceBundle | None:
        """Solve CF challenge and return a ClearanceBundle.

        Args:
            affinity_key: Proxy affinity key for bundle association
            proxy_url: Proxy URL to use (empty string for direct)
            target_url: Target URL to solve CF challenge for

        Returns:
            ClearanceBundle on success, None on failure
        """
        cfg = get_config()
        mode = ClearanceMode.parse(cfg.get_str("proxy.clearance.mode", "none"))
        if mode != ClearanceMode.TURNSTILE:
            return None

        result = await self._solve(
            proxy_url=proxy_url,
            target_url=target_url,
        )
        if not result:
            logger.warning(
                "turnstile clearance refresh failed: affinity={} proxy={} target={}",
                affinity_key,
                proxy_url or "<direct>",
                target_url,
            )
            return None

        host = result.get("clearance_host", "grok.com")
        return ClearanceBundle(
            bundle_id=f"turnstile:{affinity_key}@{host}",
            cf_cookies=result.get("cookies", ""),
            user_agent=result.get("user_agent", ""),
            affinity_key=affinity_key,
            clearance_host=host,
            state=ClearanceBundleState.VALID,
        )

    async def _solve(
        self,
        *,
        proxy_url: str,
        target_url: str,
    ) -> dict[str, str] | None:
        """Solve CF challenge using patchright + playwright-captcha.

        Returns:
            Dict with cookies, user_agent, clearance_host on success
        """
        target = target_url.strip() or "https://grok.com"
        cfg = get_config()

        # Read Turnstile-specific config
        headless = cfg.get_bool("proxy.clearance.turnstile_headless", True)
        wait_before = cfg.get_int("proxy.clearance.cf_solver_wait_before", 10)
        wait_after = cfg.get_int("proxy.clearance.cf_solver_wait_after", 0)
        max_attempts = cfg.get_int("proxy.clearance.cf_solver_max_attempts", 5)
        attempt_delay = cfg.get_int("proxy.clearance.cf_solver_attempt_delay", 3)
        click_delay = cfg.get_int("proxy.clearance.cf_solver_click_delay", 10)
        checkbox_delay = cfg.get_int("proxy.clearance.cf_solver_checkbox_delay", 8)
        checkbox_attempts = cfg.get_int(
            "proxy.clearance.cf_solver_checkbox_attempts", 15
        )

        try:
            from patchright.async_api import async_playwright
            from playwright_captcha import CaptchaType, ClickSolver, FrameworkType
        except ImportError as exc:
            logger.error(
                "turnstile provider requires patchright "
                "and playwright-captcha: error={}",
                exc,
            )
            return None

        proxy_config = {"server": proxy_url} if proxy_url else None

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=headless,
                    proxy=proxy_config,
                    channel=PLAYWRIGHT_CHANNEL,
                    args=[
                        "--disable-gpu",
                        "--disable-software-rasterizer",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                    ],
                )
                logger.debug(
                    "turnstile browser launched: headless={} proxy={}",
                    headless,
                    proxy_url or "None",
                )

                context = await browser.new_context(
                    user_agent=BROWSER_USER_AGENT,
                    viewport=PLAYWRIGHT_VIEWPORT,
                    extra_http_headers={
                        "sec-ch-ua": BROWSER_SEC_CH_UA,
                        "sec-ch-ua-mobile": BROWSER_SEC_CH_UA_MOBILE,
                        "sec-ch-ua-platform": BROWSER_SEC_CH_UA_PLATFORM,
                    },
                )
                page = await context.new_page()

                logger.info(
                    "turnstile solving: ua={ua} sec-ch-ua={sec_ch_ua}",
                    ua=BROWSER_USER_AGENT,
                    sec_ch_ua=BROWSER_SEC_CH_UA,
                )

                async with ClickSolver(
                    framework=FrameworkType.PATCHRIGHT,
                    page=page,
                    max_attempts=max_attempts,
                    attempt_delay=attempt_delay,
                ) as solver:
                    await page.goto(target, timeout=120000)
                    logger.debug("turnstile waiting {}s before solving...", wait_before)
                    await asyncio.sleep(wait_before)

                    content_selector = _expected_content_selector(target)
                    logger.debug(
                        "turnstile content selector: target={} selector={}",
                        target,
                        content_selector,
                    )
                    await solver.solve_captcha(
                        captcha_container=page,
                        captcha_type=CaptchaType.CLOUDFLARE_INTERSTITIAL,
                        expected_content_selector=content_selector,
                        solve_click_delay=click_delay,
                        wait_checkbox_delay=checkbox_delay,
                        wait_checkbox_attempts=checkbox_attempts,
                    )

                    logger.debug("turnstile waiting {}s after solving...", wait_after)
                    await asyncio.sleep(wait_after)

                    # Capture the browser's actual URL before closing
                    final_url = page.url

                cookies = await context.cookies()
                cookie_string, cf_clearance, cf_domain = _extract_cf_cookies(cookies)

                await browser.close()

                if not cf_clearance:
                    logger.error("turnstile cf_clearance cookie not found")
                    return None

                host = (
                    urlparse(final_url).hostname
                    or urlparse(target).hostname
                    or "grok.com"
                ).lower()
                target_host = (urlparse(target).hostname or "").lower()

                # Warn if the browser was redirected to a different domain
                if host != target_host:
                    logger.warning(
                        "turnstile redirect detected: target={} final_url={} "
                        "cf_clearance may be for wrong domain",
                        target,
                        final_url,
                    )

                logger.info(
                    "turnstile success: cf_clearance={}... host={} cf_domain={} final_url={}",
                    cf_clearance[:20],
                    host,
                    cf_domain,
                    final_url,
                )

                return {
                    "cookies": cookie_string,
                    "user_agent": BROWSER_USER_AGENT,
                    "clearance_host": host,
                }

        except Exception as exc:
            logger.error("turnstile solve failed: error={}", exc, exc_info=True)
            return None


__all__ = ["TurnstileClearanceProvider"]
