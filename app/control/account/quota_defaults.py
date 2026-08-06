"""Default quota windows and pool inference logic.

Canonical quota totals per pool type (from upstream rate-limits API):

              auto    fast    expert    heavy    grok_4_3    console
  basic          —      30       —        —         —         20        window: fast=86400s, console=3600s
  super         50     140      50        —        50         —        window: 7200 s
  heavy        150     400     150       20       150         —        window: 7200 s

Pool inference uses ``auto.total`` as the primary signal for super/heavy
accounts; basic accounts no longer expose auto/expert windows locally.
Console quota: 20 queries / 60 min window, rotation threshold at remaining <= 12.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .enums import QuotaSource
from .models import AccountQuotaSet, QuotaWindow

if TYPE_CHECKING:
    from app.dataplane.reverse.protocol.xai_console_usage import ConsoleUsageResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _w(remaining: int, total: int, window_seconds: int) -> QuotaWindow:
    return QuotaWindow(
        remaining=remaining,
        total=total,
        window_seconds=window_seconds,
        reset_at=None,
        synced_at=None,
        source=QuotaSource.DEFAULT,
    )


# ---------------------------------------------------------------------------
# Per-pool default quota sets
# ---------------------------------------------------------------------------

BASIC_FAST_LIMIT = 30
BASIC_FAST_WINDOW_SECONDS = 86_400

BASIC_CONSOLE_LIMIT = 20
BASIC_CONSOLE_WINDOW_SECONDS = 3600

BASIC_QUOTA_DEFAULTS = AccountQuotaSet(
    auto=_w(0, 0, 0),  # unsupported on basic accounts
    fast=_w(BASIC_FAST_LIMIT, BASIC_FAST_LIMIT, BASIC_FAST_WINDOW_SECONDS),
    expert=_w(0, 0, 0),  # unsupported on basic accounts
    console=_w(BASIC_CONSOLE_LIMIT, BASIC_CONSOLE_LIMIT, BASIC_CONSOLE_WINDOW_SECONDS),
    quota_build=_w(100, 100, 7_200),  # 100 queries / 2 h
)

SUPER_QUOTA_DEFAULTS = AccountQuotaSet(
    auto=_w(50, 50, 7_200),  # 50  queries / 2 h
    fast=_w(140, 140, 7_200),  # 140 queries / 2 h
    expert=_w(50, 50, 7_200),  # 50  queries / 2 h
    grok_4_3=_w(50, 50, 7_200),  # 50  queries / 2 h
    quota_build=_w(100, 100, 7_200),  # 100 queries / 2 h
)

HEAVY_QUOTA_DEFAULTS = AccountQuotaSet(
    auto=_w(150, 150, 7_200),  # 150 queries / 2 h
    fast=_w(400, 400, 7_200),  # 400 queries / 2 h
    expert=_w(150, 150, 7_200),  # 150 queries / 2 h
    heavy=_w(20, 20, 7_200),  # 20  queries / 2 h
    grok_4_3=_w(150, 150, 7_200),  # 150 queries / 2 h
    quota_build=_w(100, 100, 7_200),  # 100 queries / 2 h
)

# Default quota totals for Build mode
BUILD_QUOTA_DEFAULTS = AccountQuotaSet(
    auto=_w(0, 0, 0),  # unsupported on build accounts
    fast=_w(30, 30, 86_400),  # 30 queries / 24 h
    expert=_w(0, 0, 0),  # unsupported on build accounts
    console=_w(0, 0, 0),  # unsupported on build accounts
    quota_build=_w(100, 100, 7_200),  # 100 queries / 2 h
)

# Map pool name → defaults object (used by backends on upsert).
_POOL_DEFAULTS: dict[str, AccountQuotaSet] = {
    "basic": BASIC_QUOTA_DEFAULTS,
    "super": SUPER_QUOTA_DEFAULTS,
    "heavy": HEAVY_QUOTA_DEFAULTS,
    "build": BUILD_QUOTA_DEFAULTS,
}

_SUPPORTED_MODE_IDS_BY_POOL: dict[str, frozenset[int]] = {
    "basic": frozenset((1, 5, 6)),
    "super": frozenset((0, 1, 2, 5, 6)),
    "heavy": frozenset((0, 1, 2, 3, 5, 6)),
    "build": frozenset((1, 6)),
}

# Mode ID → quota key in AccountQuotaSet storage
_MODE_KEYS: dict[int, str] = {
    0: "quota_auto",
    1: "quota_fast",
    2: "quota_expert",
    3: "quota_heavy",
    5: "quota_console",
    6: "quota_build",
}

# ---------------------------------------------------------------------------
# Pool inference — keyed on auto.total (unique across pool types)
# ---------------------------------------------------------------------------

_AUTO_TOTAL_TO_POOL: dict[int, str] = {
    20: "basic",
    50: "super",
    150: "heavy",
}


def default_quota_set(pool: str) -> AccountQuotaSet:
    """Return a fresh copy of the default quota set for *pool*."""
    src = _POOL_DEFAULTS.get(pool, BASIC_QUOTA_DEFAULTS)
    qs = AccountQuotaSet(
        auto=_w(src.auto.remaining, src.auto.total, src.auto.window_seconds),
        fast=_w(src.fast.remaining, src.fast.total, src.fast.window_seconds),
        expert=_w(src.expert.remaining, src.expert.total, src.expert.window_seconds),
    )
    if src.heavy is not None:
        qs.heavy = _w(src.heavy.remaining, src.heavy.total, src.heavy.window_seconds)
    if src.grok_4_3 is not None:
        qs.grok_4_3 = _w(
            src.grok_4_3.remaining, src.grok_4_3.total, src.grok_4_3.window_seconds
        )
    if src.console is not None:
        qs.console = _w(
            src.console.remaining, src.console.total, src.console.window_seconds
        )
    if src.quota_build is not None:
        qs.quota_build = _w(
            src.quota_build.remaining,
            src.quota_build.total,
            src.quota_build.window_seconds,
        )
    return qs


def supports_mode(pool: str, mode_id: int) -> bool:
    """Return whether *pool* has a default quota window for *mode_id*."""
    return mode_id in _SUPPORTED_MODE_IDS_BY_POOL.get(
        pool, _SUPPORTED_MODE_IDS_BY_POOL["basic"]
    )


def supported_mode_ids(pool: str) -> tuple[int, ...]:
    """Return the supported mode IDs for *pool* in stable request order."""
    supported = _SUPPORTED_MODE_IDS_BY_POOL.get(
        pool, _SUPPORTED_MODE_IDS_BY_POOL["basic"]
    )
    return tuple(mode_id for mode_id in (0, 1, 2, 3, 4, 5, 6) if mode_id in supported)


def default_quota_window(pool: str, mode_id: int) -> QuotaWindow | None:
    """Return the default quota window for *(pool, mode_id)*, if supported."""
    if not supports_mode(pool, mode_id):
        return None
    return default_quota_set(pool).get(mode_id)


def normalize_quota_window(
    pool: str, mode_id: int, window: QuotaWindow | None
) -> QuotaWindow | None:
    """Apply product-level quota policy for one pool/mode window."""
    if window is None or not supports_mode(pool, mode_id):
        return None
    if pool == "basic" and mode_id == 1:
        return QuotaWindow(
            remaining=max(0, min(int(window.remaining), BASIC_FAST_LIMIT)),
            total=BASIC_FAST_LIMIT,
            window_seconds=BASIC_FAST_WINDOW_SECONDS,
            reset_at=window.reset_at,
            synced_at=window.synced_at,
            source=window.source,
        )
    if pool == "basic" and mode_id == 5:
        # L1 修复：矫正历史 console 配额数据到当前常量值
        # 历史值如 200/24h、70/5min、100/1min、30/15min、30/30min 等均归一到 20/60min
        return QuotaWindow(
            remaining=max(0, min(int(window.remaining), BASIC_CONSOLE_LIMIT)),
            total=BASIC_CONSOLE_LIMIT,
            window_seconds=BASIC_CONSOLE_WINDOW_SECONDS,
            reset_at=window.reset_at,
            synced_at=window.synced_at,
            source=window.source,
        )
    return window


def normalize_quota_set(pool: str, quota_set: AccountQuotaSet) -> AccountQuotaSet:
    """Return a quota set normalized to the supported modes for *pool*."""
    defaults = default_quota_set(pool)

    auto = normalize_quota_window(pool, 0, quota_set.auto) or defaults.auto
    fast = normalize_quota_window(pool, 1, quota_set.fast) or defaults.fast
    expert = normalize_quota_window(pool, 2, quota_set.expert) or defaults.expert

    qs = AccountQuotaSet(auto=auto, fast=fast, expert=expert)
    qs.heavy = normalize_quota_window(pool, 3, quota_set.heavy)
    qs.grok_4_3 = normalize_quota_window(pool, 4, quota_set.grok_4_3)
    qs.console = normalize_quota_window(pool, 5, quota_set.console) or defaults.console
    qs.quota_build = normalize_quota_window(pool, 6, quota_set.quota_build)
    return qs


def infer_pool(windows: dict[int, QuotaWindow]) -> str:
    """Infer pool type from live quota windows returned by the rate-limits API.

    Uses ``auto.total`` (mode_id=0) as the discriminating signal.
    Falls back to ``"basic"`` when the value is absent or unrecognised.
    """
    auto_win = windows.get(0)
    if auto_win is None:
        return "basic"
    return _AUTO_TOTAL_TO_POOL.get(auto_win.total, "basic")


# ---------------------------------------------------------------------------
# Console real-usage mapping (Go PR #853 quota.go)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsoleUsageWindows:
    """Mapped console quota windows: chat (routing) + display-only media."""

    chat: QuotaWindow
    image: QuotaWindow
    video: QuotaWindow


def console_usage_windows(result: "ConsoleUsageResult") -> ConsoleUsageWindows:
    """Map a fetched ``ConsoleUsageResult`` onto account quota windows.

    Go PR #853 semantics:
    * chat — the routing window: ``window_seconds`` = 24h predicted recovery;
      ``predicted=True`` and ``reset_at = fetch time + 24h`` iff ``remaining==0``
      (upstream never confirms a reset time; the sibling parser stamps it).
    * image/video — display-only: ``window_seconds=0``, ``reset_at=None``,
      ``predicted=False``; never participate in routing or expiry.
    * ``usage_percent`` = ``(total-remaining)/total*100`` (0 when total==0);
      ``source`` stays the fetched window's REAL marker.
    """
    from app.dataplane.reverse.protocol.xai_console_usage import (
        CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S,
    )

    def _percent(total: int, remaining: int) -> float:
        if total <= 0:
            return 0.0
        return (total - remaining) / total * 100.0

    def _display_only(window: QuotaWindow) -> QuotaWindow:
        return QuotaWindow(
            remaining=window.remaining,
            total=window.total,
            window_seconds=0,
            reset_at=None,
            synced_at=window.synced_at,
            source=window.source,
            usage_percent=_percent(window.total, window.remaining),
            predicted=False,
        )

    chat = result.chat
    return ConsoleUsageWindows(
        chat=QuotaWindow(
            remaining=chat.remaining,
            total=chat.total,
            window_seconds=CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S,
            reset_at=chat.reset_at,
            synced_at=chat.synced_at,
            source=chat.source,
            usage_percent=_percent(chat.total, chat.remaining),
            predicted=chat.reset_at is not None,
        ),
        image=_display_only(result.image),
        video=_display_only(result.video),
    )


__all__ = [
    "_MODE_KEYS",
    "BUILD_QUOTA_DEFAULTS",
    "BASIC_QUOTA_DEFAULTS",
    "SUPER_QUOTA_DEFAULTS",
    "HEAVY_QUOTA_DEFAULTS",
    "ConsoleUsageWindows",
    "console_usage_windows",
    "default_quota_set",
    "default_quota_window",
    "infer_pool",
    "normalize_quota_set",
    "normalize_quota_window",
    "supported_mode_ids",
    "supports_mode",
]
