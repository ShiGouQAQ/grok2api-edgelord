"""Tests for the Go→Python port of chenyme/grok2api a05e06a2 console media
endpoints (backend/internal/infra/provider/console/media.go).

Covers: registry entries for the three ``-console`` media models, images/video
routing dispatch (console path + DPoP endpoint/lease threading), console video
create→poll lifecycle, quota-mode wiring (ModeId.CONSOLE → console window),
and trusted-host allow-list coverage of the console media hosts. No real
network — proxy and DPoP request layers are mocked.
"""

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

from app.control.account.models import AccountQuotaSet
from app.control.account.quota_defaults import default_quota_set
from app.control.model.enums import Capability, ModeId, Tier
from app.control.model.registry import get as get_model
from app.dataplane.reverse.protocol.dpop import console_v1_endpoint
from app.dataplane.reverse.transport.trusted_hosts import (
    TRUSTED_IMAGE_HOSTS,
    TRUSTED_VIDEO_HOST,
    trusted_download_url,
)
from app.platform.errors import UpstreamError, ValidationError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeAcct:
    token = "sso-test-token"


class _FakeDir:
    """Minimal account-directory double: records reserve mode_id/pools."""

    def __init__(self) -> None:
        self.reserve_calls: list[tuple[Any, Any]] = []
        self.feedback_calls: list[tuple[str, Any, int]] = []

    async def reserve(self, pool_candidates, mode_id, **kwargs):
        self.reserve_calls.append((pool_candidates, mode_id))
        return _FakeAcct()

    async def release(self, acct) -> None:
        pass

    async def feedback(self, token, kind, mode_id) -> None:
        self.feedback_calls.append((token, kind, mode_id))


class _FakeSession:
    """ResettableSession stand-in (async context manager)."""

    def __init__(self, *responses: MagicMock) -> None:
        self.responses = list(responses)

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    def request(self, method: str, *args: Any, **kwargs: Any):
        resp = self.responses.pop(0) if self.responses else None
        if resp is None:
            raise AssertionError("unexpected request call")
        return resp


def _capture_dpop(*results: tuple[int, bytes]):
    """Return a do_dpop_request side_effect that records kwargs and replies."""
    captured: dict[str, Any] = {"kwargs_list": []}
    remaining = [(status, body, {}) for status, body in results]

    async def _side_effect(manager, **kwargs):
        captured["kwargs_list"].append(kwargs)
        if not remaining:
            raise AssertionError("unexpected do_dpop_request call")
        return remaining.pop(0)

    return _side_effect, captured


@contextmanager
def _patched_console_request(*results: tuple[int, bytes]):
    """Patch the console-request plumbing; yield (captured, proxy, lease)."""
    proxy = AsyncMock()
    lease = MagicMock(proxy_url=None, cf_cookies="", cf_clearance="")
    proxy.acquire.return_value = lease
    side_effect, captured = _capture_dpop(*results)
    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=proxy),
        patch(
            "app.dataplane.reverse.protocol.xai_console_media.do_dpop_request",
            new=AsyncMock(side_effect=side_effect),
        ),
        patch(
            "app.dataplane.reverse.protocol.xai_console_media.build_session_kwargs",
            return_value={},
        ),
        patch(
            "app.dataplane.reverse.protocol.xai_console_media.ResettableSession",
            new=_FakeSession,
        ),
    ):
        yield captured, proxy, lease


def _image_envelope(*urls: str) -> bytes:
    return orjson.dumps({"data": [{"url": u} for u in urls]})


# ---------------------------------------------------------------------------
# (a) Registry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected_cap"),
    [
        (
            "grok-imagine-image-quality-console",
            Capability.IMAGE | Capability.IMAGE_EDIT,
        ),
        ("grok-imagine-image-console", Capability.IMAGE | Capability.IMAGE_EDIT),
        ("grok-imagine-video-console", Capability.VIDEO),
    ],
)
def test_registry_console_media_models(name, expected_cap):
    spec = get_model(name)
    assert spec is not None, f"{name} not registered"
    assert spec.enabled
    assert spec.mode_id == ModeId.CONSOLE
    assert spec.tier == Tier.BASIC
    assert spec.capability == expected_cap


# ---------------------------------------------------------------------------
# (b) Routing dispatch — images
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_dispatches_console_image_to_protocol():
    from app.products.openai import images

    fake_dir = _FakeDir()
    envelope = {"data": [{"url": "https://imagine-public.x.ai/out.png"}]}
    with (
        patch("app.dataplane.account._directory", fake_dir),
        patch(
            "app.dataplane.reverse.protocol.xai_console_media.generate_console_image",
            new=AsyncMock(return_value=envelope),
        ) as proto,
    ):
        result: Any = await images.generate(
            model="grok-imagine-image-console",
            prompt="a cat",
            n=2,
            size="1024x1024",
            response_format="url",
        )

    assert proto.await_count == 1
    assert proto.await_args is not None
    kwargs = proto.await_args.kwargs
    assert kwargs["model"] == "grok-imagine-image-console"
    assert kwargs["prompt"] == "a cat"
    assert kwargs["n"] == 2
    # console path reserves with ModeId.CONSOLE (5) → console quota window
    assert fake_dir.reserve_calls and fake_dir.reserve_calls[0][1] == int(
        ModeId.CONSOLE
    )
    assert isinstance(result, dict)
    assert result["data"][0]["url"] == "https://imagine-public.x.ai/out.png"


@pytest.mark.asyncio
async def test_generate_console_image_rejects_stream():
    from app.products.openai import images

    with patch("app.dataplane.account._directory", _FakeDir()):
        with pytest.raises(ValidationError):
            await images.generate(
                model="grok-imagine-image-console",
                prompt="p",
                n=1,
                stream=True,
            )


@pytest.mark.asyncio
async def test_edit_dispatches_console_image_edit():
    from app.products.openai import images

    fake_dir = _FakeDir()
    envelope = {"data": [{"url": "https://imagine-public.x.ai/edit.png"}]}
    messages = [
        {"role": "user", "content": "make it blue"},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,AAAA"},
                }
            ],
        },
    ]
    with (
        patch("app.dataplane.account._directory", fake_dir),
        patch(
            "app.dataplane.reverse.protocol.xai_console_media.edit_console_image",
            new=AsyncMock(return_value=envelope),
        ) as proto,
    ):
        result: Any = await images.edit(
            model="grok-imagine-image-console",
            messages=messages,
            n=1,
            size="1024x1024",
            response_format="url",
        )

    assert proto.await_count == 1
    assert proto.await_args is not None
    kwargs = proto.await_args.kwargs
    assert kwargs["model"] == "grok-imagine-image-console"
    assert kwargs["image_urls"] == ["data:image/png;base64,AAAA"]
    assert fake_dir.reserve_calls and fake_dir.reserve_calls[0][1] == int(
        ModeId.CONSOLE
    )
    assert isinstance(result, dict)
    assert result["data"][0]["url"] == "https://imagine-public.x.ai/edit.png"


# ---------------------------------------------------------------------------
# (b) Console protocol — image generation endpoint + DPoP threading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_console_image_generation_uses_dpop_endpoint_and_lease():
    from app.dataplane.reverse.protocol.xai_console_media import generate_console_image

    with _patched_console_request(
        (200, _image_envelope("https://imgen.x.ai/a.png"))
    ) as (
        captured,
        proxy,
        lease,
    ):
        result = await generate_console_image(
            "tok", model="grok-imagine-image-console", prompt="hi", n=1
        )

    first = captured["kwargs_list"][0]
    assert first["method"] == "POST"
    assert first["url"] == console_v1_endpoint(
        "https://console.x.ai", "/images/generations"
    )
    assert first["accept"] == "application/json"
    assert first["lease"] is lease
    assert first["credential_id"] == 0
    body = orjson.loads(first["body"])
    # public -console name → upstream console model field
    assert body["model"] == "grok-imagine-image"
    assert body["prompt"] == "hi"
    assert body["n"] == 1
    assert body["response_format"] == "url"
    assert result == {"data": [{"url": "https://imgen.x.ai/a.png"}]}


@pytest.mark.asyncio
async def test_console_image_generation_validation_and_aspect_ratio():
    from app.dataplane.reverse.protocol.xai_console_media import (
        generate_console_image,
        normalize_console_image_format,
        resolve_console_image_aspect_ratio,
    )

    assert normalize_console_image_format("") == "url"
    assert normalize_console_image_format("B64_JSON") == "b64_json"
    with pytest.raises(ValidationError):
        normalize_console_image_format("weird")
    assert resolve_console_image_aspect_ratio("", "1280x720") == "16:9"
    assert resolve_console_image_aspect_ratio("auto", "") == ""
    with pytest.raises(ValidationError):
        resolve_console_image_aspect_ratio("4:5", "")

    with _patched_console_request(
        (200, _image_envelope("https://imgen.x.ai/a.png"))
    ) as (captured, _proxy, _lease):
        await generate_console_image(
            "tok",
            model="grok-imagine-image-console",
            prompt="hi",
            n=3,
            size="1280x720",
            resolution="2k",
        )
    body = orjson.loads(captured["kwargs_list"][0]["body"])
    assert body["aspect_ratio"] == "16:9"
    assert body["resolution"] == "2k"
    assert body["n"] == 3


@pytest.mark.asyncio
async def test_console_image_generation_error_maps_to_upstream_error():
    from app.dataplane.reverse.protocol.xai_console_media import generate_console_image

    err_body = orjson.dumps({"error": {"message": "rate limited"}})
    with _patched_console_request((429, err_body)):
        with pytest.raises(UpstreamError) as excinfo:
            await generate_console_image(
                "tok", model="grok-imagine-image-console", prompt="hi", n=1
            )
    assert excinfo.value.status == 429


# ---------------------------------------------------------------------------
# (b) Console protocol — video create → poll → done
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_console_video_generation_create_poll_done():
    from app.dataplane.reverse.protocol.xai_console_media import generate_console_video

    create_body = orjson.dumps({"request_id": "vid-1"})
    pending_body = orjson.dumps({"status": "processing", "progress": 40})
    done_body = orjson.dumps(
        {
            "status": "succeeded",
            "progress": 100,
            "video": {"url": "https://vidgen.x.ai/v.mp4"},
        }
    )
    progress: list[int] = []

    async def _progress(p: int) -> None:
        progress.append(p)

    with patch("asyncio.sleep", new=AsyncMock()) as sleep_mock:
        with _patched_console_request(
            (200, create_body), (200, pending_body), (200, done_body)
        ) as (captured, _proxy, lease):
            result = await generate_console_video(
                "tok",
                model="grok-imagine-video-console",
                prompt="waves",
                duration=6,
                aspect_ratio="16:9",
                resolution="720p",
                progress_cb=_progress,
            )

    assert result.url == "https://vidgen.x.ai/v.mp4"
    assert result.content_type == "video/mp4"
    assert sleep_mock.await_count == 1
    # same lease threaded through create + both polls
    create_kwargs = captured["kwargs_list"][0]
    assert create_kwargs["method"] == "POST"
    assert create_kwargs["url"] == console_v1_endpoint(
        "https://console.x.ai", "/videos/generations"
    )
    create_body_parsed = orjson.loads(create_kwargs["body"])
    assert create_body_parsed["model"] == "grok-imagine-video"
    assert create_body_parsed["duration"] == 6
    poll_urls = [c["url"] for c in captured["kwargs_list"][1:]]
    assert poll_urls == [
        console_v1_endpoint("https://console.x.ai", "/videos/vid-1"),
        console_v1_endpoint("https://console.x.ai", "/videos/vid-1"),
    ]
    assert all(c["lease"] is lease for c in captured["kwargs_list"])
    assert 1 in progress  # create ack
    assert 40 in progress  # poll progress
    assert 100 not in progress  # capped at 99 during polling


@pytest.mark.asyncio
async def test_console_video_generation_failed_status():
    from app.dataplane.reverse.protocol.xai_console_media import generate_console_video

    create_body = orjson.dumps({"request_id": "vid-1"})
    failed_body = orjson.dumps(
        {"status": "failed", "error": {"message": "content policy"}}
    )
    with _patched_console_request((200, create_body), (200, failed_body)):
        with pytest.raises(UpstreamError) as excinfo:
            await generate_console_video(
                "tok", model="grok-imagine-video-console", prompt="p", duration=6
            )
    assert "content policy" in str(excinfo.value)


@pytest.mark.asyncio
async def test_console_video_duration_validation():
    from app.dataplane.reverse.protocol.xai_console_media import generate_console_video

    with pytest.raises(ValidationError):
        await generate_console_video(
            "tok", model="grok-imagine-video-console", prompt="p", duration=16
        )
    # image-to-video may omit prompt (Go semantics)
    with _patched_console_request(
        (200, orjson.dumps({"request_id": "v"})),
        (
            200,
            orjson.dumps(
                {"status": "ready", "video": {"url": "https://vidgen.x.ai/v.mp4"}}
            ),
        ),
    ) as (captured, _proxy, _lease):
        result = await generate_console_video(
            "tok",
            model="grok-imagine-video-console",
            prompt="",
            duration=6,
            image_url="data:image/png;base64,AAAA",
        )
    assert result.url == "https://vidgen.x.ai/v.mp4"
    body = orjson.loads(captured["kwargs_list"][0]["body"])
    assert body["image"] == {"url": "data:image/png;base64,AAAA"}
    assert "prompt" not in body


# ---------------------------------------------------------------------------
# (b) Routing dispatch — video job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_console_video_job_dispatches_console_generator():
    from app.dataplane.reverse.protocol.xai_console_media import ConsoleVideoResult
    from app.products.openai import video as video_module

    fake_dir = _FakeDir()
    job = video_module._VideoJob(
        id="video_console_test",
        model="grok-imagine-video-console",
        prompt="waves",
        seconds="6",
        size="720x1280",
        quality="standard",
        created_at=int(time.time()),
    )
    with (
        patch("app.dataplane.account._directory", fake_dir),
        patch(
            "app.dataplane.reverse.protocol.xai_console_media.generate_console_video",
            new=AsyncMock(
                return_value=ConsoleVideoResult(url="https://vidgen.x.ai/v.mp4")
            ),
        ) as proto,
        patch(
            "app.products.openai.video._download_video_bytes",
            new=AsyncMock(return_value=(b"mp4-bytes", "video/mp4")),
        ),
        patch(
            "app.products.openai.video._save_video_bytes",
            return_value=Path("/tmp/v.mp4"),
        ),
    ):
        await video_module._run_video_job(
            job,
            size="720x1280",
            resolution_name=None,
            prompt="waves",
            seconds=6,
            preset=None,
        )

    assert proto.await_count == 1
    assert job.status == "completed"
    assert job.progress == 100
    assert job.video_url == "https://vidgen.x.ai/v.mp4"
    assert fake_dir.reserve_calls[0][1] == int(ModeId.CONSOLE)


@pytest.mark.asyncio
async def test_create_video_console_seconds_range():
    from app.products.openai import video as video_module

    with (
        patch("app.dataplane.account._directory", _FakeDir()),
        patch("app.products.openai.video._run_video_job", new=AsyncMock()),
    ):
        # 3s is console-valid (1..15) though not in the grok.com set
        result = await video_module.create_video(
            model="grok-imagine-video-console",
            prompt="waves",
            seconds=3,
            size="1280x720",
        )
        assert result["status"] == "queued"
        with pytest.raises(ValidationError):
            await video_module.create_video(
                model="grok-imagine-video-console",
                prompt="waves",
                seconds=20,
                size="1280x720",
            )


# ---------------------------------------------------------------------------
# (c) Quota-mode wiring
# ---------------------------------------------------------------------------


def test_console_media_quota_resolution_uses_console_window():
    """ModeId.CONSOLE → AccountQuotaSet.get(5) is the console window (basic)."""
    qs: AccountQuotaSet = default_quota_set("basic")
    for name in (
        "grok-imagine-image-quality-console",
        "grok-imagine-image-console",
        "grok-imagine-video-console",
    ):
        spec = get_model(name)
        assert spec is not None
        assert spec.mode_id == ModeId.CONSOLE
        assert qs.get(int(spec.mode_id)) is qs.console
        assert qs.get(int(spec.mode_id)) is not None


# ---------------------------------------------------------------------------
# (d) Trusted-host allow-list covers console media hosts
# ---------------------------------------------------------------------------


def test_console_media_download_hosts_trusted():
    assert "imgen.x.ai" in TRUSTED_IMAGE_HOSTS
    assert "imagine-public.x.ai" in TRUSTED_IMAGE_HOSTS
    assert "assets.grok.com" in TRUSTED_IMAGE_HOSTS
    assert TRUSTED_VIDEO_HOST == "vidgen.x.ai"
    for url in (
        "https://assets.grok.com/x.png",
        "https://imagine-public.x.ai/x.png",
        "https://imgen.x.ai/x.png",
        "https://vidgen.x.ai/v.mp4",
        "https://cdn1.vidgen.x.ai/v.mp4",
    ):
        assert trusted_download_url(url) == url
    with pytest.raises(ValueError):
        trusted_download_url("https://evil.example/x.png")
    with pytest.raises(ValueError):
        trusted_download_url("http://vidgen.x.ai/v.mp4")  # http never trusted
