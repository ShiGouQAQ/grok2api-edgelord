"""Admin Build-account availability detection.

Port of Go bcc6435f + b4c7baab (scoped): each Build account is probed with a
fixed non-streaming POST /responses (model ``grok-4.5``, input ``hello,test``)
and the response is classified via errors.py's UpstreamError flags:

  ok      — 2xx response
  invalid — OAuth credential rejected (401 after one manual refresh retry, or
            permanent refresh failure) → REAUTH_REQUIRED
  failed  — network error / quota exhaustion / model denial / non-2xx
            (no account-state change)

softNetworkCooldown semantics (Go selector_session): network failures
(status==0) never bump reauth/expired counters — the account's fail state is
left untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import orjson

from app.platform.config.snapshot import get_config
from app.platform.errors import UpstreamError, should_invalidate_build_forbidden
from app.platform.logging.logger import logger
from app.platform.runtime.clock import now_ms

from .build_refresh import refresh_build_token, refresh_build_token_manual
from .invalid_credentials import mark_account_reauth_required

if TYPE_CHECKING:
    from .repository import AccountRepository

BUILD_DETECT_MODEL = "grok-4.5"
BUILD_DETECT_PROMPT = "hello,test"

# Go softNetworkCooldown: transport/5xx failures isolate the account for only
# 5s instead of accumulating fail count. The 5s isolation itself lives in the
# routing selector (Wave-2 F); detection only guarantees no counter bumps.
SOFT_NETWORK_COOLDOWN_SEC = 5

_DETECT_TIMEOUT_S = 30.0


async def detect_build_account(
    repo: "AccountRepository",
    token: str,
    *,
    max_attempts: int = 999,
) -> dict[str, Any]:
    """Detect availability of one Build account; returns outcome dict.

    ``max_attempts`` gates the 401 refresh-retry (account.build_detect
    .max_attempts, 0 rejected by the endpoint): <= 1 means no refresh retry.
    """
    records = await repo.get_accounts([token])
    record = records[0] if records else None
    if record is None or record.is_deleted():
        return {"outcome": "failed", "reason": "account not found", "httpStatus": 0}
    if record.provider != "grok_build":
        return {
            "outcome": "failed",
            "reason": "仅 Grok Build 账号支持可用性检测",
            "httpStatus": 0,
        }

    ext = dict(record.ext or {})
    now = now_ms()
    access_token = str(ext.get("build_access_token") or record.token)
    expires_at = int(ext.get("build_expires_at") or 0)

    # Go EnsureCredential(false): permanent-marked + dead access token →
    # reauth without any OAuth request (resolvePermanentRefreshFailure).
    if ext.get("build_refresh_permanent") and expires_at > 0 and expires_at <= now:
        await _mark_invalid(
            repo,
            record.token,
            "Build OAuth access token expired after permanent refresh failure",
        )
        return {
            "outcome": "invalid",
            "reason": "Build OAuth access token expired after permanent refresh failure",
            "httpStatus": 0,
        }

    # Expired access token (not permanent-marked) → refresh once before probe.
    if expires_at > 0 and expires_at <= now and ext.get("build_refresh_token"):
        refreshed = await refresh_build_token(str(ext["build_refresh_token"]))
        if refreshed is None:
            await _apply_permanent_refresh_failure(repo, record.token, ext)
            await _mark_invalid(
                repo,
                record.token,
                "Build OAuth refresh credential permanently rejected",
            )
            return {
                "outcome": "invalid",
                "reason": "Build OAuth refresh credential permanently rejected",
                "httpStatus": 0,
            }
        ext = _apply_refreshed_tokens(ext, refreshed, now)
        await _persist_ext(repo, record.token, ext)

    status, body = await _probe_classified(
        str(ext.get("build_access_token") or access_token)
    )
    if status == 0:
        return {
            "outcome": "failed",
            "reason": body,
            "httpStatus": 0,
        }
    if status == 401:
        return await _handle_detect_401(
            repo, record.token, ext, max_attempts=max_attempts
        )
    return await _classify_detect_response(repo, record.token, status, body)


async def _handle_detect_401(
    repo: "AccountRepository",
    token: str,
    ext: dict[str, Any],
    *,
    max_attempts: int,
) -> dict[str, Any]:
    """401 → refresh credentials once (manual retry) → retry probe.

    Port of Go handleBuildDetectUnauthorized + ef10c4cb retryPermanentOnce:
    the admin-initiated retry bypasses the permanent-refresh short-circuit
    once; after that single retry the permanent status returns (marker
    re-applied on failure).
    """
    refresh_token = str(ext.get("build_refresh_token") or "")
    if not refresh_token or max_attempts <= 1:
        await _mark_invalid(
            repo, token, "Grok Build OAuth credential rejected after refresh"
        )
        return {
            "outcome": "invalid",
            "reason": "Grok Build OAuth credential rejected after refresh",
            "httpStatus": 401,
        }

    try:
        refreshed = await refresh_build_token_manual(token, refresh_token)
    except RuntimeError:
        # Singleflight guard: another manual retry is in flight — do not
        # issue a second OAuth request; no state change.
        return {
            "outcome": "failed",
            "reason": "manual retry already in progress",
            "httpStatus": 401,
        }

    if refreshed is None:
        # Permanent failure: re-apply the permanent marker (permanent status
        # returns after the single bypass attempt) and mark invalid.
        await _apply_permanent_refresh_failure(repo, token, ext)
        await _mark_invalid(
            repo, token, "Grok Build OAuth credential rejected after refresh"
        )
        return {
            "outcome": "invalid",
            "reason": "Grok Build OAuth credential rejected after refresh",
            "httpStatus": 401,
        }

    ext = _apply_refreshed_tokens(ext, refreshed, now_ms())
    await _persist_ext(repo, token, ext)
    status, body = await _probe_classified(str(ext["build_access_token"]))
    if status == 0:
        return {"outcome": "failed", "reason": body, "httpStatus": 0}
    if status == 401:
        await _mark_invalid(
            repo, token, "Grok Build OAuth credential rejected after refresh"
        )
        return {
            "outcome": "invalid",
            "reason": "Grok Build OAuth credential rejected after refresh",
            "httpStatus": 401,
        }
    return await _classify_detect_response(repo, token, status, body)


async def _classify_detect_response(
    repo: "AccountRepository",
    token: str,
    status: int,
    body: str,
) -> dict[str, Any]:
    """Classify the probe response via errors.py's UpstreamError flags.

    Port of Go finishBuildDetectResponse + b4c7baab quota signals:
    credential rejection → invalid (reauth); quota exhaustion / model denial /
    non-2xx → failed with no state change.
    """
    exc = UpstreamError.from_http_response(
        f"Build detection HTTP {status}", status=status, body=body
    )
    if exc.credential_rejected:
        if status == 403 and not _chat_denial_marks_reauth(status, exc):
            # Chat 403 blocked-user/denial: model-scoped by default (Go
            # markBuildChatDeniedAsReauth=false) — account stays in pool.
            return {
                "outcome": "failed",
                "reason": f"Build chat endpoint access denied for {BUILD_DETECT_MODEL}",
                "httpStatus": status,
            }
        await _mark_invalid(
            repo, token, f"Build OAuth credential rejected (HTTP {status})"
        )
        return {
            "outcome": "invalid",
            "reason": f"Build OAuth credential rejected (HTTP {status})",
            "httpStatus": status,
        }
    if exc.quota_exhausted or exc.free_quota_exhausted or exc.model_quota_exhausted:
        return {
            "outcome": "failed",
            "reason": "Build quota exhausted",
            "httpStatus": status,
        }
    if exc.permanent_account_denial:
        return {
            "outcome": "failed",
            "reason": f"Build chat endpoint access denied for {BUILD_DETECT_MODEL}",
            "httpStatus": status,
        }
    if status < 200 or status >= 300:
        return {
            "outcome": "failed",
            "reason": f"upstream detect failed: HTTP {status}",
            "httpStatus": status,
        }
    return {"outcome": "ok", "reason": "", "httpStatus": status}


def _chat_denial_marks_reauth(status: int, exc: UpstreamError) -> bool:
    """Gate 403 chat-denial → reauth on config + errors.py invalidation rules.

    account.build_detect.mark_build_chat_denied_as_reauth (default false)
    mirrors Go RoutingConfig.MarkBuildChatDeniedAsReauth; the code-list gate
    reuses Task-1-A's should_invalidate_build_forbidden (safety rejections
    never invalidate).
    """
    if not get_config("account.build_detect.mark_build_chat_denied_as_reauth", False):
        return False
    return should_invalidate_build_forbidden(
        status,
        str((exc.details or {}).get("upstream_code") or ""),
        (exc.details or {}).get("body", ""),
        safety_rejected=exc.safety_rejected,
        account_scoped=exc.account_scoped,
    )


async def _persist_ext(
    repo: "AccountRepository", token: str, ext: dict[str, Any]
) -> None:
    """Persist a refreshed ext snapshot (selection-session reuse: the updated
    credential is carried through the rest of the detection, not reloaded)."""
    from .commands import AccountPatch

    await repo.patch_accounts([AccountPatch(token=token, ext_merge=ext)])


async def _mark_invalid(repo: "AccountRepository", token: str, reason: str) -> None:
    """Mark the account REAUTH_REQUIRED (Go markBuildDetectReauth)."""
    try:
        await mark_account_reauth_required(repo, token, reason, source="build detect")
    except Exception as exc:
        logger.warning(
            "build detect reauth mark failed: token={}... error={}", token[:10], exc
        )


async def _apply_permanent_refresh_failure(
    repo: "AccountRepository", token: str, ext: dict[str, Any]
) -> None:
    """Re-apply the permanent-refresh marker (permanent status returns)."""
    from .commands import AccountPatch

    await repo.patch_accounts(
        [
            AccountPatch(
                token=token,
                ext_merge={
                    **ext,
                    "build_refresh_permanent": True,
                    "build_refresh_error": "refresh_token_invalid",
                },
            )
        ]
    )


def _apply_refreshed_tokens(
    ext: dict[str, Any], refreshed: Any, now: int
) -> dict[str, Any]:
    """Merge a successful refresh into ext and clear the permanent marker."""
    from .build_refresh import compute_refresh_due_at

    new_expires_at = now + int(getattr(refreshed, "expires_in", 3600)) * 1000
    return {
        **ext,
        "build_access_token": refreshed.access_token,
        "build_refresh_token": refreshed.refresh_token
        or ext.get("build_refresh_token", ""),
        "build_id_token": getattr(refreshed, "id_token", "")
        or ext.get("build_id_token", ""),
        "build_expires_at": int(new_expires_at),
        "build_refresh_due_at": int(
            compute_refresh_due_at(new_expires_at / 1000, refreshed.access_token) * 1000
        ),
        "build_refresh_permanent": False,
        "build_refresh_error": "",
    }


async def _probe_classified(access_token: str) -> tuple[int, str]:
    """Probe, mapping transport failures to (0, reason).

    softNetworkCooldown: a status==0 failure is a failed outcome — the
    caller must not accumulate reauth/expired counters for it.
    """
    try:
        return await _probe_build(access_token)
    except UpstreamError as exc:
        return 0, str(exc)


async def _probe_build(access_token: str) -> tuple[int, str]:
    """POST /responses non-streaming with the fixed detection probe.

    Returns (status_code, body). Transport failures raise UpstreamError with
    status=0 — the softNetworkCooldown signal (no fail-count accumulation).
    """
    from app.dataplane.proxy import get_proxy_runtime
    from app.dataplane.proxy.adapters.headers import build_build_headers
    from app.dataplane.proxy.adapters.session import (
        ResettableSession,
        build_session_kwargs,
    )
    from app.dataplane.reverse.runtime.endpoint_table import (
        BUILD_BASE,
        BUILD_RESPONSES,
    )

    proxy = await get_proxy_runtime()
    lease = await proxy.acquire(clearance_origin=BUILD_BASE)
    headers = build_build_headers(
        access_token=access_token,
        agent_id="build-detect",
        model=BUILD_DETECT_MODEL,
        is_stream=False,
        is_trace=False,
    )
    payload = orjson.dumps(
        {
            "model": BUILD_DETECT_MODEL,
            "input": BUILD_DETECT_PROMPT,
            "stream": False,
            "max_output_tokens": 256,
        }
    )
    session_kwargs = build_session_kwargs(lease=lease, disable_fingerprint=True)

    async with ResettableSession(**session_kwargs) as session:
        try:
            response = await session.post(
                BUILD_RESPONSES,
                headers=headers,
                data=payload,
                timeout=_DETECT_TIMEOUT_S,
            )
        except Exception as exc:
            await proxy.feedback(
                lease,
                _transport_feedback(),
            )
            raise UpstreamError(
                f"Build detection transport failed: {exc}", status=0
            ) from exc
        body = await response.atext()
        return response.status_code, body


def _transport_feedback():
    from app.control.proxy.models import ProxyFeedback, ProxyFeedbackKind

    return ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)


__all__ = [
    "BUILD_DETECT_MODEL",
    "BUILD_DETECT_PROMPT",
    "SOFT_NETWORK_COOLDOWN_SEC",
    "detect_build_account",
]
