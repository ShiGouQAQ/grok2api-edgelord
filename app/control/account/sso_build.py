"""SSO→Build credential conversion.

Port of Go web/sso_build.go with PKCE-CS path from GrokRegisterAgent.

Two paths:
  1. Device Flow (primary): OAuth Device Authorization Grant flow (Go upstream-verified)
  2. PKCE-CS (fallback): uses CreateCookieSetterLink gRPC-web for session materialization

Device Flow path (matching Go upstream sso_build.go):
  1. GET accounts.x.ai/ — SSO pre-validation
  2. POST auth.x.ai/oauth2/device/code — start device flow
  3. GET {verification_uri_complete} — establish SSO session
  4. POST auth.x.ai/oauth2/device/verify — auto-verify device
  5. POST auth.x.ai/oauth2/device/approve — auto-approve consent
  6. Poll POST auth.x.ai/oauth2/token — wait for access_token
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from app.platform.logging.logger import logger
import re
import secrets
import ssl
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

import aiohttp
from yarl import URL as _URL

from app.dataplane.proxy.adapters.session import normalize_proxy_url
from app.platform.auth.grpc_web_codec import (
    decode_message as _grpc_decode_message,
)
from app.platform.auth.grpc_web_codec import (
    encode_string as _grpc_encode_string,
)
from app.platform.auth.grpc_web_codec import (
    frame_request as _grpc_frame_request,
)
from app.platform.auth.grpc_web_codec import (
    parse_response as _grpc_parse_response,
)
from app.platform.auth.oauth_device import DeviceFlowClient
from app.platform.config.snapshot import get_config
from app.platform.errors import UpstreamError

if TYPE_CHECKING:
    from app.control.proxy.models import ProxyLease
    from app.dataplane.proxy.adapters.profile import ProxyProfile

CLIENT_ID = DeviceFlowClient.CLIENT_ID
DEVICE_URL = DeviceFlowClient.DEVICE_URL
VERIFY_URL = DeviceFlowClient.VERIFY_URL
APPROVE_URL = DeviceFlowClient.APPROVE_URL
TOKEN_URL = DeviceFlowClient.TOKEN_URL
SCOPE = DeviceFlowClient.SCOPE
REDIRECT_URI = "http://127.0.0.1:56121/callback"
ACCOUNTS_ORIGIN = "https://accounts.x.ai/"
AUTHORIZATION_ENDPOINT = "https://auth.x.ai/oauth2/authorize"
CREATE_COOKIE_SETTER_RPC = (
    "https://accounts.x.ai/auth_mgmt.AuthManagement/CreateCookieSetterLink"
)


class SSOCredentialRejected(UpstreamError):
    """SSO credential hard-rejected by upstream.

    Raised for an invalid/expired SSO token both during PKCE-CS minting
    (mirrors Go sso_build.go @8f979d4: invalid SSO → ErrUnauthorized →
    markSSOCredentialRejected) and during Device Flow pre-validation
    (redirected to sign-in). Propagates instead of falling back to the
    other mint path — a rejected SSO token would mint an unverified token
    there. Carries credential_rejected=True so account marking is
    automatic in convert paths.
    """

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            status=401,
            account_scoped=True,
            credential_rejected=True,
        )


# ─── Data classes ───────────────────────────────────────────────────────────


@dataclass
class BuildCredentialSeed:
    """Structured Build credential seed (port of Go provider.CredentialSeed).

    Supports both attribute access and dict-like access for
    backward compatibility with existing callers.
    """

    provider: str = "grok_build"
    auth_type: str = "oauth"
    access_token: str = ""
    refresh_token: str = ""
    id_token: str = ""
    expires_in: int = 21600
    name: str = ""
    email: str = ""
    user_id: str = ""
    team_id: str = ""
    source_key: str = ""
    oidc_client_id: str = field(default="b1a00492-073a-47ea-816f-4c329264a828")

    def __getitem__(self, key: str) -> Any:
        """Dict-like access for backward compatibility."""
        mapping = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "id_token": self.id_token,
            "expires_in": str(self.expires_in),
            "provider": self.provider,
            "auth_type": self.auth_type,
            "name": self.name,
            "email": self.email,
            "user_id": self.user_id,
            "team_id": self.team_id,
            "source_key": self.source_key,
            "oidc_client_id": self.oidc_client_id,
        }
        if key in mapping:
            return mapping[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like .get() for backward compatibility."""
        try:
            return self[key]
        except KeyError:
            return default


# ─── SSO token normalization ────────────────────────────────────────────────


def _sanitize_sso_token(value: str) -> str:
    """Strip whitespace and control characters from an SSO token value."""
    return value.strip().replace("\r", "").replace("\n", "").replace("\x00", "")


def normalize_sso_token(token: str) -> str:
    """Normalize SSO token.

    Accepts a raw SSO value, a bare 'sso=' prefix, or a full 'Cookie:'
    header — in the cookie case the 'sso' / 'sso-rw' field value is
    extracted (case-insensitive). Falls back to chopping at the first ';'
    when no cookie field matches. Removes control characters.
    Port of Go normalizeSSOToken() (sub2api sso_device.go).
    """
    value = token.strip()
    if value.lower().startswith("cookie:"):
        value = value[len("cookie:") :].strip()
    for part in value.split(";"):
        name, sep, field_value = part.strip().partition("=")
        if not sep:
            continue
        if name.strip().lower() in ("sso", "sso-rw"):
            return _sanitize_sso_token(field_value)
    if ";" in value:
        value = value.split(";")[0].strip()
    return _sanitize_sso_token(value)


async def _acquire_mint_lease() -> ProxyLease | None:
    """Acquire a live proxy lease for the SSO→Build mint, or None on failure.

    The lease carries the turnstile/flaresolverr solved bundle (cf_cookies +
    user_agent + proxy_url) — the same source ordinary requests use, so the
    cf_clearance Cloudflare issued matches the UA/TLS fingerprint we present.
    """
    try:
        from app.control.proxy.models import ProxyScope
        from app.dataplane.proxy import get_proxy_runtime

        proxy = await get_proxy_runtime()
        # Build mint gets fresh-tunnel proxy rotation (Wave-1 D port of Go
        # 75f4f7a7): ProxyScope.BUILD rotates the egress per request on
        # proxy_pool mode.
        lease = await proxy.acquire(
            scope=ProxyScope.BUILD, clearance_origin=ACCOUNTS_ORIGIN
        )
        if not isinstance(getattr(lease, "cf_cookies", ""), str):
            return None
        return lease
    except Exception:
        return None


async def _resolve_mint_profile(lease: ProxyLease | None = None) -> ProxyProfile:
    """Resolve the full proxy profile (cf_cookies + user_agent + browser) for mint.

    Prefers a live lease from the proxy directory; falls back to the
    configured clearance profile. Returns the complete profile — not just the
    cf_clearance value — so header UA, client-hints and curl_cffi
    impersonation all align with the Cloudflare-issued clearance.
    """
    from app.dataplane.proxy.adapters.profile import resolve_proxy_profile

    if lease is None:
        lease = await _acquire_mint_lease()
    return resolve_proxy_profile(lease)


async def _resolve_cf_clearance_value(cfg: Any | None = None) -> str:
    """Backward-compatible thin wrapper: resolve just the cf_clearance value.

    Full-profile resolution moved to _resolve_mint_profile(); kept for legacy
    callers/tests that only need the single cookie value (and only consume
    the lease's cf_cookies, never its UA).
    """
    lease = await _acquire_mint_lease()
    if lease is not None and lease.cf_cookies:
        from app.dataplane.proxy.adapters.profile import extract_cookie_value

        extracted = extract_cookie_value(lease.cf_cookies, "cf_clearance")
        if extracted:
            return extracted
    from app.control.proxy.config import resolve_clearance_config

    return resolve_clearance_config(cfg).cf_clearance or ""


# ─── URL safety ─────────────────────────────────────────────────────────────


def safe_xai_url(raw: str) -> bool:
    """Only allow HTTPS URLs to *.x.ai domains.

    Port of Go safeXAIURL().
    """
    try:
        parsed = urllib.parse.urlparse(raw)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or not parsed.hostname
        ):
            return False
        host = parsed.hostname.lower()
        return host == "x.ai" or host.endswith(".x.ai")
    except Exception:
        return False


# ─── JWT claims extraction ──────────────────────────────────────────────────


def decode_build_claims(token: str) -> dict[str, Any] | None:
    """Decode JWT payload (second segment, base64url).

    Port of Go decodeBuildClaims().
    """
    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        data = base64.urlsafe_b64decode(parts[1] + "==")
        return json.loads(data)
    except Exception:
        return None


def _claim_string(claims: dict[str, Any] | None, key: str) -> str:
    """Safely extract a string claim.

    Port of Go claimString().
    """
    if not claims:
        return ""
    value = claims.get(key)
    return str(value).strip() if isinstance(value, str) else ""


def _first_value(*values: str) -> str:
    """Return the first non-empty stripped value.

    Port of Go firstValue().
    """
    for v in values:
        if v.strip():
            return v.strip()
    return ""


def _extract_urls_from_grpc_fields(fields: list[dict[str, Any]]) -> list[str]:
    """Recursively extract HTTPS URLs from gRPC response fields."""
    urls: list[str] = []
    for f in fields:
        if f.get("type") == "string":
            value = str(f.get("value") or "")
            if value.startswith(("http://", "https://")):
                urls.append(value)
        elif f.get("type") == "bytes" and f.get("hex"):
            try:
                urls.extend(
                    _extract_urls_from_grpc_fields(
                        _grpc_decode_message(bytes.fromhex(f["hex"]))
                    )
                )
            except Exception:
                pass
    return urls


# ─── PKCE helpers ───────────────────────────────────────────────────────────


def _b64url(raw: bytes) -> str:
    """Base64url encode without padding."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _code_verifier() -> str:
    """Generate a PKCE code verifier (48 cryptographically-random bytes)."""
    return _b64url(secrets.token_bytes(48))


def _code_challenge(verifier: str) -> str:
    """Derive a PKCE S256 code challenge from a verifier string."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


# ─── PKCE-CS path ───────────────────────────────────────────────────────────


async def _mint_via_pkce_cs(
    sso_token: str,
    proxy_url: str | None = None,
) -> BuildCredentialSeed:
    """PKCE-CS fallback: use CreateCookieSetterLink gRPC-web for session materialization.

    Registration-bot reverse-engineered RPC — only reached when Device Flow
    fails transiently (SSO-invalid errors never fall through to this path).
    """
    from curl_cffi.requests import AsyncSession as CurlAsyncSession

    from app.dataplane.proxy.adapters.session import build_session_kwargs

    # Reuse the proxy lease three-piece (cf_cookies + user_agent + browser):
    # session kwargs derive impersonate/TLS fingerprint from the lease profile
    # so they match the cf_clearance Cloudflare issued.
    lease = await _acquire_mint_lease()
    profile = await _resolve_mint_profile(lease)
    session_headers: dict[str, str] = {}
    if profile.user_agent:
        session_headers["User-Agent"] = profile.user_agent
    kwargs = build_session_kwargs(lease=lease, extra={"headers": session_headers})
    if not kwargs.get("proxies") and not kwargs.get("proxy") and proxy_url:
        kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

    async with CurlAsyncSession(**kwargs) as session:
        # Set SSO cookies + full clearance cookie bundle (not just the
        # cf_clearance value) on all relevant domains.
        # CF clearance is needed to avoid Cloudflare 403 on accounts.x.ai / auth.x.ai.
        # Four domains per reference cpa_pkce_mint.py _set_sso_cookie (adds .accounts.x.ai).
        for domain in ("accounts.x.ai", ".accounts.x.ai", ".x.ai", "auth.x.ai"):
            session.cookies.set("sso", sso_token, domain=domain, path="/")
            session.cookies.set("sso-rw", sso_token, domain=domain, path="/")
            for part in profile.cf_cookies.split(";"):
                name, _, value = part.partition("=")
                name, value = name.strip(), value.strip()
                if name and value:
                    session.cookies.set(name, value, domain=domain, path="/")

        # Generate PKCE params
        verifier = _code_verifier()
        challenge = _code_challenge(verifier)
        state = secrets.token_hex(16)
        nonce = secrets.token_hex(16)

        # Build authorization URL and consent URL
        auth_params = {
            "client_id": CLIENT_ID,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "nonce": nonce,
            "plan": "generic",
            "redirect_uri": REDIRECT_URI,
            "referrer": "grok-build",
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
        }
        auth_url = AUTHORIZATION_ENDPOINT + "?" + urllib.parse.urlencode(auth_params)
        consent_url = f"{ACCOUNTS_ORIGIN}oauth2/consent?" + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "scope": SCOPE,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "nonce": nonce,
            }
        )

        # GET authorize URL to establish SSO-authenticated session
        await session.get(auth_url, allow_redirects=False, timeout=30)

        # Call CreateCookieSetterLink gRPC-web
        msg = _grpc_encode_string(1, consent_url) + _grpc_encode_string(
            2,
            f"{ACCOUNTS_ORIGIN}sign-in",
        )
        grpc_headers = {
            "content-type": "application/grpc-web+proto",
            "x-grpc-web": "1",
            "x-user-agent": "connect-es/2.1.1",
            "accept": "*/*",
            "origin": ACCOUNTS_ORIGIN,
            "referer": f"{ACCOUNTS_ORIGIN}sign-in?redirect=oauth2-provider",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        }

        grpc_resp = await session.post(
            CREATE_COOKIE_SETTER_RPC,
            headers=grpc_headers,
            data=_grpc_frame_request(msg),
            timeout=45,
        )

        # Parse gRPC-web response. Header grpc-status wins over body trailers
        # (reference _parse_grpc_error order: header first, then trailers).
        parsed = _grpc_parse_response(grpc_resp.content)
        raw_status = grpc_resp.headers.get("grpc-status") or ""
        if raw_status.isdigit():
            grpc_status = int(raw_status)
        elif parsed.get("grpc_status") is not None:
            grpc_status = int(parsed["grpc_status"])
        else:
            grpc_status = 0

        if grpc_status != 0:
            grpc_msg = urllib.parse.unquote(
                grpc_resp.headers.get("grpc-message")
                or parsed.get("trailers", {}).get("grpc-message")
                or "CreateCookieSetterLink failed"
            )
            lowered = grpc_msg.lower()
            if any(
                marker in lowered
                for marker in (
                    "bad-credentials",
                    "bad credentials",
                    "unauthenticated",
                    "wke=",
                )
            ):
                raise SSOCredentialRejected(f"PKCE-CS credential rejected: {grpc_msg}")
            raise RuntimeError(f"PKCE-CS gRPC error: {grpc_msg}")

        # Extract cookie_setter URL from response messages
        fields = parsed["messages"][0] if parsed.get("messages") else []
        urls = _extract_urls_from_grpc_fields(fields)
        cookie_setter = next(
            (u for u in urls if "set-cookie" in u.lower()),
            urls[0] if urls else "",
        )
        if not cookie_setter:
            raise RuntimeError("PKCE-CS: no cookie setter URL in gRPC response")

        # Follow cookie-setter URL chain (max 6 hops)
        code: str | None = None
        current = cookie_setter
        for _ in range(6):
            if "code=" in current and (
                current.startswith(REDIRECT_URI) or "127.0.0.1" in current
            ):
                parsed_qs = urllib.parse.urlparse(current)
                qs_params = urllib.parse.parse_qs(parsed_qs.query)
                if qs_params.get("state", [None])[0] == state:
                    code = qs_params.get("code", [None])[0]
                break
            if "set-cookie" not in current.lower():
                break

            c_resp = await session.get(current, allow_redirects=False, timeout=30)
            loc = c_resp.headers.get("location") or ""
            logger.debug(
                "PKCE-CS set-cookie hop: HTTP {} loc={}", c_resp.status_code, loc[:100]
            )

            if not loc:
                if c_resp.status_code == 200:
                    body_text = c_resp.text
                    code_match = re.search(r'"code"\s*:\s*"([^"]+)"', body_text)
                    if code_match:
                        code = code_match.group(1)
                break
            current = urljoin(current, loc)

        # If cookie setter chain didn't yield code, fail fast unless it reached
        # consent/code — then submit the consent page reached via the chain.
        if not code:
            if "consent" not in current and "code=" not in current:
                raise RuntimeError(
                    "PKCE-CS: cookie-setter did not reach consent/code: "
                    f"{current[:180]}"
                )
            if "code=" in current:
                parsed_qs = urllib.parse.urlparse(current)
                qs_params = urllib.parse.parse_qs(parsed_qs.query)
                if qs_params.get("state", [None])[0] == state:
                    code = qs_params.get("code", [None])[0]
            else:
                consent_resp = await session.get(
                    current, allow_redirects=False, timeout=30
                )
                page_html = consent_resp.text or ""
                current_url = str(consent_resp.url)

                # Check if redirected to a URL with code
                loc = consent_resp.headers.get("location") or ""
                if loc and "code=" in loc:
                    parsed_loc = urllib.parse.urlparse(urljoin(current_url, loc))
                    qs_params = urllib.parse.parse_qs(parsed_loc.query)
                    if qs_params.get("state", [None])[0] == state:
                        code = qs_params.get("code", [None])[0]
                elif "consent" in current_url or "consent" in page_html:
                    # Submit consent form POST (plain POST == Allow, no action field)
                    fm = re.search(
                        r'<form\b[^>]*method="POST"[^>]*action="([^"]+)"[^>]*>'
                        r"(.*?)</form>",
                        page_html,
                        re.IGNORECASE | re.DOTALL,
                    )
                    if not fm:
                        fm = re.search(
                            r'<form\b[^>]*action="([^"]+)"[^>]*method="POST"[^>]*>'
                            r"(.*?)</form>",
                            page_html,
                            re.IGNORECASE | re.DOTALL,
                        )
                    if not fm:
                        raise RuntimeError("PKCE-CS: consent page has no POST form")
                    action_url, form_inner = fm.group(1), fm.group(2)
                    fields_dict: dict[str, str] = {}
                    for inp in re.findall(
                        r"<input\b([^>]*)/?>", form_inner, re.IGNORECASE
                    ):
                        nm = re.search(r'name="([^"]*)"', inp, re.IGNORECASE)
                        vl = re.search(r'value="([^"]*)"', inp, re.IGNORECASE)
                        if nm:
                            fields_dict[nm.group(1)] = vl.group(1) if vl else ""
                    if not fields_dict:
                        raise RuntimeError("PKCE-CS: consent form has no input fields")

                    post_resp = await session.post(
                        urljoin(current_url, action_url),
                        data=fields_dict,
                        headers={
                            "content-type": "application/x-www-form-urlencoded",
                            "origin": ACCOUNTS_ORIGIN,
                            "referer": current_url,
                            "sec-fetch-site": "same-origin",
                            "sec-fetch-mode": "cors",
                            "sec-fetch-dest": "empty",
                        },
                        allow_redirects=False,
                        timeout=30,
                    )

                    loc = post_resp.headers.get("location") or ""
                    if loc and "code=" in loc:
                        parsed_loc = urllib.parse.urlparse(urljoin(current_url, loc))
                        qs_params = urllib.parse.parse_qs(parsed_loc.query)
                        if qs_params.get("state", [None])[0] == state:
                            code = qs_params.get("code", [None])[0]
                    else:
                        body_text = post_resp.text or ""
                        code_match = re.search(r'"code"\s*:\s*"([^"]+)"', body_text)
                        if code_match:
                            code = code_match.group(1)
                        else:
                            code_match = re.search(
                                r"code=([A-Za-z0-9._~\-]+)", body_text
                            )
                            if code_match and "error" not in code_match.group(0):
                                code = code_match.group(1)
                        if not code and "access_denied" in (loc + body_text).lower():
                            raise RuntimeError("PKCE-CS: access_denied")

        if not code:
            raise RuntimeError("PKCE-CS: no authorization code obtained")

        # Exchange code for token
        token_resp = await session.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "code_verifier": verifier,
            },
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=45,
        )
        token_data = token_resp.json()

        if not token_data.get("access_token") or not token_data.get("refresh_token"):
            raise RuntimeError("PKCE-CS: no access_token/refresh_token in response")

        access_token = token_data["access_token"]
        id_token = token_data.get("id_token") or ""

        # Extract JWT claims
        claims = decode_build_claims(id_token) or decode_build_claims(access_token)
        user_id = _claim_string(claims, "sub")
        email = _claim_string(claims, "email")
        team_id = _claim_string(claims, "team_id")
        name = _first_value(email, "SSO Build", user_id, "Grok Build account")

        return BuildCredentialSeed(
            access_token=access_token,
            refresh_token=token_data.get("refresh_token", ""),
            id_token=id_token,
            expires_in=int(token_data.get("expires_in", 21600)),
            name=name,
            email=email,
            user_id=user_id,
            team_id=team_id,
            source_key="sso-build:" + hashlib.sha256(access_token.encode()).hexdigest(),
            oidc_client_id=CLIENT_ID,
        )


# ─── Device Flow path ───────────────────────────────────────────────────────


async def _mint_via_device_flow(sso_token: str) -> BuildCredentialSeed:
    """Device Flow path: OAuth Device Authorization Grant.

    Port of Go sso_build.go. Used as fallback when PKCE-CS fails.
    Includes:
    - SSO pre-validation (GET accounts.x.ai/)
    - Check final URL for 'consent' after verify, 'done' after approve
    - slow_down backoff +5s (not +2s), max 30s
    - error_description parsing in poll responses
    - Accept HTTP 200-399 for verify/approve
    - Returns structured BuildCredentialSeed
    """
    # Reuse the lease three-piece: proxy URL + UA + clearance from one source
    # so the cf_clearance Cloudflare issued matches the UA we present.
    lease = await _acquire_mint_lease()
    profile = await _resolve_mint_profile(lease)

    cfg = get_config()
    raw_proxy = str(cfg.get_str("proxy.egress.proxy_url", ""))
    config_proxy = normalize_proxy_url(raw_proxy) if raw_proxy else None
    proxy_url = (
        normalize_proxy_url(lease.proxy_url)
        if lease is not None and lease.proxy_url
        else config_proxy
    )

    ssl_ctx = ssl.create_default_context()
    if proxy_url and cfg.get_bool("proxy.egress.skip_ssl_verify", False):
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    cookie_jar = aiohttp.CookieJar()
    scheme = urlparse(proxy_url).scheme.lower() if proxy_url else ""
    if proxy_url and scheme.startswith("socks"):
        from aiohttp_socks import ProxyConnector

        connector = ProxyConnector.from_url(proxy_url, ssl=ssl_ctx)
        http_proxy: str | None = None
    else:
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        http_proxy = proxy_url

    timeout = aiohttp.ClientTimeout(total=30)
    session_headers = {"User-Agent": profile.user_agent} if profile.user_agent else None
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        cookie_jar=cookie_jar,
        headers=session_headers,
    ) as session:
        # Seed SSO + sso-rw (required for write ops like approve) on auth/accounts domains.
        # Without cf_clearance, Cloudflare returns 403 on accounts.x.ai pre-validation.
        cookie_jar.update_cookies(
            {"sso": sso_token, "sso-rw": sso_token}, _URL("https://auth.x.ai")
        )
        cookie_jar.update_cookies(
            {"sso": sso_token, "sso-rw": sso_token}, _URL("https://accounts.x.ai")
        )
        if profile.cf_clearance:
            cookie_jar.update_cookies(
                {"cf_clearance": profile.cf_clearance}, _URL("https://accounts.x.ai")
            )
            cookie_jar.update_cookies(
                {"cf_clearance": profile.cf_clearance}, _URL("https://auth.x.ai")
            )

        # 1. SSO pre-validation: GET accounts.x.ai/ — check not redirected to sign-in
        async with session.get(
            ACCOUNTS_ORIGIN,
            allow_redirects=True,
            proxy=http_proxy,
        ) as pre_resp:
            final_url = str(pre_resp.url)
            if (
                pre_resp.status == 401
                or "sign-in" in final_url
                or "sign-up" in final_url
            ):
                raise SSOCredentialRejected(
                    "SSO token invalid or expired: redirected to sign-in"
                )
            if pre_resp.status < 200 or pre_resp.status >= 400:
                raise RuntimeError(f"SSO pre-validation failed ({pre_resp.status})")

        # 2. Start device flow
        async with session.post(
            DEVICE_URL,
            data={"client_id": CLIENT_ID, "scope": SCOPE, "referrer": "grok-build"},
            proxy=http_proxy,
        ) as resp:
            if resp.status < 200 or resp.status >= 300:
                raise RuntimeError(
                    f"Device code request failed ({resp.status}): {(await resp.text())[:200]}"
                )
            device = await resp.json()

        device_code = device.get("device_code")
        user_code = device.get("user_code")
        verification_uri_complete = device.get("verification_uri_complete", "")
        poll_interval = int(device.get("interval", 5))
        expires_in = int(device.get("expires_in", 1800))

        if (
            not device_code
            or not user_code
            or not safe_xai_url(verification_uri_complete)
        ):
            raise ValueError(f"Incomplete device flow response: {device}")
        if poll_interval <= 0:
            poll_interval = 5
        if expires_in <= 0:
            expires_in = 1800

        # 3. Visit verification_uri_complete
        if verification_uri_complete:
            async with session.get(
                verification_uri_complete,
                allow_redirects=True,
                proxy=http_proxy,
            ) as _resp:
                if _resp.status < 200 or _resp.status >= 400:
                    logger.warning(
                        "SSO→Build verification URI returned status={}", _resp.status
                    )

        # 4. Verify device — check final URL contains "consent"
        async with session.post(
            VERIFY_URL,
            data={"user_code": user_code},
            proxy=http_proxy,
        ) as resp:
            final_url = str(resp.url)
            if resp.status < 200 or resp.status >= 400:
                raise RuntimeError(
                    f"Device verify failed ({resp.status}): {(await resp.text())[:200]}"
                )
            if "consent" not in final_url:
                raise RuntimeError("SSO auto-verify failed: no 'consent' in final URL")

        # 5. Approve device — check final URL contains "done"
        async with session.post(
            APPROVE_URL,
            data={
                "user_code": user_code,
                "action": "allow",
                "principal_type": "User",
                "principal_id": "",
            },
            proxy=http_proxy,
        ) as resp:
            final_url = str(resp.url)
            if resp.status < 200 or resp.status >= 400:
                raise RuntimeError(
                    f"Device approve failed ({resp.status}): {(await resp.text())[:200]}"
                )
            if "done" not in final_url:
                raise RuntimeError("SSO auto-approve failed: no 'done' in final URL")

        # 6. Poll for token
        poll_deadline_s = min(expires_in, 75)
        current_interval = max(poll_interval, 1)
        deadline = time.monotonic() + poll_deadline_s

        while time.monotonic() < deadline:
            async with session.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": CLIENT_ID,
                    "device_code": device_code,
                },
                proxy=http_proxy,
            ) as resp:
                body = await resp.json()

                # Success: status 200-299 and access_token present
                if resp.status >= 200 and resp.status < 300 and "access_token" in body:
                    access_token = body["access_token"]
                    id_token = body.get("id_token") or ""
                    claims = decode_build_claims(id_token) or decode_build_claims(
                        access_token
                    )
                    user_id = _claim_string(claims, "sub")
                    email = _claim_string(claims, "email")
                    team_id = _claim_string(claims, "team_id")
                    name = _first_value(
                        email, "SSO Build", user_id, "Grok Build account"
                    )

                    logger.info("SSO→Build Device Flow success")
                    return BuildCredentialSeed(
                        access_token=access_token,
                        refresh_token=body.get("refresh_token", ""),
                        id_token=id_token,
                        expires_in=int(body.get("expires_in", 3600)),
                        name=name,
                        email=email,
                        user_id=user_id,
                        team_id=team_id,
                        source_key="sso-build:"
                        + hashlib.sha256(access_token.encode()).hexdigest(),
                        oidc_client_id=CLIENT_ID,
                    )

                error = body.get("error", "")
                error_desc = body.get("error_description", "")

                if error == "slow_down":
                    current_interval = min(current_interval + 5, 30)
                    logger.info(
                        "SSO→Build slow_down, interval now {}s", current_interval
                    )
                elif error in ("access_denied", "expired_token"):
                    raise PermissionError(
                        f"SSO→Build conversion denied: {error_desc or error}"
                    )
                elif error == "authorization_pending":
                    logger.debug(
                        "SSO→Build poll still pending (interval={}s)", current_interval
                    )
                else:
                    if resp.status >= 400:
                        # x.ai may surface credential denials as a non-standard
                        # error code with an 'Access denied' description.
                        deny_hint = f"{error} {error_desc}".lower()
                        if "access denied" in deny_hint or "access_denied" in deny_hint:
                            raise PermissionError(
                                f"SSO→Build conversion denied: {error_desc or error}"
                            )
                        raise RuntimeError(
                            "SSO→Build Device Flow poll failed "
                            f"({resp.status}): {error_desc or error or json.dumps(body)[:200]}"
                        )
                    logger.info(
                        "SSO→Build poll status={} error={} desc={}",
                        resp.status,
                        error or "none",
                        (error_desc or "")[:100],
                    )

            await asyncio.sleep(current_interval)

        raise TimeoutError("SSO→Build Device Flow timed out")


# ─── Public API ─────────────────────────────────────────────────────────────


async def convert_sso_to_build(sso_token: str) -> BuildCredentialSeed:
    """Convert a grok.com SSO token to Build OAuth credentials.

    Device Flow first — the Go upstream-verified path (chenyme sso_build.go).
    Falls back to PKCE-CS only on transient (non-credential) failure.
    Invalid-SSO errors (PermissionError from poll, SSOCredentialRejected
    from pre-validation or PKCE-CS) propagate without fallback.

    Returns a BuildCredentialSeed with access_token, refresh_token, etc.
    """
    token = normalize_sso_token(sso_token)
    if not token:
        raise ValueError("Empty SSO token after normalization")

    cfg = get_config()
    raw_proxy = str(cfg.get_str("proxy.egress.proxy_url", ""))
    proxy_url = normalize_proxy_url(raw_proxy) if raw_proxy else None

    # Device Flow first — Go upstream-verified path
    try:
        logger.info("SSO→Build: trying Device Flow path")
        return await _mint_via_device_flow(token)
    except (PermissionError, SSOCredentialRejected):
        # SSO invalid/expired (PermissionError: device-poll denial;
        # SSOCredentialRejected: pre-validation or PKCE-CS) — both paths
        # share the same credential, so falling back would only run the
        # registration-bot path for nothing.
        raise
    except Exception as df_err:
        logger.warning(
            "SSO→Build Device Flow failed, falling back to PKCE-CS: {}", df_err
        )

    # PKCE-CS fallback (registration-bot path). Last resort: any failure
    # (incl. SSOCredentialRejected) propagates so the account is marked.
    logger.info("SSO→Build: trying PKCE-CS path")
    return await _mint_via_pkce_cs(token, proxy_url=proxy_url)


__all__ = [
    "BuildCredentialSeed",
    "SSOCredentialRejected",
    "convert_sso_to_build",
    "decode_build_claims",
    "normalize_sso_token",
    "safe_xai_url",
]
