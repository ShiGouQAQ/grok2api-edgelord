"""Shared helper: map an UpstreamError to the correct ProxyFeedbackKind.

All transport modules (assets, media, livekit, imagine_ws …) use this so the
mapping stays consistent and clearance bundles are properly invalidated.

Rules
-----
401  → UNAUTHORIZED  (invalidates clearance bundle)
403  → CHALLENGE only if solvable CF markers detected
       NODE_BANNED if IP banned markers detected (need node switch, not solve)
       else FORBIDDEN
429  → RATE_LIMITED
≥500 → UPSTREAM_5XX
else → TRANSPORT_ERROR

Note: xAI uses Cloudflare CDN, so all responses have ``server: cloudflare``
and ``cf-ray`` headers.  We must inspect the *body* to decide whether a 403
is a real CF challenge (HTML) or an account-level error (JSON).

Two categories of CF blocking:
1. Solvable CF challenge (need cf_clearance solve): challenge-platform, just a moment
2. IP banned (need node switch, NOT solve): Attention Required!, DDoS protection, You have been blocked
"""

from app.platform.errors import UpstreamError
from app.control.proxy.models import ProxyFeedback, ProxyFeedbackKind

# Solvable CF challenge markers — need cf_clearance solve
_CF_CHALLENGE_MARKERS = (
    "challenge-platform",
    "just a moment",
    "cf.errors.css",
)

# IP banned markers — need node switch, NOT cf_clearance solve
# These indicate the proxy IP is fully blocked by Cloudflare
_CF_BANNED_MARKERS = (
    "attention required!",
    "ddos protection by cloudflare",
    "you have been blocked",
)


def _is_cf_challenge(body: str) -> bool:
    """Return *True* if *body* looks like a solvable Cloudflare challenge page."""
    lower = body.lower()[:500]
    return (
        any(m in lower for m in _CF_CHALLENGE_MARKERS)
        or "<!doctype html>" in lower[:200]
    )


def _is_node_banned(body: str) -> bool:
    """Return *True* if *body* indicates the proxy IP is banned by Cloudflare."""
    lower = body.lower()[:500]
    return any(m in lower for m in _CF_BANNED_MARKERS)


def upstream_feedback(exc: UpstreamError) -> ProxyFeedback:
    """Return a ``ProxyFeedback`` for an ``UpstreamError`` response."""
    status = exc.status or 0
    if status == 401:
        kind = ProxyFeedbackKind.UNAUTHORIZED
    elif status == 403:
        body = getattr(exc, "body", "") or ""
        if _is_node_banned(body):
            kind = ProxyFeedbackKind.NODE_BANNED
        elif _is_cf_challenge(body):
            kind = ProxyFeedbackKind.CHALLENGE
        else:
            kind = ProxyFeedbackKind.FORBIDDEN
    elif status == 429:
        kind = ProxyFeedbackKind.RATE_LIMITED
    elif status >= 500:
        kind = ProxyFeedbackKind.UPSTREAM_5XX
    else:
        kind = ProxyFeedbackKind.TRANSPORT_ERROR
    return ProxyFeedback(kind=kind, status_code=status or None)


__all__ = ["upstream_feedback"]
