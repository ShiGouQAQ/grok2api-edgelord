"""Tests for UpstreamError mapper methods (to_feedback_kind, to_proxy_feedback_kind, to_result_category)."""

from __future__ import annotations

import pytest

from app.control.account.enums import FeedbackKind
from app.control.proxy.models import ProxyFeedbackKind
from app.dataplane.reverse.types import ResultCategory
from app.platform.errors import UpstreamError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _err(
    status: int,
    *,
    credential_rejected: bool = False,
    quota_exhausted: bool = False,
    permanent_account_denial: bool = False,
    body: str = "",
) -> UpstreamError:
    return UpstreamError(
        "test",
        status=status,
        body=body,
        credential_rejected=credential_rejected,
        quota_exhausted=quota_exhausted,
        permanent_account_denial=permanent_account_denial,
    )


# ---------------------------------------------------------------------------
# Scenario data: (row#, kwargs, expected_fb, expected_pfb, expected_rc)
# ---------------------------------------------------------------------------

SCENARIOS: list[tuple[int, dict, FeedbackKind, ProxyFeedbackKind, ResultCategory]] = [
    # 1: 401 bare
    (
        1,
        {"status": 401},
        FeedbackKind.UNAUTHORIZED,
        ProxyFeedbackKind.UNAUTHORIZED,
        ResultCategory.AUTH_FAILURE,
    ),
    # 2: 401 + credential_rejected
    (
        2,
        {"status": 401, "credential_rejected": True},
        FeedbackKind.UNAUTHORIZED,
        ProxyFeedbackKind.UNAUTHORIZED,
        ResultCategory.AUTH_FAILURE,
    ),
    # 3: 402 bare (falls through every branch)
    (
        3,
        {"status": 402},
        FeedbackKind.SERVER_ERROR,
        ProxyFeedbackKind.TRANSPORT_ERROR,
        ResultCategory.UNKNOWN,
    ),
    # 4: 429 bare
    (
        4,
        {"status": 429},
        FeedbackKind.RATE_LIMITED,
        ProxyFeedbackKind.RATE_LIMITED,
        ResultCategory.RATE_LIMITED,
    ),
    # 5: 429 + quota_exhausted
    (
        5,
        {"status": 429, "quota_exhausted": True},
        FeedbackKind.RATE_LIMITED,
        ProxyFeedbackKind.RATE_LIMITED,
        ResultCategory.RATE_LIMITED,
    ),
    # 6: 403 bare
    (
        6,
        {"status": 403},
        FeedbackKind.FORBIDDEN,
        ProxyFeedbackKind.FORBIDDEN,
        ResultCategory.FORBIDDEN,
    ),
    # 7: 403 + credential_rejected (hits credential guard first)
    (
        7,
        {"status": 403, "credential_rejected": True},
        FeedbackKind.UNAUTHORIZED,
        ProxyFeedbackKind.UNAUTHORIZED,
        ResultCategory.AUTH_FAILURE,
    ),
    # 8: 403 + permanent_account_denial (DIVERGENCE — see test below)
    (
        8,
        {"status": 403, "permanent_account_denial": True},
        FeedbackKind.FORBIDDEN,
        ProxyFeedbackKind.FORBIDDEN,
        ResultCategory.AUTH_FAILURE,
    ),
    # 9: 403 + quota_exhausted (hits quota guard first)
    (
        9,
        {"status": 403, "quota_exhausted": True},
        FeedbackKind.RATE_LIMITED,
        ProxyFeedbackKind.RATE_LIMITED,
        ResultCategory.RATE_LIMITED,
    ),
    # 10: 404 bare
    (
        10,
        {"status": 404},
        FeedbackKind.SERVER_ERROR,
        ProxyFeedbackKind.TRANSPORT_ERROR,
        ResultCategory.NOT_FOUND,
    ),
    # 11: 500 bare
    (
        11,
        {"status": 500},
        FeedbackKind.SERVER_ERROR,
        ProxyFeedbackKind.UPSTREAM_5XX,
        ResultCategory.UPSTREAM_5XX,
    ),
    # 12: 502 bare
    (
        12,
        {"status": 502},
        FeedbackKind.SERVER_ERROR,
        ProxyFeedbackKind.UPSTREAM_5XX,
        ResultCategory.UPSTREAM_5XX,
    ),
    # 13: 200 bare (success-range status, but still an error object)
    (
        13,
        {"status": 200},
        FeedbackKind.SERVER_ERROR,
        ProxyFeedbackKind.TRANSPORT_ERROR,
        ResultCategory.UNKNOWN,
    ),
    # 14: 0 bare (network-level failure)
    (
        14,
        {"status": 0},
        FeedbackKind.SERVER_ERROR,
        ProxyFeedbackKind.TRANSPORT_ERROR,
        ResultCategory.UNKNOWN,
    ),
]


# ---------------------------------------------------------------------------
# Test classes — one per mapper
# ---------------------------------------------------------------------------


class TestToFeedbackKind:
    """UpstreamError.to_feedback_kind() → FeedbackKind mapping."""

    @pytest.mark.parametrize(
        "row,kwargs,expected,_pfb,_rc",
        SCENARIOS,
        ids=[f"row{s[0]}" for s in SCENARIOS],
    )
    def test_feedback_kind(
        self,
        row: int,
        kwargs: dict,
        expected: FeedbackKind,
        _pfb: ProxyFeedbackKind,
        _rc: ResultCategory,
    ) -> None:
        err = _err(**kwargs)
        assert err.to_feedback_kind() is expected

    def test_403_permanent_denial_returns_forbidden(self) -> None:
        """403+permanent_account_denial → FORBIDDEN (not UNAUTHORIZED).

        to_feedback_kind checks permanent_account_denial BEFORE falling
        through to the status==403 branch, but both map to FORBIDDEN.
        """
        err = _err(status=403, permanent_account_denial=True)
        assert err.to_feedback_kind() is FeedbackKind.FORBIDDEN

    def test_credential_rejected_overrides_status(self) -> None:
        """credential_rejected=True on any status → UNAUTHORIZED (first guard wins)."""
        err = _err(status=500, credential_rejected=True)
        assert err.to_feedback_kind() is FeedbackKind.UNAUTHORIZED


class TestToProxyFeedbackKind:
    """UpstreamError.to_proxy_feedback_kind() → ProxyFeedbackKind mapping."""

    @pytest.mark.parametrize(
        "row,kwargs,_fb,expected,_rc",
        SCENARIOS,
        ids=[f"row{s[0]}" for s in SCENARIOS],
    )
    def test_proxy_feedback_kind(
        self,
        row: int,
        kwargs: dict,
        _fb: FeedbackKind,
        expected: ProxyFeedbackKind,
        _rc: ResultCategory,
    ) -> None:
        err = _err(**kwargs)
        assert err.to_proxy_feedback_kind() is expected

    def test_5xx_maps_to_upstream_5xx(self) -> None:
        err = _err(status=503)
        assert err.to_proxy_feedback_kind() is ProxyFeedbackKind.UPSTREAM_5XX

    def test_non_5xx_non_known_maps_to_transport_error(self) -> None:
        err = _err(status=418)  # I'm a teapot
        assert err.to_proxy_feedback_kind() is ProxyFeedbackKind.TRANSPORT_ERROR


class TestToResultCategory:
    """UpstreamError.to_result_category() → ResultCategory mapping."""

    @pytest.mark.parametrize(
        "row,kwargs,_fb,_pfb,expected",
        SCENARIOS,
        ids=[f"row{s[0]}" for s in SCENARIOS],
    )
    def test_result_category(
        self,
        row: int,
        kwargs: dict,
        _fb: FeedbackKind,
        _pfb: ProxyFeedbackKind,
        expected: ResultCategory,
    ) -> None:
        err = _err(**kwargs)
        assert err.to_result_category() is expected

    def test_404_maps_to_not_found(self) -> None:
        err = _err(status=404)
        assert err.to_result_category() is ResultCategory.NOT_FOUND

    def test_402_maps_to_unknown(self) -> None:
        """402 with no flags falls through every branch to UNKNOWN."""
        err = _err(status=402)
        assert err.to_result_category() is ResultCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Key divergence: 403 + permanent_account_denial
# ---------------------------------------------------------------------------


class Test403PermanentDenialDivergence:
    """403+permanent_account_denial: to_feedback_kind=FORBIDDEN, to_result_category=AUTH_FAILURE.

    Intentional: state machine sees 'access denied' (don't retry),
    pipeline sees 'auth failure' (terminate).
    """

    def test_divergence_explicit(self) -> None:
        err = _err(status=403, permanent_account_denial=True)

        # Account layer: FORBIDDEN → don't retry, don't rotate
        assert err.to_feedback_kind() is FeedbackKind.FORBIDDEN

        # Proxy layer: also FORBIDDEN → don't switch nodes
        assert err.to_proxy_feedback_kind() is ProxyFeedbackKind.FORBIDDEN

        # Pipeline layer: AUTH_FAILURE → terminate stream
        assert err.to_result_category() is ResultCategory.AUTH_FAILURE

    def test_divergence_without_flag_is_consistent(self) -> None:
        """Plain 403 (no permanent_account_denial) → all three agree on FORBIDDEN."""
        err = _err(status=403)

        assert err.to_feedback_kind() is FeedbackKind.FORBIDDEN
        assert err.to_proxy_feedback_kind() is ProxyFeedbackKind.FORBIDDEN
        assert err.to_result_category() is ResultCategory.FORBIDDEN
