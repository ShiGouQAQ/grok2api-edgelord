"""Tests for the config-driven routing attempt policy.

Port of chenyme/grok2api commits 15146556 + 72340380
(``routingAttemptPolicy`` / ``newRoutingAttemptPolicy`` in
backend/internal/application/gateway/service.go, plus ``routing.maxAttempts``
validation in backend/internal/infra/config/config.go).
"""

import pytest

from app.dataplane.account.selector import current_strategy, set_strategy
from app.products import _routing_policy as rp
from app.products._account_selection import selection_max_retries


@pytest.fixture
def quota_strategy():
    """Switch the process-global selection strategy to quota and restore it."""
    prev = current_strategy()
    set_strategy("quota")
    try:
        yield
    finally:
        set_strategy(prev)


# ---------------------------------------------------------------------------
# new_routing_attempt_policy — faithful Go semantics
# ---------------------------------------------------------------------------


class TestNewRoutingAttemptPolicy:
    def test_unlimited_sentinel_allows_unbounded_attempts(self):
        policy = rp.new_routing_attempt_policy(-1)
        assert policy.unlimited
        # Any attempt number, however large, is allowed while candidates remain.
        for attempt in range(0, 10_000, 97):
            assert policy.allows(attempt)
            assert policy.has_next(attempt)

    def test_non_positive_configured_falls_back_to_three(self):
        for configured in (0, -5, -100):
            policy = rp.new_routing_attempt_policy(configured)
            assert not policy.unlimited
            assert policy.limit == 3
        policy = rp.new_routing_attempt_policy(0)
        assert policy.allows(2)
        assert not policy.allows(3)

    def test_configured_limit_bounds_attempts(self):
        policy = rp.new_routing_attempt_policy(200)
        assert policy.limit == 200
        assert policy.allows(0)
        assert policy.allows(199)
        assert not policy.allows(200)
        assert policy.has_next(198)
        assert not policy.has_next(199)
        assert policy.total_attempts == 200
        assert policy.retry_budget == 199

    def test_configured_limit_three_bounds_attempts(self):
        policy = rp.new_routing_attempt_policy(3)
        assert policy.allows(2)
        assert not policy.allows(3)
        assert policy.has_next(1)
        assert not policy.has_next(2)


# ---------------------------------------------------------------------------
# routing_attempt_policy — config-driven resolution + validation
# ---------------------------------------------------------------------------


class TestConfigDrivenResolution:
    def test_config_unlimited_sentinel_accepted(self, monkeypatch):
        monkeypatch.setattr(rp, "get_config", lambda key, default=None: -1)
        policy = rp.routing_attempt_policy()
        assert policy.unlimited

    def test_config_zero_rejected(self, monkeypatch):
        monkeypatch.setattr(rp, "get_config", lambda key, default=None: 0)
        with pytest.raises(ValueError, match="routing.max_routing_attempts"):
            rp.routing_attempt_policy()

    def test_config_above_cap_rejected(self, monkeypatch):
        monkeypatch.setattr(rp, "get_config", lambda key, default=None: 201)
        with pytest.raises(ValueError, match="routing.max_routing_attempts"):
            rp.routing_attempt_policy()

    def test_config_below_sentinel_rejected(self, monkeypatch):
        monkeypatch.setattr(rp, "get_config", lambda key, default=None: -2)
        with pytest.raises(ValueError, match="routing.max_routing_attempts"):
            rp.routing_attempt_policy()

    def test_unset_config_preserves_random_five_retries(self, monkeypatch):
        monkeypatch.setattr(rp, "get_config", lambda key, default=None: None)
        prev = current_strategy()
        set_strategy("random")
        try:
            expected = selection_max_retries() + 1
            policy = rp.routing_attempt_policy()
        finally:
            set_strategy(prev)
        assert policy.limit == expected
        assert policy.limit == 6

    def test_unset_config_preserves_quota_one_retry(self, quota_strategy, monkeypatch):
        monkeypatch.setattr(rp, "get_config", lambda key, default=None: None)
        # Quota strategy retries retry.max_retries (=1) times => 2 attempts.
        policy = rp.routing_attempt_policy()
        assert policy.limit == 2
        assert policy.allows(1)
        assert not policy.allows(2)

    def test_unset_config_with_explicit_legacy_retries(self, monkeypatch):
        monkeypatch.setattr(rp, "get_config", lambda key, default=None: None)
        # Product loops pass their module-level selection_max_retries so
        # per-module monkeypatching keeps working (test_image_edit pattern).
        policy = rp.routing_attempt_policy(1)
        assert policy.limit == 2
        assert policy.allows(1)
        assert not policy.allows(2)

    def test_explicit_two_hundred_is_honored_as_two_hundred(self, monkeypatch):
        # Go: newRoutingAttemptPolicy(configured) with configured=200 -> 200
        # attempts. 200 is the legal max, NOT a "not overridden" sentinel.
        monkeypatch.setattr(rp, "get_config", lambda key, default=None: 200)
        policy = rp.routing_attempt_policy(1)
        assert policy.limit == 200
        assert not policy.unlimited
        assert policy.allows(199)
        assert not policy.allows(200)

    def test_explicit_three_is_honored(self, monkeypatch):
        monkeypatch.setattr(rp, "get_config", lambda key, default=None: 3)
        policy = rp.routing_attempt_policy(1)
        assert policy.limit == 3
        assert policy.allows(2)
        assert not policy.allows(3)

    def test_explicit_non_default_limit_activates_config(self, monkeypatch):
        monkeypatch.setattr(rp, "get_config", lambda key, default=None: 100)
        policy = rp.routing_attempt_policy(1)
        assert policy.limit == 100
        assert not policy.unlimited


# ---------------------------------------------------------------------------
# Stored Responses: previous_response_id forces attempts=1 (Go ownership != nil)
# ---------------------------------------------------------------------------


class TestStoredResponsesSingleAttempt:
    @pytest.mark.asyncio
    async def test_stored_response_forces_single_attempt(self, monkeypatch):
        from types import SimpleNamespace

        from app.platform.errors import UpstreamError
        from app.products.openai import responses as resp_mod

        spec = SimpleNamespace(
            mode_id=2,
            pool_candidates=lambda: ["super"],
            model_name="grok-4.20-auto",
            is_chat=lambda: True,
            is_console_chat=lambda: False,
            is_build=lambda: False,
        )
        monkeypatch.setattr(resp_mod, "resolve_model", lambda model: spec)
        monkeypatch.setattr(resp_mod, "_configured_retry_codes", lambda cfg: {401})
        monkeypatch.setattr(resp_mod, "_should_retry_upstream", lambda exc, codes: True)

        calls = {"reserves": 0, "releases": 0}

        class _Acct:
            token = "tok_abc12345678"

        class _Dir:
            async def reserve(
                self, pool_candidates, mode_id, now_s_override=None, exclude_tokens=None
            ):
                calls["reserves"] += 1
                return _Acct()

            async def release(self, acct):
                calls["releases"] += 1

            async def feedback(self, token, kind, mode, now_s_val=None):
                pass

        monkeypatch.setattr("app.dataplane.account._directory", _Dir())

        async def _failing_stream(*args, **kwargs):
            raise UpstreamError("stored response failed: 401 unauthorized", status=401)
            yield  # unreachable: keep it an async generator

        monkeypatch.setattr(resp_mod, "_stream_chat", _failing_stream)

        async def _noop(*a, **k):
            pass

        monkeypatch.setattr(resp_mod, "_quota_sync", _noop)
        monkeypatch.setattr(resp_mod, "_fail_sync", _noop)

        with pytest.raises(UpstreamError):
            await resp_mod.create(
                model="grok-4.20-auto",
                input_val="hello",
                instructions=None,
                stream=False,
                emit_think=False,
                temperature=0.7,
                top_p=1.0,
                previous_response_id="resp_abc123",
            )

        # Stored-response chain: exactly one attempt, no account swapping.
        assert calls["reserves"] == 1
        assert calls["releases"] == 1

    @pytest.mark.asyncio
    async def test_non_stored_keeps_legacy_attempt_budget(self, monkeypatch):
        import app.products._routing_policy as rp_mod

        monkeypatch.setattr(rp_mod, "get_config", lambda key, default=None: None)
        from types import SimpleNamespace

        from app.platform.errors import UpstreamError
        from app.products.openai import responses as resp_mod

        spec = SimpleNamespace(
            mode_id=2,
            pool_candidates=lambda: ["super"],
            model_name="grok-4.20-auto",
            is_chat=lambda: True,
            is_console_chat=lambda: False,
            is_build=lambda: False,
        )
        monkeypatch.setattr(resp_mod, "resolve_model", lambda model: spec)
        monkeypatch.setattr(resp_mod, "selection_max_retries", lambda: 5)
        monkeypatch.setattr(resp_mod, "_configured_retry_codes", lambda cfg: {401})
        monkeypatch.setattr(resp_mod, "_should_retry_upstream", lambda exc, codes: True)

        calls = {"reserves": 0, "releases": 0}

        class _Acct:
            token = "tok_abc12345678"

        class _Dir:
            async def reserve(
                self, pool_candidates, mode_id, now_s_override=None, exclude_tokens=None
            ):
                calls["reserves"] += 1
                return _Acct()

            async def release(self, acct):
                calls["releases"] += 1

            async def feedback(self, token, kind, mode, now_s_val=None):
                pass

        monkeypatch.setattr("app.dataplane.account._directory", _Dir())

        async def _failing_stream(*args, **kwargs):
            raise UpstreamError("upstream failed: 401 unauthorized", status=401)
            yield  # unreachable: keep it an async generator

        monkeypatch.setattr(resp_mod, "_stream_chat", _failing_stream)

        async def _noop(*a, **k):
            pass

        monkeypatch.setattr(resp_mod, "_quota_sync", _noop)
        monkeypatch.setattr(resp_mod, "_fail_sync", _noop)

        with pytest.raises(UpstreamError):
            await resp_mod.create(
                model="grok-4.20-auto",
                input_val="hello",
                instructions=None,
                stream=False,
                emit_think=False,
                temperature=0.7,
                top_p=1.0,
            )

        # No stored response: legacy random budget (5 retries -> 6 attempts).
        assert calls["reserves"] == 6
        assert calls["releases"] == 6
