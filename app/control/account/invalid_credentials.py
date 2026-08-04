"""Shared handling for upstream invalid-credential failures."""

from typing import TYPE_CHECKING

from app.platform.errors import UpstreamError
from app.platform.logging.logger import logger
from app.platform.runtime.clock import now_ms

from .commands import AccountPatch
from .enums import AccountStatus, FeedbackKind

if TYPE_CHECKING:
    from .repository import AccountRepository


async def mark_account_invalid_credentials(
    repo: "AccountRepository",
    token: str,
    exc: BaseException,
    *,
    source: str,
) -> bool:
    """Mark *token* as invalid when *exc* matches Grok invalid credentials."""
    from app.dataplane.reverse.protocol.xai_usage import is_invalid_credentials_error

    if not is_invalid_credentials_error(exc):
        return False

    record = next(iter(await repo.get_accounts([token])), None)
    reason = "invalid_credentials"
    # SSO-class accounts (Web/Console, incl. image/video which normalize to
    # grok_web) get REAUTH_REQUIRED instead of EXPIRED — the SSO cookie may
    # still work elsewhere, so preserve the account. Only build-token deaths
    # (provider grok_build) hard-expire. Mirrors refresh._expire_invalid_credentials.
    if (
        record is not None
        and not record.is_deleted()
        and record.provider in ("grok_web", "grok_console")
    ):
        return await mark_account_reauth_required(repo, token, reason, source=source)
    ts = now_ms()
    ext = record.ext if record is not None else {}

    await repo.patch_accounts(
        [
            AccountPatch(
                token=token,
                status=AccountStatus.EXPIRED,
                last_fail_at=ts,
                last_fail_reason=reason,
                state_reason=reason,
                ext_merge={
                    **ext,
                    "expired_at": ts,
                    "expired_reason": reason,
                },
            )
        ]
    )
    logger.info(
        "account expired from {}: token={}... status={} upstream_status={}",
        source,
        token[:10],
        AccountStatus.EXPIRED,
        getattr(exc, "status", None) if isinstance(exc, UpstreamError) else None,
    )
    return True


async def mark_account_reauth_required(
    repo: "AccountRepository",
    token: str,
    reason: str,
    *,
    source: str,
) -> bool:
    """Mark *token* as needing re-authentication (REAUTH_REQUIRED).

    Preserves the account (unlike EXPIRED) — it leaves the selection pool
    but stays recoverable via refresh success or manual restore. Mirrors Go
    MarkReauthRequired.
    """
    record = next(iter(await repo.get_accounts([token])), None)
    if record is None or record.is_deleted():
        return False
    reason = str(reason)[:512]
    ts = now_ms()
    ext = record.ext or {}
    await repo.patch_accounts(
        [
            AccountPatch(
                token=token,
                status=AccountStatus.REAUTH_REQUIRED,
                last_fail_at=ts,
                last_fail_reason=reason,
                state_reason=reason,
                ext_merge={
                    **ext,
                    "reauth_at": ts,
                    "reauth_reason": reason,
                },
            )
        ]
    )
    logger.info(
        "account reauth required from {}: token={}... status={}",
        source,
        token[:10],
        AccountStatus.REAUTH_REQUIRED,
    )
    return True


def feedback_kind_for_error(exc: BaseException | None) -> FeedbackKind:
    """Map an upstream exception to the appropriate account feedback kind."""
    if exc is None:
        return FeedbackKind.SERVER_ERROR
    if isinstance(exc, UpstreamError):
        return exc.to_feedback_kind()
    status = getattr(exc, "status", 0)
    if status == 429:
        return FeedbackKind.RATE_LIMITED
    if status == 401:
        return FeedbackKind.UNAUTHORIZED
    if status == 403:
        return FeedbackKind.FORBIDDEN
    return FeedbackKind.SERVER_ERROR


__all__ = [
    "feedback_kind_for_error",
    "mark_account_invalid_credentials",
    "mark_account_reauth_required",
]
