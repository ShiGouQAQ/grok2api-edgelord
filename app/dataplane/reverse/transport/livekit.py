"""LiveKit transport — fetch session token.

``fetch_livekit_token`` POSTs to /rest/livekit/tokens with proxy support.
"""

from typing import Any, Dict

from app.platform.logging.logger import logger
from app.platform.config.snapshot import get_config
from app.platform.errors import UpstreamError
from app.control.proxy.models import (
    ProxyFeedback,
    ProxyFeedbackKind,
    ProxyScope,
    RequestKind,
)
from app.dataplane.proxy import get_proxy_runtime
from app.dataplane.reverse.protocol.xai_livekit import (
    LIVEKIT_TOKEN_URL,
    build_token_request_payload,
)
from app.dataplane.reverse.transport.http import post_json
from app.dataplane.reverse.transport._proxy_feedback import upstream_feedback


# ------------------------------------------------------------------
# Token fetch
# ------------------------------------------------------------------


async def fetch_livekit_token(
    token: str,
    *,
    voice: str = "ara",
    personality: str = "assistant",
    speed: float = 1.0,
    custom_instruction: str = "",
) -> Dict[str, Any]:
    """Fetch a LiveKit session token for *token*.

    Returns the parsed JSON body from /rest/livekit/tokens.
    Raises ``UpstreamError`` on failure.
    """
    cfg = get_config()
    timeout_s = cfg.get_float("voice.timeout", 60.0)

    proxy = await get_proxy_runtime()
    lease = await proxy.acquire(scope=ProxyScope.APP, kind=RequestKind.HTTP)

    payload = build_token_request_payload(
        voice=voice,
        personality=personality,
        speed=speed,
        custom_instruction=custom_instruction,
    )

    try:
        result = await post_json(
            LIVEKIT_TOKEN_URL,
            token,
            payload,
            lease=lease,
            timeout_s=timeout_s,
            origin="https://grok.com",
            referer="https://grok.com/",
        )
    except UpstreamError as exc:
        await proxy.feedback(lease, upstream_feedback(exc))
        raise
    except Exception as exc:
        await proxy.feedback(
            lease, ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
        )
        raise UpstreamError(f"fetch_livekit_token: transport error: {exc}") from exc

    await proxy.feedback(
        lease,
        ProxyFeedback(kind=ProxyFeedbackKind.SUCCESS, status_code=200),
    )
    logger.debug("livekit session token fetched")
    return result


__all__ = ["fetch_livekit_token"]
