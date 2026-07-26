from enum import StrEnum

import orjson

BUILD_BASE = "https://cli-chat-proxy.grok.com/v1"
XAI_FALLBACK = "https://api.x.ai/v1"

_FALLBACK_CAPABLE_OPERATIONS: frozenset[str] = frozenset(
    {
        "responses",
        "compact",
        "videos/generations",
        "videos",
    }
)


class BuildRouteMode(StrEnum):
    AUTO = "auto"  # Super + no bot flag → try Build, 403 → XAI
    BUILD = "build"  # Force Build native path
    XAI = "xai"  # Force XAI fallback path


def inference_base_for_operation(
    route_mode: BuildRouteMode,
    *,
    is_super: bool,
    is_bot: bool = False,
) -> str:
    """Return the upstream base URL based on routing rules.

    AUTO mode routing:
    - Super + not bot → BUILD_BASE (primary)
    - Not super OR bot → XAI_FALLBACK
    BUILD mode → BUILD_BASE
    XAI mode → XAI_FALLBACK
    """
    if route_mode == BuildRouteMode.XAI:
        return XAI_FALLBACK
    if route_mode == BuildRouteMode.AUTO:
        if is_super and not is_bot:
            return BUILD_BASE
        return XAI_FALLBACK
    return BUILD_BASE  # BUILD mode


def should_probe_xai_fallback(
    status: int,
    route_mode: BuildRouteMode,
    is_super: bool,
    operation: str,
) -> bool:
    """Determine if a 403 should trigger fallback probe."""
    return (
        status == 403
        and route_mode == BuildRouteMode.AUTO
        and is_super
        and operation in _FALLBACK_CAPABLE_OPERATIONS
    )


def is_definitive_account_block_body(body: str) -> bool:
    """Check if the response body indicates a permanent account block."""
    try:
        data = orjson.loads(body)
    except Exception:
        return False
    error = data.get("error", data)
    if isinstance(error, dict):
        code = str(error.get("code", ""))
        msg = str(error.get("message", ""))
        text = f"{code} {msg}".lower()
        return any(s in text for s in ("blocked-user", "user is blocked"))
    return False
