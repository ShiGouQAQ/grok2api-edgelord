"""Console x.ai ``/v1/usage`` quota fetch — Go→Python port of chenyme/grok2api
PR #853 ``backend/internal/infra/provider/console/quota.go``.

Replaces the fake/local console quota with real upstream data. x.ai requires
DPoP proofs on console.x.ai; the DPoP protocol lives in ``dpop.py`` while this
module owns the quota semantics: proxy lease → DPoP GET ``/v1/usage`` → strict
payload validation → three ``QuotaWindow`` values (chat/image/video, REAL
source). The chat window is the only one used for routing/recovery — image and
video are display-only (Go keeps their ``WindowSeconds`` at 0).

Error taxonomy (refresh.py distinguishes by ``isinstance``):
* 401, or 403 with a definitive account-block body → ``UpstreamError`` with
  ``credential_rejected=True`` — the account itself is bad.
* 403 otherwise → ``ConsoleClearanceRequiredError`` (an ``UpstreamError``)
  carrying ``invalidate_clearance`` — clearance/egress is bad, account may be
  fine; the caller decides.
* Malformed quota payload (missing kinds / invalid values) → ``ConsoleQuotaError``
  (status 502) — transient, caller falls back to the local quota.
* Transport/network failure → ``UpstreamError`` status 502.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, NoReturn

import orjson

from app.control.account.enums import QuotaSource
from app.control.account.models import QuotaWindow
from app.control.proxy.models import ProxyFeedback, ProxyFeedbackKind
from app.dataplane.proxy.adapters.headers import build_console_headers
from app.dataplane.proxy.adapters.session import ResettableSession, build_session_kwargs
from app.dataplane.reverse.protocol.dpop import (
    DPoPError,
    DPoPSessionManager,
    DPoPTokenEndpointError,
    do_dpop_request,
)
from app.dataplane.reverse.runtime.endpoint_table import CONSOLE_BASE, CONSOLE_USAGE
from app.platform.errors import UpstreamError
from app.platform.logging.logger import logger

CONSOLE_QUOTA_TIMEOUT_S = 30  # Go consoleQuotaTimeout = 30 * time.Second
# Go consolePredictedChatRecoveryWindow = 24 * time.Hour — the console API does
# not return a reset time, so an exhausted chat quota is predicted to recover
# in 24h (the account-level rotation recovers sooner in practice).
CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S = 86400

_CONSOLE_QUOTA_KINDS: tuple[str, str, str] = ("chat", "image", "video")
# Definitive account-block markers (Go provider.IsDefinitiveAccountBlockText
# checks "blocked-user"/"user is blocked"; keep the repo's full credential
# marker set so the DPoP token endpoint and the GET agree).
_DEFINITIVE_BLOCK_MARKERS: tuple[str, ...] = (
    "blocked-user",
    "user is blocked",
    "email-domain-rejected",
    "account suspended",
    "token revoked",
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConsoleQuotaError(UpstreamError):
    """Quota payload missing kinds / invalid values — transient, fall back to local."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status=502)


class ConsoleClearanceRequiredError(UpstreamError):
    """Console 403 without a definitive account block — clearance/egress is bad.

    ``invalidate_clearance`` mirrors Go ``lease.InvalidateClearance()``: the
    caller (quota refresh) decides whether to force a clearance refresh; the
    account itself must NOT be marked reauth/expired for this.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int = 403,
        body: str = "",
        invalidate_clearance: bool = False,
    ) -> None:
        super().__init__(message, status=status, body=body)
        self.invalidate_clearance: bool = invalidate_clearance


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ConsoleUsageResult:
    """Parsed upstream console quota — three windows plus raw payload.

    ``used`` maps kind → used-in-window (display only; ``QuotaWindow`` has no
    ``used`` field).
    """

    chat: QuotaWindow
    image: QuotaWindow
    video: QuotaWindow
    used: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def _is_definitive_block_body(body: str) -> bool:
    """Go ``provider.IsDefinitiveAccountBlockBody`` — whole-body marker check.

    Case-insensitive contains over the raw body (JSON or not), matching the
    repo's credential-rejection markers in ``errors.py``.
    """
    if not body:
        return False
    text = body.lower()
    return any(marker in text for marker in _DEFINITIVE_BLOCK_MARKERS)


def _status_feedback(status: int) -> ProxyFeedback:
    """Map a non-2xx status to proxy feedback (Go FeedbackForScope statusCode)."""
    if status == 429:
        kind = ProxyFeedbackKind.RATE_LIMITED
    elif status >= 500:
        kind = ProxyFeedbackKind.UPSTREAM_5XX
    else:
        kind = ProxyFeedbackKind.FORBIDDEN
    return ProxyFeedback(kind=kind, status_code=status)


def _feedback_forbidden(status: int, body: str) -> ProxyFeedback:
    return ProxyFeedback(
        kind=ProxyFeedbackKind.FORBIDDEN,
        status_code=status,
        reason=_parse_body_code(body),
    )


def _parse_body_code(body: str) -> str:
    """Extract the 'code' field from an upstream JSON error body (chat path parity)."""
    if not body:
        return ""
    try:
        parsed = orjson.loads(body)
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict):
                code = error.get("code", "")
            else:
                code = parsed.get("code", "")
            return str(code) if code else ""
    except (orjson.JSONDecodeError, TypeError, AttributeError):
        pass
    return ""


# ---------------------------------------------------------------------------
# DPoP wiring
# ---------------------------------------------------------------------------


def _make_post_json_fn(
    session: ResettableSession,
    token: str,
    lease: Any,
    timeout_s: float,
) -> Callable[
    [str, dict[str, str], dict[str, Any]], Awaitable[tuple[int, dict[str, Any]]]
]:
    """``post_json_fn`` for DPoPSessionManager: POST {base}/v1/dpop/token.

    Merges the lease-derived console headers (cookies, UA, clearance) into the
    DPoP layer's headers so the token exchange is authenticated like the chat
    path. Returns ``(status, data)`` with ``data`` the parsed JSON dict, or
    the raw body text on non-2xx (dpop.py formats it into the error body).
    """

    async def post_json(
        endpoint: str, headers: dict[str, str], payload: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        merged = {**headers, **build_console_headers(token, lease=lease)}
        try:
            response = await session.post(
                endpoint, headers=merged, data=orjson.dumps(payload), timeout=timeout_s
            )
        except Exception as exc:
            raise DPoPError(
                f"Console DPoP token transport failed: {exc}", invalidate_clearance=True
            ) from exc
        body = (
            response.content
            if isinstance(response.content, bytes)
            else bytes(response.content or b"")
        )
        status = response.status_code
        try:
            parsed = orjson.loads(body)
        except (orjson.JSONDecodeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            data: dict[str, Any] = parsed
        else:
            # dpop.py checks the body text for definitive account blocks, so
            # carry the raw text through even when it is not JSON.
            data = {"raw_body": body.decode("utf-8", errors="replace")}
        return status, data

    return post_json


def _make_request_fn(
    session: ResettableSession, timeout_s: float
) -> Callable[
    [dict[str, str], bytes | None], Awaitable[tuple[int, bytes, dict[str, str]]]
]:
    """``request_fn`` for do_dpop_request: the DPoP-authenticated GET /v1/usage."""

    async def request_fn(
        headers: dict[str, str], _body: bytes | None
    ) -> tuple[int, bytes, dict[str, str]]:
        response = await session.get(CONSOLE_USAGE, headers=headers, timeout=timeout_s)
        resp_body = (
            response.content
            if isinstance(response.content, bytes)
            else bytes(response.content or b"")
        )
        return response.status_code, resp_body, {}

    return request_fn


# ---------------------------------------------------------------------------
# Response handling
# ---------------------------------------------------------------------------


async def _handle_dpop_token_error(
    proxy: Any, lease: Any, exc: DPoPTokenEndpointError
) -> NoReturn:
    """Relay a DPoP token-endpoint failure as if the GET returned it (Go doDPoPRequest)."""
    status = exc.status
    body_text = exc.body.decode("utf-8", errors="replace")
    if status == 401 or (status == 403 and _is_definitive_block_body(body_text)):
        await proxy.feedback(lease, _feedback_forbidden(status, body_text))
        raise UpstreamError(
            f"Console usage rejected: DPoP token endpoint {status}",
            status=status,
            body=body_text,
            credential_rejected=True,
        ) from exc
    if status == 403:
        await proxy.feedback(lease, _feedback_forbidden(status, body_text))
        raise ConsoleClearanceRequiredError(
            f"Console usage rejected: DPoP token endpoint {status}",
            status=403,
            body=body_text,
            invalidate_clearance=exc.invalidate_clearance,
        ) from exc
    await proxy.feedback(lease, _status_feedback(status))
    raise UpstreamError(
        f"Console usage rejected: DPoP token endpoint {status}",
        status=status,
        body=body_text,
    ) from exc


async def _handle_usage_status(
    proxy: Any, lease: Any, status: int, body_text: str
) -> NoReturn:
    """Map a non-2xx GET /v1/usage status to proxy feedback + a typed error."""
    if status == 401 or (status == 403 and _is_definitive_block_body(body_text)):
        await proxy.feedback(lease, _feedback_forbidden(status, body_text))
        raise UpstreamError(
            f"Console usage rejected: {status}",
            status=status,
            body=body_text,
            credential_rejected=True,
        )
    if status == 403:
        # Go lease.InvalidateClearance() — clearance/egress is bad, not the account.
        await proxy.feedback(lease, _feedback_forbidden(status, body_text))
        raise ConsoleClearanceRequiredError(
            f"Console usage returned {status}",
            status=403,
            body=body_text,
            invalidate_clearance=True,
        )
    await proxy.feedback(lease, _status_feedback(status))
    raise UpstreamError(
        f"Console usage returned {status}", status=status, body=body_text
    )


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------


def parse_console_usage_payload(
    payload: dict[str, Any], now_ms: int | None = None
) -> ConsoleUsageResult:
    """Validate the ``{quotas: [...]}`` payload and build the three windows.

    Go semantics: all three kinds (chat/image/video) must be present, each
    must satisfy ``0 <= used/remaining <= limit``; the chat window gets the
    predicted 24h recovery window (``reset_at = now + 24h`` iff ``remaining == 0``),
    image/video are display-only (``window_seconds = 0``, no reset).
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    raw_quotas = payload.get("quotas")
    if not isinstance(raw_quotas, list):
        raise ConsoleQuotaError("Console usage response missing quotas")
    by_kind: dict[str, dict[str, Any]] = {}
    for quota in raw_quotas:
        if not isinstance(quota, dict):
            continue
        kind = str(quota.get("kind", "")).strip().lower()
        if kind:
            by_kind[kind] = quota
    missing = [kind for kind in _CONSOLE_QUOTA_KINDS if kind not in by_kind]
    if missing:
        raise ConsoleQuotaError(
            f"Console usage response missing quota kind(s): {', '.join(missing)}"
        )

    windows: dict[str, QuotaWindow] = {}
    used: dict[str, int] = {}
    for kind in _CONSOLE_QUOTA_KINDS:
        quota = by_kind[kind]
        limit = quota.get("limit")
        used_now = quota.get("used")
        remaining = quota.get("remaining")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not isinstance(used_now, int)
            or isinstance(used_now, bool)
            or not isinstance(remaining, int)
            or isinstance(remaining, bool)
        ):
            raise ConsoleQuotaError(f"Console {kind} quota fields invalid")
        if limit < 0 or used_now < 0 or remaining < 0 or remaining > limit:
            raise ConsoleQuotaError(
                f"Console {kind} quota invalid: limit={limit} used={used_now} remaining={remaining}"
            )
        window_seconds = 0
        reset_at: int | None = None
        if kind == "chat":
            window_seconds = CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S
            if remaining == 0:
                reset_at = now_ms + window_seconds * 1000
        windows[kind] = QuotaWindow(
            remaining=remaining,
            total=limit,
            window_seconds=window_seconds,
            reset_at=reset_at,
            synced_at=now_ms,
            source=QuotaSource.REAL,
        )
        used[kind] = used_now
    return ConsoleUsageResult(
        chat=windows["chat"],
        image=windows["image"],
        video=windows["video"],
        used=used,
        raw=payload,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def fetch_console_usage(
    token: str, *, timeout_s: float = CONSOLE_QUOTA_TIMEOUT_S
) -> ConsoleUsageResult:
    """Fetch the real console quota via a DPoP-authenticated GET /v1/usage.

    Acquires a proxy lease (console clearance origin), mints/loads a DPoP
    session, performs the GET, validates the payload strictly, and reports
    proxy feedback. Raises the typed errors described at module top.
    """
    from app.dataplane.proxy import get_proxy_runtime

    proxy = await get_proxy_runtime()
    lease = await proxy.acquire(clearance_origin=CONSOLE_BASE)

    # ponytail: per-call DPoP manager — the manager's post_json_fn needs the
    # per-lease headers, so no cross-call token cache (one extra token POST per
    # quota fetch). Promote to a module-level manager with per-call header
    # injection if quota-fetch latency matters.
    async with ResettableSession(**build_session_kwargs(lease=lease)) as session:
        manager = DPoPSessionManager(
            _make_post_json_fn(session, token, lease, timeout_s),
            is_definitive_block=_is_definitive_block_body,
        )
        try:
            status, resp_body, _ = await do_dpop_request(
                manager,
                method="GET",
                url=CONSOLE_USAGE,
                body=None,
                accept="application/json",
                credential_id=0,
                node_id=0,
                sso_token=token,
                request_fn=_make_request_fn(session, timeout_s),
            )
        except DPoPTokenEndpointError as exc:
            await _handle_dpop_token_error(proxy, lease, exc)
        except DPoPError as exc:
            await proxy.feedback(
                lease, ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
            )
            raise UpstreamError(f"Console DPoP failed: {exc}", status=502) from exc
        except UpstreamError:
            # ResettableSession wraps transport failures into 502 UpstreamError.
            await proxy.feedback(
                lease, ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
            )
            raise
        except Exception as exc:
            await proxy.feedback(
                lease, ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
            )
            raise UpstreamError(
                f"Console usage fetch failed: {exc}", status=502
            ) from exc

        body_text = resp_body.decode("utf-8", errors="replace")

        if status < 200 or status >= 300:
            await _handle_usage_status(proxy, lease, status, body_text)

        await proxy.feedback(
            lease, ProxyFeedback(kind=ProxyFeedbackKind.SUCCESS, status_code=status)
        )

        try:
            payload = orjson.loads(resp_body)
        except (orjson.JSONDecodeError, ValueError):
            raise ConsoleQuotaError(
                "Console usage response is not valid JSON"
            ) from None
        if not isinstance(payload, dict):
            raise ConsoleQuotaError("Console usage response is not a JSON object")
        result = parse_console_usage_payload(payload)
        logger.debug(
            "console usage fetched: chat_remaining={} chat_total={} image_remaining={} video_remaining={}",
            result.chat.remaining,
            result.chat.total,
            result.image.remaining,
            result.video.remaining,
        )
        return result


__all__ = [
    "CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S",
    "CONSOLE_QUOTA_TIMEOUT_S",
    "ConsoleClearanceRequiredError",
    "ConsoleQuotaError",
    "ConsoleUsageResult",
    "fetch_console_usage",
    "parse_console_usage_payload",
]
