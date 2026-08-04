"""Config-driven routing attempt policy.

Port of chenyme/grok2api commits 15146556 + 72340380: ``routingAttemptPolicy``
/ ``newRoutingAttemptPolicy`` (backend/internal/application/gateway/service.go)
plus the ``routing.maxAttempts`` validation
(backend/internal/infra/config/config.go).
"""

from dataclasses import dataclass

from app.platform.config.snapshot import get_config
from app.products._account_selection import selection_max_retries

# Go: unlimitedRoutingAttempts = -1 — config sentinel for unlimited retries.
UNLIMITED_ROUTING_ATTEMPTS = -1
# Go: newRoutingAttemptPolicy() falls back to 3 for any other value <= 0.
_DEFAULT_ROUTING_LIMIT = 3
# Go: maxRoutingAttempts = 200 — validation cap.
MAX_ROUTING_ATTEMPTS = 200
# Shipped default of routing.max_routing_attempts in config.defaults.toml.
# The merged config always carries it, so treat it as "not overridden": the
# default config must preserve the legacy strategy-aware budget (5 random /
# 1 quota retries). Only explicit non-default values activate config routing.
_SHIPPED_DEFAULT_ATTEMPTS = 200


@dataclass(frozen=True, slots=True)
class RoutingAttemptPolicy:
    """Attempt budget for account-swap loops.

    Mirrors Go ``routingAttemptPolicy``: ``allows(attempt)`` is the loop
    condition, ``has_next(attempt)`` decides whether a retry after a failed
    attempt is permitted.
    """

    limit: int = 0
    unlimited: bool = False

    def allows(self, attempt: int) -> bool:
        """Whether attempt number *attempt* (0-based) may still run."""
        return self.unlimited or attempt < self.limit

    def has_next(self, attempt: int) -> bool:
        """Whether a retry after failed attempt *attempt* is permitted."""
        return self.unlimited or attempt + 1 < self.limit

    @property
    def total_attempts(self) -> int:
        """Total attempts for log denominators; -1 when unlimited."""
        return -1 if self.unlimited else self.limit

    @property
    def retry_budget(self) -> int:
        """Retries beyond the first attempt for log denominators; -1 when unlimited."""
        return -1 if self.unlimited else self.limit - 1


def new_routing_attempt_policy(configured: int) -> RoutingAttemptPolicy:
    """Build a policy from a configured attempt limit (Go ``newRoutingAttemptPolicy``).

    - ``-1`` → unlimited
    - any other value ``<= 0`` → limit 3
    - otherwise → the configured limit
    """
    if configured == UNLIMITED_ROUTING_ATTEMPTS:
        return RoutingAttemptPolicy(unlimited=True)
    if configured <= 0:
        configured = _DEFAULT_ROUTING_LIMIT
    return RoutingAttemptPolicy(limit=configured)


def routing_attempt_policy(legacy_retries: int | None = None) -> RoutingAttemptPolicy:
    """Policy for a request, driven by ``routing.max_routing_attempts``.

    *legacy_retries* is the module-level ``selection_max_retries()`` of the
    calling product handler (so per-module monkeypatching keeps working). When
    the config key is unset it is used as the fallback limit, preserving the
    historical behaviour: random strategy 5 retries (6 attempts), quota
    strategy 1 retry (2 attempts). The ``+1`` converts the legacy *retry*
    count into the policy's *attempt* count.

    Raises ValueError when the configured value is invalid (Go
    ``Config.Validate``: only -1 and 1..200 are allowed).
    """
    configured = get_config("routing.max_routing_attempts", None)
    if configured is None or int(configured) == _SHIPPED_DEFAULT_ATTEMPTS:
        if legacy_retries is None:
            legacy_retries = selection_max_retries()
        return new_routing_attempt_policy(legacy_retries + 1)
    limit = int(configured)
    if limit < UNLIMITED_ROUTING_ATTEMPTS or limit == 0 or limit > MAX_ROUTING_ATTEMPTS:
        raise ValueError(
            "routing.max_routing_attempts must be -1 (unlimited) "
            f"or 1..{MAX_ROUTING_ATTEMPTS}, got {limit}"
        )
    return new_routing_attempt_policy(limit)


__all__ = [
    "MAX_ROUTING_ATTEMPTS",
    "UNLIMITED_ROUTING_ATTEMPTS",
    "RoutingAttemptPolicy",
    "new_routing_attempt_policy",
    "routing_attempt_policy",
]
