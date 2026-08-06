"""Console x.ai media protocol — Go→Python port of chenyme/grok2api
backend/internal/infra/provider/console/media.go (commit a05e06a2).

Image generation/editing and video generation via console.x.ai/v1 with DPoP
auth (RFC 9449). Reuses the DPoP machinery from dpop.py and the request
structure from xai_console_usage.py: proxy lease (console clearance origin) →
do_dpop_request (same-lease token exchange) → strict validation → typed
errors. Media downloads are NOT performed here — callers reuse
download_asset/get_bytes_stream which enforce the trusted-host allow-list
(trusted_hosts.py, ported from Go trustedConsoleImageURL/trustedConsoleVideoHost).

Error taxonomy (mirrors Go):
* 401, or 403 with a definitive account-block body → UpstreamError
  credential_rejected=True (the account itself is bad).
* 403 otherwise → ConsoleClearanceRequiredError (clearance/egress is bad,
  Go lease.InvalidateClearance()).
* Other non-2xx → new_console_media_upstream_error (Go
  newConsoleMediaUpstreamError: safe summary, 160-char cap, redaction).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlparse

import orjson

from app.control.proxy.models import ProxyFeedback, ProxyFeedbackKind
from app.dataplane.proxy.adapters.headers import build_console_headers
from app.dataplane.proxy.adapters.session import ResettableSession, build_session_kwargs
from app.dataplane.reverse.protocol.dpop import (
    DPoPError,
    DPoPSessionManager,
    DPoPTokenEndpointError,
    console_v1_endpoint,
    do_dpop_request,
)
from app.dataplane.reverse.protocol.xai_console_chat import _lease_node_id
from app.dataplane.reverse.protocol.xai_console_usage import (
    ConsoleClearanceRequiredError,
    _is_definitive_block_body,
)
from app.dataplane.reverse.runtime.endpoint_table import CONSOLE_BASE
from app.platform.errors import UpstreamError, ValidationError
from app.platform.logging.logger import logger

# Go constants (media.go): consoleMediaBodyLimit=2MiB (response reads),
# consoleVideoPollEvery=2s, consoleMaxEditImages=3, consoleMaxVideoImages=1,
# consoleMediaOutputAttempts=3 (download retries — covered by callers).
CONSOLE_MEDIA_TIMEOUT_S = 30.0
CONSOLE_VIDEO_POLL_EVERY_S = 2.0
CONSOLE_MAX_EDIT_IMAGES = 3
CONSOLE_MAX_VIDEO_IMAGES = 1
CONSOLE_MAX_IMAGE_N = 10
CONSOLE_MAX_VIDEO_DURATION_S = 15

# public model name → console.x.ai upstream model field
# (Go mediaCatalog UpstreamModel; Python keeps grok.com names untouched)
CONSOLE_MEDIA_MODELS: dict[str, str] = {
    "grok-imagine-image-quality-console": "grok-imagine-image-quality",
    "grok-imagine-image-console": "grok-imagine-image",
    "grok-imagine-video-console": "grok-imagine-video",
}

_VIDEO_DONE_STATUSES = frozenset({"done", "completed", "succeeded", "success", "ready"})
_VIDEO_FAILED_STATUSES = frozenset(
    {"failed", "expired", "cancelled", "canceled", "error"}
)
_VIDEO_PENDING_STATUSES = frozenset({"pending", "processing", "in_progress", "queued"})

_ASPECT_RATIO_MAP: dict[str, str] = {
    "1:1": "1:1",
    "16:9": "16:9",
    "9:16": "9:16",
    "4:3": "4:3",
    "3:4": "3:4",
    "3:2": "3:2",
    "2:3": "2:3",
    "2:1": "2:1",
    "1:2": "1:2",
    "1024x1024": "1:1",
    "1280x720": "16:9",
    "720x1280": "9:16",
    "1792x1024": "3:2",
    "1536x1024": "3:2",
    "1024x1792": "2:3",
    "1024x1536": "2:3",
}

_ERROR_SUMMARY_LIMIT = 160


@dataclass(slots=True, frozen=True)
class ConsoleVideoResult:
    url: str
    content_type: str = "video/mp4"


# ---------------------------------------------------------------------------
# Validation / normalization (Go normalizeConsole* / validConsoleMediaInputURL)
# ---------------------------------------------------------------------------


def normalize_console_image_format(value: str | None) -> str:
    fmt = (value or "").strip().lower()
    if not fmt:
        return "url"
    if fmt not in {"url", "b64_json"}:
        raise ValidationError(
            "response_format must be url or b64_json", param="response_format"
        )
    return fmt


def normalize_console_image_resolution(value: str | None) -> str:
    resolution = (value or "").strip().lower()
    if not resolution:
        return ""
    if resolution not in {"1k", "2k"}:
        raise ValidationError("resolution must be 1k or 2k", param="resolution")
    return resolution


def resolve_console_image_aspect_ratio(
    aspect_ratio: str | None, size: str | None
) -> str:
    value = (aspect_ratio or "").strip().lower()
    if not value:
        value = (size or "").strip().lower()
    if not value or value == "auto":
        return ""
    resolved = _ASPECT_RATIO_MAP.get(value)
    if not resolved:
        raise ValidationError(
            "aspect_ratio or size not supported", param="aspect_ratio"
        )
    return resolved


def valid_console_media_input_url(value: str, media_type: str) -> bool:
    """Go validConsoleMediaInputURL: https URL or ``data:{media_type}/...;base64,``."""
    lower = value.strip().lower()
    if lower.startswith(f"data:{media_type}/"):
        return ";base64," in lower
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc) and parsed.username is None


# ---------------------------------------------------------------------------
# Error helpers (Go newConsoleMediaUpstreamError / safeConsoleMedia*)
# ---------------------------------------------------------------------------


def safe_console_media_text(value: str) -> str:
    text = " ".join(value.split())
    if "authorization" in text.lower() or "cookie" in text.lower():
        return "上游拒绝请求"
    if len(text) <= _ERROR_SUMMARY_LIMIT:
        return text
    end = _ERROR_SUMMARY_LIMIT
    while end > 0:
        try:
            text[:end].encode("utf-8").decode("utf-8")
            break
        except UnicodeDecodeError:
            end -= 1
    return text[:end]


def safe_console_media_error_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("message", "code", "type"):
            raw = value.get(key)
            if raw is None:
                continue
            text = safe_console_media_text(str(raw))
            if text:
                return text
    if isinstance(value, str):
        return safe_console_media_text(value)
    return ""


def new_console_media_upstream_error(status: int, body: bytes) -> UpstreamError:
    """Go newConsoleMediaUpstreamError: ``Console 媒体上游返回 {status}: {message}``."""
    message = ""
    try:
        payload = orjson.loads(body)
    except (orjson.JSONDecodeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        message = safe_console_media_error_value(payload.get("error"))
        if not message:
            message = safe_console_media_error_value(payload.get("message"))
    summary = f"Console media upstream returned {status}"
    if message:
        summary += f": {message}"
    return UpstreamError(
        summary, status=status, body=body.decode("utf-8", errors="replace")
    )


# ---------------------------------------------------------------------------
# Video status parsing (Go parseConsoleVideoCreate / parseConsoleVideoStatus)
# ---------------------------------------------------------------------------


def parse_console_video_create(body: bytes) -> str:
    try:
        payload = orjson.loads(body)
    except (orjson.JSONDecodeError, ValueError) as exc:
        raise UpstreamError(
            f"Parse console video create response: {exc}", status=502
        ) from exc
    request_id = (
        str(payload.get("request_id") or "").strip()
        if isinstance(payload, dict)
        else ""
    )
    if not request_id:
        raise UpstreamError(
            "Console video create response missing request_id", status=502
        )
    return request_id


def parse_console_video_status(
    body: bytes,
) -> tuple[str, int, str | None, bool]:
    """Return ``(url, progress, error_message, done)`` (Go parseConsoleVideoStatus)."""
    try:
        payload = orjson.loads(body)
    except (orjson.JSONDecodeError, ValueError) as exc:
        raise UpstreamError(
            f"Parse console video status response: {exc}", status=502
        ) from exc
    if not isinstance(payload, dict):
        raise UpstreamError("Console video status response invalid", status=502)
    status = str(payload.get("status") or "").strip().lower()
    try:
        progress = min(99, max(0, int(payload.get("progress") or 0)))
    except (TypeError, ValueError):
        progress = 0
    video = payload.get("video")
    video_url = str(video.get("url") or "").strip() if isinstance(video, dict) else ""

    if status in _VIDEO_DONE_STATUSES:
        if not video_url:
            raise UpstreamError(
                "Console video generation completed without content URL", status=502
            )
        # Go reports min(99, progress) on every poll, including the done payload.
        return video_url, progress, None, True
    if status in _VIDEO_FAILED_STATUSES:
        error = safe_console_media_error_value(payload.get("error"))
        return "", progress, error or status, False
    if status in _VIDEO_PENDING_STATUSES:
        return "", progress, None, False
    raise UpstreamError(
        f"Console video status invalid: {safe_console_media_text(status)!r}",
        status=502,
    )


# ---------------------------------------------------------------------------
# DPoP wiring (xai_console_usage.py structure — same-lease token exchange)
# ---------------------------------------------------------------------------


def _make_post_json_fn(
    session: ResettableSession,
    token: str,
    lease: Any,
    timeout_s: float,
) -> Callable[
    [str, dict[str, str], dict[str, Any], Any],
    Awaitable[tuple[int, dict[str, Any]]],
]:
    async def post_json(
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        lease: Any | None = None,
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
            data = {"raw_body": body.decode("utf-8", errors="replace")}
        return status, data

    return post_json


def _make_request_fn(
    session: ResettableSession, method: str, url: str, timeout_s: float
) -> Callable[
    [dict[str, str], bytes | None], Awaitable[tuple[int, bytes, dict[str, str]]]
]:
    async def request_fn(
        headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes, dict[str, str]]:
        response = await session.request(
            method, url, headers=headers, data=body, timeout=timeout_s
        )
        resp_body = (
            response.content
            if isinstance(response.content, bytes)
            else bytes(response.content or b"")
        )
        return response.status_code, resp_body, {}

    return request_fn


async def _handle_dpop_token_error(
    proxy: Any, lease: Any, exc: DPoPTokenEndpointError
) -> None:
    status = exc.status
    body_text = exc.body.decode("utf-8", errors="replace")
    if status == 401 or (status == 403 and _is_definitive_block_body(body_text)):
        await proxy.feedback(
            lease,
            ProxyFeedback(
                kind=ProxyFeedbackKind.FORBIDDEN,
                status_code=status,
                reason=_parse_body_code(body_text),
            ),
        )
        raise UpstreamError(
            f"Console media rejected: DPoP token endpoint {status}",
            status=status,
            body=body_text,
            credential_rejected=True,
        ) from exc
    if status == 403:
        await proxy.feedback(
            lease,
            ProxyFeedback(
                kind=ProxyFeedbackKind.FORBIDDEN,
                status_code=status,
                reason=_parse_body_code(body_text),
                invalidate_clearance=exc.invalidate_clearance,
            ),
        )
        raise ConsoleClearanceRequiredError(
            f"Console media rejected: DPoP token endpoint {status}",
            status=403,
            body=body_text,
            invalidate_clearance=exc.invalidate_clearance,
        ) from exc
    await proxy.feedback(lease, _status_feedback(status))
    raise UpstreamError(
        f"Console media rejected: DPoP token endpoint {status}",
        status=status,
        body=body_text,
    ) from exc


def _parse_body_code(body: str) -> str:
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


def _status_feedback(status: int) -> ProxyFeedback:
    if status == 429:
        kind = ProxyFeedbackKind.RATE_LIMITED
    elif status >= 500:
        kind = ProxyFeedbackKind.UPSTREAM_5XX
    else:
        kind = ProxyFeedbackKind.FORBIDDEN
    return ProxyFeedback(kind=kind, status_code=status)


async def console_media_request(
    token: str,
    *,
    method: str,
    url: str,
    body: bytes | None,
    accept: str = "application/json",
    timeout_s: float = CONSOLE_MEDIA_TIMEOUT_S,
    lease: Any | None = None,
) -> tuple[int, bytes]:
    """One DPoP-authenticated console media request.

    Acquires its own lease (console clearance origin) unless *lease* is given
    (video keeps one lease across create + polls). Raises the typed errors
    described at module top; returns ``(status, body)`` on any HTTP status.
    """
    from app.dataplane.proxy import get_proxy_runtime

    proxy = await get_proxy_runtime()
    if lease is None:
        lease = await proxy.acquire(clearance_origin=CONSOLE_BASE)

    # ponytail: per-call DPoP manager — same structure as xai_console_usage.py;
    # no cross-call token cache (one extra token POST per media request).
    # Promote to the chat manager's per-token cache if media latency matters.
    async with ResettableSession(**build_session_kwargs(lease=lease)) as session:
        manager = DPoPSessionManager(
            _make_post_json_fn(session, token, lease, timeout_s),
            browser_headers=lambda lease: build_console_headers(token, lease=lease),
            is_definitive_block=_is_definitive_block_body,
        )
        try:
            status, resp_body, _ = await do_dpop_request(
                manager,
                method=method,
                url=url,
                body=body,
                accept=accept,
                credential_id=0,
                node_id=_lease_node_id(lease),
                sso_token=token,
                lease=lease,
                request_fn=_make_request_fn(session, method, url, timeout_s),
            )
        except DPoPTokenEndpointError as exc:
            await _handle_dpop_token_error(proxy, lease, exc)
        except DPoPError as exc:
            await proxy.feedback(
                lease, ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
            )
            raise UpstreamError(
                f"Console media DPoP failed: {exc}", status=502
            ) from exc
        except UpstreamError:
            await proxy.feedback(
                lease, ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
            )
            raise
        except Exception as exc:
            await proxy.feedback(
                lease, ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
            )
            raise UpstreamError(
                f"Console media request failed: {exc}", status=502
            ) from exc
        return status, resp_body


def _check_media_status(status: int, body: bytes) -> None:
    """Go forwardConsoleMedia/doConsoleVideoJSON non-2xx handling."""
    if 200 <= status < 300:
        return
    raise new_console_media_upstream_error(status, body)


def _parse_media_envelope(body: bytes) -> list[dict[str, Any]]:
    """Parse ``{data: [...]}`` (Go localizeConsoleImageResponse data checks)."""
    try:
        envelope = orjson.loads(body)
    except (orjson.JSONDecodeError, ValueError) as exc:
        raise UpstreamError(f"Parse console image response: {exc}", status=502) from exc
    items = envelope.get("data") if isinstance(envelope, dict) else None
    if not isinstance(items, list) or not items or len(items) > CONSOLE_MAX_IMAGE_N:
        raise UpstreamError("Console image response missing valid data", status=502)
    return [item for item in items if isinstance(item, dict)]


# ---------------------------------------------------------------------------
# Image generation (Go GenerateImage + forwardConsoleMedia)
# ---------------------------------------------------------------------------


async def generate_console_image(
    token: str,
    *,
    model: str,
    prompt: str,
    n: int = 1,
    size: str = "1024x1024",
    response_format: str = "url",
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    timeout_s: float = CONSOLE_MEDIA_TIMEOUT_S,
) -> dict[str, Any]:
    """POST console.x.ai/v1/images/generations; return the parsed envelope.

    Envelope items keep the upstream ``url`` field; output shaping (download /
    re-host / b64) is the caller's job so the external response matches the
    grok.com surface (images.py ``_resolve_image_output``).
    """
    if not (1 <= n <= CONSOLE_MAX_IMAGE_N):
        raise ValidationError("n must be between 1 and 10", param="n")
    fmt = normalize_console_image_format(response_format)
    ratio = resolve_console_image_aspect_ratio(aspect_ratio, size)
    res = normalize_console_image_resolution(resolution)
    payload: dict[str, Any] = {
        "model": CONSOLE_MEDIA_MODELS[model],
        "prompt": prompt,
        "n": n,
        "response_format": fmt,
    }
    if ratio:
        payload["aspect_ratio"] = ratio
    if res:
        payload["resolution"] = res
    status, body = await console_media_request(
        token,
        method="POST",
        url=console_v1_endpoint(CONSOLE_BASE, "/images/generations"),
        body=orjson.dumps(payload),
        timeout_s=timeout_s,
    )
    _check_media_status(status, body)
    items = _parse_media_envelope(body)
    fmt = normalize_console_image_format(response_format)
    for index, item in enumerate(items):
        if fmt == "b64_json":
            b64 = item.get("b64_json")
            if not isinstance(b64, str) or not b64.strip():
                raise UpstreamError(
                    f"Console image response item {index + 1} missing b64_json",
                    status=502,
                )
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            raise UpstreamError(
                f"Console image response item {index + 1} missing URL", status=502
            )
        item["url"] = url
    return {"data": items}


# ---------------------------------------------------------------------------
# Image edit (Go EditImage)
# ---------------------------------------------------------------------------


async def edit_console_image(
    token: str,
    *,
    model: str,
    prompt: str,
    image_urls: list[str],
    n: int = 1,
    size: str = "1024x1024",
    response_format: str = "url",
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    timeout_s: float = CONSOLE_MEDIA_TIMEOUT_S,
) -> dict[str, Any]:
    """POST console.x.ai/v1/images/edits; return the parsed envelope."""
    if not (1 <= n <= CONSOLE_MAX_IMAGE_N):
        raise ValidationError("n must be between 1 and 10", param="n")
    cleaned = [value.strip() for value in image_urls if value and value.strip()]
    if not cleaned or len(cleaned) > CONSOLE_MAX_EDIT_IMAGES:
        raise ValidationError(
            "Console image edit requires 1 to 3 images", param="image_urls"
        )
    for value in cleaned:
        if not valid_console_media_input_url(value, "image"):
            raise ValidationError(
                "Each edit image must be an HTTPS URL or image data URL",
                param="image_urls",
            )
    fmt = normalize_console_image_format(response_format)
    ratio = resolve_console_image_aspect_ratio(aspect_ratio, size)
    res = normalize_console_image_resolution(resolution)
    images = [{"type": "image_url", "url": value} for value in cleaned]
    payload: dict[str, Any] = {
        "model": CONSOLE_MEDIA_MODELS[model],
        "prompt": prompt,
        "n": n,
        "response_format": fmt,
    }
    if len(images) == 1:
        payload["image"] = images[0]
    else:
        payload["images"] = images
    if ratio:
        payload["aspect_ratio"] = ratio
    if res:
        payload["resolution"] = res
    status, body = await console_media_request(
        token,
        method="POST",
        url=console_v1_endpoint(CONSOLE_BASE, "/images/edits"),
        body=orjson.dumps(payload),
        timeout_s=timeout_s,
    )
    _check_media_status(status, body)
    items = _parse_media_envelope(body)
    fmt = normalize_console_image_format(response_format)
    for index, item in enumerate(items):
        if fmt == "b64_json":
            b64 = item.get("b64_json")
            if not isinstance(b64, str) or not b64.strip():
                raise UpstreamError(
                    f"Console image response item {index + 1} missing b64_json",
                    status=502,
                )
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            raise UpstreamError(
                f"Console image response item {index + 1} missing URL", status=502
            )
        item["url"] = url
    return {"data": items}


# ---------------------------------------------------------------------------
# Video generation (Go GenerateVideo + doConsoleVideoJSON)
# ---------------------------------------------------------------------------


async def generate_console_video(
    token: str,
    *,
    model: str,
    prompt: str,
    duration: int,
    aspect_ratio: str = "",
    resolution: str = "",
    image_url: str | None = None,
    progress_cb: Callable[[int], Awaitable[None]] | None = None,
    timeout_s: float = CONSOLE_MEDIA_TIMEOUT_S,
) -> ConsoleVideoResult:
    """POST /videos/generations then poll GET /videos/{id} until done.

    One proxy lease spans the whole create+poll cycle (Go GenerateVideo).
    """
    if not (1 <= duration <= CONSOLE_MAX_VIDEO_DURATION_S):
        raise ValidationError(
            "duration must be between 1 and 15 seconds", param="duration"
        )
    cleaned_resolution = (resolution or "").strip().lower()
    if cleaned_resolution and cleaned_resolution not in {"480p", "720p"}:
        raise ValidationError(
            "grok-imagine-video supports only 480p or 720p", param="resolution"
        )
    cleaned_prompt = (prompt or "").strip()
    cleaned_image = (image_url or "").strip() if image_url else ""
    if cleaned_image and not valid_console_media_input_url(cleaned_image, "image"):
        raise ValidationError(
            "Video first-frame must be an HTTPS URL or image data URL",
            param="image_url",
        )
    if not cleaned_prompt and not cleaned_image:
        raise ValidationError(
            "Text-to-video requires a prompt; image-to-video may omit it",
            param="prompt",
        )

    payload: dict[str, Any] = {
        "model": CONSOLE_MEDIA_MODELS[model],
        "duration": duration,
    }
    if cleaned_prompt:
        payload["prompt"] = cleaned_prompt
    if aspect_ratio.strip():
        payload["aspect_ratio"] = aspect_ratio.strip()
    if cleaned_resolution:
        payload["resolution"] = cleaned_resolution
    if cleaned_image:
        payload["image"] = {"url": cleaned_image}

    from app.dataplane.proxy import get_proxy_runtime

    proxy = await get_proxy_runtime()
    lease = await proxy.acquire(clearance_origin=CONSOLE_BASE)
    logger.debug(
        "console video create: model={} duration={} prompt={} image={}",
        CONSOLE_MEDIA_MODELS[model],
        duration,
        bool(cleaned_prompt),
        bool(cleaned_image),
    )

    status, body = await console_media_request(
        token,
        method="POST",
        url=console_v1_endpoint(CONSOLE_BASE, "/videos/generations"),
        body=orjson.dumps(payload),
        timeout_s=timeout_s,
        lease=lease,
    )
    _check_media_status(status, body)
    request_id = parse_console_video_create(body)
    if progress_cb is not None:
        await progress_cb(1)

    while True:
        status, body = await console_media_request(
            token,
            method="GET",
            url=console_v1_endpoint(CONSOLE_BASE, f"/videos/{quote(request_id)}"),
            body=None,
            timeout_s=timeout_s,
            lease=lease,
        )
        _check_media_status(status, body)
        url, progress, error, done = parse_console_video_status(body)
        if progress_cb is not None and progress > 0:
            await progress_cb(progress)
        if error:
            raise UpstreamError(f"Console video generation failed: {error}", status=502)
        if done:
            return ConsoleVideoResult(url=url)
        # Go GenerateVideo: poll, then wait consoleVideoPollEvery (ticker).
        await asyncio.sleep(CONSOLE_VIDEO_POLL_EVERY_S)


__all__ = [
    "CONSOLE_MEDIA_MODELS",
    "CONSOLE_MAX_EDIT_IMAGES",
    "CONSOLE_MAX_IMAGE_N",
    "CONSOLE_MAX_VIDEO_DURATION_S",
    "CONSOLE_MAX_VIDEO_IMAGES",
    "CONSOLE_VIDEO_POLL_EVERY_S",
    "ConsoleVideoResult",
    "edit_console_image",
    "generate_console_image",
    "generate_console_video",
    "new_console_media_upstream_error",
    "normalize_console_image_format",
    "normalize_console_image_resolution",
    "parse_console_video_create",
    "parse_console_video_status",
    "resolve_console_image_aspect_ratio",
    "safe_console_media_error_value",
    "safe_console_media_text",
    "valid_console_media_input_url",
]
