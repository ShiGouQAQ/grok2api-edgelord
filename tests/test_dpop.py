"""Tests for the DPoP (RFC 9449) protocol layer — port of chenyme/grok2api PR #853 console/dpop.go.

All network access is injected (post_json_fn / request_fn) — nothing here touches the wire.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import uuid
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from app.dataplane.reverse.protocol.dpop import (
    DPOP_SESSION_CACHE_LIMIT,
    DPoPError,
    DPoPSession,
    DPoPSessionManager,
    DPoPTokenEndpointError,
    do_dpop_request,
    dpop_htu,
    dpop_jwk_thumbprint,
    dpop_session_cache_key,
    parse_dpop_access_token,
    public_dpop_jwk,
    sign_dpop_proof,
)


# ---------------------------------------------------------------------------
# Reference implementations (independent of the module under test)
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _ref_thumbprint(jwk: dict[str, str]) -> str:
    """RFC 7638 thumbprint computed with an explicitly ordered canonical JSON
    (crv, kty, x, y — the order mandated by the RFC) — independent of the module."""
    canonical = json.dumps(
        {"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"], "y": jwk["y"]},
        separators=(",", ":"),
    ).encode()
    return _b64url(hashlib.sha256(canonical).digest())


def _make_access_token(
    jwk: dict[str, str], *, exp: int | None = None, jkt: str | None = None
) -> str:
    """Build a fake DPoP access token (JWT) whose cnf.jkt defaults to the thumbprint of `jwk`."""
    payload = {
        "exp": exp if exp is not None else int(time.time()) + 3600,
        "cnf": {"jkt": jkt or _ref_thumbprint(jwk)},
    }
    header = {"alg": "ES256", "typ": "at+jwt"}

    def part(obj: dict[str, Any]) -> str:
        return _b64url(json.dumps(obj, separators=(",", ":"), sort_keys=True).encode())

    return f"{part(header)}.{part(payload)}.signature"


class FakeTokenEndpoint:
    """Injected post_json_fn stand-in: records calls, serves scripted responses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.status = 200
        self.body: dict[str, Any] | None = None
        self.gate: asyncio.Event | None = None
        self.started = 0

    async def __call__(
        self,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any],
        lease: Any | None = None,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append((url, dict(headers), json_body))
        self.started += 1
        if self.gate is not None:
            self.gate.set()
            await asyncio.sleep(0.05)
        if self.body is not None:
            return self.status, self.body
        jwk = json_body["jwk"]
        return self.status, {
            "access_token": _make_access_token(jwk),
            "token_type": "DPoP",
            "expires_in": 3600,
        }


class FakeRequestFn:
    """Injected request_fn stand-in: serves a scripted list of (status, body, headers) responses."""

    def __init__(self, responses: list[tuple[int, bytes, dict[str, str]]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[dict[str, str], bytes | None]] = []

    async def __call__(
        self, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes, dict[str, str]]:
        self.calls.append((dict(headers), body))
        return self.responses.pop(0)


def _manager(
    endpoint: FakeTokenEndpoint | None = None, **kwargs: Any
) -> DPoPSessionManager:
    endpoint = endpoint or FakeTokenEndpoint()
    return DPoPSessionManager(
        endpoint,
        browser_headers={"cookie": "sso=test-cookie", "user-agent": "grok2api-test"},
        **kwargs,
    )


def _session(**kwargs: Any) -> DPoPSession:
    key = ec.generate_private_key(ec.SECP256R1())
    return DPoPSession(
        access_token=kwargs.get("access_token", "session-token"),
        private_key=kwargs.get("private_key", key),
        public_jwk=kwargs.get("public_jwk", public_dpop_jwk(key)),
        expires_at=kwargs.get("expires_at", int(time.time() * 1000) + 3600_000),
    )


# ---------------------------------------------------------------------------
# JWK / thumbprint
# ---------------------------------------------------------------------------


def test_public_dpop_jwk_shape_and_encoding() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    jwk = public_dpop_jwk(key)
    assert jwk == {"kty": "EC", "crv": "P-256", "x": jwk["x"], "y": jwk["y"]}
    assert jwk["kty"] == "EC"
    assert jwk["crv"] == "P-256"
    # x/y are unpadded b64url of exactly 32 bytes (P-256 coordinate width)
    assert len(_b64decode(jwk["x"])) == 32
    assert len(_b64decode(jwk["y"])) == 32
    assert "=" not in jwk["x"] and "=" not in jwk["y"]
    assert "+" not in jwk["x"] and "/" not in jwk["x"]


def test_dpop_jwk_thumbprint_matches_reference() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    jwk = public_dpop_jwk(key)
    # Reference: RFC 7638 canonical JSON is {"crv":...,"kty":...,"x":...,"y":...} → sha256 → b64url
    expected = _ref_thumbprint(jwk)
    assert dpop_jwk_thumbprint(jwk) == expected
    # Deterministic and 43-char b64url sha256
    assert dpop_jwk_thumbprint(jwk) == dpop_jwk_thumbprint(jwk)
    assert len(dpop_jwk_thumbprint(jwk)) == 43


def test_dpop_jwk_thumbprint_is_roundtrip_stable_via_public_key() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    assert dpop_jwk_thumbprint(public_dpop_jwk(key)) == dpop_jwk_thumbprint(
        public_dpop_jwk(key.public_key())
    )


# ---------------------------------------------------------------------------
# HTU
# ---------------------------------------------------------------------------


def test_dpop_htu_empty_path_uses_slash() -> None:
    assert dpop_htu("https://console.x.ai") == "https://console.x.ai/"
    assert dpop_htu("https://console.x.ai?foo=1") == "https://console.x.ai/"


def test_dpop_htu_escapes_special_chars_go_style() -> None:
    # Go encodePath escapes everything except alnum, -_.~, $&+,/:;=@ (and '?' escapes too)
    assert dpop_htu("https://console.x.ai/v1/a b") == "https://console.x.ai/v1/a%20b"
    assert (
        dpop_htu("https://console.x.ai/v1/responses")
        == "https://console.x.ai/v1/responses"
    )
    # space + unicode + percent sign all escaped; sub-delims preserved
    assert dpop_htu("https://x.ai/v1/naïve path") == "https://x.ai/v1/na%C3%AFve%20path"
    assert dpop_htu("https://x.ai/v1/a%2Fb") == "https://x.ai/v1/a%252Fb"
    assert (
        dpop_htu("https://x.ai/v1/a:b@c,d;e=f&g+h$i")
        == "https://x.ai/v1/a:b@c,d;e=f&g+h$i"
    )


def test_dpop_htu_keeps_host_and_port() -> None:
    assert (
        dpop_htu("https://console.x.ai:8443/v1/x") == "https://console.x.ai:8443/v1/x"
    )


# ---------------------------------------------------------------------------
# Access token parsing
# ---------------------------------------------------------------------------


def test_parse_dpop_access_token_ok() -> None:
    jwk = public_dpop_jwk(ec.generate_private_key(ec.SECP256R1()))
    exp = int(time.time()) + 300
    token = _make_access_token(jwk, exp=exp)
    parsed_exp, jkt = parse_dpop_access_token(token)
    assert parsed_exp == exp
    assert jkt == _ref_thumbprint(jwk)


def test_parse_dpop_access_token_malformed() -> None:
    with pytest.raises(DPoPError):
        parse_dpop_access_token("not-a-jwt")
    with pytest.raises(DPoPError):
        parse_dpop_access_token("a.b")
    # missing exp / missing cnf.jkt / non-positive exp all rejected (Go: "claims 无效")
    with pytest.raises(DPoPError):
        parse_dpop_access_token(
            _make_access_token(
                public_dpop_jwk(ec.generate_private_key(ec.SECP256R1())), exp=0
            )
        )


def test_parse_dpop_access_token_missing_jkt_rejected() -> None:
    jwk = public_dpop_jwk(ec.generate_private_key(ec.SECP256R1()))
    token = _make_access_token(jwk)
    no_cnf = (
        token.split(".")[0]
        + "."
        + _b64url(
            json.dumps({"exp": int(time.time()) + 300}, separators=(",", ":")).encode()
        )
        + "."
        + token.split(".")[2]
    )
    with pytest.raises(DPoPError):
        parse_dpop_access_token(no_cnf)


# ---------------------------------------------------------------------------
# Proof signing
# ---------------------------------------------------------------------------


def _verify_proof(
    proof: str, session: DPoPSession
) -> tuple[dict[str, Any], dict[str, Any]]:
    header_b64, payload_b64, sig_b64 = proof.split(".")
    header = json.loads(_b64decode(header_b64))
    claims = json.loads(_b64decode(payload_b64))
    raw_sig = _b64decode(sig_b64)
    assert len(raw_sig) == 64  # JWS raw r||s, not DER
    r = int.from_bytes(raw_sig[:32], "big")
    s = int.from_bytes(raw_sig[32:], "big")
    der = encode_dss_signature(r, s)
    session.private_key.public_key().verify(
        der, f"{header_b64}.{payload_b64}".encode(), ec.ECDSA(hashes.SHA256())
    )
    return header, claims


def test_sign_dpop_proof_structure_and_signature() -> None:
    session = _session()
    proof = sign_dpop_proof(
        session, method="post", url="https://console.x.ai/v1/responses"
    )
    header, claims = _verify_proof(proof, session)
    assert header["typ"] == "dpop+jwt"
    assert header["alg"] == "ES256"
    assert header["jwk"] == session.public_jwk
    assert claims["htm"] == "POST"
    assert claims["htu"] == "https://console.x.ai/v1/responses"
    assert isinstance(claims["iat"], int) and claims["iat"] > 0
    assert len(claims["jti"]) == 36  # uuid4 string, Go uuid.NewString() format


def test_proof_ath_is_sha256_of_access_token() -> None:
    session = _session(access_token="my-access-token")
    _, claims = _verify_proof(
        sign_dpop_proof(session, method="GET", url="https://console.x.ai/v1/models"),
        session,
    )
    assert claims["ath"] == _b64url(hashlib.sha256(b"my-access-token").digest())


def test_proof_jti_unique_per_signature() -> None:
    session = _session()
    p1 = sign_dpop_proof(session, method="GET", url="https://console.x.ai/v1/x")
    p2 = sign_dpop_proof(session, method="GET", url="https://console.x.ai/v1/x")
    _, c1 = _verify_proof(p1, session)
    _, c2 = _verify_proof(p2, session)
    assert c1["jti"] != c2["jti"]


def test_proof_htm_uppercased() -> None:
    session = _session()
    _, claims = _verify_proof(
        sign_dpop_proof(session, method="post", url="https://console.x.ai/v1/y"),
        session,
    )
    assert claims["htm"] == "POST"


# ---------------------------------------------------------------------------
# Session cache + fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_fetch_caches_and_reuses() -> None:
    endpoint = FakeTokenEndpoint()
    mgr = _manager(endpoint)
    s1 = await mgr.get_or_fetch("https://console.x.ai", 1, 0, "sso-1")
    s2 = await mgr.get_or_fetch("https://console.x.ai", 1, 0, "sso-1")
    assert s1 is s2
    assert endpoint.started == 1
    # fetch URL mirrors Go consoleV1Endpoint: base + /v1/dpop/token
    assert endpoint.calls[0][0] == "https://console.x.ai/v1/dpop/token"
    # body carries the public jwk
    jwk = endpoint.calls[0][2]["jwk"]
    assert jwk["kty"] == "EC" and jwk["crv"] == "P-256"
    # headers include Content-Type + injected browser headers
    hdrs = endpoint.calls[0][1]
    assert hdrs["Content-Type"] == "application/json"
    assert hdrs["cookie"] == "sso=test-cookie"


@pytest.mark.asyncio
async def test_cache_key_partitions_by_all_factors() -> None:
    endpoint = FakeTokenEndpoint()
    mgr = _manager(endpoint)
    await mgr.get_or_fetch("https://console.x.ai", 1, 0, "sso-1")
    await mgr.get_or_fetch("https://console.x.ai", 2, 0, "sso-1")
    await mgr.get_or_fetch("https://console.x.ai", 1, 7, "sso-1")
    await mgr.get_or_fetch("https://console.x.ai", 1, 0, "sso-2")
    assert endpoint.started == 4


def test_cache_key_format_and_hash() -> None:
    key = dpop_session_cache_key("https://console.x.ai/", 42, 7, "secret-sso")
    assert key.startswith("https://console.x.ai|42|7|")
    assert key.split("|")[3] == hashlib.sha256(b"secret-sso").hexdigest()
    # trailing slash trimmed, distinct tokens hash differently
    assert dpop_session_cache_key(
        "https://console.x.ai", 1, 0, "a"
    ) != dpop_session_cache_key("https://console.x.ai", 1, 0, "b")


@pytest.mark.asyncio
async def test_expired_session_is_refetched() -> None:
    endpoint = FakeTokenEndpoint()
    mgr = _manager(endpoint)
    s1 = await mgr.get_or_fetch("https://console.x.ai", 1, 0, "sso-1")
    # Cached entry now sits inside the 20s refresh skew → must be dropped on next access
    expired = _session(expires_at=int(time.time() * 1000) + 5_000)
    key = dpop_session_cache_key("https://console.x.ai", 1, 0, "sso-1")
    mgr._cache[key] = expired
    s2 = await mgr.get_or_fetch("https://console.x.ai", 1, 0, "sso-1")
    assert s1 is not s2
    assert endpoint.started == 2


def test_cached_expiry_boundary() -> None:
    mgr = _manager()
    key = dpop_session_cache_key("https://console.x.ai", 1, 0, "s")
    now_ms = int(time.time() * 1000)
    # inside the 20s skew → evicted
    mgr._store(key, _session(expires_at=now_ms + 19_000))
    assert mgr._cached(key) is None
    # beyond the skew → kept
    mgr._store(key, _session(expires_at=now_ms + 30_000))
    assert mgr._cached(key) is not None


@pytest.mark.asyncio
async def test_singleflight_coalesces_concurrent_fetches() -> None:
    endpoint = FakeTokenEndpoint()
    endpoint.gate = asyncio.Event()
    mgr = _manager(endpoint)

    async def call() -> DPoPSession:
        return await mgr.get_or_fetch("https://console.x.ai", 1, 0, "sso-1")

    t1 = asyncio.create_task(call())
    await endpoint.gate.wait()  # first fetch is in flight
    t2 = asyncio.create_task(call())
    s1, s2 = await asyncio.gather(t1, t2)
    assert endpoint.started == 1
    assert s1 is s2


@pytest.mark.asyncio
async def test_fetch_failure_propagates_to_all_waiters() -> None:
    async def fail(
        _url: str,
        _headers: dict[str, str],
        _body: dict[str, Any],
        _lease: Any | None = None,
    ) -> tuple[int, dict[str, Any]]:
        return 500, {}

    mgr = _manager(FakeTokenEndpoint())
    mgr._post_json_fn = fail  # test seam

    async def call() -> DPoPSession:
        return await mgr.get_or_fetch("https://console.x.ai", 1, 0, "s")

    with pytest.raises(DPoPTokenEndpointError):
        await asyncio.gather(call(), call())


@pytest.mark.asyncio
async def test_token_type_and_lifetime_validated() -> None:
    jwk = public_dpop_jwk(ec.generate_private_key(ec.SECP256R1()))

    async def wrong_type(
        _url: str, _h: dict[str, str], _b: dict[str, Any], _lease: Any | None = None
    ) -> tuple[int, dict[str, Any]]:
        return 200, {
            "access_token": _make_access_token(jwk),
            "token_type": "Bearer",
            "expires_in": 3600,
        }

    mgr = _manager()
    mgr._post_json_fn = wrong_type
    with pytest.raises(DPoPError):
        await mgr.get_or_fetch("https://console.x.ai", 1, 0, "s")

    async def expires_in_zero(
        _url: str, _h: dict[str, str], _b: dict[str, Any], _lease: Any | None = None
    ) -> tuple[int, dict[str, Any]]:
        return 200, {
            "access_token": _make_access_token(jwk),
            "token_type": "DPoP",
            "expires_in": 0,
        }

    mgr._post_json_fn = expires_in_zero
    with pytest.raises(DPoPError):
        await mgr.get_or_fetch("https://console.x.ai", 1, 0, "s")

    async def expires_in_too_long(
        _url: str, _h: dict[str, str], _b: dict[str, Any], _lease: Any | None = None
    ) -> tuple[int, dict[str, Any]]:
        return 200, {
            "access_token": _make_access_token(jwk),
            "token_type": "DPoP",
            "expires_in": 3601,
        }

    mgr._post_json_fn = expires_in_too_long
    with pytest.raises(DPoPError):
        await mgr.get_or_fetch("https://console.x.ai", 1, 0, "s")


@pytest.mark.asyncio
async def test_cnf_jkt_mismatch_rejects_session() -> None:
    jwk = public_dpop_jwk(ec.generate_private_key(ec.SECP256R1()))
    other = public_dpop_jwk(ec.generate_private_key(ec.SECP256R1()))

    async def mismatched(
        _url: str, _h: dict[str, str], _b: dict[str, Any], _lease: Any | None = None
    ) -> tuple[int, dict[str, Any]]:
        return 200, {
            "access_token": _make_access_token(jwk, jkt=_ref_thumbprint(other)),
            "token_type": "DPoP",
            "expires_in": 3600,
        }

    mgr = _manager()
    mgr._post_json_fn = mismatched
    with pytest.raises(DPoPError):
        await mgr.get_or_fetch("https://console.x.ai", 1, 0, "s")
    assert mgr._cache == {}  # rejected session must not be cached


@pytest.mark.asyncio
async def test_jwt_exp_shortens_session_lifetime() -> None:
    exp = (
        int(time.time()) + 25
    )  # 25s: >20s skew so fetch succeeds, but shorter than expires_in 3600

    async def short_exp(
        _url: str,
        _h: dict[str, str],
        json_body: dict[str, Any],
        _lease: Any | None = None,
    ) -> tuple[int, dict[str, Any]]:
        return 200, {
            "access_token": _make_access_token(json_body["jwk"], exp=exp),
            "token_type": "DPoP",
            "expires_in": 3600,
        }

    mgr = _manager()
    mgr._post_json_fn = short_exp
    session = await mgr.get_or_fetch("https://console.x.ai", 1, 0, "s")
    expected = min(int(time.time() * 1000) + 3600_000, exp * 1000)
    assert abs(session.expires_at - expected) < 2000


@pytest.mark.asyncio
async def test_jwt_exp_inside_skew_rejected() -> None:
    exp = int(time.time()) + 10  # 10s < 20s skew → fetch must fail

    async def expiring_soon(
        _url: str,
        _h: dict[str, str],
        json_body: dict[str, Any],
        _lease: Any | None = None,
    ) -> tuple[int, dict[str, Any]]:
        return 200, {
            "access_token": _make_access_token(json_body["jwk"], exp=exp),
            "token_type": "DPoP",
            "expires_in": 3600,
        }

    mgr = _manager()
    mgr._post_json_fn = expiring_soon
    with pytest.raises(DPoPError):
        await mgr.get_or_fetch("https://console.x.ai", 1, 0, "s")


@pytest.mark.asyncio
async def test_403_non_definitive_signals_clearance_invalidation() -> None:
    async def forbidden(
        _url: str, _h: dict[str, str], _b: dict[str, Any], _lease: Any | None = None
    ) -> tuple[int, dict[str, Any]]:
        return 403, {"error": {"code": "challenge_required"}}

    mgr = _manager()
    mgr._post_json_fn = forbidden
    with pytest.raises(DPoPTokenEndpointError) as ei:
        await mgr.get_or_fetch("https://console.x.ai", 1, 0, "s")
    assert ei.value.status == 403
    assert ei.value.invalidate_clearance is True


@pytest.mark.asyncio
async def test_403_definitive_block_keeps_clearance() -> None:
    async def forbidden(
        _url: str, _h: dict[str, str], _b: dict[str, Any], _lease: Any | None = None
    ) -> tuple[int, dict[str, Any]]:
        return 403, {"error": {"message": "definitive account block"}}

    mgr = _manager(is_definitive_block=lambda body: "definitive" in body)
    mgr._post_json_fn = forbidden
    with pytest.raises(DPoPTokenEndpointError) as ei:
        await mgr.get_or_fetch("https://console.x.ai", 1, 0, "s")
    assert ei.value.status == 403
    assert ei.value.invalidate_clearance is False


# ---------------------------------------------------------------------------
# LRU cap
# ---------------------------------------------------------------------------


def test_lru_cap_evicts_oldest() -> None:
    mgr = _manager()
    session = _session()
    for i in range(DPOP_SESSION_CACHE_LIMIT + 1):
        mgr._store(f"key-{i}", session)
    assert len(mgr._cache) == DPOP_SESSION_CACHE_LIMIT
    assert "key-0" not in mgr._cache
    assert f"key-{DPOP_SESSION_CACHE_LIMIT}" in mgr._cache


def test_store_refresh_moves_to_front() -> None:
    mgr = _manager()
    session = _session()
    mgr._store("a", session)
    mgr._store("b", session)
    mgr._store("c", session)
    mgr._store("b", session)  # touch b → LRU order is now a, c, b
    assert list(mgr._cache) == ["a", "c", "b"]
    # fill past the cap: the least-recently-used entry (a) is evicted, not b
    for i in range(DPOP_SESSION_CACHE_LIMIT - 2):
        mgr._store(f"fill-{i}", session)
    assert len(mgr._cache) == DPOP_SESSION_CACHE_LIMIT
    assert "a" not in mgr._cache
    assert "b" in mgr._cache


# ---------------------------------------------------------------------------
# do_dpop_request orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_do_dpop_request_success_headers() -> None:
    mgr = _manager()
    req = FakeRequestFn([(200, b"ok-body", {"content-type": "application/json"})])
    status, body, resp_headers = await do_dpop_request(
        mgr,
        method="POST",
        url="https://console.x.ai/v1/responses",
        body=b'{"model":"grok-4.3-console"}',
        accept="text/event-stream",
        credential_id=1,
        node_id=0,
        sso_token="sso-1",
        request_fn=req,
    )
    assert status == 200
    assert body == b"ok-body"
    assert resp_headers == {"content-type": "application/json"}
    hdrs, sent_body = req.calls[0]
    assert sent_body == b'{"model":"grok-4.3-console"}'
    assert hdrs["Authorization"].startswith("DPoP ")
    assert len(hdrs["DPoP"].split(".")) == 3
    assert hdrs["Content-Type"] == "application/json"
    assert hdrs["Accept"] == "text/event-stream"
    assert hdrs["cookie"] == "sso=test-cookie"  # browser headers preserved


@pytest.mark.asyncio
async def test_do_dpop_request_x_cluster_only_on_responses() -> None:
    mgr = _manager()
    req = FakeRequestFn([(200, b"ok", {})])
    await do_dpop_request(
        mgr,
        method="GET",
        url="https://console.x.ai/v1/responses",
        body=None,
        accept=None,
        credential_id=1,
        node_id=0,
        sso_token="s",
        request_fn=req,
    )
    assert req.calls[0][0]["x-cluster"] == "https://us-east-1.api.x.ai"

    req2 = FakeRequestFn([(200, b"ok", {})])
    await do_dpop_request(
        mgr,
        method="GET",
        url="https://console.x.ai/v1/models",
        body=None,
        accept=None,
        credential_id=1,
        node_id=0,
        sso_token="s",
        request_fn=req2,
    )
    assert "x-cluster" not in req2.calls[0][0]


@pytest.mark.asyncio
async def test_do_dpop_request_no_content_type_without_body() -> None:
    mgr = _manager()
    req = FakeRequestFn([(200, b"ok", {})])
    await do_dpop_request(
        mgr,
        method="GET",
        url="https://console.x.ai/v1/models",
        body=None,
        accept=None,
        credential_id=1,
        node_id=0,
        sso_token="s",
        request_fn=req,
    )
    assert "Content-Type" not in req.calls[0][0]


@pytest.mark.asyncio
async def test_do_dpop_request_401_retries_once_with_fresh_session() -> None:
    endpoint = FakeTokenEndpoint()
    mgr = _manager(endpoint)
    req = FakeRequestFn([(401, b"unauthorized", {}), (200, b"ok", {})])
    status, body, _ = await do_dpop_request(
        mgr,
        method="POST",
        url="https://console.x.ai/v1/responses",
        body=b"{}",
        accept=None,
        credential_id=1,
        node_id=0,
        sso_token="sso-1",
        request_fn=req,
    )
    assert status == 200
    assert body == b"ok"
    assert len(req.calls) == 2
    assert endpoint.started == 2  # session invalidated + refetched
    assert req.calls[0][0]["Authorization"] != req.calls[1][0]["Authorization"]
    assert req.calls[0][0]["DPoP"] != req.calls[1][0]["DPoP"]


@pytest.mark.asyncio
async def test_do_dpop_request_second_401_propagates() -> None:
    mgr = _manager()
    req = FakeRequestFn([(401, b"no", {}), (401, b"no", {})])
    status, body, _ = await do_dpop_request(
        mgr,
        method="GET",
        url="https://console.x.ai/v1/models",
        body=None,
        accept=None,
        credential_id=1,
        node_id=0,
        sso_token="s",
        request_fn=req,
    )
    assert status == 401
    assert len(req.calls) == 2


@pytest.mark.asyncio
async def test_do_dpop_request_propagates_token_endpoint_error() -> None:
    async def forbidden(
        _url: str, _h: dict[str, str], _b: dict[str, Any], _lease: Any | None = None
    ) -> tuple[int, dict[str, Any]]:
        return 403, {"error": "challenge"}

    mgr = _manager()
    mgr._post_json_fn = forbidden
    req = FakeRequestFn([])
    with pytest.raises(DPoPTokenEndpointError) as ei:
        await do_dpop_request(
            mgr,
            method="GET",
            url="https://console.x.ai/v1/models",
            body=None,
            accept=None,
            credential_id=1,
            node_id=0,
            sso_token="s",
            request_fn=req,
        )
    assert ei.value.status == 403
    assert ei.value.invalidate_clearance is True
    assert req.calls == []  # request_fn never called


def test_invalidate_requires_token_match() -> None:
    mgr = _manager()
    session = _session(access_token="tok-a")
    key = "k"
    mgr._store(key, session)
    mgr.invalidate(
        key, "tok-b"
    )  # wrong token → kept (Go: concurrent refresh protection)
    assert mgr._cached(key) is not None
    mgr.invalidate(key, "tok-a")
    assert mgr._cached(key) is None


@pytest.mark.asyncio
async def test_invalidated_session_refetches() -> None:
    endpoint = FakeTokenEndpoint()
    mgr = _manager(endpoint)
    s1 = await mgr.get_or_fetch("https://console.x.ai", 1, 0, "sso-1")
    key = dpop_session_cache_key("https://console.x.ai", 1, 0, "sso-1")
    mgr.invalidate(key, s1.access_token)
    s2 = await mgr.get_or_fetch("https://console.x.ai", 1, 0, "sso-1")
    assert s1 is not s2
    assert endpoint.started == 2
