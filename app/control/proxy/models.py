"""Control-plane proxy domain models."""

from enum import IntEnum, StrEnum
from typing import Self

from pydantic import BaseModel


class ProxyScope(StrEnum):
    APP = "app"  # grok.com API calls
    BUILD = "build"  # Grok Build (proxy-pool tunnels rotate per request)
    ASSET = "asset"  # static asset / CDN fetches


class RequestKind(StrEnum):
    HTTP = "http"
    WEBSOCKET = "websocket"
    GRPC = "grpc"


class EgressMode(StrEnum):
    DIRECT = "direct"  # no proxy
    SINGLE_PROXY = "single_proxy"  # one fixed proxy URL
    PROXY_POOL = "proxy_pool"  # rotate from a pool
    MIHOMO = "mihomo"  # Mihomo proxy manager


class ClearanceMode(StrEnum):
    NONE = "none"  # no CF clearance required
    MANUAL = "manual"  # operator-supplied cf_cookies
    FLARESOLVERR = "flaresolverr"  # maintained by FlareSolverr
    TURNSTILE = "turnstile"  # local browser automation via playwright-captcha

    @classmethod
    def parse(cls, value: str | Self) -> Self:
        if isinstance(value, cls):
            return value
        normalized = str(value or "").strip().lower()
        if not normalized:
            return cls.NONE
        return cls(normalized)


class EgressNodeState(IntEnum):
    HEALTHY = 0
    DEGRADED = 1
    UNHEALTHY = 2


class ClearanceBundleState(IntEnum):
    VALID = 0
    STALE = 1
    INVALID = 2


class ProxyFeedbackKind(StrEnum):
    SUCCESS = "success"
    CHALLENGE = "challenge"  # CF JS challenge / captcha (solvable)
    NODE_BANNED = "node_banned"  # IP banned by CF (need node switch, not solve)
    UNAUTHORIZED = "unauthorized"  # 401 on proxy auth
    FORBIDDEN = "forbidden"  # 403 not CF-related
    RATE_LIMITED = "rate_limited"  # 429
    UPSTREAM_5XX = "upstream_5xx"
    TRANSPORT_ERROR = "transport_error"


class EgressNode(BaseModel):
    node_id: str
    proxy_url: str | None = None  # None → direct
    scope: ProxyScope = ProxyScope.APP
    state: EgressNodeState = EgressNodeState.HEALTHY
    health: float = 1.0
    failure_count: int = 0  # consecutive failures; reset by success
    inflight: int = 0
    last_used: int | None = None  # ms


class ClearanceBundle(BaseModel):
    bundle_id: str
    cf_cookies: str = ""
    user_agent: str = ""
    state: ClearanceBundleState = ClearanceBundleState.VALID
    affinity_key: str = ""  # associates bundle with an egress node
    clearance_host: str = "grok.com"
    last_refresh_at: int | None = None  # ms


class ProxyLease(BaseModel):
    lease_id: str
    proxy_url: str | None = None
    cf_cookies: str = ""
    user_agent: str = ""
    clearance_host: str = "grok.com"
    scope: ProxyScope = ProxyScope.APP
    kind: RequestKind = RequestKind.HTTP
    acquired_at: int = 0  # ms
    # Build + proxy_pool + non-sticky: force a fresh tunnel per request so
    # the exit IP rotates (Go 75f4f7a7 request.Close = true).
    fresh_tunnel: bool = False

    @property
    def has_proxy(self) -> bool:
        return bool(self.proxy_url)


class ProxyFeedback(BaseModel):
    kind: ProxyFeedbackKind
    status_code: int | None = None
    reason: str = ""
    retry_after_ms: int | None = None
    # Go lease.InvalidateClearance(): the associated clearance bundle is bad
    # (DPoP token endpoint 403 on a non-definitive block). Kind stays FORBIDDEN
    # (account-level semantics) but the bundle must still be invalidated so the
    # next acquire() re-solves instead of reusing stale cf_clearance.
    invalidate_clearance: bool = False


__all__ = [
    "ProxyScope",
    "RequestKind",
    "EgressMode",
    "ClearanceMode",
    "EgressNodeState",
    "ClearanceBundleState",
    "ProxyFeedbackKind",
    "EgressNode",
    "ClearanceBundle",
    "ProxyLease",
    "ProxyFeedback",
]
