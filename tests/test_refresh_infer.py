"""Unit tests for _infer_pool_from_live_windows in refresh.py.

Covers the ported Go 4f34707 mode_id=3 (heavy) boundary.
"""

from app.control.account.enums import QuotaSource
from app.control.account.models import QuotaWindow
from app.control.account.refresh import _infer_pool_from_live_windows


def _w(total: int, **kw) -> QuotaWindow:
    """Quick QuotaWindow factory — only total matters for inference."""
    return QuotaWindow(
        remaining=kw.get("remaining", 0),
        total=total,
        window_seconds=kw.get("window_seconds", 3600),
        reset_at=kw.get("reset_at"),
        synced_at=kw.get("synced_at"),
        source=QuotaSource.REAL,
    )


class TestInferPoolFromLiveWindows:
    """_infer_pool_from_live_windows — pure logic, no IO."""

    # --- Auto (mode 0) inference ---

    def test_auto_total_20_infers_basic(self):
        windows = {0: _w(20)}
        assert _infer_pool_from_live_windows(windows) == "basic"

    def test_auto_total_50_infers_super(self):
        windows = {0: _w(50)}
        assert _infer_pool_from_live_windows(windows) == "super"

    def test_auto_total_150_infers_heavy(self):
        windows = {0: _w(150)}
        assert _infer_pool_from_live_windows(windows) == "heavy"

    def test_no_auto_window_returns_none(self):
        """仅非自动模式的配额，无 auto 窗口 → None"""
        windows = {1: _w(20)}
        assert _infer_pool_from_live_windows(windows) is None

    def test_empty_windows_returns_none(self):
        assert _infer_pool_from_live_windows({}) is None

    # --- Expert (mode 2) fallback ---

    def test_expert_total_50_infers_super(self):
        windows = {2: _w(50)}
        assert _infer_pool_from_live_windows(windows) == "super"

    def test_expert_total_150_infers_heavy(self):
        windows = {2: _w(150)}
        assert _infer_pool_from_live_windows(windows) == "heavy"

    # --- Grok 4.3 (mode 4) fallback ---

    def test_grok_4_3_total_50_infers_super(self):
        windows = {4: _w(50)}
        assert _infer_pool_from_live_windows(windows) == "super"

    def test_grok_4_3_total_150_infers_heavy(self):
        windows = {4: _w(150)}
        assert _infer_pool_from_live_windows(windows) == "heavy"

    # --- Heavy (mode 3) — 边界新增 (port of Go 4f34707) ---

    def test_heavy_mode3_total_20_infers_heavy(self):
        """mode_id=3, total=20 → heavy"""
        windows = {3: _w(20)}
        assert _infer_pool_from_live_windows(windows) == "heavy"

    def test_heavy_mode3_total_20_with_other_modes(self):
        """mode 3 heavy 优先于 mode 2 的 non-matching total"""
        windows = {2: _w(10), 3: _w(20)}
        assert _infer_pool_from_live_windows(windows) == "heavy"

    def test_heavy_mode3_with_mode4_super_not_confused(self):
        """mode 3 total!=20, mode 4 total=50 → super"""
        windows = {3: _w(10), 4: _w(50)}
        assert _infer_pool_from_live_windows(windows) == "super"

    # --- 组合场景 ---

    def test_auto_basic_takes_priority_over_mode3(self):
        """auto=20 (basic) 先于 mode3 检查 → 返回 basic（auto 分支先 return）"""
        windows = {0: _w(20), 3: _w(20)}
        assert _infer_pool_from_live_windows(windows) == "basic"

    def test_all_modes_zero_total(self):
        """所有 mode 的 total=0 → None"""
        windows = {0: _w(0), 1: _w(0), 2: _w(0), 3: _w(0)}
        assert _infer_pool_from_live_windows(windows) is None

    # --- 非标准 total ---

    def test_unknown_total_returns_none(self):
        windows = {2: _w(99)}
        assert _infer_pool_from_live_windows(windows) is None

    def test_negative_total_does_not_match(self):
        """total < 0 → None"""
        windows = {3: _w(-1)}
        assert _infer_pool_from_live_windows(windows) is None
