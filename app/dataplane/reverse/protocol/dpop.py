"""DPoP (RFC 9449) protocol layer for console.x.ai — Go→Python port of
chenyme/grok2api PR #853 ``backend/internal/infra/provider/console/dpop.go``.

x.ai now requires DPoP-bound access tokens on console.x.ai ``/v1/responses``
(403 ``unauthorized:dpop-required``). This module is transport-agnostic: all
HTTP is injected via ``post_json_fn`` / ``request_fn`` callables, so callers
own proxies, cookies and retries while this module owns the DPoP protocol.

Semantics mirror the Go original:

* Session cache key ``{base_url}|{credential_id}|{node_id}|{sha256(sso_token)}``,
  LRU-capped at :data:`DPOP_SESSION_CACHE_LIMIT` (4096).
* Concurrent fetches for the same key coalesce (singleflight).
* A session is refreshed when ``expires_at <= now + DPOP_REFRESH_SKEW_MS``
  (20 s) and its lifetime is ``min(expires_in, JWT exp)``.
* The access token's ``cnf.jkt`` must equal the local JWK thumbprint, else the
  session is rejected.
* Proofs are ES256 JWTs (raw ``r||s`` JWS encoding, not DER) with
  ``typ=dpop+jwt`` and claims ``jti/htm/htu/iat/ath``.
* ``do_dpop_request`` retries exactly once on 401 after invalidating the
  session; a token-endpoint failure raises :class:`DPoPTokenEndpointError`
  carrying ``invalidate_clearance`` so callers can refresh CF clearance.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from app.platform.logging.logger import logger

DPOP_SESSION_CACHE_LIMIT = 4096
DPOP_REFRESH_SKEW_MS = 20000
DPOP_DPOP_TOKEN_PATH = "/v1/dpop/token"

_DPOP_TOKEN_MAX_LIFETIME_S = 3600  # Go maxDPoPTokenLifetime = time.Hour
_X_CLUSTER_HEADER = "https://us-east-1.api.x.ai"

_P256_BYTE_WIDTH = 32

PostJsonFn = Callable[
    [str, dict[str, str], dict[str, Any]], Awaitable[tuple[int, dict[str, Any]]]
]
RequestFn = Callable[
    [dict[str, str], bytes | None], Awaitable[tuple[int, bytes, dict[str, str]]]
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DPoPError(Exception):
    """Base DPoP failure. ``invalidate_clearance`` tells callers to refresh CF clearance."""

    def __init__(self, message: str, *, invalidate_clearance: bool = False) -> None:
        super().__init__(message)
        self.invalidate_clearance: bool = invalidate_clearance


class DPoPTokenEndpointError(DPoPError):
    """The ``{base}/v1/dpop/token`` endpoint returned a non-2xx status.

    Mirrors Go's ``dpopTokenEndpointError`` (which is returned as an HTTP
    response to the caller). ``status``/``body`` carry the endpoint's reply;
    ``invalidate_clearance`` is set when a 403 is *not* a definitive account
    block (Go: ``lease.InvalidateClearance()``).
    """

    def __init__(
        self, status: int, body: bytes, *, invalidate_clearance: bool = False
    ) -> None:
        super().__init__(
            f"Console DPoP token endpoint returned {status}",
            invalidate_clearance=invalidate_clearance,
        )
        self.status: int = status
        self.body: bytes = body


# ---------------------------------------------------------------------------
# JWK helpers
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sha256_b64url(data: bytes) -> str:
    return _b64url(hashlib.sha256(data).digest())


def public_dpop_jwk(
    key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey,
) -> dict[str, str]:
    """Build the public JWK ``{kty, crv, x, y}`` for a P-256 key (Go ``publicDPoPJWK``).

    x/y are the raw 32-byte coordinates, unpadded base64url.
    """
    if isinstance(key, ec.EllipticCurvePrivateKey):
        key = key.public_key()
    numbers = key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64url(numbers.x.to_bytes(_P256_BYTE_WIDTH, "big")),
        "y": _b64url(numbers.y.to_bytes(_P256_BYTE_WIDTH, "big")),
    }


def dpop_jwk_thumbprint(public_jwk: dict[str, str]) -> str:
    """RFC 7638 JWK thumbprint (Go ``dpopJWKThumbprint``).

    Canonical JSON is ``{"crv":...,"kty":...,"x":...,"y":...}`` — Go marshals
    with sorted map keys, so ``sort_keys=True`` reproduces ``crv,kty,x,y``.
    """
    canonical = json.dumps(
        {
            "crv": public_jwk["crv"],
            "kty": public_jwk["kty"],
            "x": public_jwk["x"],
            "y": public_jwk["y"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_b64url(canonical.encode("utf-8"))


# ---------------------------------------------------------------------------
# Access token parsing
# ---------------------------------------------------------------------------


def parse_dpop_access_token(token: str) -> tuple[int, str]:
    """Decode a DPoP access token (no verification) → ``(exp, cnf.jkt)``.

    Mirrors Go ``parseDPoPAccessToken``: rejects malformed JWTs, missing or
    non-positive ``exp``, and missing ``cnf.jkt``.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise DPoPError("Console DPoP access token format invalid")
    try:
        payload = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        raise DPoPError("Console DPoP access token payload invalid") from exc
    exp = claims.get("exp")
    cnf = claims.get("cnf")
    jkt = cnf.get("jkt") if isinstance(cnf, dict) else None
    if (
        not isinstance(exp, int)
        or exp <= 0
        or not isinstance(jkt, str)
        or not jkt.strip()
    ):
        raise DPoPError("Console DPoP access token claims invalid")
    return exp, jkt


# ---------------------------------------------------------------------------
# HTU / endpoint helpers
# ---------------------------------------------------------------------------


def dpop_htu(url: str) -> str:
    """DPoP ``htu`` claim: ``scheme://host`` + Go-``EscapedPath`` (``/`` if empty).

    Go's ``encodePath`` escaping keeps ``-_.~ $&+,/:;=@`` and escapes
    everything else (spaces, ``?``, ``%``, non-ASCII); ``quote`` with that
    safe set reproduces it exactly.
    """
    parts = urlsplit(url)
    path = quote(parts.path, safe="$&+,/:;=@")
    if not path:
        path = "/"
    return f"{parts.scheme}://{parts.netloc}{path}"


def console_v1_endpoint(base_url: str, path: str) -> str:
    """Mirror Go ``consoleV1Endpoint``: ``base + /v1 + path``, tolerating a base already ending in ``/v1``."""
    base = base_url.strip().rstrip("/")
    path = "/" + path.strip().lstrip("/")
    if base.endswith("/v1"):
        return base + path.removeprefix("/v1")
    return base + path


def _base_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DPoPSession:
    access_token: str
    private_key: ec.EllipticCurvePrivateKey
    public_jwk: dict[str, str]
    expires_at: int  # epoch ms


def dpop_session_cache_key(
    base_url: str, credential_id: int, node_id: int, sso_token: str
) -> str:
    """Go ``dpopSessionCacheKey``: ``base_url|credential_id|node_id|sha256(sso_token)``."""
    base = base_url.strip().rstrip("/")
    hashed = hashlib.sha256(sso_token.encode("utf-8")).hexdigest()
    return f"{base}|{credential_id}|{node_id}|{hashed}"


class DPoPSessionManager:
    """LRU-capped, singleflight-coalesced cache of :class:`DPoPSession`.

    ``post_json_fn`` performs the actual ``POST {base}/v1/dpop/token`` call;
    ``browser_headers`` are merged into every request (cookies, UA, …) and
    ``is_definitive_block`` classifies 403 bodies (Go
    ``provider.IsDefinitiveAccountBlockBody``). ``browser_headers`` may be a
    callable re-derived at each exchange, mirroring Go's per-request
    ``applyBrowserHeaders`` with the current lease.
    """

    def __init__(
        self,
        post_json_fn: PostJsonFn,
        *,
        browser_headers: dict[str, str] | Callable[[], dict[str, str]] | None = None,
        is_definitive_block: Callable[[str], bool] | None = None,
    ) -> None:
        self._post_json_fn: PostJsonFn = post_json_fn
        self._is_definitive_block: Callable[[str], bool] | None = is_definitive_block
        self.browser_headers: dict[str, str] | Callable[[], dict[str, str]] = (
            browser_headers or {}
        )
        self._cache: OrderedDict[str, DPoPSession] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[DPoPSession]] = {}

    def resolve_browser_headers(self) -> dict[str, str]:
        """Headers for the next token exchange — a callable is resolved fresh."""
        headers = self.browser_headers
        if isinstance(headers, dict):
            return headers.copy()
        return dict(headers())

    # -- cache --------------------------------------------------------------

    def _cached(self, key: str) -> DPoPSession | None:
        session = self._cache.get(key)
        if session is None:
            return None
        if session.expires_at <= int(time.time() * 1000) + DPOP_REFRESH_SKEW_MS:
            _ = self._cache.pop(key, None)
            return None
        _ = self._cache.move_to_end(key)
        return session

    def _store(self, key: str, session: DPoPSession) -> None:
        self._cache[key] = session
        _ = self._cache.move_to_end(key)
        while len(self._cache) > DPOP_SESSION_CACHE_LIMIT:
            self._cache.popitem(last=False)

    def invalidate(self, key: str, access_token: str = "") -> None:
        """Drop a cached session (used on 401).

        Like Go, only removes when ``access_token`` matches (or is empty) so a
        concurrent refresh is never clobbered, and never cancels an in-flight
        fetch — a burst of 401s must stay coalesced on one token exchange.
        """
        session = self._cache.get(key)
        if session is None or (access_token and session.access_token != access_token):
            return
        _ = self._cache.pop(key, None)

    # -- fetch --------------------------------------------------------------

    async def get_or_fetch(
        self, base_url: str, credential_id: int, node_id: int, sso_token: str
    ) -> DPoPSession:
        """Go ``get``: return the cached session or fetch one, coalesced per key."""
        key = dpop_session_cache_key(base_url, credential_id, node_id, sso_token)
        cached = self._cached(key)
        if cached is not None:
            return cached
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch_and_store(key, base_url))
            self._inflight[key] = task
            task.add_done_callback(lambda _done: self._inflight.pop(key, None))
        return await asyncio.shield(task)

    async def _fetch_and_store(self, key: str, base_url: str) -> DPoPSession:
        # Singleflight closure re-check: the cache may have been populated by a
        # concurrent refresh between the outer lookup and this task starting.
        cached = self._cached(key)
        if cached is not None:
            return cached
        session = await self._fetch(base_url)
        self._store(key, session)
        logger.debug("dpop session cached", key=key, expires_at_ms=session.expires_at)
        return session

    async def _fetch(self, base_url: str) -> DPoPSession:
        """Go ``fetchDPoPSession``: mint a fresh P-256 key, exchange it for a
        DPoP-bound access token, and validate the binding.
        """
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_jwk = public_dpop_jwk(private_key)
        endpoint = console_v1_endpoint(base_url, DPOP_DPOP_TOKEN_PATH)
        status, data = await self._post_json_fn(
            endpoint,
            {"Content-Type": "application/json", **self.resolve_browser_headers()},
            {"jwk": public_jwk},
        )
        if status < 200 or status >= 300:
            body_text = (
                data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
            )
            definitive = (
                self._is_definitive_block is not None
                and self._is_definitive_block(body_text)
            )
            raise DPoPTokenEndpointError(
                status,
                body_text.encode("utf-8"),
                invalidate_clearance=status == 403 and not definitive,
            )

        access_token = data.get("access_token")
        token_type = data.get("token_type")
        expires_in = data.get("expires_in")
        if not isinstance(access_token, str) or not access_token.strip():
            raise DPoPError("Console DPoP token response invalid")
        if not isinstance(token_type, str) or token_type.strip().lower() != "dpop":
            raise DPoPError("Console DPoP token response invalid")
        if (
            not isinstance(expires_in, int)
            or expires_in <= 0
            or expires_in > _DPOP_TOKEN_MAX_LIFETIME_S
        ):
            raise DPoPError("Console DPoP token lifetime invalid")

        thumbprint = dpop_jwk_thumbprint(public_jwk)
        token_exp, token_jkt = parse_dpop_access_token(access_token)
        if token_jkt != thumbprint:
            raise DPoPError("Console DPoP token does not match local key")

        now_ms = int(time.time() * 1000)
        expires_at = min(now_ms + expires_in * 1000, token_exp * 1000)
        if expires_at <= now_ms + DPOP_REFRESH_SKEW_MS:
            raise DPoPError("Console DPoP token expired or expiring soon")

        logger.debug(
            "dpop session fetched", endpoint=endpoint, expires_at_ms=expires_at
        )
        return DPoPSession(
            access_token=access_token,
            private_key=private_key,
            public_jwk=public_jwk,
            expires_at=expires_at,
        )


# ---------------------------------------------------------------------------
# Proof signing
# ---------------------------------------------------------------------------


def sign_dpop_proof(
    session: DPoPSession,
    *,
    method: str,
    url: str,
    iat: int | None = None,
    jti: str | None = None,
) -> str:
    """Sign an ES256 DPoP proof JWT (Go ``applyDPoPAuthorization``).

    JWS-encoded signature: the ECDSA DER signature from ``cryptography`` is
    decoded into ``(r, s)`` and re-encoded as the raw 64-byte ``r||s``
    concatenation RFC 9449 requires.
    """
    header = {"typ": "dpop+jwt", "alg": "ES256", "jwk": session.public_jwk}
    claims = {
        "jti": jti if jti is not None else str(uuid.uuid4()),
        "htm": method.upper(),
        "htu": dpop_htu(url),
        "iat": iat if iat is not None else int(time.time()),
        "ath": _sha256_b64url(session.access_token.encode("utf-8")),
    }
    encoded = (
        f"{_b64url(json.dumps(header, sort_keys=True, separators=(',', ':')).encode('utf-8'))}."
        f"{_b64url(json.dumps(claims, sort_keys=True, separators=(',', ':')).encode('utf-8'))}"
    )
    der = session.private_key.sign(encoded.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    raw = r.to_bytes(_P256_BYTE_WIDTH, "big") + s.to_bytes(_P256_BYTE_WIDTH, "big")
    return f"{encoded}.{_b64url(raw)}"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def do_dpop_request(
    manager: DPoPSessionManager,
    *,
    method: str,
    url: str,
    body: bytes | None,
    accept: str | None,
    credential_id: int,
    node_id: int,
    sso_token: str,
    request_fn: RequestFn,
) -> tuple[int, bytes, dict[str, str]]:
    """Go ``doDPoPRequest``: perform one DPoP-authenticated request with a
    single 401 retry against a freshly fetched session.

    Raises :class:`DPoPTokenEndpointError` (with ``invalidate_clearance``)
    when the session cannot be fetched — the token endpoint's status/body are
    the signal the caller should relay (Go returns the endpoint error as the
    HTTP response).
    """
    base_url = _base_of(url)
    key = dpop_session_cache_key(base_url, credential_id, node_id, sso_token)
    for attempt in range(2):
        session = await manager.get_or_fetch(
            base_url, credential_id, node_id, sso_token
        )
        headers = manager.resolve_browser_headers()
        headers["Authorization"] = f"DPoP {session.access_token}"
        headers["DPoP"] = sign_dpop_proof(session, method=method, url=url)
        if body:
            headers["Content-Type"] = "application/json"
        if accept and accept.strip():
            headers["Accept"] = accept
        if urlsplit(url).path.endswith("/responses"):
            headers["x-cluster"] = _X_CLUSTER_HEADER
        status, resp_body, resp_headers = await request_fn(headers, body)
        if status != 401 or attempt > 0:
            return status, resp_body, resp_headers
        manager.invalidate(key, session.access_token)
        logger.info("dpop 401, invalidating session and retrying", key=key)
    raise DPoPError("Console DPoP retry state invalid")
