"""Client key domain model (port of Go ``domain/clientkey.Key``).

Timestamps are stored as epoch milliseconds (int), matching the codebase-wide
``now_ms()`` convention; ISO-8601 conversion happens in the DTO layer.
"""

from dataclasses import dataclass, field

# Go DefaultRPMLimit / DefaultMaxConcurrent — applied when the field is not
# explicitly provided.  Explicit ``0`` means "unlimited" (Go RPMUnlimited /
# ConcurrencyUnlimited semantics).
DEFAULT_RPM_LIMIT = 120
DEFAULT_MAX_CONCURRENT = 8

_KEY_PREFIX = "grok2api_"
_PREFIX_HEX_CHARS = 8  # prefix = "grok2api_" + first 8 hex chars (16 chars total)
_SECRET_HEX_CHARS = 32


@dataclass
class ClientKey:
    id: int = 0
    name: str = ""
    prefix: str = ""
    secret: str = ""  # plaintext — same posture as account tokens in SQLite
    enabled: bool = True
    expires_at: int | None = None  # epoch ms
    rpm_limit: int = DEFAULT_RPM_LIMIT  # 0 = unlimited
    max_concurrent: int = DEFAULT_MAX_CONCURRENT  # 0 = unlimited
    billing_limit_usd_ticks: int = 0  # 0 = unlimited; 1 USD = 1_000_000 ticks
    billed_usage_usd_ticks: int = 0
    allow_model_aliases: bool = False
    allowed_model_ids: list[str] = field(default_factory=list)  # empty = all
    provider_scope: list[str] = field(default_factory=list)  # empty = all
    tier_scope: list[str] = field(default_factory=list)  # empty = all
    last_used_at: int | None = None  # epoch ms
    created_at: int = 0
    updated_at: int = 0

    def is_available(self, now_ms_val: int) -> bool:
        if not self.enabled:
            return False
        return self.expires_at is None or now_ms_val < self.expires_at

    def allows_model(self, model_id: str) -> bool:
        if not self.allowed_model_ids:
            return True
        return model_id in self.allowed_model_ids


__all__ = [
    "ClientKey",
    "DEFAULT_RPM_LIMIT",
    "DEFAULT_MAX_CONCURRENT",
]
