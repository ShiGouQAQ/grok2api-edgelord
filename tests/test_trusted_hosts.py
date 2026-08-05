"""Media download SSRF hardening (Go a05e06a2 port) — allow-list unit tests."""

import pytest

from app.dataplane.reverse.transport.trusted_hosts import (
    MAX_MEDIA_BODY_BYTES,
    TRUSTED_MEDIA_HOSTS,
    is_trusted_media_host,
    trusted_download_url,
)
from app.dataplane.reverse.protocol.xai_assets import resolve_download_url

TRUSTED = [
    "https://assets.grok.com/a/b.png",
    "https://imagine-public.x.ai/x.webp",
    "https://imgen.x.ai/y.jpg",
    "https://vidgen.x.ai/z.mp4",
    "https://cdn.vidgen.x.ai/z.mp4",  # Go allows *.vidgen.x.ai subdomains
]

UNTRUSTED = [
    "https://evil.com/a.png",
    "https://assets.grok.com.evil.com/a.png",  # suffix must be full host
    "https://grok.com/a.png",  # grok.com itself is not a media host
    "http://assets.grok.com/a.png",  # scheme must be https (allow-list path)
    "https://user@assets.grok.com/a.png",  # userinfo rejected
    "https://assets.grok.com@evil.com/a.png",
    "ftp://assets.grok.com/a.png",
    "/relative/path.png",
]


def test_trusted_hosts_pass():
    for url in TRUSTED:
        assert trusted_download_url(url) == url


def test_untrusted_hosts_rejected():
    for url in UNTRUSTED:
        with pytest.raises(ValueError):
            trusted_download_url(url)


def test_media_host_set_matches_go():
    assert TRUSTED_MEDIA_HOSTS == {
        "assets.grok.com",
        "imagine-public.x.ai",
        "imgen.x.ai",
        "vidgen.x.ai",
    }
    assert is_trusted_media_host("Assets.Grok.COM")  # case-insensitive
    assert is_trusted_media_host("a.b.vidgen.x.ai")
    assert not is_trusted_media_host("vidgen.x.ai.evil.com")


def test_base_url_same_host_fallback():
    # Go trustedConsoleImageURL: same scheme+host as baseURL also passes.
    assert (
        trusted_download_url(
            "http://internal.host/img.png", base_url="http://internal.host"
        )
        == "http://internal.host/img.png"
    )
    with pytest.raises(ValueError):
        trusted_download_url(
            "http://internal.host/img.png", base_url="https://internal.host"
        )
    with pytest.raises(ValueError):
        trusted_download_url(
            "http://other.host/img.png", base_url="http://internal.host"
        )


def test_body_limit_matches_go():
    assert MAX_MEDIA_BODY_BYTES == 32 << 20


def test_resolve_download_url_enforces_allow_list():
    # Full trusted URL passes through unchanged.
    url, origin, referer = resolve_download_url("https://assets.grok.com/foo.png")
    assert url == "https://assets.grok.com/foo.png"
    assert origin == "https://assets.grok.com"
    assert referer == "https://assets.grok.com/"

    # Relative/absolute paths resolve onto the trusted base host.
    assert resolve_download_url("/foo.png")[0] == "https://assets.grok.com/foo.png"
    assert resolve_download_url("foo.png")[0] == "https://assets.grok.com/foo.png"

    # Full URL on an untrusted host is rejected.
    with pytest.raises(ValueError):
        resolve_download_url("https://evil.com/foo.png")
