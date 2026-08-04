"""HTTP/WebSocket header builders for reverse-proxy requests.

All values are sanitized to ASCII-safe Latin-1 before use.
"""

import base64
import random
import re
import string
import uuid
from typing import Optional
from urllib.parse import urlparse


from app.platform.logging.logger import logger
from app.platform.config.snapshot import get_config
from app.platform.config.browser import (
    BROWSER_SEC_CH_UA,
    BROWSER_SEC_CH_UA_MOBILE,
    BROWSER_SEC_CH_UA_PLATFORM,
)
from app.control.proxy.models import ProxyLease
from app.dataplane.proxy.adapters.profile import ProxyProfile, resolve_proxy_profile

# ---------------------------------------------------------------------------
# Unicode → ASCII normalisation map
# ---------------------------------------------------------------------------

_CHAR_MAP = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00a0": " ",
        "\u2007": " ",
        "\u202f": " ",
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\ufeff": "",
    }
)


def _sanitize(value: Optional[str], *, field: str, strip_spaces: bool = False) -> str:
    raw = "" if value is None else str(value)
    out = raw.translate(_CHAR_MAP)
    out = re.sub(r"\s+", "", out) if strip_spaces else out.strip()
    out = out.encode("latin-1", errors="ignore").decode("latin-1")
    if out != raw:
        logger.debug(
            "header sanitized: field={} original_len={} sanitized_len={}",
            field,
            len(raw),
            len(out),
        )
    return out


# ---------------------------------------------------------------------------
# Statsig / request-id generation
# ---------------------------------------------------------------------------


def _statsig_id() -> str:
    """Generate a Statsig evaluation fallback ID.

    The real browser's fetch interceptor tries to evaluate Statsig gates for
    each request.  When the Statsig SDK is not yet initialised (headless,
    first paint, etc.) it catches the error and falls back to::

        btoa("x1:" + error.toString())

    The server accepts this fallback.  We reproduce the exact format with
    varied error messages to avoid a static fingerprint.
    """
    cfg = get_config()
    if cfg.get_bool("features.dynamic_statsig", False):
        if random.choice((True, False)):
            rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
            msg = f"x1:TypeError: Cannot read properties of null (reading 'children[\\'{rand}\\']')"
        else:
            rand = "".join(random.choices(string.ascii_lowercase, k=10))
            msg = (
                f"x1:TypeError: Cannot read properties of undefined (reading '{rand}')"
            )
        return base64.b64encode(msg.encode()).decode()
    return (
        "ZTpUeXBlRXJyb3I6IENhbm5vdCByZWFkIHByb3BlcnRpZXMgb2YgdW5kZWZpbmVkIChyZWFkaW5nICdjaGls"
        "ZE5vZGVzJyk="
    )


# ---------------------------------------------------------------------------
# Client-hints helpers
# ---------------------------------------------------------------------------


def _major_version(browser: Optional[str], ua: Optional[str]) -> Optional[str]:
    for src in (browser or "", ua or ""):
        m = re.search(r"(\d{2,3})", src)
        if m:
            return m.group(1)
    return None


def _platform(ua: str) -> Optional[str]:
    u = ua.lower()
    if "windows" in u:
        return "Windows"
    if "mac os x" in u or "macintosh" in u:
        return "macOS"
    if "android" in u:
        return "Android"
    if "iphone" in u or "ipad" in u:
        return "iOS"
    if "linux" in u:
        return "Linux"
    return None


def _arch(ua: str) -> Optional[str]:
    u = ua.lower()
    if "aarch64" in u or "arm" in u:
        return "arm"
    if "x86_64" in u or "x64" in u or "win64" in u or "intel" in u:
        return "x86"
    return None


def _client_hints(browser: Optional[str], ua: Optional[str]) -> dict[str, str]:
    """Build Sec-CH-UA client hints headers.

    Uses centralized browser config constants for consistent fingerprinting.
    Returns empty dict for non-chromium browsers.
    """
    b = (browser or "").lower()
    u = (ua or "").lower()
    is_chromium = any(k in b for k in ("chrome", "chromium", "edge", "brave")) or any(
        k in u for k in ("chrome", "chromium", "edg")
    )
    if not is_chromium or "firefox" in u or ("safari" in u and "chrome" not in u):
        return {}

    return {
        "Sec-Ch-Ua": BROWSER_SEC_CH_UA,
        "Sec-Ch-Ua-Mobile": BROWSER_SEC_CH_UA_MOBILE,
        "Sec-Ch-Ua-Platform": BROWSER_SEC_CH_UA_PLATFORM,
        "Sec-Ch-Ua-Model": "",
        "Sec-Ch-Ua-Arch": _arch(ua or "") or "x86",
        "Sec-Ch-Ua-Bitness": "64",
    }


# ---------------------------------------------------------------------------
# Lease resolution
# ---------------------------------------------------------------------------


def _resolve_profile(lease: ProxyLease | None) -> ProxyProfile:
    return resolve_proxy_profile(lease)


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------


def build_sso_cookie(
    sso_token: str,
    *,
    lease: ProxyLease | None = None,
    cf_cookies: str | None = None,
    cf_clearance: str | None = None,
) -> str:
    """Build the Cookie header value for an SSO-authenticated request.

    When *cf_clearance* is not provided, the value is resolved from the lease's
    cf_cookies profile or falls back to the config's cf_clearance (supporting
    both ``proxy.clearance.cf_clearance`` and legacy ``proxy.cf_clearance`` paths).
    Historical bug: earlier v2.0 releases silently defaulted cf_clearance to the
    empty string when not passed explicitly, causing Cookies without a CF
    clearance token and immediate 403 from Cloudflare on every grok.com call.
    """
    tok = sso_token[4:] if sso_token.startswith("sso=") else sso_token
    tok = _sanitize(tok, field="sso_token", strip_spaces=True)

    cookie = f"sso={tok}; sso-rw={tok}"
    profile = _resolve_profile(lease)
    eff_cookies = _sanitize(
        cf_cookies if cf_cookies is not None else profile.cf_cookies, field="cf_cookies"
    )
    eff_clearance = _sanitize(
        cf_clearance if cf_clearance is not None else profile.cf_clearance,
        field="cf_clearance",
        strip_spaces=True,
    )

    if eff_clearance and eff_cookies:
        if re.search(r"(?:^|;\s*)cf_clearance=", eff_cookies):
            eff_cookies = re.sub(
                r"(^|;\s*)cf_clearance=[^;]*",
                r"\1cf_clearance=" + eff_clearance,
                eff_cookies,
                count=1,
            )
        else:
            eff_cookies = f"{eff_cookies.rstrip('; ')}; cf_clearance={eff_clearance}"
    elif eff_clearance:
        eff_cookies = f"cf_clearance={eff_clearance}"

    if eff_cookies:
        cookie += f"; {eff_cookies}"
    return cookie


def build_http_headers(
    cookie_token: str,
    *,
    content_type: Optional[str] = None,
    origin: Optional[str] = None,
    referer: Optional[str] = None,
    lease: ProxyLease | None = None,
) -> dict[str, str]:
    """Build headers for a standard HTTP reverse-proxy request."""
    profile = _resolve_profile(lease)
    raw_ua = profile.user_agent
    ua = _sanitize(raw_ua, field="user_agent")
    browser = profile.browser
    org = _sanitize(origin or "https://grok.com", field="origin")
    ref = _sanitize(referer or "https://grok.com/", field="referer")

    ct = content_type or "application/json"
    if ct == "application/json":
        accept = "*/*"
        fd = "empty"
    elif ct in ("image/jpeg", "image/png", "video/mp4", "video/webm"):
        accept = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        )
        fd = "document"
    else:
        accept = "*/*"
        fd = "empty"

    org_host = urlparse(org).hostname
    ref_host = urlparse(ref).hostname
    site = "same-origin" if org_host and org_host == ref_host else "same-site"

    headers: dict[str, str] = {
        "Accept": accept,
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Baggage": (
            "sentry-environment=production,"
            "sentry-release=d6add6fb0460641fd482d767a335ef72b9b6abb8,"
            "sentry-public_key=b311e0f2690c81f25e2c4cf6d4f7ce1c"
        ),
        "Content-Type": ct,
        "Origin": org,
        "Priority": "u=1, i",
        "Referer": ref,
        "Sec-Fetch-Dest": fd,
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": site,
        "User-Agent": ua,
        "x-statsig-id": _statsig_id(),
        "x-xai-request-id": str(uuid.uuid4()),
    }
    headers.update(_client_hints(browser, raw_ua))
    headers["Cookie"] = build_sso_cookie(cookie_token, lease=lease)

    logger.debug("http headers built: header_count={}", len(headers))
    return headers


def build_ws_headers(
    token: Optional[str] = None,
    *,
    origin: Optional[str] = None,
    extra: Optional[dict[str, str]] = None,
    lease: ProxyLease | None = None,
) -> dict[str, str]:
    """Build headers for a WebSocket upgrade request."""
    profile = _resolve_profile(lease)
    raw_ua = profile.user_agent
    ua = _sanitize(raw_ua, field="user_agent")
    browser = profile.browser
    org = _sanitize(origin or "https://grok.com", field="origin")

    headers: dict[str, str] = {
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Origin": org,
        "Pragma": "no-cache",
        "User-Agent": ua,
    }
    headers.update(_client_hints(browser, raw_ua))
    if token:
        headers["Cookie"] = build_sso_cookie(token, lease=lease)
    if extra:
        headers.update(extra)
    return headers


def build_console_headers(
    sso_token: str,
    *,
    lease: ProxyLease | None = None,
    content_type: str = "application/json",
) -> dict[str, str]:
    """Build headers for console.x.ai/v1/responses requests.

    抓包确认的认证方式：
    - Authorization: Bearer anonymous  （固定值）
    - Cookie: sso=<token>; sso-rw=<token>; cf_clearance=...  （身份 + CF clearance）

    cf_clearance 从 proxy lease 的 clearance profile 中获取（与 grok.com 共用同一套机制）。
    """
    tok = sso_token[4:] if sso_token.startswith("sso=") else sso_token
    tok = _sanitize(tok, field="sso_token", strip_spaces=True)

    # 复用现有 clearance profile（cf_clearance / user_agent）
    profile = _resolve_profile(lease)
    ua = _sanitize(profile.user_agent, field="user_agent")
    cf_clearance = _sanitize(
        profile.cf_clearance, field="cf_clearance", strip_spaces=True
    )

    cookie = f"sso={tok}; sso-rw={tok}"
    eff_cookies = _sanitize(profile.cf_cookies, field="cf_cookies")
    if cf_clearance and eff_cookies:
        if re.search(r"(?:^|;\s*)cf_clearance=", eff_cookies):
            eff_cookies = re.sub(
                r"(^|;\s*)cf_clearance=[^;]*",
                r"\1cf_clearance=" + cf_clearance,
                eff_cookies,
                count=1,
            )
        else:
            eff_cookies = f"{eff_cookies.rstrip('; ')}; cf_clearance={cf_clearance}"
    elif cf_clearance:
        eff_cookies = f"cf_clearance={cf_clearance}"
    if eff_cookies:
        cookie += f"; {eff_cookies}"

    headers: dict[str, str] = {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Authorization": "Bearer anonymous",
        "Content-Type": content_type,
        "Cookie": cookie,
        "Origin": "https://console.x.ai",
        "Priority": "u=1, i",
        "Referer": "https://console.x.ai/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": ua
        or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        "x-cluster": "https://us-east-1.api.x.ai",
    }
    headers.update(_client_hints(profile.browser, profile.user_agent))
    return headers


def build_build_headers(
    access_token: str,
    agent_id: str,
    *,
    client_version: str | None = None,
    client_identifier: str | None = None,
    session_id: str | None = None,
    model: str | None = None,
    turn_idx: str | None = None,
    is_stream: bool = True,
    is_trace: bool = True,
) -> dict[str, str]:
    """Build headers for Grok Build (grok-shell) requests.

    Port of Go adapter.go applyHeaders() + doResponseRequest():
    - is_stream=True  → Accept: text/event-stream, Accept-Encoding: identity
    - is_stream=False → Accept: application/json
    - is_trace=True   → inference headers (x-authenticateresponse, x-grok-req-id,
                        x-grok-agent-id, traceparent, session_id)
    - is_trace=False  → control-plane headers (x-userid, x-email)
    """
    cfg = get_config()
    client_version = client_version or cfg.get_str("build.client_version", "0.2.119")
    client_identifier = client_identifier or cfg.get_str(
        "build.client_identifier", "grok-shell"
    )
    token_auth = cfg.get_str("build.token_auth", "xai-grok-cli")
    user_agent = cfg.get_str("build.user_agent", "")
    tok = _sanitize(access_token, field="access_token", strip_spaces=True)
    aid = _sanitize(agent_id, field="agent_id", strip_spaces=True)

    headers: dict[str, str] = {
        "Authorization": f"Bearer {tok}",
        "x-xai-token-auth": token_auth,
        "x-grok-client-version": client_version,
        "x-grok-client-identifier": client_identifier,
        "x-grok-client-mode": "headless",
        "Content-Type": "application/json",
        "User-Agent": user_agent or f"grok-shell/{client_version} (linux; x86_64)",
        "Accept": "text/event-stream" if is_stream else "application/json",
        "Accept-Encoding": "identity" if is_stream else "gzip",
    }

    # Inference headers — only when trace=True (Go: applyHeaders trace param)
    if is_trace:
        trace_id = uuid.uuid4().hex[:16]
        span_id = uuid.uuid4().hex[:8]
        headers["x-authenticateresponse"] = "authenticate-response"
        headers["x-grok-agent-id"] = aid
        headers["x-grok-req-id"] = str(uuid.uuid4())
        headers["traceparent"] = f"00-{trace_id}-{span_id}-01"

        if session_id:
            sid = _sanitize(session_id, field="session_id", strip_spaces=True)
            headers["x-grok-session-id"] = sid
            headers["x-grok-conv-id"] = sid
    # Control-plane headers (Go: x-userid, x-email) omitted — not used by current callers

    if model:
        headers["x-grok-model-override"] = _sanitize(model, field="model")
    if turn_idx:
        headers["x-grok-turn-idx"] = _sanitize(turn_idx, field="turn_idx")

    return headers


__all__ = [
    "build_http_headers",
    "build_sso_cookie",
    "build_ws_headers",
    "build_console_headers",
    "build_build_headers",
]
