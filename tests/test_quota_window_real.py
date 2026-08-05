"""QuotaWindow real-console construction — Go PR #853 quota-model port.

Verifies ``console_usage_windows()``: usage_percent math (incl. the total==0
edge), the predicted 24h-recovery flag on an exhausted chat window,
display-only image/video windows, and that the new fields survive dict
round-tripping (persistence). No network.
"""

from app.control.account.enums import QuotaSource
from app.control.account.models import QuotaWindow
from app.control.account.quota_defaults import console_usage_windows
from app.dataplane.reverse.protocol.xai_console_usage import (
    CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S,
    ConsoleUsageResult,
)

NOW_MS = 2_000_000_000_000


def _window(
    remaining: int,
    total: int,
    *,
    window_seconds: int = 0,
    reset_at: int | None = None,
    synced_at: int = NOW_MS,
) -> QuotaWindow:
    return QuotaWindow(
        remaining=remaining,
        total=total,
        window_seconds=window_seconds,
        reset_at=reset_at,
        synced_at=synced_at,
        source=QuotaSource.REAL,
    )


def _result(*, chat_remaining: int = 15) -> ConsoleUsageResult:
    chat_reset = (
        NOW_MS + CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S * 1000
        if chat_remaining == 0
        else None
    )
    return ConsoleUsageResult(
        chat=_window(
            chat_remaining,
            20,
            window_seconds=CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S,
            reset_at=chat_reset,
        ),
        image=_window(10, 10),
        video=_window(3, 5),
        used={"chat": 20 - chat_remaining, "image": 0, "video": 2},
    )


def test_chat_window_usage_percent_and_source():
    """chat remaining=15/20 → usage_percent=25.0, upstream source, 24h window."""
    mapped = console_usage_windows(_result()).chat

    assert mapped.usage_percent == 25.0
    assert mapped.source == QuotaSource.REAL
    assert mapped.window_seconds == CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S
    assert mapped.predicted is False
    assert mapped.reset_at is None
    assert mapped.remaining == 15
    assert mapped.total == 20


def test_exhausted_chat_window_is_predicted_recovery():
    """chat remaining=0 → predicted=True, reset_at = fetch time + 24h."""
    mapped = console_usage_windows(_result(chat_remaining=0)).chat

    assert mapped.predicted is True
    assert mapped.reset_at == NOW_MS + CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S * 1000
    assert mapped.usage_percent == 100.0
    assert mapped.remaining == 0


def test_total_zero_usage_percent_is_zero():
    """total==0 must not divide by zero — usage_percent stays 0.0."""
    empty = ConsoleUsageResult(
        chat=_window(0, 0),
        image=_window(0, 0),
        video=_window(0, 0),
    )
    mapped = console_usage_windows(empty)

    assert mapped.chat.usage_percent == 0.0
    assert mapped.image.usage_percent == 0.0
    assert mapped.video.usage_percent == 0.0


def test_image_video_are_display_only():
    """image/video → window_seconds=0, reset_at=None, predicted=False."""
    mapped = console_usage_windows(_result())

    for win in (mapped.image, mapped.video):
        assert win.window_seconds == 0
        assert win.reset_at is None
        assert win.predicted is False
        assert win.source == QuotaSource.REAL
    assert mapped.image.usage_percent == 0.0  # 10/10 → 0% used
    assert mapped.video.usage_percent == 40.0  # 3/5 → 40% used


def test_quota_window_dict_round_trip_preserves_new_fields():
    """usage_percent/predicted survive to_dict/from_dict (persistence)."""
    w = QuotaWindow(
        remaining=0,
        total=20,
        window_seconds=CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S,
        reset_at=123,
        synced_at=456,
        source=QuotaSource.REAL,
        usage_percent=100.0,
        predicted=True,
    )
    restored = QuotaWindow.from_dict(w.to_dict())

    assert restored.usage_percent == 100.0
    assert restored.predicted is True
    assert restored.reset_at == 123


def test_quota_window_from_legacy_dict_defaults():
    """Dicts written before the new fields load usage_percent=0.0, predicted=False."""
    restored = QuotaWindow.from_dict(
        {
            "remaining": 20,
            "total": 20,
            "window_seconds": 3600,
            "reset_at": None,
            "synced_at": None,
            "source": 0,
        }
    )

    assert restored.usage_percent == 0.0
    assert restored.predicted is False
    assert restored.remaining == 20
