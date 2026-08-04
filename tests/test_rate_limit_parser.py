"""Tests for the Build Team+Model rate-limit metadata parser (Go rate_limit.go port)."""

from app.dataplane.reverse.protocol.rate_limit import (
    RateLimitMetadata,
    parse_rate_limit_metadata,
    rate_limit_from_response,
)


class TestParseRateLimitMetadata:
    def test_build_team_rps(self):
        """Go 用例：team + model + Requests per Second → RPS metadata"""
        body = (
            '{"code":"resource-exhausted","error":"Too many requests for team '
            "f1692451-874f-4765-ab9b-5285f6c6ff65 and model grok-4.5-build-free. "
            "Your team's rate limit is — Requests per Second (actual/limit): 2/2.\"}"
        )
        meta = parse_rate_limit_metadata(body)
        assert meta is not None
        assert meta.requests_per_second == 2.0
        assert meta.requests_per_minute is None
        assert meta.team_id == "f1692451-874f-4765-ab9b-5285f6c6ff65"
        assert meta.model == "grok-4.5-build-free"
        assert meta.resets_in_seconds == 2.0  # RPS 默认冷却 2s

    def test_requests_per_minute(self):
        """Requests per Minute → requests_per_minute，默认冷却 60s"""
        meta = parse_rate_limit_metadata("Requests per Minute (actual/limit): 5/10")
        assert meta is not None
        assert meta.requests_per_minute == 5.0
        assert meta.requests_per_second is None
        assert meta.resets_in_seconds == 60.0

    def test_resets_in_window(self):
        """resets in: 1d 2h 3m 4s → 93784 秒"""
        meta = parse_rate_limit_metadata(
            "Requests per Second (actual/limit): 1/1, resets in: 1d 2h 3m 4s"
        )
        assert meta is not None
        assert meta.resets_in_seconds == 93784.0

    def test_rps_resets_in_below_floor_clamped(self):
        """RPS + resets in 1s → 钳制到默认 2s"""
        meta = parse_rate_limit_metadata(
            "Requests per Second (actual/limit): 2/2, resets in: 1s"
        )
        assert meta is not None
        assert meta.resets_in_seconds == 2.0

    def test_model_with_quotes_and_trailing_punctuation(self):
        """model 名带引号/尾随标点 → 剥离"""
        meta = parse_rate_limit_metadata(
            'model "grok-4.5-build-free.", Requests per Minute (actual/limit): 3/3'
        )
        assert meta is not None
        assert meta.model == "grok-4.5-build-free"

    def test_no_usage_match_returns_none(self):
        """普通 429 body（无 RPS/RPM 元数据）→ None"""
        assert (
            parse_rate_limit_metadata(
                '{"error":"You are sending requests too quickly"}'
            )
            is None
        )

    def test_empty_body_returns_none(self):
        assert parse_rate_limit_metadata("") is None

    def test_returns_dataclass(self):
        meta = parse_rate_limit_metadata("Requests per Minute (actual/limit): 1/2")
        assert isinstance(meta, RateLimitMetadata)


class TestRateLimitFromResponse:
    def test_backfills_retry_after_from_body(self):
        """429 + body 带 resets-in、无 Retry-After 头 → 回填 header"""
        headers: dict[str, str] = {}
        meta = rate_limit_from_response(
            429,
            headers,
            "Requests per Second (actual/limit): 2/2, resets in: 30s",
        )
        assert meta is not None
        assert meta.resets_in_seconds == 30.0
        assert headers.get("Retry-After") == "30"

    def test_retry_after_header_wins(self):
        """429 + Retry-After 头存在 → 用 header 值，不回填"""
        headers: dict[str, str] = {"Retry-After": "120"}
        meta = rate_limit_from_response(
            429,
            headers,
            "Requests per Second (actual/limit): 2/2, resets in: 30s",
        )
        assert meta is not None
        assert meta.resets_in_seconds == 120.0
        assert headers["Retry-After"] == "120"

    def test_non_429_returns_none(self):
        assert (
            rate_limit_from_response(403, {}, "Requests per Minute (actual/limit): 1/1")
            is None
        )

    def test_429_without_metadata_returns_none(self):
        assert rate_limit_from_response(429, {}, "rate limited") is None

    def test_no_resets_no_header_keeps_default(self):
        headers: dict[str, str] = {}
        meta = rate_limit_from_response(
            429, headers, "Requests per Minute (actual/limit): 1/1"
        )
        assert meta is not None
        assert meta.resets_in_seconds == 60.0
        assert headers.get("Retry-After") == "60"
