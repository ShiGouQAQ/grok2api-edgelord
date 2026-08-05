"""SSO token → provider inference.

XAI console SSO JWTs carry only a ``session_id`` claim and no ``exp``
(verified against the production account table: 5673/5673 active tokens
matched). Grok Web SSO cookies decode to JWTs with ``exp`` (or are opaque
strings). This lets import paths tag console tokens with
``provider="grok_console"`` so the refresh pipeline uses
console.x.ai /v1/usage (DPoP) instead of grok.com/rest/rate-limits, which
rejects console tokens with 401 (2026-08-05 production burst).
"""

from __future__ import annotations

import base64
import json
from typing import Any


def decode_sso_jwt(token: str) -> dict[str, Any] | None:
    """Decode a JWT payload without verifying the signature.

    Returns the payload dict, or ``None`` when *token* is not a JWT (fewer
    than two dotted segments or undecodable payload).
    """
    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        data = base64.urlsafe_b64decode(parts[1] + "==")
        payload = json.loads(data)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


_CONSOLE_ONLY_CLAIMS = frozenset(("session_id", "sid"))


def infer_provider(token: str) -> str | None:
    """Infer the account provider from the token shape.

    Returns ``"grok_console"`` for a console SSO JWT (no ``exp``, claims
    restricted to session identifiers), ``None`` otherwise — the caller then
    keeps its default (``grok_web``).
    """
    payload = decode_sso_jwt(token)
    if payload is None:
        return None
    if "exp" in payload:
        return None
    if set(payload) <= _CONSOLE_ONLY_CLAIMS:
        return "grok_console"
    return None
