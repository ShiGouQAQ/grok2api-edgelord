"""Trusted-host allow-list for media downloads (Go a05e06a2 port).

Mirrors ``console/trustedConsoleImageURL`` and ``trustedConsoleVideoHost``
from the Go upstream (chenyme/grok2api, backend/internal/infra/provider/
console/media.go): media bytes are only fetched from known grok/x.ai media
hosts, and redirect targets are re-validated against the same allow-list
after the request completes.
"""

from urllib.parse import urlparse

# Exact-match image hosts (Go trustedConsoleImageHost).
TRUSTED_IMAGE_HOSTS = frozenset(
    {"assets.grok.com", "imagine-public.x.ai", "imgen.x.ai"}
)

# Video host: vidgen.x.ai plus any subdomain (Go trustedConsoleVideoHost).
TRUSTED_VIDEO_HOST = "vidgen.x.ai"

# Union used by the shared download path — images and videos both flow
# through download_asset / get_bytes_stream.
TRUSTED_MEDIA_HOSTS = TRUSTED_IMAGE_HOSTS | {TRUSTED_VIDEO_HOST}

# Body cap for media downloads (Go consoleImageBodyLimit = 32 << 20).
MAX_MEDIA_BODY_BYTES = 32 << 20


def is_trusted_media_host(host: str) -> bool:
    host = (host or "").strip().lower()
    return host in TRUSTED_MEDIA_HOSTS or host.endswith("." + TRUSTED_VIDEO_HOST)


def trusted_download_url(raw_url: str, *, base_url: str | None = None) -> str:
    """Return *raw_url* unchanged when its host is trusted, else raise ValueError.

    Mirrors Go ``trustedConsoleImageURL``: an https URL whose hostname is on
    the media allow-list passes; otherwise it must share scheme+host with
    *base_url*. URLs with userinfo or no hostname are always rejected.
    """
    url = raw_url.strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.username or parsed.password or not host:
        raise ValueError("Media download URL is not trusted")
    if parsed.scheme == "https" and is_trusted_media_host(host):
        return url
    if base_url:
        base = urlparse(base_url)
        if (
            parsed.scheme in ("http", "https")
            and parsed.scheme == base.scheme
            and host == (base.hostname or "").lower()
        ):
            return url
    raise ValueError(f"Media download host not trusted: {host}")


__all__ = [
    "TRUSTED_IMAGE_HOSTS",
    "TRUSTED_VIDEO_HOST",
    "TRUSTED_MEDIA_HOSTS",
    "MAX_MEDIA_BODY_BYTES",
    "is_trusted_media_host",
    "trusted_download_url",
]
