"""Platform-level exception hierarchy."""

from enum import StrEnum

import orjson


class ErrorKind(StrEnum):
    VALIDATION = "invalid_request_error"
    AUTHENTICATION = "authentication_error"
    RATE_LIMIT = "rate_limit_exceeded"
    UPSTREAM = "upstream_error"
    SERVER = "server_error"


class AppError(Exception):
    """Base exception for all application errors."""

    def __init__(
        self,
        message: str,
        *,
        kind: ErrorKind = ErrorKind.SERVER,
        code: str = "internal_error",
        status: int = 500,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.code = code
        self.status = status
        self.details = details or {}

    def to_dict(self) -> dict:
        err = {
            "message": self.message,
            "type": self.kind,
            "code": self.code,
            "param": self.details.get("param", None),
        }
        # body is intentionally exposed for upstream debugging
        if "body" in self.details:
            err["body"] = self.details["body"]
        return {"error": err}


class ValidationError(AppError):
    def __init__(
        self, message: str, *, param: str = "", code: str = "invalid_value"
    ) -> None:
        super().__init__(
            message,
            kind=ErrorKind.VALIDATION,
            code=code,
            status=400,
            details={"param": param},
        )
        self.param = param


class AuthError(AppError):
    def __init__(self, message: str = "Invalid or missing API key") -> None:
        super().__init__(
            message,
            kind=ErrorKind.AUTHENTICATION,
            code="invalid_api_key",
            status=401,
        )


class RateLimitError(AppError):
    def __init__(self, message: str = "No available accounts") -> None:
        super().__init__(
            message,
            kind=ErrorKind.RATE_LIMIT,
            code="rate_limit_exceeded",
            status=429,
        )


class UpstreamError(AppError):
    """Upstream request failure with structured classification.

    Boolean flags (all ``False`` when unknown):
        account_scoped             — failure is specific to one account (not global)
        permanent_account_denial   — account is permanently banned
        quota_exhausted            — paid quota exhausted (includes free exhaustion)
        free_quota_exhausted       — free-tier quota exhausted
        model_quota_exhausted      — per-model free quota exhausted
        credential_rejected        — access token / cookie rejected
        safety_rejected            — request-level content safety denial (not account-scoped)
        upstream_code              — upstream error code string (e.g. ``access_denied``)
        fingerprint                — dedup key ``status:normalized_code``
    """

    def __init__(
        self,
        message: str,
        *,
        status: int = 502,
        body: str = "",
        account_scoped: bool = False,
        permanent_account_denial: bool = False,
        quota_exhausted: bool = False,
        free_quota_exhausted: bool = False,
        model_quota_exhausted: bool = False,
        credential_rejected: bool = False,
        safety_rejected: bool = False,
        upstream_code: str = "",
        fingerprint: str = "",
    ) -> None:
        _details: dict = {"body": body}
        if fingerprint:
            _details["fingerprint"] = fingerprint
        if upstream_code:
            _details["upstream_code"] = upstream_code
        super().__init__(
            message,
            kind=ErrorKind.UPSTREAM,
            code="upstream_error",
            status=status,
            details=_details,
        )
        self.account_scoped = account_scoped
        self.permanent_account_denial = permanent_account_denial
        self.quota_exhausted = quota_exhausted
        self.free_quota_exhausted = free_quota_exhausted
        self.model_quota_exhausted = model_quota_exhausted
        self.credential_rejected = credential_rejected
        self.safety_rejected = safety_rejected
        self.upstream_code = upstream_code
        self.fingerprint = fingerprint

    @staticmethod
    def from_http_response(
        message: str,
        *,
        status: int = 502,
        body: str = "",
    ) -> "UpstreamError":
        upstream_code, upstream_type, upstream_message = _extract_error_metadata(body)
        fingerprint, kw = _classify_upstream_status(
            status,
            upstream_code,
            upstream_type,
            upstream_message,
            body=body,
        )
        upstream_code = upstream_code or ""
        kw["fingerprint"] = fingerprint
        return UpstreamError(
            message,
            status=status,
            body=body,
            upstream_code=upstream_code,
            **kw,
        )

    def to_feedback_kind(self) -> "FeedbackKind":
        from app.control.account.enums import FeedbackKind

        if self.credential_rejected or self.status == 401:
            return FeedbackKind.UNAUTHORIZED
        if self.quota_exhausted or self.status == 429:
            return FeedbackKind.RATE_LIMITED
        if self.permanent_account_denial or self.status == 403:
            return FeedbackKind.FORBIDDEN
        if self.status >= 500:
            return FeedbackKind.SERVER_ERROR
        return FeedbackKind.SERVER_ERROR

    def to_proxy_feedback_kind(self) -> "ProxyFeedbackKind":
        from app.control.proxy.models import ProxyFeedbackKind

        if self.credential_rejected or self.status == 401:
            return ProxyFeedbackKind.UNAUTHORIZED
        if self.quota_exhausted or self.status == 429:
            return ProxyFeedbackKind.RATE_LIMITED
        if self.permanent_account_denial or self.status == 403:
            return ProxyFeedbackKind.FORBIDDEN
        if self.status >= 500:
            return ProxyFeedbackKind.UPSTREAM_5XX
        return ProxyFeedbackKind.TRANSPORT_ERROR


def _normalize_failure_code(value: str) -> str:
    cleaned: list[str] = []
    for ch in value.lower().strip():
        if ch.isalnum():
            cleaned.append(ch)
        elif ch in "-_.:":
            cleaned.append("_")
        if len(cleaned) >= 48:
            break
    return "".join(cleaned).strip("_")


def _extract_error_metadata(body: str) -> tuple[str, str, str]:
    if not body:
        return ("", "", "")
    try:
        payload = orjson.loads(body)
    except (orjson.JSONDecodeError, ValueError, TypeError):
        return ("", "", body[:200])
    if not isinstance(payload, dict):
        return ("", "", "")
    error = payload.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or payload.get("code") or "")
        etype = str(error.get("type") or payload.get("type") or "")
        msg = str(
            error.get("message") or error.get("error") or payload.get("message") or ""
        )
        return (code, etype, msg)
    code = str(payload.get("code") or "")
    etype = str(payload.get("type") or "")
    msg = str(payload.get("error") or payload.get("message") or "")
    return (code, etype, msg)


def _classify_upstream_status(
    status: int,
    upstream_code: str,
    upstream_type: str,
    upstream_message: str,
    *,
    body: str = "",
) -> tuple[str, dict]:
    kw: dict[str, bool | str] = {
        "account_scoped": False,
        "permanent_account_denial": False,
        "quota_exhausted": False,
        "free_quota_exhausted": False,
        "model_quota_exhausted": False,
        "credential_rejected": False,
        "safety_rejected": False,
    }
    text = " ".join([upstream_code, upstream_type, upstream_message]).lower()
    if status == 401:
        kw["account_scoped"] = True
        kw["credential_rejected"] = True
    elif status == 402:
        kw["account_scoped"] = True
        kw["quota_exhausted"] = True
    elif status == 403:
        # Safety denials are request-scoped: inspect both structured metadata
        # and the raw body so SAFETY_CHECK_TYPE_* markers still match when they
        # only appear in nested text. Port of d00698ac.
        if _is_safety_rejection(text) or (
            body and _is_safety_rejection(str(body).lower())
        ):
            kw["safety_rejected"] = True
        else:
            if "access to the chat endpoint is denied" in text:
                kw["permanent_account_denial"] = True
            # Bare permission-denied is not enough: content safety rejections
            # and other policy 403s share that code (d1205d85). Exact match on
            # the joined text (locked by legacy callers passing the code) and
            # on the message alone (Go semantics: code may be e.g. operation-denied).
            if (
                text.strip(" .!\t\r\n") == "access denied"
                or upstream_message.lower().strip(" .!\t\r\n") == "access denied"
            ):
                kw["permanent_account_denial"] = True
            # free-usage and per-model free usage are distinct so their
            # recovery scopes stay independent (d1205d85).
            if "used all the included free usage for model" in text:
                kw["model_quota_exhausted"] = True
            if "subscription:free-usage-exhausted" in text:
                kw["free_quota_exhausted"] = True
            if "personal-team-blocked:spending-limit" in text:
                kw["quota_exhausted"] = True
            kw["quota_exhausted"] = kw["quota_exhausted"] or kw["free_quota_exhausted"]
            if not kw["quota_exhausted"] and _contains_any(
                text,
                "authentication",
                "unauthorized",
                "invalid token",
                "token expired",
                "invalid-credentials",
                "bad-credentials",
                "blocked-user",
                "email-domain-rejected",
                "session not found",
                "session-expired",
                "failed to look up session id",
                "account suspended",
                "token revoked",
            ):
                kw["credential_rejected"] = True
            kw["account_scoped"] = (
                kw["permanent_account_denial"]
                or kw["quota_exhausted"]
                or kw["credential_rejected"]
            )
            if _contains_any(
                text,
                "quota",
                "billing",
                "subscription",
                "entitlement",
                "token",
                "usage-exhausted",
                "insufficient",
                "spending-limit",
            ):
                kw["account_scoped"] = True
    elif status == 429:
        kw["account_scoped"] = True
        if "used all the included free usage for model" in text:
            kw["model_quota_exhausted"] = True
        if "subscription:free-usage-exhausted" in text:
            kw["free_quota_exhausted"] = True
        if "personal-team-blocked:spending-limit" in text:
            kw["quota_exhausted"] = True
        kw["quota_exhausted"] = kw["quota_exhausted"] or kw["free_quota_exhausted"]
    fingerprint_part = _normalize_failure_code(
        upstream_code or upstream_type or upstream_message or "unknown"
    )
    return f"{status}:{fingerprint_part}", kw


def _is_safety_rejection(text: str) -> bool:
    """Return whether *text* marks a request-level content safety denial."""
    lower = text.lower()
    return "content violates usage guidelines" in lower or "safety_check_type_" in lower


def _contains_any(text: str, *signals: str) -> bool:
    return any(s in text for s in signals)


_DEFAULT_403_INVALIDATION_CODES: frozenset[str] = frozenset(
    {
        "blocked-user",
        "user is blocked",
        "email-domain-rejected",
        "account suspended",
        "token revoked",
    }
)


def should_invalidate_build_forbidden(
    status: int,
    upstream_code: str,
    upstream_message: str,
    *,
    safety_rejected: bool = False,
    account_scoped: bool = False,
) -> bool:
    """Check if a 403 error should trigger account invalidation.

    Port of 09388e5 + d00698ac: configurable Grok Build 403 invalidation rules.
    Safety rejections are request-scoped and must never invalidate; only
    account-scoped failures (quota / credential / permanent denial) qualify.
    Matches upstream_code and upstream_message against configurable error codes
    (features.build_403_invalidation_codes) or the built-in default list.
    """
    if status != 403:
        return False
    if safety_rejected:
        return False
    if not account_scoped:
        return False
    from app.platform.config.snapshot import get_config

    custom = get_config("features.build_403_invalidation_codes", "")
    if isinstance(custom, str) and custom.strip():
        codes = {c.strip().lower() for c in custom.split(",") if c.strip()}
    else:
        codes = set(_DEFAULT_403_INVALIDATION_CODES)
    text = f"{upstream_code} {upstream_message}".lower()
    return any(code in text for code in codes)


class StreamIdleTimeout(AppError):
    def __init__(self, timeout_s: float) -> None:
        super().__init__(
            f"Stream idle timeout after {timeout_s}s",
            kind=ErrorKind.UPSTREAM,
            code="stream_idle_timeout",
            status=504,
        )


__all__ = [
    "ErrorKind",
    "AppError",
    "ValidationError",
    "AuthError",
    "RateLimitError",
    "UpstreamError",
    "StreamIdleTimeout",
]
