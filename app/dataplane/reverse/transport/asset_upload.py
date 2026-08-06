"""Asset upload transport — direct base64 upload to Grok.

Calls POST /rest/app-chat/upload-file with base64-encoded content and
returns the file metadata ID used as a file attachment reference in chat.
"""

import asyncio
import base64
import re
from urllib.parse import urlparse

import orjson

from app.platform.logging.logger import logger
from app.platform.config.snapshot import get_config
from app.platform.errors import UpstreamError, ValidationError
from app.dataplane.proxy import get_proxy_runtime
from app.dataplane.proxy.adapters.headers import build_sso_cookie
from app.dataplane.proxy.adapters.headers import build_http_headers
from app.dataplane.proxy.adapters.session import ResettableSession, build_session_kwargs
from app.dataplane.reverse.protocol.xai_assets import resolve_asset_reference
from app.control.proxy.feedback import build_feedback
from app.control.proxy.models import ProxyFeedback, ProxyFeedbackKind

_UPLOAD_URL = "https://grok.com/rest/app-chat/upload-file"
_X_USER_ID_RE = re.compile(r"(?:^|;\s*)x-userid=([^;]+)")

# Global semaphore — limits concurrent upload_file() calls across all requests.
# Initialised lazily on first call so the event loop is guaranteed to be running.
_upload_sem: asyncio.Semaphore | None = None


def _get_upload_sem() -> asyncio.Semaphore:
    global _upload_sem
    if _upload_sem is None:
        n = max(1, int(get_config("batch.asset_upload_concurrency", 10)))
        _upload_sem = asyncio.Semaphore(n)
    return _upload_sem


# ---------------------------------------------------------------------------
# File-input parsing
# ---------------------------------------------------------------------------


def _is_url(value: str) -> bool:
    try:
        p = urlparse(value)
        return bool(p.scheme in {"http", "https"} and p.netloc)
    except (ValueError, TypeError):
        return False


def parse_data_uri(data_uri: str) -> tuple[str, str, str]:
    """Split a data URI into (filename, base64_content, mime_type).

    Raises ``ValidationError`` on invalid input.
    """
    if not data_uri.startswith("data:"):
        raise ValidationError("File input must be a URL or data URI", param="content")

    try:
        header, b64 = data_uri.split(",", 1)
    except ValueError:
        raise ValidationError(
            "Malformed data URI: missing comma separator", param="content"
        )

    if ";base64" not in header:
        raise ValidationError("Data URI must be base64-encoded", param="content")

    mime = header[5:].split(";", 1)[0].strip() or "application/octet-stream"
    b64 = re.sub(r"\s+", "", b64)
    if not b64:
        raise ValidationError("Data URI has empty payload", param="content")

    ext = mime.split("/")[-1] if "/" in mime else "bin"
    return f"file.{ext}", b64, mime


# ---------------------------------------------------------------------------
# Core upload function
# ---------------------------------------------------------------------------


async def upload_file(
    token: str,
    filename: str,
    mime: str,
    b64: str,
) -> tuple[str, str]:
    """Upload base64-encoded file content to Grok.

    Args:
        token:    SSO session token.
        filename: Original file name (used for content-type inference).
        mime:     MIME type string (e.g. ``"image/png"``).
        b64:      Base64-encoded file content (no data-URI prefix).

    Returns:
        ``(file_id, file_uri)`` — file_id is used as a file attachment ref.

    Raises:
        ``UpstreamError`` on HTTP failure.
    """
    async with _get_upload_sem():
        return await _upload_file_inner(token, filename, mime, b64)


async def _upload_file_inner(
    token: str,
    filename: str,
    mime: str,
    b64: str,
) -> tuple[str, str]:
    cfg = get_config()
    timeout_s = cfg.get_float("asset.upload_timeout", 60.0)

    proxy = await get_proxy_runtime()
    lease = await proxy.acquire()

    payload = orjson.dumps(
        {
            "fileName": filename,
            "fileMimeType": mime,
            "content": b64,
        }
    )
    headers = build_http_headers(token, lease=lease)
    kwargs = build_session_kwargs(lease=lease)

    try:
        async with ResettableSession(**kwargs) as session:
            response = await session.post(
                _UPLOAD_URL,
                headers=headers,
                data=payload,
                timeout=timeout_s,
            )

        body_bytes = response.content
        if response.status_code != 200:
            diagnostic = _upload_response_diagnostic(body_bytes, truncated=True)
            logger.error(
                "asset upload request failed: status={} diagnostic={}",
                response.status_code,
                diagnostic,
            )
            is_cloudflare = "just a moment" in diagnostic.lower()
            await proxy.feedback(
                lease,
                build_feedback(response.status_code, is_cloudflare=is_cloudflare),
            )
            raise UpstreamError(
                f"Asset upload returned {response.status_code}: {diagnostic}",
                status=response.status_code,
                body=diagnostic,
            )

        await proxy.feedback(
            lease,
            ProxyFeedback(kind=ProxyFeedbackKind.SUCCESS, status_code=200),
        )

        try:
            result = orjson.loads(body_bytes)
        except (orjson.JSONDecodeError, ValueError) as parse_exc:
            diagnostic = _upload_response_diagnostic(body_bytes)
            raise UpstreamError(
                f"Asset upload response invalid: {parse_exc} (response: {diagnostic})"
            ) from parse_exc
        file_id = result.get("fileMetadataId") or result.get("fileId", "")
        file_uri = result.get("fileUri", "")
        logger.info(
            "asset upload completed: filename={!r} file_id={}", filename, file_id
        )
        return file_id, file_uri

    except UpstreamError:
        raise
    except Exception as exc:
        await proxy.feedback(
            lease,
            ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR),
        )
        raise UpstreamError(f"Asset upload transport error: {exc}") from exc


def _validate_remote_url(url: str) -> None:
    """Validate that a remote URL is safe to fetch."""
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError) as exc:
        raise ValidationError(f"Invalid URL: {exc}", param="image_url") from exc

    if parsed.scheme not in ("http", "https"):
        raise ValidationError("URL must use http or https scheme", param="image_url")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValidationError("URL must have a hostname", param="image_url")

    # Block private/internal addresses to prevent SSRF
    private_prefixes = (
        "127.",
        "10.",
        "192.168.",
        "172.16.",
        "172.17.",
        "172.18.",
        "172.19.",
        "172.20.",
        "172.21.",
        "172.22.",
        "172.23.",
        "172.24.",
        "172.25.",
        "172.26.",
        "172.27.",
        "172.28.",
        "172.29.",
        "172.30.",
        "172.31.",
        "0.",
        "169.254.",
    )
    if any(hostname.startswith(prefix) for prefix in private_prefixes):
        raise ValidationError(
            "URL must not point to private/internal addresses", param="image_url"
        )

    if hostname in ("localhost", "[::1]", ""):
        raise ValidationError("URL must not point to localhost", param="image_url")


async def upload_from_input(token: str, file_input: str) -> tuple[str, str]:
    """High-level helper: parse *file_input* (URL or data URI) and upload.

    Returns ``(file_id, file_uri)``.
    """
    if _is_url(file_input):
        _validate_remote_url(file_input)
        # Fetch the remote URL and re-upload as base64.
        proxy = await get_proxy_runtime()
        lease = await proxy.acquire()
        try:
            headers = build_http_headers(token, lease=lease)
            kwargs = build_session_kwargs(lease=lease)
            async with ResettableSession(**kwargs) as session:
                resp = await session.get(file_input, headers=headers, timeout=30.0)
            raw = resp.content
            if resp.status_code != 200:
                diagnostic = _upload_response_diagnostic(raw, truncated=True)
                await proxy.feedback(
                    lease,
                    ProxyFeedback(
                        kind=ProxyFeedbackKind.UPSTREAM_5XX
                        if resp.status_code >= 500
                        else ProxyFeedbackKind.FORBIDDEN,
                        status_code=resp.status_code,
                    ),
                )
                raise UpstreamError(
                    f"Failed to fetch input URL: {resp.status_code}: {diagnostic}",
                    status=resp.status_code,
                )
            mime = (
                resp.headers.get("content-type", "").split(";")[0].strip()
                or "application/octet-stream"
            )
            filename = file_input.split("/")[-1].split("?")[0] or "download"
            b64 = base64.b64encode(raw).decode()
        except UpstreamError:
            raise
        except Exception as exc:
            await proxy.feedback(
                lease, ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
            )
            raise UpstreamError(f"Asset fetch transport error: {exc}") from exc

        await proxy.feedback(lease, ProxyFeedback(kind=ProxyFeedbackKind.SUCCESS))
        return await upload_file(token, filename, mime, b64)

    # Data URI
    filename, b64, mime = parse_data_uri(file_input)
    return await upload_file(token, filename, mime, b64)


def resolve_uploaded_asset_reference(token: str, file_id: str, file_uri: str) -> str:
    """Resolve an uploaded asset to the content URL required by image-edit."""
    user_id = _extract_user_id(token)
    url = resolve_asset_reference(file_id, file_uri, user_id=user_id)
    if url:
        return url
    raise UpstreamError("Could not resolve uploaded asset reference URL")


def _extract_user_id(token: str) -> str | None:
    cookie = build_sso_cookie(token)
    match = _X_USER_ID_RE.search(cookie)
    if match:
        return match.group(1)
    return None


def _upload_response_diagnostic(body: bytes, truncated: bool = False) -> str:
    """Create a diagnostic string from upload response body."""
    value = " ".join(body.decode("utf-8", "replace").split())
    if not value:
        value = "<empty>"
    max_chars = 120
    if len(value) > max_chars:
        value = value[:max_chars]
        truncated = True
    if truncated:
        value += "..."
    return value


__all__ = [
    "upload_file",
    "upload_from_input",
    "parse_data_uri",
    "resolve_uploaded_asset_reference",
]
